"""Bounded resource probe and conservative training auto-configuration.

The probe is intentionally small: it decodes a deterministic sample of published
training images and never creates an Ultralytics image cache.  Recommendations are
advisory until the user applies them in the Training view.
"""

from __future__ import annotations

import math
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

GIB = 1024 ** 3
MIB = 1024 ** 2
IMAGE_SUFFIXES = {".bmp", ".dng", ".jpeg", ".jpg", ".mpo", ".png", ".tif", ".tiff", ".webp"}
LOADER_SAMPLE_LIMIT = 24
LOADER_SAMPLE_BYTES_LIMIT = 128 * MIB
RAM_CACHE_HEADROOM_FRACTION = 0.35
RAM_CACHE_MIN_HEADROOM_BYTES = 8 * GIB
RAM_CACHE_ESTIMATE_MULTIPLIER = 1.20


def _even_sample(items: list[Path], limit: int) -> list[Path]:
    if len(items) <= limit:
        return items
    if limit <= 1:
        return [items[0]]
    return [items[round(index * (len(items) - 1) / (limit - 1))] for index in range(limit)]


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return float(ordered[position])


def benchmark_image_loader(
    dataset_dir: Path,
    *,
    imgsz: int,
    sample_limit: int = LOADER_SAMPLE_LIMIT,
    sample_bytes_limit: int = LOADER_SAMPLE_BYTES_LIMIT,
) -> dict[str, Any]:
    """Measure bounded image read/decode/resize work without mutating the dataset."""

    import cv2
    import numpy as np

    discovery_started = time.perf_counter()
    split_counts: dict[str, int] = {}
    all_images: list[Path] = []
    train_images: list[Path] = []
    total_file_bytes = 0
    for split in ("train", "val", "test"):
        directory = Path(dataset_dir) / split / "images"
        images = sorted(
            (
                path
                for path in directory.iterdir()
                if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
            ),
            key=lambda path: path.name.lower(),
        ) if directory.is_dir() else []
        split_counts[split] = len(images)
        if split == "train":
            train_images = images
        all_images.extend(images)
        for path in images:
            try:
                total_file_bytes += path.stat().st_size
            except OSError:
                pass
    discovery_seconds = time.perf_counter() - discovery_started

    candidates = _even_sample(train_images, max(1, int(sample_limit)))
    sampled: list[Path] = []
    scheduled_bytes = 0
    for path in candidates:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if sampled and scheduled_bytes + size > sample_bytes_limit:
            break
        sampled.append(path)
        scheduled_bytes += size

    read_seconds = 0.0
    decode_seconds = 0.0
    resize_seconds = 0.0
    sample_seconds: list[float] = []
    sampled_bytes = 0
    decoded = 0
    failures: list[str] = []
    benchmark_started = time.perf_counter()
    for path in sampled:
        item_started = time.perf_counter()
        try:
            started = time.perf_counter()
            payload = path.read_bytes()
            read_seconds += time.perf_counter() - started
            sampled_bytes += len(payload)

            started = time.perf_counter()
            image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
            decode_seconds += time.perf_counter() - started
            if image is None:
                raise ValueError("decoder returned no image")

            started = time.perf_counter()
            if image.shape[0] != imgsz or image.shape[1] != imgsz:
                cv2.resize(image, (imgsz, imgsz), interpolation=cv2.INTER_AREA)
            resize_seconds += time.perf_counter() - started
            decoded += 1
        except Exception as exc:  # noqa: BLE001 - individual corrupt images are reported
            if len(failures) < 5:
                failures.append(f"{path.name}: {type(exc).__name__}: {exc}")
        finally:
            sample_seconds.append(time.perf_counter() - item_started)
    benchmark_seconds = time.perf_counter() - benchmark_started

    def decode_for_parallel_probe(path: Path) -> bool:
        try:
            payload = path.read_bytes()
            image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                return False
            if image.shape[0] != imgsz or image.shape[1] != imgsz:
                cv2.resize(image, (imgsz, imgsz), interpolation=cv2.INTER_AREA)
            return True
        except Exception:
            return False

    # This is a warm-cache concurrency proxy, not a claim that Python threads are the
    # PyTorch DataLoader. OpenCV releases the GIL for decode/resize, so the ranking is
    # still useful for choosing how much parallel input work this machine can sustain.
    concurrency_profiles: list[dict[str, Any]] = []
    cpu_limit = max(1, min(8, os.cpu_count() or 1))
    for worker_count in sorted({1, 2, 4, cpu_limit}):
        if worker_count > cpu_limit or not sampled:
            continue
        started = time.perf_counter()
        if worker_count == 1:
            outcomes = [decode_for_parallel_probe(path) for path in sampled]
        else:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                outcomes = list(executor.map(decode_for_parallel_probe, sampled))
        seconds = time.perf_counter() - started
        successful = sum(1 for outcome in outcomes if outcome)
        concurrency_profiles.append({
            "workers": worker_count,
            "decoded_count": successful,
            "seconds": round(seconds, 6),
            "images_per_second": round(successful / seconds, 3) if seconds else None,
        })

    # Ultralytics caches resized uint8 images, not compressed source bytes.  The 20%
    # margin covers aspect-ratio padding and Python/container overhead while keeping
    # the estimate transparent and deliberately conservative.
    decoded_cache_estimate = int(
        len(all_images) * max(1, int(imgsz)) * max(1, int(imgsz)) * 3
        * RAM_CACHE_ESTIMATE_MULTIPLIER
    )
    return {
        "schema_name": "geotile_training_loader_probe",
        "schema_version": 1,
        "sample_limit": int(sample_limit),
        "sample_count": len(sampled),
        "decoded_count": decoded,
        "failure_count": len(sampled) - decoded,
        "failures": failures,
        "split_image_counts": split_counts,
        "image_count": len(all_images),
        "train_image_count": len(train_images),
        "total_file_bytes": total_file_bytes,
        "sampled_bytes": sampled_bytes,
        "discovery_seconds": round(discovery_seconds, 6),
        "benchmark_seconds": round(benchmark_seconds, 6),
        "read_seconds": round(read_seconds, 6),
        "decode_seconds": round(decode_seconds, 6),
        "resize_seconds": round(resize_seconds, 6),
        "images_per_second": round(decoded / benchmark_seconds, 3) if benchmark_seconds else None,
        "read_mib_per_second": round(sampled_bytes / MIB / read_seconds, 3) if read_seconds else None,
        "median_image_ms": round(statistics.median(sample_seconds) * 1000, 3) if sample_seconds else None,
        "p95_image_ms": round(_percentile(sample_seconds, 0.95) * 1000, 3) if sample_seconds else None,
        "concurrency_probe_semantics": "warm_cache_opencv_thread_proxy",
        "concurrency_profiles": concurrency_profiles,
        "estimated_ram_cache_bytes": decoded_cache_estimate,
        "cache_note": "Read timing depends on the operating-system file cache.",
    }


