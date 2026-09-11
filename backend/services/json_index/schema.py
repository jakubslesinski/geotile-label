"""Schemas and paths for rebuildable project indexes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

INDEX_SCHEMA_VERSION = 2
REVISION_SCHEMA_VERSION = 1
BUILDER_VERSION = "3"

PROJECT_REVISION_SCHEMA = "geotile_project_revision"
INDEX_STATE_SCHEMA = "geotile_json_index_state"
SCENES_INDEX_SCHEMA = "geotile_scenes_index"
ANNOTATION_SUMMARY_SCHEMA = "geotile_project_annotation_summary"
SCENE_SUMMARY_SCHEMA = "geotile_scene_summary"

_TRUE_VALUES = {"1", "true", "yes", "on"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_index_enabled() -> bool:
    return str(os.environ.get("GEOTILE_JSON_INDEX_V2") or "").strip().lower() in _TRUE_VALUES


def json_index_diagnostic_enabled() -> bool:
    return (
        str(os.environ.get("GEOTILE_JSON_INDEX_V2_DIAGNOSTIC") or "").strip().lower()
        in _TRUE_VALUES
    )


def json_index_delta_enabled() -> bool:
    """Enable P2.2 single-scene deltas whenever v2 is active, unless disabled.

    The separate override is an operational kill switch: ``0`` forces the P2.1
    dirty/rebuild path without disabling indexed reads or deleting artifacts.
    """

    if not json_index_enabled():
        return False
    raw = os.environ.get("GEOTILE_JSON_INDEX_V2_DELTA")
    if raw is None:
        return True
    return str(raw).strip().lower() in _TRUE_VALUES


def json_index_tracking_required(project_root: Path) -> bool:
    """Keep an adopted index invalidatable even while v2 reads are disabled.

    A project that has never enabled JSON Index v2 remains a zero-write legacy
    project. Once revision/state artifacts exist, source mutations must keep
    advancing the revision so disabling and later re-enabling the read path
    cannot expose a stale materialized view.
    """

    if json_index_enabled():
        return True
    paths = JsonIndexPaths(project_root)
    return paths.revision.is_file() or paths.state.is_file()


@dataclass(frozen=True)
class JsonIndexPaths:
    project_root: Path

    @property
    def revision(self) -> Path:
        return self.project_root / "project_revision.json"

    @property
    def indexes(self) -> Path:
        return self.project_root / "indexes"

    @property
    def state(self) -> Path:
        return self.indexes / "index_state.json"

    @property
    def scenes(self) -> Path:
        return self.indexes / "scenes_index_v2.json"

    @property
    def annotations(self) -> Path:
        return self.indexes / "annotation_summary_v2.json"

    @property
    def lock(self) -> Path:
        return self.indexes / ".index.lock"

    def scene_summary(self, scene_id: str) -> Path:
        return self.project_root / "scenes" / scene_id / "summary.json"


def revision_document(project_id: str, revision: int = 0, last_mutation: str | None = None) -> dict:
    return {
        "schema_name": PROJECT_REVISION_SCHEMA,
        "schema_version": REVISION_SCHEMA_VERSION,
        "project_id": project_id,
        "revision": max(0, int(revision)),
        "updated_at": utc_now(),
        "last_mutation": last_mutation,
    }


def index_state_document(
    project_id: str,
    source_revision: int,
    *,
    status: str,
    **values,
) -> dict:
    return {
        "schema_name": INDEX_STATE_SCHEMA,
        "schema_version": INDEX_SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION,
        "project_id": project_id,
        "source_revision": max(0, int(source_revision)),
        "status": status,
        "updated_at": utc_now(),
        **values,
    }
