"""Export YOLO/COCO/VOC + ZIP download."""

import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from db.storage import load_json, project_exists
from models.job import JobCreateRequest, JobType, PriorityClass, ResourceClass
from services.export_yolo import export_yolo_dataset
from services.export_coco import export_coco_dataset
from services.export_voc import export_voc_dataset
from services.export_sidecars import generate_export_sidecars
from services.dataset_runs import (
    DatasetPublicationError,
    read_run_json,
    require_dataset_exact_identities,
    resolve_dataset_path,
)
from services.dataset_package import build_dataset_package
from services.source_annotation_export import export_source_annotations_geoparquet
from services.export_geospatial import export_source_annotations_geospatial
from services.archive_io import (
    remove_temporary_archive,
    temporary_archive_path,
    write_zip_atomic,
)
from services.artifact_exports import (
    dataset_export_cache_key,
    estimate_dataset_export,
    normalize_zip_destination,
)
from services.async_bridge import run_blocking
from services.jobs.scheduler import submit_job
from services.jobs.store import JobStoreError

router = APIRouter()


class SaveZipRequest(BaseModel):
    output_path: str
    run_id: str | None = None


class DatasetExportJobRequest(BaseModel):
    run_id: str | None = None
    formats: list[str] = Field(default_factory=lambda: ["yolo"])
    output_path: str | None = None


class ArtifactCleanupJobRequest(BaseModel):
    cache_retention_days: int = 30
    partial_retention_hours: int = 24


class SourceAnnotationsGeoParquetRequest(BaseModel):
    output_path: str


class SourceAnnotationsGeospatialRequest(BaseModel):
    output_path: str


