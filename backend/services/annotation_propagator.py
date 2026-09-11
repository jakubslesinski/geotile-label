"""Propagate scene-level annotations to individual tiles."""

from models.annotation import Annotation
from models.tiling_config import TilingConfig, TileInfo
from services.attribute_engine import compute_tile_attributes


def propagate_annotations(
    scene_annotations: list[Annotation],
    tiles: list[TileInfo],
    config: TilingConfig,
    min_box_fraction: float = 0.3,
) -> dict[str, list[list[float]]]:
    """
    Clip scene annotations onto each tile.

    Returns: {tile_filename: [[class_id, cx0, cy0, cx1, cy1], ...]}

    Adapted from label-tiles sliding_window.py translate_and_clip_bbox.
    """
    annotations, _attributes = propagate_annotations_with_attributes(
        scene_annotations,
        tiles,
        config,
        min_box_fraction,
    )
    return annotations


def propagate_annotations_with_attributes(
    scene_annotations: list[Annotation],
    tiles: list[TileInfo],
    config: TilingConfig,
    min_box_fraction: float = 0.3,
) -> tuple[dict[str, list[list[float]]], dict[str, list[dict]]]:
    ts = config.tile_size
    result: dict[str, list[list[float]]] = {}
    attribute_result: dict[str, list[dict]] = {}

    for tile in tiles:
        tile_anns: list[list[float]] = []
        tile_attributes: list[dict] = []

        for ann in scene_annotations:
            if ann.is_negative:
                continue
            attributes = compute_tile_attributes(ann, tile, ts, min_box_fraction)
            if attributes is None:
                continue
            tile_attributes.append(attributes)
            if attributes["exportable_yolo"]:
                tile_anns.append([ann.class_id, *attributes["bbox_yolo_norm"]])

        result[tile.filename] = tile_anns
        attribute_result[tile.filename] = tile_attributes

    return result, attribute_result
