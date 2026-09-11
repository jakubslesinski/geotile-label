"""Read-only whole-scene inference benchmark using the production predictor."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.common import add_common_arguments, configure_environment, consume_generator, output_path


OPERATION = "whole_scene_inference"


def prediction_signature(predictions: list[dict]) -> tuple[str, dict[str, int], float]:
    canonical = []
    geometry_counts: dict[str, int] = {}
    confidence_sum = 0.0
    for prediction in predictions:
        geometry_type = str(prediction.get("geometry_type") or "bbox")
        geometry_counts[geometry_type] = geometry_counts.get(geometry_type, 0) + 1
        confidence = round(float(prediction.get("confidence") or 0.0), 5)
        confidence_sum += confidence
        rotated = prediction.get("rotated_bbox") or {}
        canonical.append(
            {
                "class_id": int(prediction.get("class_id") or 0),
                "confidence": confidence,
                "bbox": [round(float(value), 4) for value in prediction.get("bbox") or []],
                "geometry_type": geometry_type,
                "rotated_bbox": {
                    key: round(float(rotated[key]), 4)
                    for key in ("cx", "cy", "width", "height", "angle_deg")
                    if key in rotated
                },
            }
        )
    canonical.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest(), geometry_counts, round(confidence_sum, 5)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser, collect_gpu=True)
    parser.add_argument("--scene-path", required=True)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--tile-size", type=int, default=640)
    parser.add_argument("--buffer", type=int, default=64)
    parser.add_argument("--img-size", type=int)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.4)
    parser.add_argument("--batch-size", default="auto", help="auto or integer 1..64")
    parser.add_argument("--prefetch-batches", type=int, default=2)
    parser.add_argument("--progress-interval-ms", type=int, default=250)
    return parser


def run(args: argparse.Namespace) -> Path:
    configure_environment(args)

    from models.prediction import PredictionConfig
    from services.performance_metrics import PerformanceRecorder
    from services.predictor import execute_prediction, resolve_device

    scene_path = Path(args.scene_path).expanduser().resolve()
    model_path = Path(args.model_path).expanduser().resolve()
    destination = output_path(args, OPERATION)
    device = resolve_device() if args.device == "auto" else args.device
    batch_size: str | int = (
        "auto" if str(args.batch_size).lower() == "auto" else int(args.batch_size)
    )

    with PerformanceRecorder(
        OPERATION,
        output_path=destination,
        cache_state=args.cache_state,
        project_id=args.project_id,
        storage_profile=args.storage_profile,
        collect_gpu=args.collect_gpu_info,
        inputs={
            "scene_name": scene_path.name,
            "scene_bytes": scene_path.stat().st_size if scene_path.is_file() else None,
            "model_name": model_path.name,
            "model_bytes": model_path.stat().st_size if model_path.is_file() else None,
            "device": device,
            "tile_size": args.tile_size,
            "buffer": args.buffer,
            "img_size": args.img_size,
            "confidence": args.confidence,
            "iou": args.iou,
            "batch_size": batch_size,
            "prefetch_batches": args.prefetch_batches,
        },
        metadata_value={"read_only": True, "persists_prediction_session": False},
    ) as recorder:
        if not scene_path.is_file():
            raise FileNotFoundError(f"Scene not found: {scene_path}")
        if not model_path.is_file():
            raise FileNotFoundError(f"Model not found: {model_path}")
        config = PredictionConfig(
            model_path=str(model_path),
            conf=args.confidence,
            iou=args.iou,
            tile_size=args.tile_size,
            buffer=args.buffer,
            device=device,
            img_size=args.img_size,
            batch_size=batch_size,
            prefetch_batches=args.prefetch_batches,
            progress_interval_ms=args.progress_interval_ms,
        )
        inference_performance: dict = {}
        with recorder.stage("execute_prediction") as timer:
            predictions, progress_events = consume_generator(
                execute_prediction(
                    scene_path,
                    config,
                    performance_callback=lambda value: inference_performance.update(value),
                )
            )
            timer.record(
                progress_events=progress_events,
                predictions=len(predictions or []),
            )
        elapsed = recorder.report["stages"]["execute_prediction"]["wall_seconds"]
        windows_processed = int(inference_performance.get("windows_processed") or 0)
        recorder.set_metric("progress_events", progress_events)
        recorder.set_metric("windows_processed", windows_processed)
        recorder.set_metric("predictions", len(predictions or []))
        signature, geometry_counts, confidence_sum = prediction_signature(predictions or [])
        recorder.set_metric("prediction_signature_sha256", signature)
        recorder.set_metric("prediction_geometry_counts", geometry_counts)
        recorder.set_metric("prediction_confidence_sum", confidence_sum)
        recorder.set_metric(
            "windows_per_second", round(windows_processed / max(elapsed, 1e-9), 3)
        )
        recorder.set_metric("pipeline", inference_performance)
    return destination


def main() -> None:
    args = build_parser().parse_args()
    destination = run(args)
    print(destination)


if __name__ == "__main__":
    main()
