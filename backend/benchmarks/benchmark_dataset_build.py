"""Deterministic synthetic benchmark of the dataset materialization pipeline."""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.common import add_common_arguments, configure_environment, consume_generator, output_path

import numpy as np
from PIL import Image


OPERATION = "dataset_build_synthetic"


def dataset_tree_signature(root: Path) -> str:
    """Stable content fingerprint independent of the temporary benchmark path."""
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument("--tile-count", type=int, default=64)
    parser.add_argument("--tile-size", type=int, default=256)
    parser.add_argument("--scene-count", type=int, default=4)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--memory-budget-mib", type=int)
    parser.add_argument("--seed", type=int, default=42)
    return parser


def run_synthetic_dataset_benchmark(
    *,
    output: Path,
    tile_count: int,
    tile_size: int,
    seed: int,
    cache_state: str = "unspecified",
    storage_profile: str = "unspecified",
    scene_count: int = 4,
    workers: int | None = None,
    memory_budget_mib: int | None = None,
) -> Path:
    if tile_count < 1:
        raise ValueError("tile_count must be positive")
    if tile_size < 32:
        raise ValueError("tile_size must be at least 32")
    if scene_count < 1 or scene_count > tile_count:
        raise ValueError("scene_count must be between 1 and tile_count")

    from models.dataset_config import DatasetConfig
    from models.preprocessing import PreprocessingProfile
    from models.tiling_config import TileInfo
    from services.dataset_builder import build_dataset
    from services.performance_metrics import PerformanceRecorder

    rng = np.random.default_rng(seed)

    with tempfile.TemporaryDirectory(prefix="geotile-benchmark-dataset-") as temp_name:
        temp_root = Path(temp_name)
        tiles: list[TileInfo] = []
        annotations: dict[str, list[list[float]]] = {}
        scene_context: dict[str, dict] = {}
        scene_source_map: dict[str, str] = {}
        scene_manifests: dict[str, dict] = {}
        remaining = tile_count
        global_index = 0
        for scene_index in range(scene_count):
            count = remaining // (scene_count - scene_index)
            remaining -= count
            columns = int(math.ceil(math.sqrt(count)))
            rows = int(math.ceil(count / columns))
            width, height = columns * tile_size, rows * tile_size
            scene_id = f"benchmark-scene-{scene_index:03d}"
            source_path = temp_root / f"{scene_id}.png"
            source = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
            Image.fromarray(source, mode="RGB").save(source_path, "PNG", compress_level=1)
            del source
            scene_context[scene_id] = {"filename": source_path.name}
            scene_source_map[scene_id] = str(source_path)
            scene_manifests[scene_id] = {}
            for local_index in range(count):
                row, col = divmod(local_index, columns)
                filename = f"{scene_id}__tile_{row:04d}_{col:04d}.png"
                tiles.append(
                    TileInfo(
                        tile_id=f"tile-{global_index}",
                        scene_id=scene_id,
                        filename=filename,
                        col=col,
                        row=row,
                        x0=col * tile_size,
                        y0=row * tile_size,
                        x1=(col + 1) * tile_size,
                        y1=(row + 1) * tile_size,
                        review_status="reviewed",
                    )
                )
                annotations[filename] = [[0, 0.5, 0.5, 0.2, 0.2]]
                global_index += 1

        profile = PreprocessingProfile(
            profile_id="benchmark-rgb",
            name="Benchmark RGB",
            modality="EO",
            input_quantity="dn",
            radiometric_transform="none",
            percentile_stretch=False,
            rgb_conversion="native_rgb",
        )
        config = DatasetConfig(
            tile_selection="all",
            split_mode="random_tile",
            split_seed=seed,
        )
        dataset_dir = temp_root / "dataset"
        pipeline_metrics: dict = {}

        previous_workers = os.environ.get("GEOTILE_DATASET_WRITE_WORKERS")
        previous_budget = os.environ.get("GEOTILE_DATASET_RAM_MIB")
        if workers is not None:
            os.environ["GEOTILE_DATASET_WRITE_WORKERS"] = str(workers)
        if memory_budget_mib is not None:
            os.environ["GEOTILE_DATASET_RAM_MIB"] = str(memory_budget_mib)

        try:
            with PerformanceRecorder(
                OPERATION,
                output_path=output,
                cache_state=cache_state,
                storage_profile=storage_profile,
                inputs={
                    "tile_count": tile_count,
                    "tile_size": tile_size,
                    "scene_count": scene_count,
                    "seed": seed,
                    "workers": workers,
                    "memory_budget_mib": memory_budget_mib,
                },
                metadata_value={"synthetic": True, "read_only_project": True},
            ) as recorder:
                with recorder.stage("build_dataset") as timer:
                    generator = build_dataset(
                        tiles,
                        annotations,
                        {},
                        dataset_dir,
                        config,
                        {0: "object"},
                        scene_context=scene_context,
                        source_annotations_by_scene={key: [] for key in scene_context},
                        scene_source_map=scene_source_map,
                        preprocessing_profile=profile,
                        tile_size=tile_size,
                        scene_manifests=scene_manifests,
                        pipeline_metrics=pipeline_metrics,
                    )
                    stats, progress_events = consume_generator(generator)
                    images = list(dataset_dir.glob("*/images/*.png"))
                    output_bytes = sum(path.stat().st_size for path in images)
                    timer.record(
                        tiles=len(images),
                        progress_events=progress_events,
                        output_bytes=output_bytes,
                    )

                output_signature = dataset_tree_signature(dataset_dir)
                elapsed = recorder.report["stages"]["build_dataset"]["wall_seconds"]
                recorder.set_metric("tiles_written", len(images))
                recorder.set_metric("output_bytes", output_bytes)
                recorder.set_metric("output_signature", output_signature)
                recorder.set_metric("tiles_per_second", round(len(images) / max(elapsed, 1e-9), 3))
                recorder.set_metric("pipeline", pipeline_metrics)
                recorder.set_metric(
                    "stats_used_tiles",
                    int(getattr(getattr(stats, "tile_summary", None), "used", len(images))),
                )
        finally:
            if previous_workers is None:
                os.environ.pop("GEOTILE_DATASET_WRITE_WORKERS", None)
            else:
                os.environ["GEOTILE_DATASET_WRITE_WORKERS"] = previous_workers
            if previous_budget is None:
                os.environ.pop("GEOTILE_DATASET_RAM_MIB", None)
            else:
                os.environ["GEOTILE_DATASET_RAM_MIB"] = previous_budget
    return output


def run(args: argparse.Namespace) -> Path:
    configure_environment(args)
    return run_synthetic_dataset_benchmark(
        output=output_path(args, OPERATION),
        tile_count=args.tile_count,
        tile_size=args.tile_size,
        seed=args.seed,
        cache_state=args.cache_state,
        storage_profile=args.storage_profile,
        scene_count=args.scene_count,
        workers=args.workers,
        memory_budget_mib=args.memory_budget_mib,
    )


def main() -> None:
    args = build_parser().parse_args()
    destination = run(args)
    print(destination)


if __name__ == "__main__":
    main()
