"""Scene model — one per image file in a project."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from models.project import SceneInfo

ReviewStatus = Literal["none", "accepted", "rejected", "needs_fix"]


class Scene(BaseModel):
    id: str
    filename: str
    display_name: str | None = None
    source_id: str | None = None
    package_id: str | None = None
    provider_scene_id: str | None = None
    product_type: str | None = None
    raster_format: str | None = None
    raster_part_count: int | None = None
    gsd_m: float | None = None
    raster_ref: dict | None = None
    raster_kind: str | None = None
    working_variant_id: str | None = None
    working_variant_fingerprint: str | None = None
    working_grid_uid: str | None = None
    working_asset_locked: bool = False
    preparation_status: str = "ready"
    # Status piramidy wyświetlania (display overviews): ready (zbudowana po stronie projektu),
    # native (źródło ma własne overviews / nie dotyczy), pending (w kolejce/nie zbudowana),
    # error. "building" (aktualnie liczona) wyprowadza frontend z bieżącego joba importu.
    overview_status: str | None = None
    overview_type: str | None = None
    overview_factors: list[int] = Field(default_factory=list)
    overview_fingerprint: str | None = None
    overview_sidecar_fingerprint: str | None = None
    overview_changed_at: datetime | None = None
    scene_info: SceneInfo | None = None
    scene_info_version: str | None = None
    manifest_schema_version: int | None = None
    source_scene_uid: str | None = None
    source_identity_status: str | None = None
    modality: str | None = None
    georeferencing: str | None = None
    sensor: str | None = None
    provider: str | None = None
    acquisition_datetime_utc: str | None = None
    metadata_status: str | None = None
    profile_warning_count: int = 0
    status: str = "pending"  # pending | tiled
    # --- Review verdict (team workflow) ---------------------------------------
    # Deliberately scene-level: a manager reviews the WORK on a scene, not each
    # object. `status` above is the tiling state and is not reused for this.
    review_status: ReviewStatus = "none"
    review_comment: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    # Bumped every time a verdict is set; an incoming review from an older round is
    # ignored, so a stale package can never overwrite a newer verdict.
    review_round: int = 0
    # Optional pointers at specific annotations ("this one is sloppy"). Rare — a
    # verdict never requires them. Stored on the scene so importing a review never
    # touches annotation geometry.
    review_pins: list[dict] = Field(default_factory=list)
    annotation_count: int = 0
    tile_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
