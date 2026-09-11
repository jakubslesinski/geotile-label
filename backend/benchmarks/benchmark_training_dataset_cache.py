"""Benchmark P2.3 OBB staging without writing to the source project.

Example (PowerShell)::

    python backend/benchmarks/benchmark_training_dataset_cache.py `
      --dataset-dir "$env:APPDATA/GeoTileLabel/data/projects/<id>/dataset_runs/<run>" `
      --output benchmark-results/p2_3/training_dataset_cache.json

The temporary project is created under the system temp directory. For representative
hardlink results it should be on the same volume as ``dataset-dir``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services import training_dataset  # noqa: E402


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * ratio))]


def timed(callable_) -> tuple[Any, float]:
    started = time.perf_counter()
    result = callable_()
    return result, time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--warm-runs", type=int, default=5)
    args = parser.parse_args()

    source = args.dataset_dir.resolve()
    manifest_path = source / "dataset_run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset_run_id = str(manifest.get("run_id") or source.name)
    dataset_input_hash = manifest.get("input_hash")
    preprocessing_hash = (manifest.get("preprocessing_profile") or {}).get("profile_hash")
    source_before = {
        name: sha256_file(source / name)
        for name in ("dataset_run_manifest.json", "data.yaml", "data_obb.yaml")
    }

    with tempfile.TemporaryDirectory(prefix="geotile-p2-3-") as temporary:
        temp_root = Path(temporary)
        project_root = temp_root / "project"
        original_project_dir = training_dataset.project_dir
        training_dataset.project_dir = lambda _project_id: project_root
        try:
            legacy_times: list[float] = []
            for index in range(2):
                _path, elapsed = timed(
                    lambda index=index: training_dataset.stage_obb_dataset(
                        source,
                        temp_root / f"legacy-{index}",
                    )
                )
                legacy_times.append(elapsed)

            cold, cold_seconds = timed(lambda: training_dataset.resolve_obb_training_dataset(
                project_id="benchmark",
                dataset_run_id=dataset_run_id,
                dataset_dir=source,
                dataset_input_hash=dataset_input_hash,
                preprocessing_hash=preprocessing_hash,
                cache_enabled=True,
            ))
            warm_times: list[float] = []
            warm_results: list[dict[str, Any]] = []
            for _index in range(max(1, args.warm_runs)):
                result, elapsed = timed(lambda: training_dataset.resolve_obb_training_dataset(
                    project_id="benchmark",
                    dataset_run_id=dataset_run_id,
                    dataset_dir=source,
                    dataset_input_hash=dataset_input_hash,
                    preprocessing_hash=preprocessing_hash,
                    cache_enabled=True,
                ))
                warm_results.append(result)
                warm_times.append(elapsed)
            cache_manifest = json.loads(
                Path(cold["cache_manifest"]).read_text(encoding="utf-8")
            )
        finally:
            training_dataset.project_dir = original_project_dir

    source_after = {
        name: sha256_file(source / name)
        for name in ("dataset_run_manifest.json", "data.yaml", "data_obb.yaml")
    }
    legacy_median = statistics.median(legacy_times)
    warm_median = statistics.median(warm_times)
    report = {
        "schema_name": "geotile_training_dataset_cache_benchmark",
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_dir": str(source),
        "dataset_run_id": dataset_run_id,
        "dataset_input_hash": dataset_input_hash,
        "preprocessing_hash": preprocessing_hash,
        "source_volume": source.drive,
        "temporary_volume": Path(tempfile.gettempdir()).drive,
        "hardlink_volume_match": source.drive.casefold() == Path(tempfile.gettempdir()).drive.casefold(),
        "legacy_rebuild_seconds": legacy_times,
        "legacy_rebuild_median_seconds": legacy_median,
        "cache_cold_seconds": cold_seconds,
        "cache_warm_seconds": warm_times,
        "cache_warm_median_seconds": warm_median,
        "cache_warm_p95_seconds": percentile(warm_times, 0.95),
        "warm_speedup_vs_legacy": legacy_median / warm_median if warm_median else None,
        "all_warm_hits": all(item.get("cache_hit") is True for item in warm_results),
        "cache_key": cold["cache_key"],
        "cache_manifest": {
            key: cache_manifest.get(key)
            for key in (
                "schema_version",
                "exporter_version",
                "payload_file_count",
                "logical_size_bytes",
                "allocated_size_estimate_bytes",
                "storage",
                "dependencies",
            )
        },
        "source_descriptors_before": source_before,
        "source_descriptors_after": source_after,
        "source_unchanged": source_before == source_after,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
