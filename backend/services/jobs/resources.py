"""Cross-process resource leases for GPU, CPU-heavy and I/O-heavy jobs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from services.jobs.processes import capture_process_identity, process_status
from services.jobs.store import interprocess_lock, read_json, runtime_dir, utc_now, write_json_atomic


def resource_limits() -> dict[str, int]:
    cpu_default = max(1, min(2, (os.cpu_count() or 2) // 2))
    return {
        "gpu_exclusive": 1,
        "cpu_heavy": max(1, int(os.getenv("GEOTILE_JOB_CPU_LIMIT", str(cpu_default)))),
        "io_heavy": max(1, int(os.getenv("GEOTILE_JOB_IO_LIMIT", "1"))),
        "io_metadata": max(1, int(os.getenv("GEOTILE_JOB_METADATA_IO_LIMIT", "1"))),
    }


def leases_dir() -> Path:
    path = runtime_dir() / "leases"
    path.mkdir(parents=True, exist_ok=True)
    return path


def lease_path(job_id: str) -> Path:
    return leases_dir() / f"{job_id}.json"


def _lease_lock() -> Path:
    return runtime_dir() / ".resources.lock"


def _clean_stale_locked() -> list[str]:
    removed: list[str] = []
    for path in leases_dir().glob("*.json"):
        lease = read_json(path, default={}) or {}
        if process_status(lease.get("holder")) in {"absent", "recycled"}:
            path.unlink(missing_ok=True)
            removed.append(str(lease.get("job_id") or path.stem))
    return removed


def acquire_resource(
    project_id: str,
    job_id: str,
    resource_class: str,
) -> dict[str, Any] | None:
    with interprocess_lock(_lease_lock()) as acquired:
        if not acquired:
            return None
        _clean_stale_locked()
        limits = resource_limits()
        limit = limits.get(resource_class)
        if limit is None:
            raise ValueError(f"Unknown resource class: {resource_class}")
        active = []
        for path in leases_dir().glob("*.json"):
            lease = read_json(path, default={}) or {}
            if lease.get("resource_class") == resource_class:
                active.append(lease)
        if len(active) >= limit:
            return None
        lease = {
            "schema_name": "geotile_job_resource_lease",
            "schema_version": 1,
            "job_id": job_id,
            "project_id": project_id,
            "resource_class": resource_class,
            "holder": capture_process_identity(),
            "acquired_at": utc_now(),
            "updated_at": utc_now(),
        }
        write_json_atomic(lease_path(job_id), lease)
        return lease


def update_resource_holder(job_id: str, identity: dict[str, Any]) -> bool:
    with interprocess_lock(_lease_lock()) as acquired:
        if not acquired:
            return False
        path = lease_path(job_id)
        lease = read_json(path, default={}) or {}
        if lease.get("job_id") != job_id:
            return False
        lease["holder"] = identity
        lease["updated_at"] = utc_now()
        write_json_atomic(path, lease)
        return True


def release_resource(job_id: str) -> bool:
    with interprocess_lock(_lease_lock()) as acquired:
        if not acquired:
            return False
        path = lease_path(job_id)
        lease = read_json(path, default={}) or {}
        if lease and lease.get("job_id") != job_id:
            return False
        existed = path.exists()
        path.unlink(missing_ok=True)
        return existed


def list_leases() -> list[dict[str, Any]]:
    with interprocess_lock(_lease_lock()) as acquired:
        if not acquired:
            return []
        _clean_stale_locked()
        return [read_json(path, default={}) or {} for path in leases_dir().glob("*.json")]
