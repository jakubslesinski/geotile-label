"""Project revision and durable dirty-marker protocol."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from services.json_index.locks import project_index_lock
from services.json_index.schema import (
    INDEX_STATE_SCHEMA,
    PROJECT_REVISION_SCHEMA,
    JsonIndexPaths,
    index_state_document,
    revision_document,
    utc_now,
)
from services.jobs.store import read_json, write_json_atomic


def read_project_revision(project_root: Path, project_id: str) -> dict[str, Any]:
    paths = JsonIndexPaths(project_root)
    value = read_json(paths.revision, default={}) or {}
    if value.get("schema_name") != PROJECT_REVISION_SCHEMA:
        return revision_document(project_id, 0, None)
    result = dict(value)
    result["revision"] = max(0, int(result.get("revision") or 0))
    return result


def read_index_state(project_root: Path) -> dict[str, Any]:
    value = read_json(JsonIndexPaths(project_root).state, default={}) or {}
    return dict(value) if value.get("schema_name") == INDEX_STATE_SCHEMA else {}


def _write_revision_locked(
    project_root: Path,
    project_id: str,
    revision: int,
    last_mutation: str | None,
) -> dict[str, Any]:
    value = revision_document(project_id, revision, last_mutation)
    write_json_atomic(JsonIndexPaths(project_root).revision, value)
    return value


def begin_project_mutation(
    project_root: Path,
    project_id: str,
    mutation: str,
) -> str:
    """Persist ``dirty`` before a source JSON mutation and return its token."""

    token = uuid.uuid4().hex
    paths = JsonIndexPaths(project_root)
    with project_index_lock(project_root):
        revision = read_project_revision(project_root, project_id)
        if not paths.revision.is_file():
            _write_revision_locked(project_root, project_id, int(revision["revision"]), None)
        state = read_index_state(project_root)
        pending = [str(item) for item in state.get("pending_mutations") or []]
        pending.append(token)
        dirty_generation = int(state.get("dirty_generation") or 0) + 1
        write_json_atomic(
            paths.state,
            index_state_document(
                project_id,
                int(revision["revision"]),
                status="dirty",
                dirty_at=state.get("dirty_at") or utc_now(),
                dirty_generation=dirty_generation,
                pending_mutations=pending,
                last_mutation=mutation,
            ),
        )
    return token


def complete_project_mutation(
    project_root: Path,
    project_id: str,
    mutation: str,
    token: str,
) -> int:
    """Advance the source revision after an atomic source write; keep indexes dirty."""

    paths = JsonIndexPaths(project_root)
    with project_index_lock(project_root):
        current = read_project_revision(project_root, project_id)
        next_revision = int(current["revision"]) + 1
        _write_revision_locked(project_root, project_id, next_revision, mutation)
        state = read_index_state(project_root)
        pending = [str(item) for item in state.get("pending_mutations") or [] if str(item) != token]
        write_json_atomic(
            paths.state,
            index_state_document(
                project_id,
                next_revision,
                status="dirty",
                dirty_at=state.get("dirty_at") or utc_now(),
                dirty_generation=max(1, int(state.get("dirty_generation") or 0)),
                pending_mutations=pending,
                last_mutation=mutation,
            ),
        )
        return next_revision


def record_external_mutation(project_root: Path, project_id: str, mutation: str) -> int:
    """Mark non-storage mutations such as a scene-directory removal."""

    token = begin_project_mutation(project_root, project_id, mutation)
    return complete_project_mutation(project_root, project_id, mutation, token)
