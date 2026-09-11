from typing import Literal

from pydantic import BaseModel, Field


class AnnotationPackageSaveRequest(BaseModel):
    output_path: str
    package_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class AnnotationImportPreviewRequest(BaseModel):
    package_paths: list[str] = Field(min_length=1)
    # Heuristic identities are never accepted silently. The caller must opt in to
    # using one unambiguous sampled-signature match for this preview and its apply.
    identity_policy: Literal["exact_only", "allow_probable"] = "exact_only"


class ScenePin(BaseModel):
    source_annotation_id: str
    comment: str | None = None


class SceneReviewRequest(BaseModel):
    review_status: Literal["none", "accepted", "rejected", "needs_fix"]
    review_comment: str | None = None
    # Optional pointers at specific annotations; a verdict never requires them.
    pins: list[ScenePin] = Field(default_factory=list)


class ReviewPackageSaveRequest(BaseModel):
    output_path: str
    # Pusty/None = tryb „Wszystkie recenzje" — paczka obejmuje wszystkie sprawdzone sceny,
    # bez filtrowania po analityku (import i tak dopasowuje sceny po source_scene_uid).
    owner_email: str | None = None
    package_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class ReviewImportRequest(BaseModel):
    package_paths: list[str] = Field(min_length=1)


class AnnotationImportApplyRequest(BaseModel):
    preview_id: str
    create_missing_classes: bool = False
    # Scenes the manager accepted. None applies every safe plan; a scene whose plan
    # would remove a large share of an owner's annotations is only applied when its
    # id is listed here — naming it IS the confirmation.
    accepted_scene_ids: list[str] | None = None
