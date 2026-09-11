"""Unified AI-assist proposal model and session container.

Every AI tool (whole-scene YOLO, and later SAM click / exemplar) produces
`AssistanceProposal` objects that flow through one staging layer (accept →
annotation / reject). Proposals belong to versioned `AssistanceSession`s so a
new run never overwrites a previous one.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

SourceTool = Literal["yolo_scene", "sam_click", "exemplar", "exemplar_sar", "exemplar_dino", "sam_text"]
ProposalStatus = Literal["pending", "accepted", "rejected"]
SessionStatus = Literal["active", "resolved"]

ASSISTANCE_SCHEMA_VERSION = 1


def _proposal_id() -> str:
    return uuid.uuid4().hex[:12]


def _session_id() -> str:
    return "assession_" + uuid.uuid4().hex[:16]


class AssistanceProposal(BaseModel):
    proposal_id: str = Field(default_factory=_proposal_id)
    session_id: str
    source_tool: SourceTool
    geometry_type: str = "bbox"  # bbox | rotated_bbox | mask
    bbox: list[float] | None = None  # [x0, y0, x1, y1] in working-grid pixels
    rotated_bbox: dict[str, Any] | None = None
    # Pikselowy poligon ramki, ktora jest PROSTOKATEM NA MAPIE. `rotated_bbox` opisuje
    # prostokat w PIKSELACH, wiec na scenach niekonforemnych renderuje sie jako
    # rownoleglobok — inaczej niz ramki rysowane recznie. `None` = brak geo albo
    # nieprzeliczalna geometria; wtedy konsument wraca do `rotated_bbox` jak dotad.
    polygon_scene_px: list[list[float]] | None = None
    mask: dict[str, Any] | None = None  # optional (RLE/polygon); unused in A0
    front_vector_scene_px: list[float] | None = None
    class_id: int | None = None
    class_name: str | None = None
    confidence: float | None = None
    model_name: str | None = None
    model_version: str | None = None
    model_sha256: str | None = None
    working_grid_uid: str | None = None
    source_window: list[int] | None = None  # [x0, y0, w, h]; None = whole scene
    preprocessing_hash: str | None = None
    status: ProposalStatus = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_legacy_prediction(self) -> dict[str, Any]:
        """Project to the shape the existing prediction UI expects (+ OBB fields)."""
        return {
            "id": self.proposal_id,
            "class_id": self.class_id if self.class_id is not None else -1,
            "class_name": self.class_name or "",
            "bbox": self.bbox or [],
            "confidence": self.confidence if self.confidence is not None else 0.0,
            "source_model": self.model_name or "",
            "status": self.status,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at,
            "geometry_type": self.geometry_type,
            "rotated_bbox": self.rotated_bbox,
            "polygon_scene_px": self.polygon_scene_px,
            "front_vector_scene_px": self.front_vector_scene_px,
            "needs_front_direction": self.geometry_type == "rotated_bbox",
        }


class AssistanceSession(BaseModel):
    schema_name: str = "geotile_assistance_session"
    schema_version: int = ASSISTANCE_SCHEMA_VERSION
    session_id: str = Field(default_factory=_session_id)
    project_id: str
    scene_id: str
    source_tool: SourceTool
    status: SessionStatus = "active"
    device: str | None = None
    model_name: str | None = None
    model_sha256: str | None = None
    working_grid_uid: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    proposals: list[AssistanceProposal] = Field(default_factory=list)

    def is_resolved(self) -> bool:
        return all(p.status != "pending" for p in self.proposals)