def system_resource_snapshot(requested_device: str) -> dict[str, Any]:
    try:
        import psutil

        memory = psutil.virtual_memory()
        logical = psutil.cpu_count(logical=True) or os.cpu_count() or 1
        physical = psutil.cpu_count(logical=False) or max(1, logical // 2)
        total_memory = int(memory.total)
        available_memory = int(memory.available)
    except Exception:
        logical = os.cpu_count() or 1
        physical = max(1, logical // 2)
        total_memory = 0
        available_memory = 0

    result: dict[str, Any] = {
        "logical_cpu_count": int(logical),
        "physical_cpu_count": int(physical),
        "memory_total_bytes": total_memory,
        "memory_available_bytes": available_memory,
        "device": requested_device,
        "multiprocessing_start_method": "spawn" if os.name == "nt" else "fork",
        "gpu_name": None,
        "vram_total_bytes": None,
        "vram_free_bytes": None,
    }
    if requested_device != "cuda":
        return result
    try:
        import torch

        if torch.cuda.is_available():
            free_bytes, total_bytes = torch.cuda.mem_get_info(0)
            result.update(
                gpu_name=torch.cuda.get_device_name(0),
                vram_total_bytes=int(total_bytes),
                vram_free_bytes=int(free_bytes),
            )
    except Exception:
        pass
    return result


def _normalise_cache(value: Any) -> bool | str:
    if value is True or str(value).strip().lower() in {"true", "ram"}:
        return "ram"
    if str(value).strip().lower() == "disk":
        return "disk"
    return False


def recommend_training_resources(
    *,
    loader: dict[str, Any],
    system: dict[str, Any],
    requested_device: str,
    imgsz: int,
    current_batch: int,
    advanced_options: dict[str, Any] | None = None,
    estimated_vram_gb: float | None = None,
) -> dict[str, Any]:
    """Turn measurements into a conservative, explainable recommendation."""

    advanced = dict(advanced_options or {})
    physical = max(1, int(system.get("physical_cpu_count") or 1))
    logical = max(1, int(system.get("logical_cpu_count") or physical))
    train_images = max(0, int(loader.get("train_image_count") or 0))
    read_seconds = max(0.0, float(loader.get("read_seconds") or 0.0))
    cpu_input_seconds = max(0.0, float(loader.get("decode_seconds") or 0.0)) + max(
        0.0, float(loader.get("resize_seconds") or 0.0)
    )
    measured_seconds = read_seconds + cpu_input_seconds
    read_share = read_seconds / measured_seconds if measured_seconds else 0.0
    read_mib_s = float(loader.get("read_mib_per_second") or 0.0)

    if read_share >= 0.55 and read_mib_s < 250:
        bottleneck = "io"
        confidence = "medium"
        workers = min(2, physical)
    elif cpu_input_seconds >= max(0.001, read_seconds * 1.5):
        bottleneck = "cpu_decode"
        confidence = "medium"
        workers = min(8 if requested_device == "cuda" else 4, max(1, physical // 2))
    elif requested_device == "cuda":
        bottleneck = "gpu_compute_likely"
        confidence = "low"
        workers = min(6, max(2, physical // 2))
    else:
        bottleneck = "balanced"
        confidence = "low"
        workers = min(4, max(1, physical // 2))
    if train_images and train_images < 256:
        workers = min(workers, 2)
    workers = max(1, int(workers))
    measured_profiles = [
        item
        for item in (loader.get("concurrency_profiles") or [])
        if int(item.get("decoded_count") or 0) > 0
        and float(item.get("images_per_second") or 0) > 0
    ]
    if measured_profiles:
        peak_throughput = max(float(item["images_per_second"]) for item in measured_profiles)
        # Prefer the smallest setting within 95% of peak to avoid spending CPU for a
        # statistically marginal gain on a 24-image probe.
        competitive = [
            item
            for item in measured_profiles
            if float(item["images_per_second"]) >= peak_throughput * 0.95
        ]
        measured_workers = min(int(item["workers"]) for item in competitive)
        workers = max(1, min(physical, measured_workers))

    # CUDA auto-batch is deliberately retained: Ultralytics targets a bounded VRAM
    # fraction and is safer across architectures than a model-name lookup table.
    if requested_device == "cuda":
        recommended_batch = -1
        batch_mode = "ultralytics_auto"
    else:
        bytes_per_image = max(1, int(imgsz)) ** 2 * 3 * 24
        available = max(0, int(system.get("memory_available_bytes") or 0))
        recommended_batch = max(1, min(8, available // bytes_per_image if available else 4))
        batch_mode = "bounded_cpu_estimate"

    current_workers_raw = advanced.get("workers")
    current_workers = (
        int(current_workers_raw)
        if isinstance(current_workers_raw, (int, float)) and int(current_workers_raw) >= 0
        else min(8, logical)
    )
    current_cache = _normalise_cache(advanced.get("cache", False))

    total_memory = max(0, int(system.get("memory_total_bytes") or 0))
    available_memory = max(0, int(system.get("memory_available_bytes") or 0))
    cache_estimate = max(0, int(loader.get("estimated_ram_cache_bytes") or 0))
    start_method = str(system.get("multiprocessing_start_method") or (
        "spawn" if os.name == "nt" else "fork"
    )).lower()
    # With Windows/spawn the dataset object is serialized into each DataLoader
    # process.  Treat every worker as a possible full cache copy; otherwise a cache
    # that looks like 12 GiB on paper can consume roughly 100 GiB with 8 workers.
    cache_copy_factor = workers + 1 if start_method in {"spawn", "forkserver"} else 1
    current_cache_copy_factor = (
        current_workers + 1 if start_method in {"spawn", "forkserver"} else 1
    )
    effective_cache_estimate = cache_estimate * cache_copy_factor
    current_effective_cache_estimate = cache_estimate * current_cache_copy_factor
    required_headroom = max(
        RAM_CACHE_MIN_HEADROOM_BYTES,
        int(total_memory * RAM_CACHE_HEADROOM_FRACTION),
    )
    remaining_after_cache = available_memory - effective_cache_estimate
    ram_cache_safe = bool(
        total_memory
        and cache_estimate
        and effective_cache_estimate <= int(total_memory * 0.45)
        and remaining_after_cache >= required_headroom
    )
    current_remaining_after_cache = available_memory - current_effective_cache_estimate
    current_ram_cache_safe = bool(
        total_memory
        and cache_estimate
        and current_effective_cache_estimate <= int(total_memory * 0.45)
        and current_remaining_after_cache >= required_headroom
    )
    # RAM cache removes both repeated compressed-file reads and repeated decode work.
    # A large free-memory number alone is still not a reason to consume it: the probe
    # must identify the input path as the likely limiter and the dataset must be large
    # enough for caching to matter.
    recommended_cache: bool | str = (
        "ram"
        if ram_cache_safe and bottleneck in {"io", "cpu_decode"} and train_images >= 256
        else False
    )

    blas_threads = max(1, min(2, physical // max(1, workers + 1)))
    current = {
        "batch": int(current_batch),
        "workers": current_workers,
        "workers_explicit": current_workers_raw is not None,
        "cache": current_cache,
    }
    recommended = {
        "batch": int(recommended_batch),
        "workers": workers,
        "cache": recommended_cache,
    }
    overrides = {
        key: {"current": current[key], "recommended": value}
        for key, value in recommended.items()
        if current[key] != value
    }
    warnings: list[str] = []
    if current_cache == "ram" and not current_ram_cache_safe:
        warnings.append("ram_cache_unsafe")
    if loader.get("failure_count"):
        warnings.append("loader_sample_failures")

    reasons = [
        f"Loader probe: {loader.get('decoded_count', 0)} decoded image(s) at "
        f"{loader.get('images_per_second') or 0} images/s.",
        f"Worker count is bounded by {physical} physical / {logical} logical CPU cores.",
        (
            f"Warm loader concurrency probe selected {workers} worker(s)."
            if measured_profiles
            else "No concurrency profile was available; worker count uses the conservative heuristic."
        ),
        "RAM cache includes possible DataLoader copies and keeps at least 35% of system "
        "RAM or 8 GiB free, whichever is larger.",
        "CUDA batch=-1 delegates sizing to Ultralytics auto-batch to reduce OOM risk."
        if requested_device == "cuda"
        else "CPU batch is capped at 8 and bounded by currently available RAM.",
    ]
    return {
        "schema_name": "geotile_training_resource_recommendation",
        "schema_version": 1,
        "bottleneck": {"kind": bottleneck, "confidence": confidence},
        "recommended_options": recommended,
        "current_options": current,
        "overrides": overrides,
        "is_recommended_applied": not overrides,
        "batch_mode": batch_mode,
        "thread_budget": {
            "data_loader_workers": workers,
            "blas_threads_per_process": blas_threads,
            "opencv_threads_per_process": blas_threads,
        },
        "memory_safety": {
            "ram_cache_safe": ram_cache_safe,
            "current_ram_cache_safe": current_ram_cache_safe,
            "estimated_ram_cache_bytes": cache_estimate,
            "effective_ram_cache_bytes": effective_cache_estimate,
            "current_effective_ram_cache_bytes": current_effective_cache_estimate,
            "cache_copy_factor": cache_copy_factor,
            "current_cache_copy_factor": current_cache_copy_factor,
            "multiprocessing_start_method": start_method,
            "available_memory_bytes": available_memory,
            "required_headroom_bytes": required_headroom,
            "remaining_after_cache_bytes": remaining_after_cache,
            "current_remaining_after_cache_bytes": current_remaining_after_cache,
        },
        "model_estimated_vram_gb": estimated_vram_gb,
        "warnings": warnings,
        "reasons": reasons,
        "loader_benchmark": loader,
        "system": system,
    }


def build_training_resource_recommendation(
    *,
    dataset_dir: Path,
    requested_device: str,
    imgsz: int,
    current_batch: int,
    advanced_options: dict[str, Any] | None = None,
    estimated_vram_gb: float | None = None,
) -> dict[str, Any]:
    loader = benchmark_image_loader(Path(dataset_dir), imgsz=imgsz)
    system = system_resource_snapshot(requested_device)
    return recommend_training_resources(
        loader=loader,
        system=system,
        requested_device=requested_device,
        imgsz=imgsz,
        current_batch=current_batch,
        advanced_options=advanced_options,
        estimated_vram_gb=estimated_vram_gb,
    )
