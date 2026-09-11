"""Verify P0.3 event-loop, generator and atomic-archive gates.

Runs without pytest so it can execute in the packed desktop Python runtime.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        raise AssertionError("No latency samples collected")
    return statistics.quantiles(values, n=100, method="inclusive")[percentile - 1]


async def _measure_health_while_worker_runs() -> dict:
    from services.async_bridge import run_blocking

    def blocking_workload() -> None:
        # Models, GDAL and ZIP compression execute native work in this bridge.  A
        # sleep gives a deterministic lower-noise event-loop isolation check.
        time.sleep(0.75)

    return await _measure_health_during(run_blocking(blocking_workload))


async def _measure_health_during(worker_coroutine) -> dict:
    import httpx

    from main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        worker = asyncio.create_task(worker_coroutine)
        await asyncio.sleep(0.03)
        latencies: list[float] = []
        while not worker.done():
            started = time.perf_counter()
            response = await client.get("/api/health")
            elapsed = time.perf_counter() - started
            if response.status_code != 200 or response.json() != {"status": "ok"}:
                raise AssertionError(f"Unexpected health response: {response.status_code}")
            latencies.append(elapsed)
            await asyncio.sleep(0.01)
        worker_result = await worker

    p95 = _percentile(latencies, 95)
    if p95 >= 0.250:
        raise AssertionError(f"/api/health p95 {p95:.3f}s exceeds 0.250s")
    return {
        "samples": len(latencies),
        "p50_ms": round(_percentile(latencies, 50) * 1000, 3),
        "p95_ms": round(p95 * 1000, 3),
        "max_ms": round(max(latencies) * 1000, 3),
        "worker_result": worker_result,
    }


async def _measure_health_during_dataset_build() -> dict:
    from benchmarks.benchmark_dataset_build import run_synthetic_dataset_benchmark
    from services.async_bridge import run_blocking

    async def build():
        with tempfile.TemporaryDirectory(prefix="geotile-p0-3-dataset-") as temp_name:
            output = Path(temp_name) / "dataset.json"
            await run_blocking(
                run_synthetic_dataset_benchmark,
                output=output,
                tile_count=96,
                tile_size=256,
                seed=42,
            )
            report = json.loads(output.read_text(encoding="utf-8"))
            return {
                "tiles": report["metrics"]["tiles_written"],
                "wall_seconds": report["stages"]["build_dataset"]["wall_seconds"],
            }

    return await _measure_health_during(build())


async def _measure_health_during_inference(
    scene_path: Path,
    model_path: Path,
    device: str,
) -> dict:
    from models.prediction import PredictionConfig
    from services.async_bridge import next_generator_in_threadpool
    from services.predictor import execute_prediction, resolve_device

    resolved_device = resolve_device() if device == "auto" else device

    async def infer():
        config = PredictionConfig(
            model_path=str(model_path),
            tile_size=640,
            buffer=64,
            device=resolved_device,
            conf=0.25,
            iou=0.4,
        )
        generator = execute_prediction(scene_path, config)
        progress_events = 0
        try:
            while True:
                step = await next_generator_in_threadpool(generator, max_progress_hz=4.0)
                if not step.has_value:
                    predictions = step.value or []
                    return {
                        "progress_events": progress_events,
                        "predictions": len(predictions),
                        "device": resolved_device,
                    }
                progress_events += 1
        finally:
            generator.close()

    return await _measure_health_during(infer())


async def _verify_generator_bridge() -> dict:
    from services.async_bridge import WorkerCancelled, next_generator_in_threadpool

    def finite_worker():
        for index in range(80):
            time.sleep(0.01)
            yield {"done": index + 1}
        return {"status": "complete"}

    generator = finite_worker()
    progress = []
    while True:
        step = await next_generator_in_threadpool(generator, max_progress_hz=4.0)
        if not step.has_value:
            result = step.value
            break
        progress.append(step.value)
    if result != {"status": "complete"}:
        raise AssertionError(f"Generator return value was lost: {result!r}")
    if not 2 <= len(progress) <= 4:
        raise AssertionError(f"Expected 2-4 throttled events, got {len(progress)}")

    cancel = asyncio.Event()

    def cancellable_worker():
        while True:
            time.sleep(0.01)
            yield {"status": "working"}

    cancelled_generator = cancellable_worker()
    await next_generator_in_threadpool(cancelled_generator, max_progress_hz=0)
    cancel.set()
    try:
        await next_generator_in_threadpool(
            cancelled_generator,
            max_progress_hz=0,
            should_cancel=cancel.is_set,
        )
    except WorkerCancelled:
        cancelled = True
    else:
        cancelled = False
    finally:
        cancelled_generator.close()
    if not cancelled:
        raise AssertionError("Generator cancellation was not propagated")
    return {"progress_events": len(progress), "cancelled": cancelled}


async def _verify_backup_download_endpoint() -> dict:
    import httpx

    from db.storage import DATA_DIR, create_project_root, save_json
    from main import app

    project_id = "p03backup"
    root = create_project_root(project_id, "P0.3 backup")
    save_json(
        project_id,
        "project",
        {
            "id": project_id,
            "name": "P0.3 backup",
            "scene_folder": "",
            "project_root": str(root),
            "created_in_appdata": True,
            "scene_count": 0,
        },
    )
    save_json(project_id, "classes", [])
    save_json(project_id, "tiling_config", {"tile_size": 640, "buffer": 0})
    save_json(project_id, "dataset_config", {})

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/projects/{project_id}/backup/download")
    if response.status_code != 200:
        raise AssertionError(f"Backup endpoint returned {response.status_code}: {response.text}")
    if response.headers.get("content-type") != "application/zip":
        raise AssertionError("Backup endpoint changed its media type")
    with tempfile.TemporaryDirectory(prefix="geotile-backup-response-") as response_dir:
        response_path = Path(response_dir) / "response.zip"
        response_path.write_bytes(response.content)
        with zipfile.ZipFile(response_path) as archive:
            names = set(archive.namelist())
    if not {"backup_manifest.json", "project.json"}.issubset(names):
        raise AssertionError(f"Backup archive contract changed: {sorted(names)}")
    runtime_archives = DATA_DIR / "runtime" / "archives"
    leftovers = list(runtime_archives.glob("*.zip")) if runtime_archives.is_dir() else []
    if leftovers:
        raise AssertionError(f"FileResponse cleanup left archives: {leftovers}")
    return {
        "status_code": response.status_code,
        "bytes": len(response.content),
        "background_cleanup": True,
    }


def _verify_archive_io() -> dict:
    from services import archive_io

    old_runtime = archive_io.ARCHIVE_RUNTIME_DIR
    old_registry = archive_io.PARTIAL_REGISTRY_PATH
    try:
        with tempfile.TemporaryDirectory(prefix="geotile-p0-3-") as temp_name:
            root = Path(temp_name)
            archive_io.ARCHIVE_RUNTIME_DIR = root / "runtime" / "archives"
            archive_io.PARTIAL_REGISTRY_PATH = root / "runtime" / "archive_partials.json"
            destination = root / "result.zip"
            archive_io.write_zip_atomic(
                destination,
                lambda archive: archive.writestr("payload.txt", b"ok"),
            )
            with zipfile.ZipFile(destination) as archive:
                if archive.read("payload.txt") != b"ok":
                    raise AssertionError("ZIP payload mismatch")
            if destination.with_name("result.zip.partial").exists():
                raise AssertionError("Successful ZIP left a .partial file")

            failed = root / "failed.zip"

            def fail(archive: zipfile.ZipFile) -> None:
                archive.writestr("incomplete.txt", b"partial")
                raise RuntimeError("expected failure")

            try:
                archive_io.write_zip_atomic(failed, fail)
            except RuntimeError:
                pass
            else:
                raise AssertionError("Expected ZIP writer failure")
            if failed.exists() or failed.with_name("failed.zip.partial").exists():
                raise AssertionError("Failed ZIP was published or left a partial")

            leaked = root / "chosen.zip.partial"
            leaked.write_bytes(b"partial")
            archive_io.PARTIAL_REGISTRY_PATH.write_text(
                json.dumps({"paths": [str(leaked)]}),
                encoding="utf-8",
            )
            cleanup = archive_io.cleanup_partial_archives()
            if leaked.exists():
                raise AssertionError("Startup cleanup did not remove tracked partial")
            return {"atomic_publish": True, "failure_cleanup": True, "startup_cleanup": cleanup}
    finally:
        archive_io.ARCHIVE_RUNTIME_DIR = old_runtime
        archive_io.PARTIAL_REGISTRY_PATH = old_registry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    parser.add_argument("--scene-path")
    parser.add_argument("--model-path")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    args = parser.parse_args()
    if bool(args.scene_path) != bool(args.model_path):
        parser.error("--scene-path and --model-path must be provided together")
    with tempfile.TemporaryDirectory(prefix="geotile-p0-3-data-") as data_name:
        os.environ["DATA_DIR"] = data_name
        report = {
            "schema_name": "geotile_p0_3_verification",
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "completed",
            "health_control": asyncio.run(_measure_health_while_worker_runs()),
            "health_dataset_build": asyncio.run(_measure_health_during_dataset_build()),
            "generator_bridge": asyncio.run(_verify_generator_bridge()),
            "backup_endpoint": asyncio.run(_verify_backup_download_endpoint()),
            "archive_io": _verify_archive_io(),
        }
        if args.scene_path:
            report["health_inference"] = asyncio.run(
                _measure_health_during_inference(
                    Path(args.scene_path).expanduser().resolve(),
                    Path(args.model_path).expanduser().resolve(),
                    args.device,
                )
            )
    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else REPO_ROOT
        / "benchmark-results"
        / f"p0_3_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(output)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
