"""YOLO AABB and OBB dataset export."""

from __future__ import annotations

import math
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml


def bbox_to_yolo(bbox: list[float], img_w: int, img_h: int) -> tuple[float, float, float, float]:
    x_min, y_min, x_max, y_max = bbox
    return (
        (x_min + x_max) / 2.0 / img_w,
        (y_min + y_max) / 2.0 / img_h,
        (x_max - x_min) / img_w,
        (y_max - y_min) / img_h,
    )


def write_yolo_label(label_path: str | Path, annotations: list[list[float]], img_w: int, img_h: int) -> None:
    path = Path(label_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for annotation in annotations:
            class_id = int(annotation[0])
            x_center, y_center, width, height = bbox_to_yolo(annotation[1:], img_w, img_h)
            handle.write(
                f"{class_id} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}\n"
            )


def write_yolo_label_normalized(
    label_path: str | Path, annotations: list[list[float]]
) -> None:
    """Write already-normalized YOLO AABB rows verbatim: ``[cls, cx, cy, w, h]`` in [0,1].

    ``tile_annotations`` produced by the build are ALREADY normalized (identical to what
    ``build_dataset`` writes). Feeding them to :func:`write_yolo_label` — which expects a
    PIXEL bbox ``[x0,y0,x1,y1]`` and re-normalizes by width/height — corrupts every box
    (negative/degenerate w,h → zero training signal). Mirror the build writer exactly.
    """
    path = Path(label_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for annotation in annotations:
            class_id = int(annotation[0])
            cx, cy, width, height = annotation[1], annotation[2], annotation[3], annotation[4]
            handle.write(f"{class_id} {cx} {cy} {width} {height}\n")


def write_yolo_obb_label(
    label_path: str | Path,
    links: list[dict[str, Any]],
    img_w: int,
    img_h: int,
) -> None:
    path = Path(label_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for link in links:
            if not link.get("exportable_yolo_obb", True):
                continue
            points = link.get("obb_tile_px") or bbox_polygon(link.get("bbox_tile_px"))
            ordered = order_polygon_clockwise(points)
            if len(ordered) != 4:
                continue
            normalized = [
                value
                for point in ordered
                for value in (
                    min(1.0, max(0.0, float(point[0]) / img_w)),
                    min(1.0, max(0.0, float(point[1]) / img_h)),
                )
            ]
            coordinates = " ".join(f"{value:.6f}" for value in normalized)
            handle.write(f"{int(link['class_id'])} {coordinates}\n")


def generate_data_yaml(
    dataset_dir: str | Path,
    class_names: dict[int, str],
    *,
    images_layout: str = "split_first",
    filename: str = "data.yaml",
    path: str | None = None,
) -> Path:
    dataset_path = Path(dataset_dir)
    prefix = "images/" if images_layout == "format_first" else ""
    suffix = "/images" if images_layout == "split_first" else ""
    # ``path`` domyślnie "." (przenośna paczka eksportu). Dla treningu w miejscu podajemy
    # ABSOLUTNĄ ścieżkę katalogu — ultralytics rozwiązuje względny ``path`` przez swój
    # ``settings.datasets_dir``/CWD, nie względem pliku yaml, więc "." kieruje w złe miejsce.
    data = {
        "path": path if path is not None else ".",
        "train": f"{prefix}train{suffix}",
        "val": f"{prefix}val{suffix}",
        "test": f"{prefix}test{suffix}",
        "names": {key: value for key, value in sorted(class_names.items())},
    }
    yaml_path = dataset_path / filename
    with yaml_path.open("w", encoding="utf-8", newline="\n") as handle:
        yaml.safe_dump(data, handle, default_flow_style=False, sort_keys=False, allow_unicode=True)
    return yaml_path


def export_yolo_dataset(
    tile_annotations: dict[str, list[list[float]]],
    dataset_dir: str | Path,
    tile_size: int,
    class_names: dict[int, str],
    *,
    output_dir: str | Path | None = None,
    tile_links: list[dict[str, Any]] | None = None,
) -> Path:
    source = Path(dataset_dir)
    target = Path(output_dir) if output_dir else source
    target.mkdir(parents=True, exist_ok=True)
    links_by_tile: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in tile_links or []:
        filename = str(link.get("dataset_tile_filename") or "")
        if filename:
            links_by_tile[filename].append(link)

    for split in ("train", "val", "test"):
        source_images = source / split / "images"
        if not source_images.exists():
            continue
        if output_dir:
            images_dir = target / "images" / split
            labels_dir = target / "labels" / split
            obb_labels_dir = target / "labels_obb" / split
            images_dir.mkdir(parents=True, exist_ok=True)
        else:
            images_dir = source_images
            labels_dir = target / split / "labels"
            obb_labels_dir = target / split / "labels_obb"
        labels_dir.mkdir(parents=True, exist_ok=True)
        obb_labels_dir.mkdir(parents=True, exist_ok=True)
        for image_path in sorted(path for path in source_images.iterdir() if path.is_file()):
            if output_dir:
                copy_or_link(image_path, images_dir / image_path.name)
            annotations = tile_annotations.get(image_path.name, [])
            # tile_annotations są JUŻ znormalizowane (jak w build_dataset) — pisz je wprost.
            # Użycie write_yolo_label (oczekuje pikseli) psułoby boxy (ujemne w/h).
            write_yolo_label_normalized(labels_dir / f"{image_path.stem}.txt", annotations)
            write_yolo_obb_label(
                obb_labels_dir / f"{image_path.stem}.txt",
                links_by_tile.get(image_path.name, []),
                tile_size,
                tile_size,
            )

    layout = "format_first" if output_dir else "split_first"
    # Eksport in-place pisze do katalogu runu — zachowaj ABSOLUTNY ``path``, żeby run pozostał
    # trenowalny (ultralytics rozwiązuje względny ``path`` przez swój datasets_dir/CWD, nie
    # względem pliku yaml). Paczka przenośna (output_dir) zachowuje względne ".".
    yaml_path_arg = None if output_dir else target.resolve().as_posix()
    data_yaml = generate_data_yaml(target, class_names, images_layout=layout, path=yaml_path_arg)
    generate_data_yaml(
        target, class_names, images_layout=layout, filename="data_obb.yaml", path=yaml_path_arg
    )
    return data_yaml


def write_obb_labels(
    dataset_dir: str | Path,
    tile_links: list[dict[str, Any]] | None,
    tile_size: int,
) -> None:
    """Write oriented ``labels_obb/`` into an in-place (split-first) run directory.

    Mirrors the OBB half of :func:`export_yolo_dataset`, but touches only ``labels_obb/``
    (leaves AABB ``labels/`` and images untouched). Called at generation time so a
    ``rotated_bbox`` project's run is trainable as OBB without a separate export step. One
    label file is written per image (empty file = background), matching the AABB layout.
    """
    source = Path(dataset_dir)
    links_by_tile: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in tile_links or []:
        filename = str(link.get("dataset_tile_filename") or "")
        if filename:
            links_by_tile[filename].append(link)
    for split in ("train", "val", "test"):
        source_images = source / split / "images"
        if not source_images.exists():
            continue
        obb_labels_dir = source / split / "labels_obb"
        obb_labels_dir.mkdir(parents=True, exist_ok=True)
        for image_path in sorted(path for path in source_images.iterdir() if path.is_file()):
            write_yolo_obb_label(
                obb_labels_dir / f"{image_path.stem}.txt",
                links_by_tile.get(image_path.name, []),
                tile_size,
                tile_size,
            )


def bbox_polygon(bbox: Any) -> list[list[float]]:
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return []
    x_min, y_min, x_max, y_max = [float(value) for value in bbox[:4]]
    return [[x_min, y_min], [x_max, y_min], [x_max, y_max], [x_min, y_max]]


def order_polygon_clockwise(points: Any) -> list[list[float]]:
    if not isinstance(points, list) or len(points) != 4:
        return []
    normalized = [[float(point[0]), float(point[1])] for point in points if len(point) >= 2]
    if len(normalized) != 4:
        return []
    center_x = sum(point[0] for point in normalized) / 4
    center_y = sum(point[1] for point in normalized) / 4
    normalized.sort(key=lambda point: math.atan2(point[1] - center_y, point[0] - center_x))
    start = min(range(4), key=lambda index: normalized[index][0] + normalized[index][1])
    return normalized[start:] + normalized[:start]


def copy_or_link(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    try:
        destination.hardlink_to(source)
    except OSError:
        shutil.copy2(source, destination)
