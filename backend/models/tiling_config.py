from typing import Literal

from pydantic import BaseModel, model_validator


ReviewStatus = Literal["unreviewed", "reviewed"]


class TilingConfig(BaseModel):
    tile_size: int = 640
    buffer: int = 0

    @property
    def stride(self) -> int:
        return self.tile_size - self.buffer


class TilingPreview(BaseModel):
    num_cols: int
    num_rows: int
    total_tiles: int
    tile_size: int
    buffer: int
    stride: int
    grid_lines_x: list[int]
    grid_lines_y: list[int]
    tile_rects: list[list[int]]  # [[x0, y0, x1, y1], ...]


class TileInfo(BaseModel):
    tile_id: str | None = None
    grid_cell_id: str | None = None
    scene_id: str | None = None
    filename: str
    col: int
    row: int
    x0: int
    y0: int
    x1: int | None = None
    y1: int | None = None
    # Source-pixel window size for GSD-normalized dataset tiles (resampled to
    # tile_size on write). None = native (window == tile_size).
    window_px: int | None = None
    geometry_px: list[list[float]] | None = None
    geometry_scene_px: list[list[float]] | None = None
    geometry_wgs84: list[list[float]] | None = None
    review_status: ReviewStatus = "unreviewed"
    exclude_from_dataset: bool = False
    reviewed_at: str | None = None
    reviewed_by: str | None = None
    reviewed: bool = False
    excluded: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_review_fields(cls, data):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if normalized.get("grid_cell_id") is None and normalized.get("tile_id") is not None:
            normalized["grid_cell_id"] = normalized["tile_id"]
        if normalized.get("tile_id") is None and normalized.get("grid_cell_id") is not None:
            normalized["tile_id"] = normalized["grid_cell_id"]
        if "review_status" not in normalized and normalized.get("reviewed"):
            normalized["review_status"] = "reviewed"
        if "exclude_from_dataset" not in normalized and normalized.get("excluded"):
            normalized["exclude_from_dataset"] = True
        return normalized

    @model_validator(mode="after")
    def sync_legacy_review_fields(self):
        if self.grid_cell_id is None:
            self.grid_cell_id = self.tile_id
        if self.tile_id is None:
            self.tile_id = self.grid_cell_id
        self.reviewed = self.review_status == "reviewed"
        self.excluded = self.exclude_from_dataset
        return self
