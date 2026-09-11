"""COCO export with polygon/rotated-box preservation."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from services.export_yolo import bbox_polygon, copy_or_link


def export_coco_dataset(
    tile_annotations: dict[str, list[list[float]]],
    dataset_dir: str | Path,
    tile_size: int,
    class_names: dict[int, str],
    splits: dict[str, list[str]] | None = None,
    *,
    output_dir: str | Path | None = None,
    tile_links: list[dict[str, Any]] | None = None,
) -> Path:
    source = Path(dataset_dir)
    target = Path(output_dir) if output_dir else source
    target.mkdir(parents=True, exist_ok=True)
    categories = [{"id": class_id, "name": name} for class_id, name in sorted(class_names.items())]
    links_by_tile: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in tile_links or []:
        filename = str(link.get("dataset_tile_filename") or "")
        if filename:
            links_by_tile[filename].append(link)

    for split in ("train", "val", "test"):
        source_images = source / split / "images"
        if not source_images.exists():
            continue
        output_images = target / "images" / split if output_dir else source_images
        output_images.mkdir(parents=True, exist_ok=True)
        images: list[dict[str, Any]] = []
        annotations: list[dict[str, Any]] = []
        annotation_id = 1
        image_paths = sorted(path for path in source_images.iterdir() if path.is_file())
        for image_id, image_path in enumerate(image_paths, start=1):
            if output_dir:
                copy_or_link(image_path, output_images / image_path.name)
            images.append({
                "id": image_id,
                "file_name": image_path.name,
                "width": tile_size,
                "height": tile_size,
            })
            links = links_by_tile.get(image_path.name, [])
            if links:
                for link in links:
                    bbox = [float(value) for value in (link.get("bbox_coco_xywh") or [])[:4]]
                    if len(bbox) != 4 or bbox[2] <= 0 or bbox[3] <= 0:
                        continue
                    polygon = link.get("geometry_tile_px") or link.get("obb_tile_px") or bbox_polygon([
                        bbox[0], bbox[1], bbox[0] + bbox[2], bbox[1] + bbox[3]
                    ])
                    segmentation = [[coordinate for point in polygon for coordinate in point[:2]]]
                    annotations.append({
                        "id": annotation_id,
                        "image_id": image_id,
                        "category_id": int(link["class_id"]),
                        "bbox": bbox,
                        "area": polygon_area(polygon),
                        "segmentation": segmentation,
                        "iscrowd": 0,
                        "source_annotation_id": link.get("source_annotation_id"),
                        "geometry_type": link.get("geometry_type", "bbox"),
                        "orientation_angle_deg": link.get("orientation_angle_deg"),
                        "front_vector_scene_px": link.get("front_vector_scene_px"),
                    })
                    annotation_id += 1
            else:
                for item in tile_annotations.get(image_path.name, []):
                    class_id = int(item[0])
                    x_min, y_min, x_max, y_max = map(float, item[1:5])
                    width, height = x_max - x_min, y_max - y_min
                    annotations.append({
                        "id": annotation_id,
                        "image_id": image_id,
                        "category_id": class_id,
                        "bbox": [x_min, y_min, width, height],
                        "area": width * height,
                        "segmentation": [[value for point in bbox_polygon(item[1:5]) for value in point]],
                        "iscrowd": 0,
                        "geometry_type": "bbox",
                    })
                    annotation_id += 1
        payload = {
            "info": {"description": f"GeoTile Label dataset - {split}"},
            "images": images,
            "annotations": annotations,
            "categories": categories,
        }
        output_path = (
            target / "annotations" / f"instances_{split}.json"
            if output_dir
            else target / split / "annotations.json"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def polygon_area(points: list[list[float]]) -> float:
    if len(points) < 3:
        return 0.0
    return abs(sum(
        float(point[0]) * float(points[(index + 1) % len(points)][1])
        - float(points[(index + 1) % len(points)][0]) * float(point[1])
        for index, point in enumerate(points)
    )) / 2.0
