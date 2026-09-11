"""GSD-normalized tiling for dataset generation (opt-in).

When ``dataset_config.target_gsd_m`` is set, each scene is re-tiled so that every
output tile represents the same ground sample distance — objects then appear at a
consistent pixel scale across scenes of different native GSD. Tiles are derived
FRESH from the source annotations (not the review catalog), so per-tile review
flags do not apply; tiles are marked reviewed so the builder treats them as
candidates. Output tiles stay ``tile_size`` px; the source window is
``window_px = round(tile_size * scene_gsd / target_gsd)`` and is resampled on write.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from models.tiling_config import TileInfo, TilingConfig
from services.attribute_engine.engine import compute_tile_attributes


def gsd_normalized_scene_tiles(
    scene_id: str,
    scene_name: str,
    width: int,
    height: int,
    scene_gsd_m: float,
    target_gsd_m: float,
    tiling_config: TilingConfig,
    source_annotations: list[dict[str, Any]],
    min_box_fraction: float,
) -> tuple[list[TileInfo], dict[str, list[list[float]]], list[dict[str, Any]]]:
    """Return (tiles, tile_annotations, tile_attribute_links) for one scene at target GSD."""
    tile_size = tiling_config.tile_size
    scale = float(scene_gsd_m) / float(target_gsd_m)
    window_px = max(1, round(tile_size * scale))
    buffer_px = max(0, round(tiling_config.buffer * scale))
    stride = max(1, window_px - buffer_px)

    tiles: list[TileInfo] = []
    tile_annotations: dict[str, list[list[float]]] = defaultdict(list)
    tile_links: list[dict[str, Any]] = []

    row_idx = 0
    for y0 in range(0, max(1, height), stride):
        row_idx += 1
        col_idx = 0
        for x0 in range(0, max(1, width), stride):
            col_idx += 1
            filename = f"{col_idx}_{row_idx}_{scene_name}.png"
            tiles.append(TileInfo(
                tile_id=f"gsd:{scene_id}:{x0}:{y0}:{window_px}",
                scene_id=scene_id,
                filename=filename,
                col=col_idx,
                row=row_idx,
                x0=x0,
                y0=y0,
                x1=x0 + window_px,
                y1=y0 + window_px,
                window_px=window_px,
                # Review flags don't apply in GSD mode — mark reviewed so the builder
                # treats every tile as a positive/negative candidate.
                review_status="reviewed",
                reviewed=True,
                exclude_from_dataset=False,
            ))
            tile_ref = {"x0": x0, "y0": y0, "filename": filename, "scene_id": scene_id}
            for annotation in source_annotations:
                # window_px acts as the tile window here; YOLO coords come out
                # normalized to [0,1], valid for the resampled tile_size output.
                attributes = compute_tile_attributes(annotation, tile_ref, window_px, min_box_fraction)
                if not attributes or not attributes.get("exportable_yolo"):
                    continue
                bbox = attributes.get("bbox_yolo_norm") or []
                if len(bbox) == 4 and attributes.get("class_id") is not None:
                    tile_annotations[filename].append([attributes["class_id"], *bbox])
                    tile_links.append(attributes)

    return tiles, tile_annotations, tile_links
