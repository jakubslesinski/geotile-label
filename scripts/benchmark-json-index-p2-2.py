"""Benchmark P2.2 paging, warm summary and annotation delta on an isolated copy.

Only authoritative JSON metadata is copied.  The source project is fingerprinted
before and after and is never registered in the temporary GeoTile Label DATA_DIR.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _load_p2_1_helpers():
    path = REPO_ROOT / "scripts" / "benchmark-json-index-p2-1.py"
    spec = importlib.util.spec_from_file_location("geotile_p2_1_benchmark_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load benchmark helpers: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helpers = _load_p2_1_helpers()

from db import storage  # noqa: E402
from db.storage import list_scene_ids, load_scene_json  # noqa: E402
from services.annotation_summary import compute_project_annotation_summary  # noqa: E402
from services.json_index.project_revision import read_index_state  # noqa: E402
from services.json_index.queries import (  # noqa: E402
    clear_project_cache,
    get_annotation_summary,
    get_scenes_page,
)
from services.json_index.rebuild import rebuild_project_indexes  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warm-reads", type=int, default=7)
    parser.add_argument("--copy-workers", type=int, default=8)
    parser.add_argument("--keep-copy", action="store_true")
    return parser.parse_args()


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def _benchmark_annotation(project_id: str) -> tuple[str, list[dict[str, Any]]]:
    for scene_id in sorted(list_scene_ids(project_id)):
        annotations = load_scene_json(project_id, scene_id, "annotations", default=[])
        if isinstance(annotations, list) and annotations and isinstance(annotations[0], dict):
            return scene_id, annotations
    raise RuntimeError("Benchmark project has no annotations")


def run(args: argparse.Namespace) -> dict[str, Any]:
    source = args.project_root.expanduser().resolve(strict=True)
    project = json.loads((source / "project.json").read_text(encoding="utf-8"))
    project_id = str(project.get("id") or source.name)
    source_before = helpers.metadata_fingerprint(source)
    temp_owner = Path(tempfile.mkdtemp(prefix="geotile-p2-2-benchmark-"))
    data_root = temp_owner / "data"
    copy_root = data_root / "projects" / project_id
    copy_stats = helpers.copy_metadata(source, copy_root, workers=args.copy_workers)

    previous_data_dir = storage.DATA_DIR
    previous_flag = os.environ.get("GEOTILE_JSON_INDEX_V2")
    previous_delta = os.environ.get("GEOTILE_JSON_INDEX_V2_DELTA")
    previous_diagnostic = os.environ.get("GEOTILE_JSON_INDEX_V2_DIAGNOSTIC")
    try:
        storage.DATA_DIR = data_root
        storage._invalidate_project_root_cache()
        clear_project_cache()
        os.environ["GEOTILE_JSON_INDEX_V2"] = "1"
        os.environ["GEOTILE_JSON_INDEX_V2_DELTA"] = "1"
        os.environ.pop("GEOTILE_JSON_INDEX_V2_DIAGNOSTIC", None)

        _index, rebuild_metrics = helpers.measure(
            lambda: rebuild_project_indexes(project_id, force=True)
        )

        clear_project_cache(project_id)
        first_page, first_page_metrics = helpers.measure(
            lambda: get_scenes_page(project_id, limit=100)
        )
        first_summary, first_summary_metrics = helpers.measure(
            lambda: get_annotation_summary(project_id)
        )

        warm_page_times: list[float] = []
        warm_summary_times: list[float] = []
        for _ in range(max(1, int(args.warm_reads))):
            started = time.perf_counter()
            get_scenes_page(project_id, limit=100)
            warm_page_times.append(time.perf_counter() - started)
            started = time.perf_counter()
            get_annotation_summary(project_id)
            warm_summary_times.append(time.perf_counter() - started)

        scene_id, annotations = _benchmark_annotation(project_id)
        synthetic = copy.deepcopy(annotations[0])
        synthetic_id = f"p2-2-benchmark-{time.time_ns()}"
        synthetic["id"] = synthetic_id
        synthetic["source_annotation_id"] = synthetic_id
        synthetic["scene_id"] = scene_id
        synthetic["annotator_email"] = "p2.2-benchmark@local.invalid"
        synthetic["created_by"] = synthetic["annotator_email"]
        synthetic["updated_by"] = synthetic["annotator_email"]
        synthetic.pop("import_id", None)
        synthetic.pop("source_package_id", None)

        _mutated, delta_metrics = helpers.measure(
            lambda: storage.mutate_scene_json(
                project_id,
                scene_id,
                "annotations",
                lambda current: [*current, synthetic],
                default=[],
            )
        )
        delta_state = read_index_state(copy_root)
        delta_summary = get_annotation_summary(project_id)
        legacy_after_delta, validation_scan_metrics = helpers.measure(
            lambda: compute_project_annotation_summary(project_id, use_cache=False)
        )
        source_after = helpers.metadata_fingerprint(source)

        result = {
            "schema_name": "geotile_json_index_p2_2_benchmark",
            "schema_version": 1,
            "source_project": {
                "project_id": project_id,
                "name": project.get("name"),
                "root": str(source),
                "fingerprint_before": source_before,
                "fingerprint_after": source_after,
                "unchanged": source_before == source_after,
            },
            "isolated_copy": {
                "root": str(copy_root) if args.keep_copy else None,
                **copy_stats,
            },
            "counts": {
                "scene_count": first_page.get("total_count"),
                "first_page_count": len(first_page.get("scenes") or []),
                "annotation_count_before_delta": first_summary.get("annotation_count"),
                "annotation_count_after_delta": delta_summary.get("annotation_count"),
            },
            "delta": {
                "scene_id": scene_id,
                "state": delta_state.get("status"),
                "document": delta_state.get("delta_document"),
                "source_revision": delta_state.get("source_revision"),
                "parity_with_full_scan": helpers.canonical_summary(delta_summary)
                == helpers.canonical_summary(legacy_after_delta),
            },
            "metrics": {
                "initial_rebuild": rebuild_metrics,
                "first_page": first_page_metrics,
                "first_summary": first_summary_metrics,
                "warm_page_median_s": statistics.median(warm_page_times),
                "warm_page_p95_s": percentile(warm_page_times, 0.95),
                "warm_summary_median_s": statistics.median(warm_summary_times),
                "warm_summary_p95_s": percentile(warm_summary_times, 0.95),
                "annotation_delta": delta_metrics,
                "validation_full_scan": validation_scan_metrics,
            },
            "gates": {
                "first_page_under_500ms": first_page_metrics["wall_s"] < 0.5,
                "warm_summary_under_200ms": statistics.median(warm_summary_times) < 0.2,
                "delta_published_ready": delta_state.get("status") == "ready"
                and delta_state.get("delta_document") == "annotations",
                "delta_parity": helpers.canonical_summary(delta_summary)
                == helpers.canonical_summary(legacy_after_delta),
                "source_unchanged": source_before == source_after,
            },
        }
        result["passed"] = all(result["gates"].values())
        return result
    finally:
        storage.DATA_DIR = previous_data_dir
        storage._invalidate_project_root_cache()
        clear_project_cache()
        if previous_flag is None:
            os.environ.pop("GEOTILE_JSON_INDEX_V2", None)
        else:
            os.environ["GEOTILE_JSON_INDEX_V2"] = previous_flag
        if previous_delta is None:
            os.environ.pop("GEOTILE_JSON_INDEX_V2_DELTA", None)
        else:
            os.environ["GEOTILE_JSON_INDEX_V2_DELTA"] = previous_delta
        if previous_diagnostic is None:
            os.environ.pop("GEOTILE_JSON_INDEX_V2_DIAGNOSTIC", None)
        else:
            os.environ["GEOTILE_JSON_INDEX_V2_DIAGNOSTIC"] = previous_diagnostic
        if not args.keep_copy:
            resolved_owner = temp_owner.resolve(strict=True)
            resolved_copy = copy_root.resolve(strict=False)
            resolved_copy.relative_to(resolved_owner)
            shutil.rmtree(resolved_owner, ignore_errors=True)


def main() -> int:
    args = parse_args()
    result = run(args)
    output = args.output.expanduser().resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
