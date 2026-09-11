"""Atomic JSON store for immutable job specs and mutable state."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from db import storage
from db.storage import _atomic_write_bytes, project_dir
from models.job import (
    ACTIVE_JOB_STATUSES,
    JOB_SCHEMA_NAME,
    JOB_SCHEMA_VERSION,
    JOB_STATE_SCHEMA_NAME,
    JOB_STATE_SCHEMA_VERSION,
    JobSpec,
    JobState,
)
from services.jobs.processes import capture_process_identity, process_status


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RUNTIME_SCOPE_PREFIX = "runtime."
LOCK_TIMEOUT_S = 10.0
LOCK_STALE_S = 120.0
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class JobStoreError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stamp}_{uuid.uuid4().hex[:8]}"


def validate_id(value: str, label: str = "job id") -> str:
    if not SAFE_ID.fullmatch(str(value)) or ".." in str(value):
        raise JobStoreError(f"Invalid {label}: {value}")
    return str(value)


def jobs_dir(project_id: str) -> Path:
    validate_id(project_id, "job scope")
    path = (
        runtime_dir() / "scopes" / project_id / "jobs"
        if project_id.startswith(RUNTIME_SCOPE_PREFIX)
        else project_dir(project_id) / "jobs"
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_dir(project_id: str, job_id: str) -> Path:
    return jobs_dir(project_id) / validate_id(job_id)


def runtime_dir() -> Path:
    path = storage.DATA_DIR / "runtime" / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json_atomic(path: Path, value: Any) -> None:
    payload = json.dumps(value, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    _atomic_write_bytes(path, payload)


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve(strict=False)).lower()
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


def _lock_is_stale(path: Path) -> bool:
    payload = read_json(path, default={}) or {}
    identity = payload.get("owner")
    status = process_status(identity)
    if status in {"absent", "recycled"}:
        return True
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age > LOCK_STALE_S and status == "unknown"


@contextmanager
def interprocess_lock(
    path: Path,
    *,
    timeout_s: float = LOCK_TIMEOUT_S,
    blocking: bool = True,
) -> Iterator[bool]:
    """Small O_EXCL lock with exact owner identity and stale-lock recovery."""

    path.parent.mkdir(parents=True, exist_ok=True)
    local = _thread_lock(path)
    acquired_local = local.acquire(blocking=blocking)
    if not acquired_local:
        yield False
        return
    token = uuid.uuid4().hex
    deadline = time.monotonic() + max(0.0, timeout_s)
    acquired = False
    try:
        while True:
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                acquired = True
                payload = json.dumps(
                    {"token": token, "owner": capture_process_identity(), "created_at": utc_now()}
                ).encode("utf-8")
                try:
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                except Exception:
                    path.unlink(missing_ok=True)
                    acquired = False
                    raise
                break
            except FileExistsError:
                if _lock_is_stale(path):
                    try:
                        path.unlink()
                    except OSError:
                        pass
                    continue
                if not blocking or time.monotonic() >= deadline:
                    break
                time.sleep(0.02)
        yield acquired
    finally:
        if acquired:
            current = read_json(path, default={}) or {}
            if current.get("token") == token:
                path.unlink(missing_ok=True)
        local.release()


def create_job(spec: JobSpec) -> tuple[dict[str, Any], dict[str, Any]]:
    target = job_dir(spec.project_id, spec.job_id)
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise JobStoreError(f"Job already exists: {spec.job_id}") from exc
    now = utc_now()
    state = JobState(
        job_id=spec.job_id,
        job_type=spec.job_type,
        project_id=spec.project_id,
        resource_class=spec.resource_class,
        priority=spec.priority,
        created_at=spec.created_at,
        updated_at=now,
    )
    try:
        write_json_atomic(target / "job.json", spec.model_dump(mode="json"))
        write_json_atomic(target / "state.json", state.model_dump(mode="json"))
        write_json_atomic(target / "artifacts.json", {})
        write_json_atomic(target / "performance.json", {})
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return spec.model_dump(mode="json"), state.model_dump(mode="json")


def read_spec(project_id: str, job_id: str) -> dict[str, Any]:
    value = read_json(job_dir(project_id, job_id) / "job.json", default={}) or {}
    if value.get("schema_name") != JOB_SCHEMA_NAME or value.get("schema_version") != JOB_SCHEMA_VERSION:
        raise JobStoreError(f"Unsupported or missing job spec: {job_id}")
    return value


def read_state(project_id: str, job_id: str) -> dict[str, Any]:
    path = job_dir(project_id, job_id) / "state.json"
    value: dict[str, Any] = {}
    # A state update uses atomic replace. On Windows/SMB, a concurrent reader can still
    # observe a very short access-denied/not-found window while the worker or antivirus
    # holds the directory entry. `read_json` intentionally maps that OSError to the default;
    # retry here so the SSE observer does not turn a transient filesystem condition into a
    # permanent "Job state not found" failure while the worker is demonstrably active.
    for attempt in range(5):
        value = read_json(path, default={}) or {}
        if value:
            break
        if attempt < 4:
            time.sleep(0.01 * (attempt + 1))
    if not value:
        raise JobStoreError(f"Job state not found: {job_id}")
    if value.get("schema_name") != JOB_STATE_SCHEMA_NAME:
        raise JobStoreError(f"Unsupported job state: {job_id}")
    return value


def mutate_state(
    project_id: str,
    job_id: str,
    updater: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> dict[str, Any]:
    directory = job_dir(project_id, job_id)
    with interprocess_lock(directory / ".state.lock") as acquired:
        if not acquired:
            raise TimeoutError(f"Timed out locking state for job {job_id}")
        state = read_state(project_id, job_id)
        updated = updater(dict(state))
        if updated is None:
            return state
        updated["schema_name"] = JOB_STATE_SCHEMA_NAME
        updated["schema_version"] = JOB_STATE_SCHEMA_VERSION
        updated["updated_at"] = utc_now()
        updated["revision"] = int(state.get("revision") or 0) + 1
        write_json_atomic(directory / "state.json", updated)
        return updated


def patch_state(project_id: str, job_id: str, **changes: Any) -> dict[str, Any]:
    return mutate_state(project_id, job_id, lambda state: {**state, **changes})


def list_jobs(project_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    root = jobs_dir(project_id)
    for candidate in root.iterdir():
        if not candidate.is_dir() or not SAFE_ID.fullmatch(candidate.name):
            continue
        spec = read_json(candidate / "job.json", default={}) or {}
        state = read_json(candidate / "state.json", default={}) or {}
        if spec and state:
            result.append({"job": spec, "state": state})
    result.sort(
        key=lambda item: (
            int(item["job"].get("priority") or 0),
            str(item["job"].get("created_at") or ""),
        ),
        reverse=True,
    )
    return result


def list_all_jobs() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for project_id in storage.list_project_ids():
        try:
            result.extend(list_jobs(project_id))
        except Exception:
            continue
    scopes = runtime_dir() / "scopes"
    if scopes.is_dir():
        for candidate in scopes.iterdir():
            if (
                not candidate.is_dir()
                or not candidate.name.startswith(RUNTIME_SCOPE_PREFIX)
                or not SAFE_ID.fullmatch(candidate.name)
            ):
                continue
            try:
                result.extend(list_jobs(candidate.name))
            except Exception:
                continue
    return result


def find_active_job(
    project_id: str,
    *,
    job_type: str | None = None,
    dedupe_key: str | None = None,
) -> dict[str, Any] | None:
    for item in list_jobs(project_id):
        spec, state = item["job"], item["state"]
        if state.get("status") not in ACTIVE_JOB_STATUSES:
            continue
        if job_type and spec.get("job_type") != job_type:
            continue
        if dedupe_key is not None and spec.get("dedupe_key") != dedupe_key:
            continue
        return item
    return None


@contextmanager
def claim_job(project_id: str, job_id: str) -> Iterator[bool]:
    path = job_dir(project_id, job_id) / ".dispatch.claim"
    with interprocess_lock(path, timeout_s=0.0, blocking=False) as acquired:
        yield acquired


def read_job_detail(project_id: str, job_id: str) -> dict[str, Any]:
    directory = job_dir(project_id, job_id)
    return {
        "job": read_spec(project_id, job_id),
        "state": read_state(project_id, job_id),
        "artifacts": read_json(directory / "artifacts.json", default={}) or {},
        "performance": read_json(directory / "performance.json", default={}) or {},
    }


def write_artifacts(project_id: str, job_id: str, value: dict[str, Any]) -> None:
    write_json_atomic(job_dir(project_id, job_id) / "artifacts.json", value)


def write_performance(project_id: str, job_id: str, value: dict[str, Any]) -> None:
    write_json_atomic(job_dir(project_id, job_id) / "performance.json", value)
