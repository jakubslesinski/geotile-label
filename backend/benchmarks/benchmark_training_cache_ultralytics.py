"""End-to-end P2.3 benchmark: legacy, cold cache and warm cache training.

This runner intentionally bypasses the project's training history. It reads one
published dataset run but creates staging under a temporary project and writes only
benchmark results below ``--output-dir``. The source dataset is fingerprinted before
and after all variants.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks import common as _benchmark_runtime  # noqa: E402,F401
from services import training_dataset  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_descriptors(source: Path) -> dict[str, dict[str, Any] | None]:
    result: dict[str, dict[str, Any] | None] = {}
    for name in ("dataset_run_manifest.json", "data.yaml", "data_obb.yaml"):
        path = source / name
        result[name] = (
            {"size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
            if path.is_file()
            else None
        )
    return result


def cache_files(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*.cache")):
        if not path.is_file():
            continue
        stat = path.stat()
        result[path.relative_to(root).as_posix()] = {
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": sha256_file(path),
        }
    return result


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(partial, path)


class MemorySampler:
    def __init__(self, interval_s: float = 0.1):
        self.interval_s = interval_s
        self.peak_rss_bytes = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        import psutil

        process = psutil.Process()
        while not self._stop.wait(self.interval_s):
            rss = 0
            candidates = [process]
            try:
                candidates.extend(process.children(recursive=True))
            except psutil.Error:
                pass
            for candidate in candidates:
                try:
                    rss += candidate.memory_info().rss
                except psutil.Error:
                    pass
            self.peak_rss_bytes = max(self.peak_rss_bytes, rss)


def event_callback(events: dict[str, Any], name: str, started: float) -> Callable[[Any], None]:
    def record(trainer: Any) -> None:
        if name in events:
            return
        now = time.perf_counter()
        events[name] = {
            "elapsed_seconds": now - started,
            "wall_time": utc_now(),
        }
        if name == "train_start":
            loader = getattr(trainer, "train_loader", None)
            dataset = getattr(loader, "dataset", None)
            events[name]["batches"] = len(loader) if loader is not None else None
            events[name]["images"] = len(dataset) if dataset is not None else None
            events[name]["effective_batch_size"] = getattr(loader, "batch_size", None)
            events[name]["effective_workers"] = getattr(loader, "num_workers", None)
        print(f"BENCHMARK_EVENT {name} {events[name]['elapsed_seconds']:.3f}s", flush=True)

    return record


def numeric_metrics(results: Any) -> dict[str, float | None]:
    raw = getattr(results, "results_dict", {}) or {}
    metrics: dict[str, float | None] = {}
    for key, value in raw.items():
        try:
            metrics[str(key)] = float(value)
        except (TypeError, ValueError):
            metrics[str(key)] = None
    return metrics


def warm_runtime(weights: Path) -> dict[str, Any]:
    """Make the first measured variant independent of one-off CUDA/import startup."""

    import torch
    import ultralytics
    from ultralytics import SETTINGS, YOLO

    SETTINGS["sync"] = False
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for this benchmark")
    started = time.perf_counter()
    torch.cuda.init()
    probe = torch.ones(1, device="cuda")
    _value = float(probe.sum().item())
    model = YOLO(str(weights))
    del model, probe
    gc.collect()
    torch.cuda.empty_cache()
    return {
        "python": sys.version,
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "ultralytics": ultralytics.__version__,
        "gpu": torch.cuda.get_device_name(0),
        "gpu_total_bytes": torch.cuda.get_device_properties(0).total_memory,
        "warmup_seconds": time.perf_counter() - started,
    }


def train_variant(
    *,
    name: str,
    data_yaml: Path,
    weights: Path,
    output_dir: Path,
    batch: int,
    workers: int,
    imgsz: int,
    collect_telemetry: bool = False,
) -> dict[str, Any]:
    import psutil
    import torch
    from ultralytics import SETTINGS, YOLO

    SETTINGS["sync"] = False
    gc.collect()
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    free_before, total_vram = torch.cuda.mem_get_info()
    rss_before = psutil.Process().memory_info().rss
    events: dict[str, Any] = {}
    started = time.perf_counter()
    sampler = MemorySampler()
    model = YOLO(str(weights))
    telemetry = None
    if collect_telemetry:
        # Keep this import out of module scope. Windows DataLoader workers import the
        # benchmark's __main__ module under the spawn start method; importing the
        # complete production worker there changes their startup surface and can
        # expose the packed runtime's known duplicate-OpenMP failure.
        from training_worker import JobState, TrainingTelemetry

        telemetry = TrainingTelemetry(
            output_dir / name,
            JobState(output_dir / name, {}),
            {
                "data_loader_workers": workers,
                "blas_threads_per_process": int(os.environ.get("OMP_NUM_THREADS", "1")),
                "opencv_threads_per_process": 0,
            },
        )
    callbacks = {
        "on_pretrain_routine_start": "pretrain_routine_start",
        "on_pretrain_routine_end": "pretrain_routine_end",
        "on_train_start": "train_start",
        "on_train_batch_start": "first_train_batch_start",
        "on_train_batch_end": "first_train_batch_end",
        "on_train_epoch_end": "train_epoch_end",
        "on_train_end": "train_end",
    }
    for callback_name, event_name in callbacks.items():
        model.add_callback(callback_name, event_callback(events, event_name, started))
    if collect_telemetry:
        assert telemetry is not None
        model.add_callback("on_train_start", telemetry.on_train_start)
        model.add_callback("on_train_epoch_start", telemetry.on_epoch_start)
        model.add_callback("on_train_batch_start", telemetry.on_batch_start)
        model.add_callback("on_train_batch_end", telemetry.on_batch_end)
        model.add_callback("on_train_epoch_end", telemetry.on_epoch_end)

    sampler.start()
    try:
        results = model.train(
            data=str(data_yaml),
            epochs=1,
            imgsz=imgsz,
            batch=batch,
            workers=workers,
            device=0,
            seed=0,
            deterministic=True,
            cache=False,
            # AMP itself is not under test. Ultralytics performs a network-backed AMP
            # self-check with an auxiliary model on the first run, which would make
            # only the legacy variant pay that cost and violate the offline contract.
            amp=False,
            val=True,
            plots=False,
            save=False,
            verbose=False,
            project=str(output_dir),
            name=name,
            exist_ok=True,
            patience=0,
            close_mosaic=0,
        )
        total_seconds = time.perf_counter() - started
        metrics = numeric_metrics(results)
    finally:
        sampler.stop()

    train_images = (events.get("train_start") or {}).get("images")
    batch_start = (events.get("first_train_batch_start") or {}).get("elapsed_seconds")
    epoch_end = (events.get("train_epoch_end") or {}).get("elapsed_seconds")
    epoch_seconds = (
        epoch_end - batch_start
        if isinstance(epoch_end, (int, float)) and isinstance(batch_start, (int, float))
        else None
    )
    throughput = (
        train_images / epoch_seconds
        if isinstance(train_images, int) and epoch_seconds and epoch_seconds > 0
        else None
    )
    free_after, _total_after = torch.cuda.mem_get_info()
    result = {
        "total_seconds": total_seconds,
        "events": events,
        "startup_to_first_batch_seconds": batch_start,
        "train_epoch_seconds": epoch_seconds,
        "train_images_per_second": throughput,
        "metrics": metrics,
        "memory": {
            "rss_before_bytes": rss_before,
            "peak_process_tree_rss_bytes": sampler.peak_rss_bytes,
            "cuda_total_bytes": total_vram,
            "cuda_free_before_bytes": free_before,
            "cuda_free_after_bytes": free_after,
            "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
            "cuda_peak_reserved_bytes": torch.cuda.max_memory_reserved(),
        },
    }
    if collect_telemetry:
        assert telemetry is not None
        result["p2_5_telemetry"] = telemetry.finalize()
    del model, results
    gc.collect()
    torch.cuda.empty_cache()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument(
        "--variants",
        default="legacy,cold_cache,warm_cache",
        help="Comma-separated subset of legacy,cold_cache,warm_cache",
    )
    parser.add_argument("--telemetry", action="store_true")
    args = parser.parse_args()

    variants = [value.strip() for value in args.variants.split(",") if value.strip()]
    allowed_variants = {"legacy", "cold_cache", "warm_cache"}
    if not variants or any(value not in allowed_variants for value in variants):
        parser.error("--variants must contain legacy, cold_cache and/or warm_cache")
    if "warm_cache" in variants and "cold_cache" not in variants:
        parser.error("warm_cache requires cold_cache in the same isolated run")

    source = args.dataset_dir.resolve()
    weights = args.weights.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "benchmark.json"
    manifest = json.loads((source / "dataset_run_manifest.json").read_text(encoding="utf-8"))
    runtime = warm_runtime(weights)
    report: dict[str, Any] = {
        "schema_name": "geotile_training_cache_ultralytics_benchmark",
        "schema_version": 1,
        "status": "running",
        "started_at": utc_now(),
        "dataset_dir": str(source),
        "dataset_run_id": manifest.get("run_id") or source.name,
        "dataset_input_hash": manifest.get("input_hash"),
        "project_annotation_mode": (manifest.get("project_profile") or {}).get("annotation_mode"),
        "benchmark_task": "obb",
        "geometry_note": (
            "The xView3 project declares bbox; labels_obb are axis-aligned polygons "
            "generated from those boxes. This is a technical OBB cache benchmark, "
            "not a model-quality benchmark."
        ),
        "preprocessing_hash": (manifest.get("preprocessing_profile") or {}).get("profile_hash"),
        "weights": str(weights),
        "weights_sha256": sha256_file(weights),
        "runtime": runtime,
        "options": {
            "epochs": 1,
            "imgsz": args.imgsz,
            "batch": args.batch,
            "workers": args.workers,
            "device": 0,
            "cache_images": False,
            "amp": False,
            "seed": 0,
            "deterministic": True,
            "p2_5_telemetry": args.telemetry,
            "variants": variants,
        },
        "source_before": source_descriptors(source),
        "variants": [],
    }
    write_json_atomic(report_path, report)

    with tempfile.TemporaryDirectory(prefix="geotile-p2-3-train-", ignore_cleanup_errors=True) as temp:
        temp_root = Path(temp)
        isolated_project = temp_root / "project"
        original_project_dir = training_dataset.project_dir
        training_dataset.project_dir = lambda _project_id: isolated_project
        try:
            for variant in variants:
                print(f"BENCHMARK_VARIANT {variant} START", flush=True)
                stage_started = time.perf_counter()
                if variant == "legacy":
                    resolution = training_dataset.resolve_obb_training_dataset(
                        project_id="benchmark",
                        dataset_run_id=str(report["dataset_run_id"]),
                        dataset_dir=source,
                        dataset_input_hash=report["dataset_input_hash"],
                        preprocessing_hash=report["preprocessing_hash"],
                        fallback_target=temp_root / "legacy" / "dataset_obb",
                        cache_enabled=False,
                    )
                else:
                    resolution = training_dataset.resolve_obb_training_dataset(
                        project_id="benchmark",
                        dataset_run_id=str(report["dataset_run_id"]),
                        dataset_dir=source,
                        dataset_input_hash=report["dataset_input_hash"],
                        preprocessing_hash=report["preprocessing_hash"],
                        cache_enabled=True,
                    )
                stage_seconds = time.perf_counter() - stage_started
                data_yaml = Path(resolution["data_yaml"])
                cache_before = cache_files(data_yaml.parent)
                variant_record: dict[str, Any] = {
                    "name": variant,
                    "started_at": utc_now(),
                    "stage_seconds": stage_seconds,
                    "resolution": resolution,
                    "ultralytics_cache_before": cache_before,
                }
                report["variants"].append(variant_record)
                write_json_atomic(report_path, report)
                try:
                    variant_record["training"] = train_variant(
                        name=variant,
                        data_yaml=data_yaml,
                        weights=weights,
                        output_dir=output_dir / "runs",
                        batch=args.batch,
                        workers=args.workers,
                        imgsz=args.imgsz,
                        collect_telemetry=args.telemetry,
                    )
                    variant_record["ultralytics_cache_after"] = cache_files(data_yaml.parent)
                    variant_record["cache_files_reused_without_rewrite"] = bool(
                        cache_before
                        and cache_before == variant_record["ultralytics_cache_after"]
                    )
                    variant_record["status"] = "completed"
                except Exception as exc:  # noqa: BLE001 - benchmark must persist the cause
                    variant_record["status"] = "failed"
                    variant_record["error"] = f"{type(exc).__name__}: {exc}"
                    variant_record["traceback"] = traceback.format_exc()
                    report["status"] = "failed"
                    report["ended_at"] = utc_now()
                    write_json_atomic(report_path, report)
                    raise
                variant_record["ended_at"] = utc_now()
                write_json_atomic(report_path, report)
                print(f"BENCHMARK_VARIANT {variant} END", flush=True)
        finally:
            training_dataset.project_dir = original_project_dir

    report["source_after"] = source_descriptors(source)
    report["source_unchanged"] = report["source_before"] == report["source_after"]
    report["status"] = "completed"
    report["ended_at"] = utc_now()
    write_json_atomic(report_path, report)
    print(f"BENCHMARK_REPORT {report_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
