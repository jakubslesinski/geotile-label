"""Tiling engine — adapted from podzial_obrazow/prep_data.py + geotiff_tiler.py."""

from pathlib import Path
from typing import Generator

import cv2
import numpy as np

from models.tiling_config import TilingConfig, TilingPreview, TileInfo
from services.scene_loader import load_scene_array


def compute_grid(width: int, height: int, config: TilingConfig) -> TilingPreview:
    """Compute tile grid info without cutting.

    Matches prep_data.py logic:
      for i in range(0, height, stride):
          for j in range(0, width, stride):
              crop = img[i:i+tile_size, j:j+tile_size]  # zero-padded at edges
    """
    stride = config.stride
    ts = config.tile_size

    # Start positions — identical to prep_data.py loop
    starts_x = list(range(0, width, stride))
    starts_y = list(range(0, height, stride))

    cols = len(starts_x)
    rows = len(starts_y)

    # Tile rectangles — each tile is tile_size x tile_size from its start
    # Edge tiles extend beyond image bounds (zero-padded), matching prep_data.py
    tile_rects = []
    for i in starts_y:
        for j in starts_x:
            tile_rects.append([j, i, j + ts, i + ts])

    # Grid lines for simple non-overlapping view (tile start positions + final edge)
    # When buffer > 0, tiles overlap so these lines show stride boundaries
    grid_lines_x = starts_x + [starts_x[-1] + ts] if starts_x else [0]
    grid_lines_y = starts_y + [starts_y[-1] + ts] if starts_y else [0]

    return TilingPreview(
        num_cols=cols,
        num_rows=rows,
        total_tiles=cols * rows,
        tile_size=ts,
        buffer=config.buffer,
        stride=stride,
        grid_lines_x=grid_lines_x,
        grid_lines_y=grid_lines_y,
        tile_rects=tile_rects,
    )


def execute_tiling(
    scene_path: str | Path,
    output_dir: str | Path,
    config: TilingConfig,
    project_name: str = "scene",
) -> Generator[dict, None, list[TileInfo]]:
    """
    Cut scene into tiles. Yields progress dicts, returns tile list.

    Adapted from prep_data.py L65-86:
      stride = tile_size - buffer
      for i in range(0, height, stride):
          for j in range(0, width, stride):
              crop = scene[i:i+tile_size, j:j+tile_size]
              zero-pad if smaller
    """
    scene_path = Path(scene_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    img = load_scene_array(scene_path)
    height, width = img.shape[:2]
    stride = config.stride
    ts = config.tile_size

    preview = compute_grid(width, height, config)
    total = preview.total_tiles
    tiles: list[TileInfo] = []
    done = 0

    row_idx = 0
    for i in range(0, height, stride):
        row_idx += 1
        col_idx = 0
        for j in range(0, width, stride):
            col_idx += 1

            crop = img[i : i + ts, j : j + ts]

            # Zero-pad edges (from prep_data.py)
            if crop.shape[0] < ts or crop.shape[1] < ts:
                padded = np.zeros((ts, ts, 3), dtype=np.uint8)
                padded[: crop.shape[0], : crop.shape[1]] = crop
                crop = padded

            filename = f"{col_idx}_{row_idx}_{project_name}.png"
            cv2.imwrite(
                str(output_dir / filename),
                cv2.cvtColor(crop, cv2.COLOR_RGB2BGR),
            )

            tile = TileInfo(
                filename=filename,
                col=col_idx,
                row=row_idx,
                x0=j,
                y0=i,
            )
            tiles.append(tile)
            done += 1

            yield {"done": done, "total": total, "filename": filename}

    return tiles
