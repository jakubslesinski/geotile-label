"""Crash-detectable rebuild of JSON Index v2."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from db.storage import project_paths
from services.json_index.locks import project_index_lock, project_rebuild_lock
from services.json_index.project_revision import read_index_state, read_project_revision
from services.json_index.projector import build_project_projection
from services.json_index.schema import (
    ANNOTATION_SUMMARY_SCHEMA,
    BUILDER_VERSION,
    INDEX_SCHEMA_VERSION,
    SCENES_INDEX_SCHEMA,
    JsonIndexPaths,
    index_state_document,
    revision_document,
    utc_now,
)
from services.jobs.store import read_json, write_json_atomic


class IndexChangedDuringBuild(RuntimeError):
    pass


def _source_revision(value: dict[str, Any]) -> int:
    raw = value.get("source_revision")
    return -1 if raw is None else int(raw)


def _set_failed_state(
    root: Path,
    project_id: str,
    build_token: str,
    source_revision: int,
    exc: BaseException,
) -> None:
    with project_index_lock(root):
        state = read_index_state(root)
        if state.get("build_token") != build_token:
            return
        write_json_atomic(
            JsonIndexPaths(root).state,
            index_state_document(
                project_id,
                source_revision,
                status="dirty",
                dirty_at=state.get("dirty_at") or utc_now(),
                dirty_generation=max(1, int(state.get("dirty_generation") or 0)),
                pending_mutations=state.get("pending_mutations") or [],
                last_error=f"{type(exc).__name__}: {exc}",
            ),
        )


def _ready_scenes_index(root: Path, project_id: str) -> dict[str, Any] | None:
    """Return a fully current published index, used to collapse rebuild stampedes."""

    paths = JsonIndexPaths(root)
    revision = read_project_revision(root, project_id)
    state = read_index_state(root)
    source_revision = int(revision["revision"])
    if not (
        state.get("status") == "ready"
        and int(state.get("schema_version") or 0) == INDEX_SCHEMA_VERSION
        and str(state.get("builder_version") or "") == BUILDER_VERSION
        and _source_revision(state) == source_revision
    ):
        return None
    scenes = read_json(paths.scenes, default={}) or {}
    annotations = read_json(paths.annotations, default={}) or {}
    for value, schema_name in (
        (scenes, SCENES_INDEX_SCHEMA),
        (annotations, ANNOTATION_SUMMARY_SCHEMA),
    ):
        if not (
            value.get("schema_name") == schema_name
            and int(value.get("schema_version") or 0) == INDEX_SCHEMA_VERSION
            and str(value.get("project_id") or "") == project_id
            and _source_revision(value) == source_revision
        ):
            return None
    return scenes


def _rebuild_project_indexes_locked(
    project_id: str,
    root: Path,
    *,
    max_attempts: int,
) -> dict[str, Any]:
    paths = JsonIndexPaths(root)
    last_error: BaseException | None = None

    for _attempt in range(max(1, int(max_attempts))):
        build_token = uuid.uuid4().hex
        with project_index_lock(root):
            revision = read_project_revision(root, project_id)
            source_revision = int(revision["revision"])
            if not paths.revision.is_file():
                write_json_atomic(paths.revision, revision_document(project_id, source_revision))
            previous = read_index_state(root)
            write_json_atomic(
                paths.state,
                index_state_document(
                    project_id,
                    source_revision,
                    status="building",
                    build_token=build_token,
                    build_started_at=utc_now(),
                    dirty_at=previous.get("dirty_at"),
                    dirty_generation=int(previous.get("dirty_generation") or 0),
                    pending_mutations=previous.get("pending_mutations") or [],
                ),
            )

        try:
            projection = build_project_projection(project_id, source_revision)
            with project_index_lock(root):
                current_revision = read_project_revision(root, project_id)
                current_state = read_index_state(root)
                if (
                    int(current_revision["revision"]) != source_revision
                    or current_state.get("build_token") != build_token
                    or current_state.get("status") != "building"
                ):
                    last_error = IndexChangedDuringBuild(
                        f"Project {project_id} changed while JSON Index v2 was rebuilding"
                    )
                    continue

                for scene_id, summary in projection.scene_summaries.items():
                    write_json_atomic(paths.scene_summary(scene_id), summary)
                write_json_atomic(paths.scenes, projection.scenes_index)
                write_json_atomic(paths.annotations, projection.annotation_summary)
                ready_at = utc_now()
                write_json_atomic(
                    paths.state,
                    index_state_document(
                        project_id,
                        source_revision,
                        status="ready",
                        built_at=ready_at,
                        dirty_at=None,
                        dirty_generation=int(current_state.get("dirty_generation") or 0),
                        pending_mutations=[],
                        builder_version=BUILDER_VERSION,
                    ),
                )
                return projection.scenes_index
        except BaseException as exc:
            _set_failed_state(root, project_id, build_token, source_revision, exc)
            raise

    if last_error is None:
        last_error = IndexChangedDuringBuild(f"Could not stabilize JSON Index v2 for {project_id}")
    raise last_error


def rebuild_project_indexes(
    project_id: str,
    *,
    max_attempts: int = 3,
    force: bool = False,
) -> dict[str, Any]:
    root = project_paths(project_id, create=True).root
    with project_rebuild_lock(root):
        if not force:
            current = _ready_scenes_index(root, project_id)
            if current is not None:
                return current
        return _rebuild_project_indexes_locked(
            project_id,
            root,
            max_attempts=max_attempts,
        )
