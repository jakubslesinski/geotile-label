"""Analyst annotation package export and manager-side import workflow."""

from fastapi import APIRouter, HTTPException, Query

from db.storage import load_json, project_exists
from models.annotation_package import (
    AnnotationImportApplyRequest,
    AnnotationImportPreviewRequest,
    AnnotationPackageSaveRequest,
    ReviewImportRequest,
    ReviewPackageSaveRequest,
    SceneReviewRequest,
)
from services.annotation_import import (
    AnnotationPackageError,
    apply_annotation_import,
    get_annotation_import_report,
    preview_annotation_import,
)
from models.job import JobCreateRequest, JobType, PriorityClass, ResourceClass
from services.annotation_package import (
    plan_annotation_package_preparation,
    preview_annotation_package,
    save_annotation_package,
)
from services.jobs.scheduler import submit_job
from services.jobs.store import JobStoreError
from services.json_index.queries import get_annotation_summary
from services.review_package import (
    ReviewPackageError,
    apply_review_import,
    preview_review_import,
    preview_review_package,
    save_review_package,
    set_scene_review,
)

router = APIRouter()


def _require_project(project_id: str) -> None:
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")


def _project_role(project_id: str) -> str:
    profile = (load_json(project_id, "project", default={}) or {}).get("profile") or {}
    return profile.get("project_role") or "labeling"


def _require_role(project_id: str, role: str, action: str) -> None:
    current = _project_role(project_id)
    if current != role:
        raise HTTPException(
            409,
            f"{action} requires a '{role}' project; this project is '{current}'.",
        )


@router.get("/annotation-summary")
def get_project_annotation_summary(
    project_id: str,
    detail: str = Query(default="full", pattern="^(full|compact)$"),
):
    _require_project(project_id)
    summary = get_annotation_summary(project_id)
    if detail == "full":
        return summary
    # The dashboard panel only needs project-level aggregates. Per-scene filter
    # data is consumed server-side by the paged scenes query, so do not transfer
    # it (or a potentially very large list of missing-author IDs) to the browser.
    return {
        key: value
        for key, value in summary.items()
        if key not in {"per_scene", "missing_author_annotation_ids"}
    }


@router.get("/annotation-package/preview")
def get_annotation_package_preview(project_id: str):
    _require_project(project_id)
    _require_role(project_id, "labeling", "Exporting an annotation package")
    return preview_annotation_package(project_id)


@router.get("/annotation-package/prepare-plan")
def get_annotation_package_prepare_plan(project_id: str):
    """Which scenes still block the export, and how many bytes it costs to unblock them.

    Metadata only — nothing is read from the rasters, so the dialog can show the price
    before the user agrees to pay it.
    """
    _require_project(project_id)
    _require_role(project_id, "labeling", "Exporting an annotation package")
    return plan_annotation_package_preparation(project_id)


@router.post("/annotation-package/prepare")
def start_annotation_package_preparation(project_id: str):
    _require_project(project_id)
    _require_role(project_id, "labeling", "Exporting an annotation package")
    plan = plan_annotation_package_preparation(project_id)
    if not plan["scene_count"]:
        # Nie zlecamy zadania, ktore nie ma co robic — inaczej uzytkownik ogladalby
        # pasek postepu konczacy sie natychmiast i nie wiedzial, czy cokolwiek zaszlo.
        return {"job_id": None, "plan": plan, "already_exact": True}
    try:
        submitted = submit_job(
            project_id,
            JobCreateRequest(
                job_type=JobType.ANNOTATION_PACKAGE_PREPARE,
                resource_class=ResourceClass.IO_HEAVY,
                priority_class=PriorityClass.USER_BACKGROUND,
                dedupe_key="annotation-package-prepare",
            ),
        )
    except JobStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "job_id": submitted["job"]["job_id"],
        "plan": plan,
        "already_exact": False,
    }


@router.post("/annotation-package/save")
def save_project_annotation_package(project_id: str, body: AnnotationPackageSaveRequest):
    _require_project(project_id)
    _require_role(project_id, "labeling", "Exporting an annotation package")
    try:
        return save_annotation_package(project_id, body.output_path, body.package_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(500, f"Annotation package export failed: {exc}") from exc


@router.post("/annotation-import/preview")
def create_annotation_import_preview(project_id: str, body: AnnotationImportPreviewRequest):
    _require_project(project_id)
    _require_role(project_id, "review", "Importing annotation packages")
    try:
        return preview_annotation_import(
            project_id,
            body.package_paths,
            identity_policy=body.identity_policy,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/annotation-import/apply")
def apply_project_annotation_import(project_id: str, body: AnnotationImportApplyRequest):
    _require_project(project_id)
    _require_role(project_id, "review", "Importing annotation packages")
    try:
        return apply_annotation_import(
            project_id,
            body.preview_id,
            create_missing_classes=body.create_missing_classes,
            accepted_scene_ids=body.accepted_scene_ids,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, AnnotationPackageError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/scene-review/{scene_id}")
def put_scene_review(project_id: str, scene_id: str, body: SceneReviewRequest):
    _require_project(project_id)
    _require_role(project_id, "review", "Reviewing scenes")
    try:
        return set_scene_review(
            project_id,
            scene_id,
            status=body.review_status,
            comment=body.review_comment,
            pins=[pin.model_dump() for pin in body.pins],
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ReviewPackageError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/review-package/preview")
def get_review_package_preview(project_id: str, owner_email: str = ""):
    _require_project(project_id)
    _require_role(project_id, "review", "Exporting a review package")
    # owner_email pusty = tryb „Wszystkie recenzje" (wszystkie sprawdzone sceny).
    return preview_review_package(project_id, owner_email)


@router.post("/review-package/save")
def save_project_review_package(project_id: str, body: ReviewPackageSaveRequest):
    _require_project(project_id)
    _require_role(project_id, "review", "Exporting a review package")
    try:
        return save_review_package(project_id, body.output_path, body.owner_email or "", body.package_id)
    except ReviewPackageError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(500, f"Review package export failed: {exc}") from exc


@router.post("/review-import/preview")
def create_review_import_preview(project_id: str, body: ReviewImportRequest):
    _require_project(project_id)
    _require_role(project_id, "labeling", "Importing a review package")
    try:
        return preview_review_import(project_id, body.package_paths)
    except ReviewPackageError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/review-import/apply")
def apply_project_review_import(project_id: str, body: ReviewImportRequest):
    _require_project(project_id)
    _require_role(project_id, "labeling", "Importing a review package")
    try:
        return apply_review_import(project_id, body.package_paths)
    except ReviewPackageError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/annotation-import/reports/{report_id}")
def read_annotation_import_report(project_id: str, report_id: str):
    _require_project(project_id)
    try:
        return get_annotation_import_report(project_id, report_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
