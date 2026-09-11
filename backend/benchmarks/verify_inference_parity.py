"""Compare batch=1 and batched whole-scene inference with numeric tolerances."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.common import configure_environment, consume_generator


def _geometry_values(prediction: dict) -> list[float]:
    values = [float(value) for value in prediction.get("bbox") or []]
    rotated = prediction.get("rotated_bbox") or {}
    values.extend(
        float(rotated[key])
        for key in ("cx", "cy", "width", "height", "angle_deg")
        if key in rotated
    )
    return values


def _sort_key(prediction: dict) -> tuple:
    return (
        int(prediction.get("class_id", -1)),
        str(prediction.get("geometry_type") or "bbox"),
        *[round(value, 3) for value in _geometry_values(prediction)],
    )


def _match_cost(first: dict, second: dict) -> float:
    cost = sum(
        abs(float(a) - float(b))
        for a, b in zip(first["bbox"], second["bbox"])
    )
    first_rotated = first.get("rotated_bbox") or {}
    second_rotated = second.get("rotated_bbox") or {}
    if first_rotated and second_rotated:
        cost += sum(
            abs(float(first_rotated[field]) - float(second_rotated[field]))
            for field in ("cx", "cy", "width", "height")
        )
        raw_angle = abs(
            float(first_rotated["angle_deg"])
            - float(second_rotated["angle_deg"])
        ) % 180.0
        cost += 0.1 * min(raw_angle, 180.0 - raw_angle)
    return cost


def compare_predictions(baseline: list[dict], candidate: list[dict]) -> dict:
    baseline_groups: dict[tuple[int, str], list[dict]] = defaultdict(list)
    candidate_groups: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for prediction in baseline:
        key = (
            int(prediction.get("class_id", -1)),
            str(prediction.get("geometry_type") or "bbox"),
        )
        baseline_groups[key].append(prediction)
    for prediction in candidate:
        key = (
            int(prediction.get("class_id", -1)),
            str(prediction.get("geometry_type") or "bbox"),
        )
        candidate_groups[key].append(prediction)

    group_counts_match = {
        str(key): (len(baseline_groups[key]), len(candidate_groups[key]))
        for key in sorted(set(baseline_groups) | set(candidate_groups))
        if len(baseline_groups[key]) != len(candidate_groups[key])
    }
    confidence_deltas: list[float] = []
    bbox_deltas: list[float] = []
    bbox_pair_deltas: list[float] = []
    rotated_deltas: list[float] = []
    angle_deltas: list[float] = []
    if not group_counts_match:
        for key in sorted(baseline_groups):
            left = baseline_groups[key]
            right = candidate_groups.get(key, [])
            spatial_bins: dict[tuple[int, int], list[int]] = defaultdict(list)
            for index, prediction in enumerate(right):
                box = prediction["bbox"]
                center = (
                    int((float(box[0]) + float(box[2])) // 2),
                    int((float(box[1]) + float(box[3])) // 2),
                )
                spatial_bins[center].append(index)
            candidate_edges: list[tuple[float, int, int]] = []
            for left_index, first in enumerate(left):
                box = first["bbox"]
                center_x = int((float(box[0]) + float(box[2])) // 2)
                center_y = int((float(box[1]) + float(box[3])) // 2)
                nearby = {
                    index
                    for dx in range(-2, 3)
                    for dy in range(-2, 3)
                    for index in spatial_bins.get((center_x + dx, center_y + dy), [])
                }
                for right_index in nearby:
                    candidate_edges.append(
                        (
                            _match_cost(first, right[right_index]),
                            left_index,
                            right_index,
                        )
                    )

            # Global shortest-edge selection avoids cascading mismatches when
            # several dense detections share almost the same center.
            pairs: dict[int, int] = {}
            used_right: set[int] = set()
            for _score, left_index, right_index in sorted(candidate_edges):
                if left_index not in pairs and right_index not in used_right:
                    pairs[left_index] = right_index
                    used_right.add(right_index)
            remaining_left = [index for index in range(len(left)) if index not in pairs]
            remaining_right = [index for index in range(len(right)) if index not in used_right]
            fallback_edges = sorted(
                (
                    _match_cost(left[left_index], right[right_index]),
                    left_index,
                    right_index,
                )
                for left_index in remaining_left
                for right_index in remaining_right
            )
            for _score, left_index, right_index in fallback_edges:
                if left_index not in pairs and right_index not in used_right:
                    pairs[left_index] = right_index
                    used_right.add(right_index)

            for left_index, second_index in sorted(pairs.items()):
                first = left[left_index]
                second = right[second_index]
                confidence_deltas.append(
                    abs(float(first["confidence"]) - float(second["confidence"]))
                )
                pair_bbox_deltas = [
                    abs(float(a) - float(b))
                    for a, b in zip(first["bbox"], second["bbox"])
                ]
                bbox_deltas.extend(pair_bbox_deltas)
                pair_bbox_delta = max(pair_bbox_deltas, default=0.0)
                bbox_pair_deltas.append(pair_bbox_delta)
                first_rotated = first.get("rotated_bbox") or {}
                second_rotated = second.get("rotated_bbox") or {}
                for field in ("cx", "cy", "width", "height"):
                    if (
                        pair_bbox_delta <= 0.5
                        and field in first_rotated
                        and field in second_rotated
                    ):
                        rotated_deltas.append(
                            abs(float(first_rotated[field]) - float(second_rotated[field]))
                        )
                if (
                    pair_bbox_delta <= 0.5
                    and "angle_deg" in first_rotated
                    and "angle_deg" in second_rotated
                ):
                    first_width = float(first_rotated.get("width") or 0.0)
                    first_height = float(first_rotated.get("height") or 0.0)
                    aspect_ratio = max(first_width, first_height) / max(
                        min(first_width, first_height), 1e-9
                    )
                    # Near-square rotated boxes do not have a stable principal
                    # angle; a 90-degree representation change is equivalent.
                    if aspect_ratio > 1.05:
                        raw_delta = abs(
                            float(first_rotated["angle_deg"])
                            - float(second_rotated["angle_deg"])
                        ) % 180.0
                        angle_deltas.append(min(raw_delta, 180.0 - raw_delta))

    def maximum(values: list[float]) -> float:
        return max(values, default=0.0)

    def mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    tolerances = {
        "geometry_min_match_rate": 0.995,
        "confidence_max_abs": 0.01,
        "confidence_mean_abs": 0.001,
        "rotated_max_abs_px": 0.5,
        "angle_max_abs_deg": 0.5,
    }
    geometry_matches = sum(value <= 0.5 for value in bbox_pair_deltas)
    geometry_match_rate = geometry_matches / max(len(bbox_pair_deltas), 1)
    deltas = {
        "geometry_match_rate": geometry_match_rate,
        "geometry_mismatches_over_0_5px": len(bbox_pair_deltas) - geometry_matches,
        "confidence_max_abs": maximum(confidence_deltas),
        "confidence_mean_abs": mean(confidence_deltas),
        "bbox_max_abs_px": maximum(bbox_deltas),
        "rotated_max_abs_px": maximum(rotated_deltas),
        "angle_max_abs_deg": maximum(angle_deltas),
    }
    passed = (
        not group_counts_match
        and geometry_match_rate >= tolerances["geometry_min_match_rate"]
        and all(
            deltas[name] <= limit
            for name, limit in tolerances.items()
            if name != "geometry_min_match_rate"
        )
    )
    return {
        "passed": passed,
        "baseline_count": len(baseline),
        "candidate_count": len(candidate),
        "group_count_mismatches": group_counts_match,
        "baseline_classes": dict(Counter(int(item["class_id"]) for item in baseline)),
        "candidate_classes": dict(Counter(int(item["class_id"]) for item in candidate)),
        "deltas": {key: round(value, 8) for key, value in deltas.items()},
        "tolerances": tolerances,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene-path", required=True)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--candidate-batch-size", default="auto")
    parser.add_argument(
        "--run-order",
        choices=("candidate-first", "baseline-first"),
        default="candidate-first",
        help="candidate-first is conservative because batch=1 receives the warm second run",
    )
    parser.add_argument("--prefetch-batches", type=int, default=2)
    parser.add_argument("--tile-size", type=int, default=640)
    parser.add_argument("--buffer", type=int, default=64)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.4)
    parser.add_argument("--output", required=True)
    parser.add_argument("--data-dir")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    configure_environment(args)
    from models.prediction import PredictionConfig
    from services.predictor import execute_prediction

    candidate_batch: str | int = (
        "auto"
        if str(args.candidate_batch_size).lower() == "auto"
        else int(args.candidate_batch_size)
    )
    common = dict(
        model_path=str(Path(args.model_path).resolve()),
        conf=args.confidence,
        iou=args.iou,
        tile_size=args.tile_size,
        buffer=args.buffer,
        device=args.device,
        prefetch_batches=args.prefetch_batches,
        progress_interval_ms=5000,
    )
    runs = []
    predictions_by_name: dict[str, list[dict]] = {}
    run_order = (
        (("candidate", candidate_batch), ("baseline", 1))
        if args.run_order == "candidate-first"
        else (("baseline", 1), ("candidate", candidate_batch))
    )
    for name, batch_size in run_order:
        performance: dict = {}
        started = time.perf_counter()
        predictions, _events = consume_generator(
            execute_prediction(
                Path(args.scene_path).resolve(),
                PredictionConfig(**common, batch_size=batch_size),
                performance_callback=performance.update,
            )
        )
        predictions_by_name[name] = predictions
        runs.append(
            {
                "name": name,
                "batch_size": batch_size,
                "wall_seconds": round(time.perf_counter() - started, 6),
                "pipeline": performance,
            }
        )

    report = {
        "schema_name": "geotile_inference_parity",
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scene_path": str(Path(args.scene_path).resolve()),
        "model_path": str(Path(args.model_path).resolve()),
        "run_order": args.run_order,
        "runs": runs,
        "comparison": compare_predictions(
            predictions_by_name["baseline"], predictions_by_name["candidate"]
        ),
    }
    destination = Path(args.output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(destination)
    if not report["comparison"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
