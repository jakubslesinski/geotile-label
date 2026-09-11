"""Measure low-zoom read latency while a real GDAL overview is built in parallel.

Both source rasters remain read-only. The overview is written beside a temporary VRT.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _probe_low_zoom(path: Path, size: int) -> float:
    from osgeo import gdal

    gdal.UseExceptions()
    started = time.perf_counter()
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    if dataset is None or dataset.RasterCount < 1:
        raise RuntimeError(f"GDAL could not open probe source: {path}")
    bands = min(3, int(dataset.RasterCount))
    for index in range(1, bands + 1):
        array = dataset.GetRasterBand(index).ReadAsArray(
            0,
            0,
            dataset.RasterXSize,
            dataset.RasterYSize,
            buf_xsize=size,
            buf_ysize=size,
            resample_alg=gdal.GRIORA_Bilinear,
        )
        if array is None:
            raise RuntimeError(f"GDAL returned no probe pixels for band {index}")
    dataset = None
    return time.perf_counter() - started


def _percentile(values: list[float], fraction: float) -> float:
    ranked = sorted(values)
    if not ranked:
        return 0.0
    position = (len(ranked) - 1) * fraction
    low = int(position)
    high = min(len(ranked) - 1, low + 1)
    weight = position - low
    return ranked[low] * (1.0 - weight) + ranked[high] * weight


def _summary(values: list[float]) -> dict[str, float | int]:
    return {
        "count": len(values),
        "mean_seconds": round(statistics.fmean(values), 6) if values else 0.0,
        "p50_seconds": round(_percentile(values, 0.5), 6),
        "p95_seconds": round(_percentile(values, 0.95), 6),
        "max_seconds": round(max(values), 6) if values else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overview-source", required=True)
    parser.add_argument("--probe-source", required=True)
    parser.add_argument("--baseline-samples", type=int, default=5)
    parser.add_argument("--concurrent-samples", type=int, default=12)
    parser.add_argument("--probe-size", type=int, default=256)
    parser.add_argument("--output")
    args = parser.parse_args()

    overview_source = Path(args.overview_source).expanduser().resolve(strict=True)
    probe_source = Path(args.probe_source).expanduser().resolve(strict=True)
    before = {
        str(path): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in {overview_source, probe_source}
    }

    from benchmarks.benchmark_scene_import import _build_overview_worker

    baseline = [
        _probe_low_zoom(probe_source, max(64, int(args.probe_size)))
        for _ in range(max(1, int(args.baseline_samples)))
    ]
    with tempfile.TemporaryDirectory(prefix="geotile-p1-7-responsive-") as temp_name:
        payload = {
            "source": str(overview_source),
            "target_dir": str(Path(temp_name) / "overview"),
            "compression": "ZSTD",
            "gdal_threads": "1",
            "predictor": 2,
            "first_factor": 2,
            "min_overview_size": 512,
            "resampling": "AVERAGE",
        }
        concurrent_latencies: list[float] = []
        with concurrent.futures.ProcessPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_build_overview_worker, payload)
            while len(concurrent_latencies) < max(1, int(args.concurrent_samples)) and not future.done():
                concurrent_latencies.append(_probe_low_zoom(probe_source, max(64, int(args.probe_size))))
            overview_result = future.result()
        if overview_result.get("error"):
            raise RuntimeError(str(overview_result["error"]))
    if not concurrent_latencies:
        raise RuntimeError("Overview completed before a concurrent latency sample was captured")

    unchanged = all(
        before[str(path)] == (path.stat().st_size, path.stat().st_mtime_ns)
        for path in {overview_source, probe_source}
    )
    if not unchanged:
        raise AssertionError("A source raster changed during responsiveness measurement")
    baseline_summary = _summary(baseline)
    concurrent_summary = _summary(concurrent_latencies)
    baseline_p95 = float(baseline_summary["p95_seconds"])
    slowdown = (
        float(concurrent_summary["p95_seconds"]) / baseline_p95
        if baseline_p95 > 0
        else None
    )
    report = {
        "schema_name": "geotile_p1_7_responsiveness",
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "overview_source": str(overview_source),
        "probe_source": str(probe_source),
        "profile": {"workers": 1, "compression": "ZSTD", "gdal_threads": "1"},
        "probe_size": int(args.probe_size),
        "baseline": baseline_summary,
        "during_overview": concurrent_summary,
        "p95_slowdown_ratio": round(slowdown, 3) if slowdown is not None else None,
        "overview": overview_result,
        "source_unchanged": True,
    }
    output = Path(args.output).expanduser().resolve(strict=False) if args.output else None
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = output.with_suffix(output.suffix + ".tmp")
        temp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, output)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
