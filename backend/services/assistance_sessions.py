"""On-disk store for AI-assist sessions.

Layout (per scene):

    scenes/<scene_id>/assistance/
    ├── active.json                 # {"active_session_id": "..."}
    └── sessions/<session_id>.json  # full AssistanceSession

A new run creates a new session file — it never overwrites a previous one.
Fully-resolved sessions beyond the newest ``MAX_SESSIONS_PER_SCENE`` are
garbage-collected so the folder does not grow without bound.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.storage import scene_dir
from models.assistance import AssistanceProposal, AssistanceSession

MAX_SESSIONS_PER_SCENE = 20
_SHA_CHUNK = 4 * 1024 * 1024
_MODEL_SHA_CACHE: dict[tuple[str, int, int], str] = {}


def _assistance_dir(project_id: str, scene_id: str) -> Path:
    path = scene_dir(project_id, scene_id) / "assistance"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sessions_dir(project_id: str, scene_id: str) -> Path:
    path = _assistance_dir(project_id, scene_id) / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session_path(project_id: str, scene_id: str, session_id: str) -> Path:
    return _sessions_dir(project_id, scene_id) / f"{session_id}.json"


def _active_path(project_id: str, scene_id: str) -> Path:
    return _assistance_dir(project_id, scene_id) / "active.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def model_sha256(model_path: str | Path | None) -> str | None:
    if not model_path:
        return None
    path = Path(model_path)
    if not path.is_file():
        return None
    resolved = path.resolve()
    stat = resolved.stat()
    cache_key = (str(resolved), stat.st_size, stat.st_mtime_ns)
    cached = _MODEL_SHA_CACHE.get(cache_key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_SHA_CHUNK), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    for key in [key for key in _MODEL_SHA_CACHE if key[0] == str(resolved) and key != cache_key]:
        _MODEL_SHA_CACHE.pop(key, None)
    _MODEL_SHA_CACHE[cache_key] = value
    return value


def preprocessing_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def write_session(project_id: str, scene_id: str, session: AssistanceSession) -> None:
    session.updated_at = _now()
    path = _session_path(project_id, scene_id, session.session_id)
    path.write_text(session.model_dump_json(indent=2), encoding="utf-8")


def load_session(project_id: str, scene_id: str, session_id: str) -> AssistanceSession | None:
    path = _session_path(project_id, scene_id, session_id)
    if not path.is_file():
        return None
    try:
        return AssistanceSession(**json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, ValueError, OSError):
        return None


def set_active_session(project_id: str, scene_id: str, session_id: str) -> None:
    _active_path(project_id, scene_id).write_text(
        json.dumps({"active_session_id": session_id}), encoding="utf-8"
    )


def get_active_session(project_id: str, scene_id: str) -> AssistanceSession | None:
    active = _active_path(project_id, scene_id)
    if not active.is_file():
        return None
    try:
        session_id = json.loads(active.read_text(encoding="utf-8")).get("active_session_id")
    except (json.JSONDecodeError, OSError):
        return None
    return load_session(project_id, scene_id, session_id) if session_id else None


def list_session_files(project_id: str, scene_id: str) -> list[Path]:
    sessions = _sessions_dir(project_id, scene_id)
    return sorted(sessions.glob("*.json"), key=lambda p: p.stat().st_mtime)


def create_session(
    project_id: str,
    scene_id: str,
    source_tool: str,
    proposals: list[AssistanceProposal],
    *,
    device: str | None = None,
    model_name: str | None = None,
    model_sha: str | None = None,
    working_grid_uid: str | None = None,
    params: dict[str, Any] | None = None,
) -> AssistanceSession:
    """Persist a new session (never overwrites), mark it active, GC old ones."""
    session = AssistanceSession(
        project_id=project_id,
        scene_id=scene_id,
        source_tool=source_tool,
        device=device,
        model_name=model_name,
        model_sha256=model_sha,
        working_grid_uid=working_grid_uid,
        params=params or {},
        proposals=proposals,
    )
    for proposal in session.proposals:
        proposal.session_id = session.session_id
    write_session(project_id, scene_id, session)
    set_active_session(project_id, scene_id, session.session_id)
    gc_sessions(project_id, scene_id)
    return session


def gc_sessions(project_id: str, scene_id: str, keep: int = MAX_SESSIONS_PER_SCENE) -> int:
    """Delete the oldest fully-resolved sessions beyond ``keep`` (newest kept).

    The active session is never deleted regardless of age.
    """
    files = list_session_files(project_id, scene_id)
    if len(files) <= keep:
        return 0
    active = get_active_session(project_id, scene_id)
    active_id = active.session_id if active else None
    removed = 0
    for path in files[: len(files) - keep]:
        session_id = path.stem
        if session_id == active_id:
            continue
        session = load_session(project_id, scene_id, session_id)
        if session is not None and not session.is_resolved():
            continue  # keep sessions with pending proposals
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def clear_sessions(project_id: str, scene_id: str) -> None:
    sessions = _sessions_dir(project_id, scene_id)
    for path in sessions.glob("*.json"):
        try:
            path.unlink()
        except OSError:
            pass
    active = _active_path(project_id, scene_id)
    if active.is_file():
        try:
            active.unlink()
        except OSError:
            pass
