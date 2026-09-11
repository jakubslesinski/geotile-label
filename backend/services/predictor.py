"""YOLO prediction service — sliding window over full scene with cross-tile NMS."""

import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Generator, Iterator
from concurrent.futures import Future, ThreadPoolExecutor

import numpy as np
from PIL import Image

from models.prediction import PredictionConfig
from services.image_preprocessor import apply_display_params
from services.inference_stitching import (
    clip_polygon_to_rect,
    merge_detections,
    polygon_to_rotated_bbox,
    rotated_bbox_to_xywhr,
)
from services.scene_loader import _estimate_geotiff_display_range
from utils.image import ensure_rgb_uint8

MODELS_ROOT = Path(os.environ.get("MODELS_ROOT", str(Path(__file__).resolve().parent.parent.parent / "data" / "models")))


class PredictionCancelled(Exception):
    """Raised when a running prediction is cancelled by the user."""


def resolve_device() -> str:
    """Pick the inference device: auto-detect CUDA, fall back to CPU.

    The default desktop runtime bundles CPU-only PyTorch, so this returns
    ``cpu`` there; in an environment with a CUDA-enabled torch it returns
    ``cuda`` with no code change. ``GEOTILE_FORCE_DEVICE`` overrides for tests
    and power users.
    """
    forced = os.environ.get("GEOTILE_FORCE_DEVICE")
    if forced:
        return forced.strip().lower()
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def scan_models_dir() -> list[dict]:
    """Scan MODELS_ROOT for .pt files."""
    MODELS_ROOT.mkdir(parents=True, exist_ok=True)
    sam_root = (MODELS_ROOT / "sam").resolve(strict=False)
    models = []
    for p in sorted(MODELS_ROOT.glob("**/*.pt")):
        if p.resolve(strict=False).is_relative_to(sam_root):
            continue
        stat = p.stat()
        models.append({
            "name": p.name,
            "path": str(p),
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    return models


def inspect_yolo_model(model_path: str | Path) -> dict:
    """Load a YOLO model and return lightweight metadata for UI validation."""
    from ultralytics import YOLO

    model_path = Path(model_path)
    model = YOLO(str(model_path))
    raw_names = getattr(model, "names", {}) or {}
    name_items = raw_names.items() if hasattr(raw_names, "items") else enumerate(raw_names)
    classes = [
        {"id": int(class_id), "name": str(name)}
        for class_id, name in sorted(name_items, key=lambda item: int(item[0]))
    ]
    return {
        "name": model_path.name,
        "path": str(model_path),
        "classes": classes,
    }


def _estimate_prediction_display(src, config: PredictionConfig) -> tuple[float | None, float | None, str | None]:
    """Estimate a global display range for YOLO preprocessing from a small sample."""
    dtype = str(src.dtypes[0]).lower()
    mode: str | None = None

    if config.preprocess_mode == "auto":
        _display_min, _display_max, mode = _estimate_geotiff_display_range(src)
    elif config.preprocess_mode == "log":
        if dtype in {"float32", "float64"} and src.count == 1:
            mode = "sar_db"
        else:
            mode = "uint16_log"

    sample_w = min(1024, src.width)
    sample_h = min(1024, src.height)
    indexes = [1]
    try:
        sample = src.read(indexes=indexes, out_shape=(len(indexes), sample_h, sample_w), masked=True)
    except Exception:
        return None, None, mode

    if hasattr(sample, "filled"):
        arr = sample.astype(np.float32).filled(np.nan)
    else:
        arr = np.asarray(sample, dtype=np.float32)
    if arr.ndim == 3:
        arr = arr[0]

    if mode == "sar_db":
        arr = 10.0 * np.log10(np.clip(arr, 1e-8, None))
    elif mode == "uint16_log":
        max_value = float(np.iinfo(np.uint16).max) if dtype == "uint16" else float(np.nanmax(arr) if np.isfinite(arr).any() else 1.0)
        arr = np.log1p(np.clip(arr, 0.0, max(max_value, 1.0)))

    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return None, None, mode

    low_pct = min(config.stretch_low, config.stretch_high)
    high_pct = max(config.stretch_low, config.stretch_high)
    if high_pct <= low_pct:
        return None, None, mode

    low, high = np.percentile(finite, [low_pct, high_pct])
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return None, None, mode
    return float(low), float(high), mode


def _finalize_prediction_crop(crop: np.ndarray, config: PredictionConfig) -> np.ndarray:
    # Avoid a full float32 copy for the overwhelmingly common identity transform.
    if config.brightness != 1.0 or config.contrast != 1.0 or config.gamma != 1.0:
        crop = apply_display_params(
            crop,
            brightness=config.brightness,
            contrast=config.contrast,
            gamma=config.gamma,
            stretch_low=0.0,
            stretch_high=100.0,
        )
    if crop.ndim == 2:
        crop = np.stack([crop, crop, crop], axis=-1)
    if crop.shape[2] > 3:
        crop = crop[:, :, :3]
    return crop


def _add_timing(timings: dict[str, float], name: str, started: float) -> None:
    timings[name] = float(timings.get(name, 0.0)) + (time.perf_counter() - started)


def _preprocess_pil_array(arr: np.ndarray, config: PredictionConfig) -> np.ndarray:
    if config.stretch_low > 0.0 or config.stretch_high < 100.0:
        arr = apply_display_params(
            arr,
            brightness=1.0,
            contrast=1.0,
            gamma=1.0,
            stretch_low=config.stretch_low,
            stretch_high=config.stretch_high,
        )
    return _finalize_prediction_crop(arr, config)


def _preprocess_geotiff_array(
    data: np.ndarray,
    config: PredictionConfig,
    display_range: tuple[float | None, float | None, str | None],
) -> np.ndarray:
    if hasattr(data, "filled"):
        data = data.filled(0)
    arr = np.transpose(data, (1, 2, 0))
    display_min, display_max, display_mode = display_range
    arr = ensure_rgb_uint8(arr, display_min, display_max, display_mode)
    return _finalize_prediction_crop(arr, config)


def _pad_to_tile(crop: np.ndarray, tile_size: int) -> np.ndarray:
    if crop.shape[0] == tile_size and crop.shape[1] == tile_size:
        return crop
    padded = np.zeros((tile_size, tile_size, 3), dtype=np.uint8)
    padded[: crop.shape[0], : crop.shape[1]] = crop
    return padded


def _pad_mask_to_tile(mask: np.ndarray, tile_size: int) -> np.ndarray:
    if mask.shape[0] == tile_size and mask.shape[1] == tile_size:
        return mask
    padded = np.zeros((tile_size, tile_size), dtype=bool)
    padded[: mask.shape[0], : mask.shape[1]] = mask
    return padded


def _detection_center_is_valid(box: np.ndarray, valid_mask: np.ndarray | None) -> bool:
    if valid_mask is None:
        return True
    center_x = int(np.floor((float(box[0]) + float(box[2])) / 2.0))
    center_y = int(np.floor((float(box[1]) + float(box[3])) / 2.0))
    if center_x < 0 or center_y < 0 or center_y >= valid_mask.shape[0] or center_x >= valid_mask.shape[1]:
        return False
    return bool(valid_mask[center_y, center_x])


@dataclass(frozen=True)
class _ReaderInfo:
    width: int
    height: int
    total_windows: int


@dataclass(frozen=True)
class _WindowSample:
    index: int
    x0: int
    y0: int
    crop: np.ndarray | None
    valid_mask: np.ndarray | None


@dataclass(frozen=True)
class _WindowBatch:
    samples: list[_WindowSample]
    bytes_held: int


@dataclass(frozen=True)
class _RawWindowSample:
    index: int
    x0: int
    y0: int
    kind: str
    payload: tuple[Any, ...]


@dataclass(frozen=True)
class _ReaderFailure:
    error: BaseException


@dataclass(frozen=True)
class _ReaderDone:
    pass


class _QueueMemory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current_bytes = 0
        self.peak_bytes = 0
        self.peak_batches = 0

    def added(self, bytes_held: int, queue_size: int) -> None:
        with self._lock:
            self.current_bytes += bytes_held
            self.peak_bytes = max(self.peak_bytes, self.current_bytes)
            self.peak_batches = max(self.peak_batches, queue_size)

    def removed(self, bytes_held: int) -> None:
        with self._lock:
            self.current_bytes = max(0, self.current_bytes - bytes_held)


def iter_prediction_windows(
    width: int,
    height: int,
    tile_size: int,
    buffer: int,
) -> Iterator[tuple[int, int, int]]:
    """Yield legacy-compatible window origins without materializing the full grid."""

    stride = tile_size - buffer
    if stride <= 0:
        raise ValueError("Prediction buffer must be smaller than tile size")
    index = 0
    for y0 in range(0, height, stride):
        for x0 in range(0, width, stride):
            yield index, x0, y0
            index += 1


def prediction_window_count(width: int, height: int, tile_size: int, buffer: int) -> int:
    stride = tile_size - buffer
    if stride <= 0:
        raise ValueError("Prediction buffer must be smaller than tile size")
    return len(range(0, height, stride)) * len(range(0, width, stride))


def resolve_inference_batch_size(config: PredictionConfig) -> int:
    if config.batch_size != "auto":
        return int(config.batch_size)
    override = os.environ.get("GEOTILE_INFERENCE_AUTO_BATCH")
    if override:
        try:
            return max(1, min(64, int(override)))
        except ValueError:
            pass
    if not str(config.device).lower().startswith("cuda"):
        return 1
    base = 4
    try:
        import torch

        free_bytes, _total_bytes = torch.cuda.mem_get_info()
        free_gib = free_bytes / (1024**3)
        if free_gib >= 48:
            base = 32
        elif free_gib >= 24:
            base = 16
        elif free_gib >= 12:
            base = 8
        elif free_gib >= 6:
            base = 4
        else:
            base = 2
    except Exception:
        base = 4
    inference_size = max(config.tile_size, config.img_size or config.tile_size)
    scaled = int(base * (640.0 / max(128, inference_size)) ** 2)
    return max(1, min(32, scaled))


def _queue_put(
    output: queue.Queue,
    item: Any,
    stop_event: threading.Event,
    should_cancel: Callable[[], bool] | None,
) -> bool:
    while not stop_event.is_set():
        if should_cancel and should_cancel():
            raise PredictionCancelled()
        try:
            output.put(item, timeout=0.1)
            return True
        except queue.Full:
            continue
    return False


def _resolve_preprocess_workers(batch_size: int) -> int:
    if batch_size <= 1:
        return 1
    override = os.environ.get("GEOTILE_INFERENCE_PREPROCESS_WORKERS")
    if override:
        try:
            return max(1, min(16, batch_size, int(override)))
        except ValueError:
            pass
    return max(1, min(8, batch_size, max(2, (os.cpu_count() or 2) // 2)))


def _preprocess_raw_window(
    sample: _RawWindowSample,
    config: PredictionConfig,
    display_range: tuple[float | None, float | None, str | None],
) -> tuple[np.ndarray | None, np.ndarray | None, float]:
    started = time.perf_counter()
    valid_mask = None
    if sample.kind == "raster":
        crop = _preprocess_geotiff_array(sample.payload[0], config, display_range)
        crop = _pad_to_tile(crop, config.tile_size)
    elif sample.kind == "pil":
        crop = _preprocess_pil_array(sample.payload[0], config)
        crop = _pad_to_tile(crop, config.tile_size)
    else:
        raise RuntimeError(f"Unknown prediction window kind: {sample.kind}")
    return crop, valid_mask, time.perf_counter() - started


def _prepare_window_batch(
    raw_batch: list[_RawWindowSample],
    *,
    config: PredictionConfig,
    display_range: tuple[float | None, float | None, str | None],
    executor: ThreadPoolExecutor | None,
    timings: dict[str, float],
) -> list[_WindowSample]:
    started = time.perf_counter()
    if executor is None:
        prepared = [
            _preprocess_raw_window(sample, config, display_range)
            for sample in raw_batch
        ]
    else:
        futures: list[Future] = [
            executor.submit(_preprocess_raw_window, sample, config, display_range)
            for sample in raw_batch
        ]
        # Resolve in source-window order so batching never changes result mapping.
        prepared = [future.result() for future in futures]
    timings["preprocess_seconds"] = float(timings.get("preprocess_seconds", 0.0)) + (
        time.perf_counter() - started
    )
    timings["preprocess_cpu_seconds"] = float(
        timings.get("preprocess_cpu_seconds", 0.0)
    ) + sum(item[2] for item in prepared)
    return [
        _WindowSample(
            index=raw.index,
            x0=raw.x0,
            y0=raw.y0,
            crop=result[0],
            valid_mask=result[1],
        )
        for raw, result in zip(raw_batch, prepared, strict=True)
    ]


def _enqueue_window_batch(
    samples: list[_WindowSample],
    output: queue.Queue,
    stop_event: threading.Event,
    should_cancel: Callable[[], bool] | None,
    queue_memory: _QueueMemory,
) -> bool:
    bytes_held = sum(
        (sample.crop.nbytes if sample.crop is not None else 0)
        + (sample.valid_mask.nbytes if sample.valid_mask is not None else 0)
        for sample in samples
    )
    queued = _WindowBatch(samples=samples, bytes_held=bytes_held)
    queue_memory.added(bytes_held, min(output.maxsize, output.qsize() + 1))
    if _queue_put(output, queued, stop_event, should_cancel):
        return True
    queue_memory.removed(bytes_held)
    return False


def _produce_window_batches(
    scene_path: Path,
    config: PredictionConfig,
    batch_size: int,
    output: queue.Queue,
    stop_event: threading.Event,
    should_cancel: Callable[[], bool] | None,
    timings: dict[str, float],
    queue_memory: _QueueMemory,
    metrics: dict[str, Any],
) -> None:
    raster_src = None
    pil_img = None
    executor = None
    try:
        setup_started = time.perf_counter()
        display_range: tuple[float | None, float | None, str | None] = (None, None, None)
        if scene_path.suffix.lower() in (
            ".tif",
            ".tiff",
            ".vrt",
            ".jp2",
            ".img",
            ".ntf",
            ".nitf",
            ".png",
        ):
            import rasterio

            raster_src = rasterio.open(scene_path)
            width, height = raster_src.width, raster_src.height
            # PNG is lossless RGB and historically used Pillow's per-window
            # display preprocessing. Keep those pixels/semantics while using
            # GDAL only as the bounded, windowed reader for very large scenes.
            if scene_path.suffix.lower() != ".png":
                display_range = _estimate_prediction_display(raster_src, config)
        else:
            pil_img = Image.open(scene_path).convert("RGB")
            width, height = pil_img.width, pil_img.height
        _add_timing(timings, "reader_setup_seconds", setup_started)

        total = prediction_window_count(width, height, config.tile_size, config.buffer)
        if not _queue_put(
            output,
            _ReaderInfo(width=width, height=height, total_windows=total),
            stop_event,
            should_cancel,
        ):
            return

        preprocess_workers = _resolve_preprocess_workers(batch_size)
        metrics["preprocess_workers"] = preprocess_workers
        if preprocess_workers > 1:
            executor = ThreadPoolExecutor(
                max_workers=preprocess_workers,
                thread_name_prefix="prediction-preprocess",
            )

        raw_batch: list[_RawWindowSample] = []
        for index, x0, y0 in iter_prediction_windows(
            width, height, config.tile_size, config.buffer
        ):
            if stop_event.is_set() or (should_cancel and should_cancel()):
                raise PredictionCancelled()
            if raster_src is not None:
                from rasterio.windows import Window

                window_width = min(config.tile_size, raster_src.width - x0)
                window_height = min(config.tile_size, raster_src.height - y0)
                indexes = list(range(1, min(raster_src.count, 3) + 1))
                read_started = time.perf_counter()
                data = raster_src.read(
                    indexes=indexes,
                    window=Window(x0, y0, window_width, window_height),
                    masked=True,
                )
                _add_timing(timings, "read_seconds", read_started)
                if scene_path.suffix.lower() == ".png":
                    if hasattr(data, "filled"):
                        data = data.filled(0)
                    raw = _RawWindowSample(
                        index, x0, y0, "pil", (np.transpose(data, (1, 2, 0)),)
                    )
                else:
                    raw = _RawWindowSample(index, x0, y0, "raster", (data,))
            elif pil_img is not None:
                read_started = time.perf_counter()
                crop = pil_img.crop(
                    (
                        x0,
                        y0,
                        min(x0 + config.tile_size, pil_img.width),
                        min(y0 + config.tile_size, pil_img.height),
                    )
                ).convert("RGB")
                arr = np.array(crop)
                _add_timing(timings, "read_seconds", read_started)
                raw = _RawWindowSample(index, x0, y0, "pil", (arr,))
            else:
                raise RuntimeError("Scene reader not initialized")

            raw_batch.append(raw)
            if len(raw_batch) >= batch_size:
                samples = _prepare_window_batch(
                    raw_batch,
                    config=config,
                    display_range=display_range,
                    executor=executor,
                    timings=timings,
                )
                if not _enqueue_window_batch(
                    samples,
                    output,
                    stop_event,
                    should_cancel,
                    queue_memory,
                ):
                    return
                raw_batch = []
        if raw_batch:
            samples = _prepare_window_batch(
                raw_batch,
                config=config,
                display_range=display_range,
                executor=executor,
                timings=timings,
            )
            if not _enqueue_window_batch(
                samples,
                output,
                stop_event,
                should_cancel,
                queue_memory,
            ):
                return
        _queue_put(output, _ReaderDone(), stop_event, should_cancel)
    except BaseException as exc:
        if not stop_event.is_set():
            try:
                _queue_put(output, _ReaderFailure(exc), stop_event, None)
            except Exception:
                pass
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        if raster_src is not None:
            raster_src.close()
        if pil_img is not None:
            pil_img.close()


def _next_reader_message(
    messages: queue.Queue,
    stop_event: threading.Event,
    should_cancel: Callable[[], bool] | None,
) -> Any:
    while not stop_event.is_set():
        if should_cancel and should_cancel():
            raise PredictionCancelled()
        try:
            return messages.get(timeout=0.1)
        except queue.Empty:
            continue
    raise PredictionCancelled()


def _as_numpy(value: Any) -> np.ndarray:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    return np.asarray(value)


def _is_cuda_oom(error: BaseException) -> bool:
    text = str(error).lower()
    return "out of memory" in text and ("cuda" in text or "cudnn" in text)


def _predict_sources(
    model: Any,
    sources: list[np.ndarray],
    config: PredictionConfig,
    imgsz: int,
    should_cancel: Callable[[], bool] | None,
    metrics: dict[str, Any],
) -> list[Any]:
    if not sources:
        return []
    if should_cancel and should_cancel():
        raise PredictionCancelled()
    source: np.ndarray | list[np.ndarray] = sources[0] if len(sources) == 1 else sources
    metrics["model_input_backend"] = "numpy"
    metrics["inference_precision"] = "fp32"
    metrics["model_calls"] = int(metrics.get("model_calls") or 0) + 1
    try:
        predicted = list(
            model.predict(
                source=source,
                imgsz=imgsz,
                conf=config.conf,
                iou=config.iou,
                device=config.device,
                verbose=False,
            )
        )
        if predicted:
            speed = getattr(predicted[0], "speed", None) or {}
            for stage in ("preprocess", "inference", "postprocess"):
                if stage in speed:
                    key = f"ultralytics_{stage}_seconds"
                    metrics[key] = float(metrics.get(key, 0.0)) + (
                        float(speed[stage]) * len(predicted) / 1000.0
                    )
        return predicted
    except RuntimeError as exc:
        if config.batch_size != "auto" or len(sources) <= 1 or not _is_cuda_oom(exc):
            raise
        metrics["oom_retries"] = int(metrics.get("oom_retries") or 0) + 1
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:
            pass
        midpoint = max(1, len(sources) // 2)
        return [
            *_predict_sources(
                model,
                sources[:midpoint],
                config,
                imgsz,
                should_cancel,
                metrics,
            ),
            *_predict_sources(
                model,
                sources[midpoint:],
                config,
                imgsz,
                should_cancel,
                metrics,
            ),
        ]


def _append_batch_detections(
    results: list[Any],
    samples: list[_WindowSample],
    *,
    width: int,
    height: int,
    geometry_type: str,
    detections: list[dict[str, Any]],
) -> str:
    for result, sample in zip(results, samples, strict=True):
        obb = getattr(result, "obb", None)
        if obb is not None and len(obb) > 0:
            geometry_type = "rotated_bbox"
            polygons = _as_numpy(obb.xyxyxyxy)
            classes = _as_numpy(obb.cls)
            confidences = _as_numpy(obb.conf)
            for index, local_polygon in enumerate(polygons):
                local_bbox = np.asarray(
                    [
                        np.min(local_polygon[:, 0]),
                        np.min(local_polygon[:, 1]),
                        np.max(local_polygon[:, 0]),
                        np.max(local_polygon[:, 1]),
                    ],
                    dtype=np.float32,
                )
                if not _detection_center_is_valid(local_bbox, sample.valid_mask):
                    continue
                scene_polygon = np.asarray(local_polygon, dtype=np.float32).copy()
                scene_polygon[:, 0] += sample.x0
                scene_polygon[:, 1] += sample.y0
                clipped = clip_polygon_to_rect(scene_polygon, width, height)
                converted = polygon_to_rotated_bbox(clipped)
                if converted is None:
                    continue
                rotated_bbox, bbox = converted
                detections.append(
                    {
                        "geometry_type": "rotated_bbox",
                        "bbox": bbox,
                        "rotated_bbox": rotated_bbox,
                        "xywhr": rotated_bbox_to_xywhr(rotated_bbox),
                        "confidence": float(confidences[index]),
                        "class_id": int(classes[index]),
                        "source_window": [sample.x0, sample.y0],
                    }
                )
            continue

        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            continue
        xyxy = _as_numpy(boxes.xyxy)
        classes = _as_numpy(boxes.cls)
        confidences = _as_numpy(boxes.conf)
        for index, local_box in enumerate(xyxy):
            if not _detection_center_is_valid(local_box, sample.valid_mask):
                continue
            scene_box = [
                max(0.0, float(local_box[0] + sample.x0)),
                max(0.0, float(local_box[1] + sample.y0)),
                min(float(width), float(local_box[2] + sample.x0)),
                min(float(height), float(local_box[3] + sample.y0)),
            ]
            detections.append(
                {
                    "geometry_type": "bbox",
                    "bbox": scene_box,
                    "confidence": float(confidences[index]),
                    "class_id": int(classes[index]),
                    "source_window": [sample.x0, sample.y0],
                }
            )
    return geometry_type


def execute_prediction(
    scene_path: str | Path,
    config: PredictionConfig,
    should_cancel: Callable[[], bool] | None = None,
    performance_callback: Callable[[dict[str, Any]], None] | None = None,
) -> Generator[dict, None, list[dict]]:
    """
    Sliding window YOLO prediction over full scene.
    Yields progress dicts, returns final prediction list.
    """
    from ultralytics import YOLO

    scene_path = Path(scene_path)
    if config.buffer >= config.tile_size:
        raise ValueError("Prediction buffer must be smaller than tile size")
    if config.stretch_high <= config.stretch_low:
        raise ValueError("Prediction stretch_high must be greater than stretch_low")

    wall_started = time.perf_counter()
    timings: dict[str, float] = {
        "reader_setup_seconds": 0.0,
        "read_seconds": 0.0,
        "preprocess_seconds": 0.0,
        "preprocess_cpu_seconds": 0.0,
        "inference_seconds": 0.0,
        "postprocess_seconds": 0.0,
        "merge_seconds": 0.0,
    }
    resolved_batch_size = resolve_inference_batch_size(config)
    metrics: dict[str, Any] = {
        "requested_batch_size": config.batch_size,
        "resolved_batch_size": resolved_batch_size,
        "prefetch_batches": config.prefetch_batches,
        "merge_method": config.merge_method,
        "model_calls": 0,
        "oom_retries": 0,
        "batches_processed": 0,
        "model_windows": 0,
    }
    messages: queue.Queue = queue.Queue(maxsize=config.prefetch_batches)
    stop_event = threading.Event()
    queue_memory = _QueueMemory()
    reader = threading.Thread(
        target=_produce_window_batches,
        args=(
            scene_path,
            config,
            resolved_batch_size,
            messages,
            stop_event,
            should_cancel,
            timings,
            queue_memory,
            metrics,
        ),
        name="prediction-window-reader",
        daemon=True,
    )
    reader.start()

    raw_detections: list[dict[str, Any]] = []
    width = height = total = 0
    geometry_type = "bbox"
    model_name = Path(config.model_path).name
    try:
        first = _next_reader_message(messages, stop_event, should_cancel)
        if isinstance(first, _ReaderFailure):
            raise first.error
        if not isinstance(first, _ReaderInfo):
            raise RuntimeError("Prediction reader did not provide scene metadata")
        width, height, total = first.width, first.height, first.total_windows

        model_started = time.perf_counter()
        model = YOLO(config.model_path)
        timings["model_setup_seconds"] = time.perf_counter() - model_started
        model_task = str(getattr(model, "task", "") or "").lower()
        if model_task == "obb":
            geometry_type = "rotated_bbox"
        raw_names = getattr(model, "names", {}) or {}
        name_items = raw_names.items() if hasattr(raw_names, "items") else enumerate(raw_names)
        class_names = {int(class_id): str(name) for class_id, name in name_items}
        imgsz = config.img_size or config.tile_size
        last_progress_at = 0.0

        while True:
            message = _next_reader_message(messages, stop_event, should_cancel)
            if isinstance(message, _ReaderFailure):
                raise message.error
            if isinstance(message, _ReaderDone):
                break
            if not isinstance(message, _WindowBatch):
                raise RuntimeError("Unknown prediction reader message")
            queue_memory.removed(message.bytes_held)
            metrics["batches_processed"] += 1
            valid_samples = [sample for sample in message.samples if sample.crop is not None]
            sources = [sample.crop for sample in valid_samples if sample.crop is not None]
            inference_started = time.perf_counter()
            results = _predict_sources(
                model,
                sources,
                config,
                imgsz,
                should_cancel,
                metrics,
            )
            _add_timing(timings, "inference_seconds", inference_started)
            if len(results) != len(valid_samples):
                raise RuntimeError(
                    f"YOLO returned {len(results)} results for {len(valid_samples)} windows"
                )
            metrics["model_windows"] += len(valid_samples)
            postprocess_started = time.perf_counter()
            geometry_type = _append_batch_detections(
                results,
                valid_samples,
                width=width,
                height=height,
                geometry_type=geometry_type,
                detections=raw_detections,
            )
            _add_timing(timings, "postprocess_seconds", postprocess_started)

            done = message.samples[-1].index + 1
            now = time.perf_counter()
            if (
                done >= total
                or last_progress_at == 0.0
                or (now - last_progress_at) * 1000.0 >= config.progress_interval_ms
            ):
                last_progress_at = now
                yield {
                    "done": done,
                    "total": total,
                    "detections_so_far": len(raw_detections),
                    "current_tile": f"{done}/{total}",
                    "model": model_name,
                    "device": config.device,
                    "batch_size": len(message.samples),
                    "resolved_batch_size": resolved_batch_size,
                }

        if should_cancel and should_cancel():
            raise PredictionCancelled()

        merge_started = time.perf_counter()
        merged, merge_backend = merge_detections(
            raw_detections,
            geometry_type=geometry_type,
            method=config.merge_method,
            iou_threshold=config.iou,
        )
        _add_timing(timings, "merge_seconds", merge_started)
        now_iso = datetime.now(timezone.utc).isoformat()
        predictions: list[dict[str, Any]] = []
        for detection in merged:
            class_id = int(detection["class_id"])
            prediction = {
                "id": uuid.uuid4().hex[:12],
                "class_id": class_id,
                "class_name": class_names.get(class_id, str(class_id)),
                "bbox": detection["bbox"],
                "confidence": float(detection["confidence"]),
                "source_model": model_name,
                "status": "pending",
                "created_at": now_iso,
                "geometry_type": detection.get("geometry_type", "bbox"),
            }
            if detection.get("rotated_bbox") is not None:
                prediction["rotated_bbox"] = detection["rotated_bbox"]
            predictions.append(prediction)

        wall_seconds = time.perf_counter() - wall_started
        performance = {
            "schema_name": "geotile_inference_performance",
            "schema_version": 1,
            **{name: round(value, 6) for name, value in timings.items()},
            **metrics,
            "wall_seconds": round(wall_seconds, 6),
            "windows_total": total,
            "windows_processed": total,
            "raw_detections": len(raw_detections),
            "merged_predictions": len(predictions),
            "geometry_type": geometry_type,
            "merge_backend": merge_backend,
            "queue_capacity_batches": config.prefetch_batches,
            "queue_capacity_bytes_estimate": (
                resolved_batch_size
                * (config.prefetch_batches + 3)
                * config.tile_size
                * config.tile_size
                * 7
            ),
            "peak_queued_batches": queue_memory.peak_batches,
            "peak_queued_bytes": queue_memory.peak_bytes,
            "windows_per_second": round(total / max(wall_seconds, 1e-9), 3),
        }
        if performance_callback is not None:
            performance_callback(performance)
        return predictions
    finally:
        stop_event.set()
        reader.join(timeout=5.0)
