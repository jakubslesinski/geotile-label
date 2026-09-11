"""HTTP API for persistent cross-view background jobs."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from db.storage import project_dir, project_exists
from models.job import JobCreateRequest
from services.jobs.events import read_events
from services.jobs.resources import list_leases, resource_limits
from services.jobs.scheduler import cancel_job, retry_job, submit_job
from services.jobs.store import JobStoreError, job_dir, list_jobs, read_job_detail


router = APIRouter()


def _require_project(project_id: str) -> None:
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")


@router.post("")
def post_job(project_id: str, body: JobCreateRequest):
    _require_project(project_id)
    try:
        return submit_job(project_id, body)
    except JobStoreError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("")
def get_jobs(
    project_id: str,
    status: str | None = Query(None),
    job_type: str | None = Query(None),
):
    _require_project(project_id)
    items = list_jobs(project_id)
    if status:
        items = [item for item in items if item["state"].get("status") == status]
    if job_type:
        items = [item for item in items if item["job"].get("job_type") == job_type]
    return {
        "schema_name": "geotile_jobs_index",
        "schema_version": 1,
        "project_id": project_id,
        "job_count": len(items),
        "resource_limits": resource_limits(),
        "leases": [lease for lease in list_leases() if lease.get("project_id") == project_id],
        "jobs": items,
    }


@router.get("/{job_id}")
def get_job(project_id: str, job_id: str):
    _require_project(project_id)
    try:
        return read_job_detail(project_id, job_id)
    except JobStoreError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{job_id}/events")
def get_job_events(
    project_id: str,
    job_id: str,
    after: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=5000),
):
    _require_project(project_id)
    try:
        detail = read_job_detail(project_id, job_id)
    except JobStoreError as exc:
        raise HTTPException(404, str(exc)) from exc
    events = read_events(project_id, job_id, after=after, limit=limit)
    return {
        "job_id": job_id,
        "status": detail["state"].get("status"),
        "events": events,
        "next": max([after, *[int(item.get("seq") or 0) for item in events]]),
    }


@router.get("/{job_id}/artifact")
def download_job_artifact(project_id: str, job_id: str):
    """Download a completed app-owned archive without loading it into memory."""

    _require_project(project_id)
    try:
        detail = read_job_detail(project_id, job_id)
    except JobStoreError as exc:
        raise HTTPException(404, str(exc)) from exc
    if detail["state"].get("status") != "completed":
        raise HTTPException(409, "Job artifact is not ready")
    artifacts = detail.get("artifacts") or {}
    if not artifacts.get("downloadable") or not artifacts.get("archive_path"):
        raise HTTPException(404, "Job has no downloadable artifact")
    candidate = Path(str(artifacts["archive_path"])).resolve(strict=False)
    allowed_roots = [
        (project_dir(project_id) / "artifacts").resolve(strict=False),
        job_dir(project_id, job_id).resolve(strict=False),
    ]
    if not any(_is_relative_to(candidate, root) for root in allowed_roots):
        raise HTTPException(403, "Artifact path is outside app-owned storage")
    if not candidate.is_file():
        raise HTTPException(410, "Artifact is no longer available")
    filename = Path(str(artifacts.get("filename") or candidate.name)).name
    return FileResponse(candidate, media_type="application/zip", filename=filename)


@router.post("/{job_id}/cancel")
def post_job_cancel(project_id: str, job_id: str):
    _require_project(project_id)
    try:
        return cancel_job(project_id, job_id)
    except JobStoreError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{job_id}/retry")
def post_job_retry(project_id: str, job_id: str):
    _require_project(project_id)
    try:
        return retry_job(project_id, job_id)
    except JobStoreError as exc:
        raise HTTPException(409, str(exc)) from exc


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
