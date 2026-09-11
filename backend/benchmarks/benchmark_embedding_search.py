"""Deterministic synthetic benchmark of near-duplicate embedding search."""

from __future__ import annotations

import argparse
import gc
from pathlib import Path
import sys
import tempfile

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.common import add_common_arguments, configure_environment, output_path

import numpy as np


OPERATION = "embedding_search_synthetic"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument("--objects", type=int, default=5000)
    parser.add_argument("--dimensions", type=int, default=384)
    parser.add_argument("--threshold", type=float, default=0.999)
    parser.add_argument("--max-pairs", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--exact-limit", type=int, default=10_000)
    parser.add_argument("--candidate-k", type=int, default=64)
    parser.add_argument("--recall-queries", type=int, default=100)
    parser.add_argument("--recall-k", type=int, default=10)
    parser.add_argument("--ram-budget-mib", type=float, default=4096.0)
    parser.add_argument(
        "--artifact-dir",
        help="Optional persistent ANN directory (allows repeatable query tuning without rebuild)",
    )
    return parser


def run_synthetic_embedding_benchmark(
    *,
    output: Path,
    objects: int,
    dimensions: int,
    threshold: float,
    max_pairs: int,
    seed: int,
    exact_limit: int = 10_000,
    candidate_k: int = 64,
    recall_queries: int = 100,
    recall_k: int = 10,
    ram_budget_mib: float = 4096.0,
    artifact_dir: Path | None = None,
    cache_state: str = "unspecified",
    storage_profile: str = "unspecified",
) -> Path:
    if objects < 2:
        raise ValueError("objects must be at least 2")
    if dimensions < 2:
        raise ValueError("dimensions must be at least 2")
    if not -1.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between -1 and 1")

    from services.embedding_analysis import near_duplicate_pairs
    from services.embedding_index import build_ann_index, restore_ann_index
    from services.performance_metrics import PerformanceRecorder

    rng = np.random.default_rng(seed)
    embeddings = rng.normal(size=(objects, dimensions)).astype(np.float32)
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-9
    duplicate_stride = max(2, objects // 20)
    for index in range(duplicate_stride, objects, duplicate_stride):
        embeddings[index] = embeddings[index - 1]
    metadata = [
        {
            "annotation_id": f"annotation-{index}",
            "scene_id": f"scene-{index // 1000}",
            "class_id": index % 5,
            "bbox": [0.0, 0.0, 16.0, 16.0],
        }
        for index in range(objects)
    ]
    index_value = {
        "embeddings": embeddings,
        "objects": metadata,
        "class_names": {class_id: f"class-{class_id}" for class_id in range(5)},
    }

    with PerformanceRecorder(
        OPERATION,
        output_path=output,
        cache_state=cache_state,
        storage_profile=storage_profile,
        inputs={
            "objects": objects,
            "dimensions": dimensions,
            "threshold": threshold,
            "max_pairs": max_pairs,
            "seed": seed,
            "exact_limit": exact_limit,
            "candidate_k": candidate_k,
            "recall_queries": recall_queries,
            "recall_k": recall_k,
            "ram_budget_mib": ram_budget_mib,
        },
        metadata_value={"synthetic": True},
    ) as recorder:
        ann_index = None
        temporary = None
        backend = "exact"
        if objects > exact_limit:
            if artifact_dir is None:
                temporary = tempfile.TemporaryDirectory(prefix="geotile-ann-benchmark-")
                used_artifact_dir = Path(temporary.name)
            else:
                used_artifact_dir = Path(artifact_dir)
                used_artifact_dir.mkdir(parents=True, exist_ok=True)
            with recorder.stage("ann_build") as timer:
                ann_info = build_ann_index(
                    used_artifact_dir,
                    embeddings,
                    exact_limit=exact_limit,
                )
                timer.record(objects=objects, backend=ann_info["backend"])
            backend = str(ann_info["backend"])
            ann_index = restore_ann_index(used_artifact_dir)

            query_count = min(max(1, recall_queries), objects)
            used_k = min(max(1, recall_k), objects - 1)
            recalls = []
            with recorder.stage("ann_recall") as timer:
                for query in rng.choice(objects, query_count, replace=False):
                    similarities = embeddings @ embeddings[int(query)]
                    exact = set(
                        np.argsort(similarities)[::-1][1 : used_k + 1].tolist()
                    )
                    approximate = [
                        int(value)
                        for value in np.asarray(
                            ann_index.search(embeddings[int(query)], used_k + 1).keys
                        )
                        if int(value) != int(query)
                    ][:used_k]
                    recalls.append(len(exact.intersection(approximate)) / used_k)
                mean_recall = float(np.mean(recalls))
                timer.record(mean_recall_at_k=mean_recall, k=used_k, queries=query_count)
            recorder.set_metric("ann_recall_at_k", round(mean_recall, 6))
            recorder.set_metric("ann_recall_gate_pass", mean_recall >= 0.98)

        try:
            duplicate_backend = "exact" if objects <= exact_limit else "cosine_lsh"
            with recorder.stage("near_duplicate_pairs") as timer:
                pairs = near_duplicate_pairs(
                    index_value,
                    threshold=threshold,
                    max_pairs=max_pairs,
                    ann_index=ann_index,
                    exact_limit=exact_limit,
                    candidate_k=candidate_k,
                )
                timer.record(objects=objects, pairs=len(pairs), backend=duplicate_backend)
        finally:
            if ann_index is not None:
                del ann_index
                gc.collect()  # release mmap before TemporaryDirectory cleanup on Windows
            if temporary is not None:
                temporary.cleanup()
        elapsed = recorder.report["stages"]["near_duplicate_pairs"]["wall_seconds"]
        recorder.set_metric("pairs_returned", len(pairs))
        recorder.set_metric("objects_per_second", round(objects / max(elapsed, 1e-9), 3))
        recorder.set_metric("embedding_bytes", int(embeddings.nbytes))
        recorder.set_metric("search_backend", backend)
        recorder.set_metric("duplicate_search_backend", duplicate_backend)
        recorder.set_metric("ram_budget_mib", float(ram_budget_mib))
        peaks = [
            float(stage["peak_rss_mb"])
            for stage in recorder.report["stages"].values()
            if stage.get("peak_rss_mb") is not None
        ]
        recorder.set_metric(
            "ram_budget_gate_pass", not peaks or max(peaks) <= float(ram_budget_mib)
        )
    return output


def run(args: argparse.Namespace) -> Path:
    configure_environment(args)
    return run_synthetic_embedding_benchmark(
        output=output_path(args, OPERATION),
        objects=args.objects,
        dimensions=args.dimensions,
        threshold=args.threshold,
        max_pairs=args.max_pairs,
        seed=args.seed,
        exact_limit=args.exact_limit,
        candidate_k=args.candidate_k,
        recall_queries=args.recall_queries,
        recall_k=args.recall_k,
        ram_budget_mib=args.ram_budget_mib,
        artifact_dir=Path(args.artifact_dir).expanduser().resolve() if args.artifact_dir else None,
        cache_state=args.cache_state,
        storage_profile=args.storage_profile,
    )


def main() -> None:
    args = build_parser().parse_args()
    destination = run(args)
    print(destination)


if __name__ == "__main__":
    main()