@router.post("/jobs")
def start_dataset_export_job(project_id: str, body: DatasetExportJobRequest):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    try:
        dataset_dir = resolve_dataset_path(project_id, body.run_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc
    run_manifest = read_run_json(dataset_dir, "dataset_run_manifest", default={})
    dataset_run_id = str(body.run_id or run_manifest.get("run_id") or "legacy")
    try:
        require_dataset_exact_identities(run_manifest)
        cache_key = dataset_export_cache_key(dataset_run_id, body.formats)
        estimate = estimate_dataset_export(dataset_dir, body.formats)
        output_path = (
            str(normalize_zip_destination(body.output_path))
            if body.output_path
            else None
        )
        submitted = submit_job(
            project_id,
            JobCreateRequest(
                job_type=JobType.DATASET_EXPORT,
                resource_class=ResourceClass.IO_HEAVY,
                priority_class=PriorityClass.USER_BACKGROUND,
                payload={
                    "dataset_dir": str(dataset_dir.resolve(strict=False)),
                    "dataset_run_id": dataset_run_id,
                    "formats": body.formats,
                    "output_path": output_path,
                    "disk_estimate": estimate,
                },
                dedupe_key=f"dataset_export:{cache_key}:{output_path or 'cache'}",
            ),
        )
    except DatasetPublicationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except JobStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {**submitted, "disk_estimate": estimate, "cache_key": cache_key}


@router.post("/cleanup-jobs")
def start_artifact_cleanup_job(project_id: str, body: ArtifactCleanupJobRequest):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    try:
        return submit_job(
            project_id,
            JobCreateRequest(
                job_type=JobType.ARTIFACT_CLEANUP,
                resource_class=ResourceClass.IO_HEAVY,
                priority_class=PriorityClass.MAINTENANCE,
                payload=body.model_dump(),
                dedupe_key="artifact_cleanup",
            ),
        )
    except JobStoreError as exc:
        raise HTTPException(409, str(exc)) from exc


def _get_export_data(project_id: str, run_id: str | None = None):
    try:
        dataset_dir = resolve_dataset_path(project_id, run_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc

    if run_id:
        tile_anns = read_run_json(dataset_dir, "tile_annotations", default={})
        run_manifest = read_run_json(dataset_dir, "dataset_run_manifest", default={})
        tiling_cfg = run_manifest.get("tiling_config") or {}
        classes = run_manifest.get("classes") or []
    else:
        tile_anns = load_json(project_id, "tile_annotations", default={})
        run_manifest = read_run_json(dataset_dir, "dataset_run_manifest", default={})
        tiling_cfg = load_json(project_id, "tiling_config")
        classes = load_json(project_id, "classes", default=[])
    try:
        require_dataset_exact_identities(run_manifest)
    except DatasetPublicationError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not tile_anns:
        raise HTTPException(400, "No tile annotations. Generate dataset first.")

    tile_size = tiling_cfg.get("tile_size", 640)
    class_names = {c["id"]: c["name"] for c in classes}
    link_manifest = read_run_json(dataset_dir, "tile_annotation_links", default={})
    if not link_manifest:
        link_manifest = load_json(project_id, "tile_annotation_links", default={})
    links = link_manifest.get("annotations", []) if isinstance(link_manifest, dict) else []
    return tile_anns, tile_size, class_names, dataset_dir, links


@router.post("/yolo")
async def export_yolo(project_id: str, run_id: str | None = Query(None)):
    return await run_blocking(_export_yolo_sync, project_id, run_id)


def _export_yolo_sync(project_id: str, run_id: str | None):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    tile_anns, tile_size, class_names, dataset_dir, links = _get_export_data(project_id, run_id)
    # Eksport do dedykowanego podkatalogu — dataset run jest NIEZMIENNY. Zapis in-place kiedyś
    # nadpisywał trenowalne labels/ i data.yaml runu (patrz historia bugów eksportu YOLO).
    export_dir = Path(dataset_dir) / "export" / "yolo"
    yaml_path = export_yolo_dataset(tile_anns, dataset_dir, tile_size, class_names,
                                    output_dir=export_dir, tile_links=links)
    sidecars = generate_export_sidecars(project_id, dataset_dir, "yolo", run_id=run_id)
    return {"status": "ok", "run_id": run_id, "data_yaml": str(yaml_path),
            "export_dir": str(export_dir), "sidecars": sidecars}


@router.post("/coco")
async def export_coco(project_id: str, run_id: str | None = Query(None)):
    return await run_blocking(_export_coco_sync, project_id, run_id)


def _export_coco_sync(project_id: str, run_id: str | None):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    tile_anns, tile_size, class_names, dataset_dir, links = _get_export_data(project_id, run_id)
    export_dir = Path(dataset_dir) / "export" / "coco"  # run niezmienny — eksport do podkatalogu
    export_coco_dataset(tile_anns, dataset_dir, tile_size, class_names,
                        output_dir=export_dir, tile_links=links)
    sidecars = generate_export_sidecars(project_id, dataset_dir, "coco", run_id=run_id)
    return {"status": "ok", "run_id": run_id, "export_dir": str(export_dir), "sidecars": sidecars}


@router.post("/voc")
async def export_voc(project_id: str, run_id: str | None = Query(None)):
    return await run_blocking(_export_voc_sync, project_id, run_id)


def _export_voc_sync(project_id: str, run_id: str | None):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    tile_anns, tile_size, class_names, dataset_dir, _links = _get_export_data(project_id, run_id)
    export_dir = Path(dataset_dir) / "export" / "voc"  # run niezmienny — eksport do podkatalogu
    export_voc_dataset(tile_anns, dataset_dir, tile_size, class_names, output_dir=export_dir)
    sidecars = generate_export_sidecars(project_id, dataset_dir, "voc", run_id=run_id)
    return {"status": "ok", "run_id": run_id, "export_dir": str(export_dir), "sidecars": sidecars}


@router.get("/download")
async def download_dataset(project_id: str, run_id: str | None = Query(None)):
    archive_path, filename = await run_blocking(
        _prepare_dataset_download,
        project_id,
        run_id,
    )
    try:
        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename=filename,
            background=BackgroundTask(remove_temporary_archive, archive_path),
        )
    except Exception:
        remove_temporary_archive(archive_path)
        raise


def _prepare_dataset_download(project_id: str, run_id: str | None) -> tuple[Path, str]:
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")

    try:
        dataset_dir = resolve_dataset_path(project_id, run_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc
    if not dataset_dir.exists():
        raise HTTPException(400, "Dataset not found. Generate it first.")

    project_data = load_json(project_id, "project")
    name = project_data.get("name", "dataset")
    try:
        package_dir = build_dataset_package(project_id, dataset_dir, run_id=run_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    filename = f'{name}_{run_id or "dataset"}.zip'
    archive_path = temporary_archive_path(filename.removesuffix(".zip"))
    write_zip_atomic(
        archive_path,
        lambda archive: _write_dataset_zip(package_dir, archive),
    )
    return archive_path, filename


@router.post("/save-zip")
async def save_dataset_zip(project_id: str, body: SaveZipRequest):
    return await run_blocking(_save_dataset_zip_sync, project_id, body)


def _save_dataset_zip_sync(project_id: str, body: SaveZipRequest):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")

    try:
        dataset_dir = resolve_dataset_path(project_id, body.run_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc
    if not dataset_dir.exists():
        raise HTTPException(400, "Dataset not found. Generate it first.")

    output_path = Path(body.output_path).expanduser()
    if not output_path.is_absolute():
        raise HTTPException(400, "output_path must be an absolute path")
    if output_path.suffix.lower() != ".zip":
        output_path = output_path.with_suffix(".zip")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        package_dir = build_dataset_package(project_id, dataset_dir, run_id=body.run_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    write_zip_atomic(
        output_path,
        lambda archive: _write_dataset_zip(package_dir, archive),
    )

    return {"status": "ok", "run_id": body.run_id, "output_path": str(output_path)}


@router.post("/source-annotations/geoparquet")
def export_source_annotations_to_geoparquet(
    project_id: str,
    body: SourceAnnotationsGeoParquetRequest,
):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    try:
        summary = export_source_annotations_geoparquet(project_id, body.output_path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(500, f"Source annotation export failed: {exc}") from exc
    return {"status": "ok", **summary}


@router.post("/source-annotations/geospatial")
def export_source_annotations_to_geospatial(
    project_id: str,
    body: SourceAnnotationsGeospatialRequest,
):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    try:
        summary = export_source_annotations_geospatial(project_id, body.output_path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(500, f"Geospatial export failed: {exc}") from exc
    return {"status": "ok", **summary}


def _write_dataset_zip(dataset_dir: Path, zf: zipfile.ZipFile) -> None:
    for file_path in dataset_dir.rglob("*"):
        if file_path.is_file():
            arcname = file_path.relative_to(dataset_dir)
            zf.write(file_path, arcname)
