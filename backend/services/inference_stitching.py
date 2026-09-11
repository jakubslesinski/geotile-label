"""Native AABB/OBB stitching primitives for whole-scene inference.

P1.2 deliberately exposes only hard NMS. Soft-NMS/WBF/edge-aware variants need
full-scene quality benchmarks and remain separate experimental roadmap items.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def _numpy_nms_classwise(
    boxes: np.ndarray,
    scores: np.ndarray,
    classes: np.ndarray,
    iou_threshold: float,
) -> np.ndarray:
    """Compatibility fallback used only when the native torch operator is unavailable."""

    keep: list[int] = []
    for class_id in np.unique(classes):
        class_indices = np.where(classes == class_id)[0]
        order = class_indices[np.argsort(-scores[class_indices], kind="stable")]
        while order.size:
            current = int(order[0])
            keep.append(current)
            if order.size == 1:
                break
            rest = order[1:]
            xx1 = np.maximum(boxes[current, 0], boxes[rest, 0])
            yy1 = np.maximum(boxes[current, 1], boxes[rest, 1])
            xx2 = np.minimum(boxes[current, 2], boxes[rest, 2])
            yy2 = np.minimum(boxes[current, 3], boxes[rest, 3])
            intersection = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
            area_current = max(
                0.0,
                float(boxes[current, 2] - boxes[current, 0])
                * float(boxes[current, 3] - boxes[current, 1]),
            )
            area_rest = np.maximum(0.0, boxes[rest, 2] - boxes[rest, 0]) * np.maximum(
                0.0, boxes[rest, 3] - boxes[rest, 1]
            )
            union = area_current + area_rest - intersection + 1e-9
            order = rest[(intersection / union) <= iou_threshold]
    return np.asarray(keep, dtype=np.int64)


def nms_aabb_classwise(
    detections: list[dict[str, Any]],
    iou_threshold: float,
) -> tuple[list[dict[str, Any]], str]:
    if not detections:
        return [], "torchvision_batched_nms"
    boxes = np.asarray([item["bbox"] for item in detections], dtype=np.float32)
    scores = np.asarray([item["confidence"] for item in detections], dtype=np.float32)
    classes = np.asarray([item["class_id"] for item in detections], dtype=np.int64)
    try:
        import torch
        from torchvision.ops import batched_nms

        keep = batched_nms(
            torch.from_numpy(boxes),
            torch.from_numpy(scores),
            torch.from_numpy(classes),
            float(iou_threshold),
        ).cpu().numpy()
        backend = "torchvision_batched_nms"
    except Exception:
        keep = _numpy_nms_classwise(boxes, scores, classes, iou_threshold)
        backend = "numpy_fallback"
    return [detections[int(index)] for index in keep], backend


def nms_obb_classwise(
    detections: list[dict[str, Any]],
    iou_threshold: float,
) -> tuple[list[dict[str, Any]], str]:
    """Class-wise native Ultralytics probabilistic-IoU NMS for xywhr boxes."""

    if not detections:
        return [], "ultralytics_fast_nms_probiou"
    try:
        import torch
        from ultralytics.utils.metrics import batch_probiou
        from ultralytics.utils.nms import TorchNMS

        boxes = torch.as_tensor(
            [item["xywhr"] for item in detections], dtype=torch.float32
        )
        scores = torch.as_tensor(
            [item["confidence"] for item in detections], dtype=torch.float32
        )
        classes = np.asarray([item["class_id"] for item in detections], dtype=np.int64)
        kept: list[int] = []
        for class_id in np.unique(classes):
            indices_np = np.where(classes == class_id)[0]
            indices = torch.as_tensor(indices_np, dtype=torch.long)
            local = TorchNMS.fast_nms(
                boxes[indices],
                scores[indices],
                float(iou_threshold),
                iou_func=batch_probiou,
            )
            kept.extend(int(indices_np[int(index)]) for index in local.cpu().tolist())
        kept.sort(key=lambda index: (-float(scores[index]), index))
        return [detections[index] for index in kept], "ultralytics_fast_nms_probiou"
    except Exception as exc:
        # There is no semantically safe AABB fallback for OBB. Failing explicitly
        # avoids silently degrading oriented geometry.
        raise RuntimeError(f"Native OBB NMS is unavailable: {exc}") from exc


def merge_detections(
    detections: list[dict[str, Any]],
    *,
    geometry_type: str,
    method: str,
    iou_threshold: float,
) -> tuple[list[dict[str, Any]], str]:
    if method != "nms":
        raise ValueError(f"Unsupported merge method: {method}")
    if geometry_type == "rotated_bbox":
        return nms_obb_classwise(detections, iou_threshold)
    return nms_aabb_classwise(detections, iou_threshold)


def clip_polygon_to_rect(
    points: np.ndarray,
    width: float,
    height: float,
) -> np.ndarray:
    """Sutherland-Hodgman clipping without degrading an OBB to its enclosing AABB."""

    polygon = [(float(x), float(y)) for x, y in np.asarray(points).reshape(-1, 2)]

    def clip(axis: int, boundary: float, keep_greater: bool) -> None:
        nonlocal polygon
        if not polygon:
            return
        output: list[tuple[float, float]] = []
        previous = polygon[-1]

        def inside(point: tuple[float, float]) -> bool:
            return point[axis] >= boundary if keep_greater else point[axis] <= boundary

        for current in polygon:
            current_inside = inside(current)
            previous_inside = inside(previous)
            if current_inside != previous_inside:
                delta = current[axis] - previous[axis]
                ratio = 0.0 if abs(delta) < 1e-12 else (boundary - previous[axis]) / delta
                other = previous[1 - axis] + ratio * (current[1 - axis] - previous[1 - axis])
                intersection = (boundary, other) if axis == 0 else (other, boundary)
                output.append(intersection)
            if current_inside:
                output.append(current)
            previous = current
        polygon = output

    clip(0, 0.0, True)
    clip(0, float(width), False)
    clip(1, 0.0, True)
    clip(1, float(height), False)
    return np.asarray(polygon, dtype=np.float32).reshape(-1, 2)


def polygon_to_rotated_bbox(points: np.ndarray) -> tuple[dict[str, float], list[float]] | None:
    points = np.asarray(points, dtype=np.float32).reshape(-1, 2)
    if len(points) < 3:
        return None
    import cv2

    (center_x, center_y), (box_width, box_height), angle_deg = cv2.minAreaRect(points)
    if box_width <= 1e-6 or box_height <= 1e-6:
        return None
    if box_width < box_height:
        box_width, box_height = box_height, box_width
        angle_deg += 90.0
    angle_deg %= 180.0
    rotated = {
        "cx": float(center_x),
        "cy": float(center_y),
        "width": float(box_width),
        "height": float(box_height),
        "angle_deg": float(angle_deg),
    }
    bbox = [
        float(np.min(points[:, 0])),
        float(np.min(points[:, 1])),
        float(np.max(points[:, 0])),
        float(np.max(points[:, 1])),
    ]
    return rotated, bbox


def rotated_bbox_to_xywhr(rotated_bbox: dict[str, float]) -> list[float]:
    return [
        float(rotated_bbox["cx"]),
        float(rotated_bbox["cy"]),
        float(rotated_bbox["width"]),
        float(rotated_bbox["height"]),
        math.radians(float(rotated_bbox["angle_deg"])),
    ]
