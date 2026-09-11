"""CRUD projects — folder-based scene scanning."""

import asyncio
import hashlib
import json
import os
import shutil
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from db.storage import (
    load_json, save_json, list_project_ids, project_exists,
    delete_project_dir, delete_scene_data, save_scene_json, scene_dir,
    list_scene_ids, load_scene_json, project_dir, SCENES_ROOT,
    create_project_root,
)
from models.job import JobCreateRequest, JobType, PriorityClass, ResourceClass
from models.project import (
    Project,
    ProjectCreate,
    default_project_profile,
)
from models.scene import Scene
from services.scene_loader import get_scene_info
from services.scene_working_storage import summarize_scene_working_storage
from services.scene_manifest import rebuild_scenes_index, refresh_scene_identity
from services.nitf.project import create_nitf_scene, scan_nitf_folder
from services.preprocessing_profiles import ensure_preprocessing_profiles
from services.archive_io import (
    remove_temporary_archive,
    temporary_archive_path,
    write_zip_atomic,
)
from services.async_bridge import run_blocking
from services.jobs.scheduler import submit_job
from services.jobs.store import JobStoreError
from services.project_backup import BACKUP_FORMAT_VERSION
from utils.scene_paths import resolve_scene_folder
from utils.browse_roots import (
    get_class_browse_roots,
    get_scene_browse_roots,
    resolve_path_in_roots,
    to_display_path,
)
router = APIRouter()

SCENE_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}
class ProjectImportBackup(BaseModel):
    backup_file: str
    scene_folder: str = ""
    name: str | None = None


class ProjectImportFolder(BaseModel):
    project_folder: str
    scene_folder: str
    name: str | None = None


class ProjectNitfCreate(BaseModel):
    name: str
    scene_folder: str
    classes_file: str | None = None
    project_location: str | None = None
    annotation_mode: str = "bbox"


def _scene_id(filename: str) -> str:
    return hashlib.md5(filename.encode()).hexdigest()[:12]


def _scan_folder(folder: Path) -> list[str]:
    """Return sorted list of image filenames in folder."""
    files = []
    for f in folder.iterdir():
        if f.is_file() and f.suffix.lower() in SCENE_EXTENSIONS:
            files.append(f.name)
    files.sort()
    return files


def _resolve_scene_folder(scene_folder: str) -> Path:
    """Resolve scene_folder path under SCENES_ROOT, preventing traversal."""
    resolved = resolve_scene_folder(SCENES_ROOT, scene_folder)
    if not resolved:
        raise HTTPException(400, f"Folder not found: {scene_folder}")
    return resolved


def _resolve_classes_file(file_path: str) -> Path | None:
    direct = Path(file_path).expanduser()
    if direct.is_absolute():
        return direct.resolve(strict=False) if direct.is_file() else None

    resolved, _root = resolve_path_in_roots(file_path, get_class_browse_roots())
    if resolved and resolved.is_file():
        return resolved
    return None


def _project_with_display(data: dict) -> dict:
    if not data:
        return data
    project = dict(data)
    project["scene_folder_display"] = to_display_path(
        project.get("scene_folder", ""),
        get_scene_browse_roots(),
    )
    return project


@router.get("/")
def _list_projects_sync():
    projects = []
    for pid in list_project_ids():
        data = load_json(pid, "project")
        if data:
            projects.append(_project_with_display(data))
    return projects


async def list_projects():
    # Praca synchroniczna (odczyt project.json per projekt) idzie do watku, zeby pod
    # obciazeniem nie czekala za zablokowana petla zdarzen — lista projektow ma byc szybka.
    return await asyncio.to_thread(_list_projects_sync)


@router.post("/")
def create_project(body: ProjectCreate):
    folder = _resolve_scene_folder(body.scene_folder)

    pid = uuid.uuid4().hex[:12]
    profile = body.profile or default_project_profile()

    # Scan for image files
    filenames = _scan_folder(folder)
    if not filenames:
        raise HTTPException(400, "No image files found in folder")
    project_root = create_project_root(pid, body.name, body.project_location)

    # Create scene records (scene_info loaded lazily on first access)
    for fname in filenames:
        sid = _scene_id(fname)
        scene = Scene(id=sid, filename=fname)
        save_scene_json(pid, sid, "scene", scene.model_dump())
        save_scene_json(pid, sid, "annotations", [])

    # Load classes from file if provided
    classes = []
    if body.classes_file:
        classes_path = _resolve_classes_file(body.classes_file)
        if not classes_path:
            raise HTTPException(400, "classes_file not found")
        with open(classes_path, "r", encoding="utf-8") as f:
            classes = json.load(f)

    # Create project record
    project = Project(
        id=pid,
        name=body.name,
        scene_folder=body.scene_folder,
        project_root=str(project_root),
        created_in_appdata=body.project_location is None,
        profile=profile,
        scene_count=len(filenames),
    )
    save_json(pid, "project", project.model_dump())
    save_json(pid, "classes", classes)
    tile_size = int(body.tile_size)
    tile_buffer = max(0, min(int(body.buffer), tile_size // 2))
    save_json(pid, "tiling_config", {"tile_size": tile_size, "buffer": tile_buffer})
    save_json(pid, "dataset_config", {
        "train_ratio": 0.7, "val_ratio": 0.2, "test_ratio": 0.1,
        "min_box_fraction": 0.3, "negative_ratio": 0.1,
        "split_mode": profile.default_split_strategy,
        "split_seed": 42,
        "block_size_tiles": 5,
        "preprocessing_profile_id": profile.default_preprocessing_profile,
    })
    ensure_preprocessing_profiles(pid)
    rebuild_scenes_index(pid)

    return _project_with_display(project.model_dump())


@router.post("/nitf")
async def create_nitf_project(body: ProjectNitfCreate):
    """Create an airborne-NITF (sensor-geometry) project from a folder of ``.ntf``
    files, or of package subfolders each containing ``.ntf`` files."""
    folder = _resolve_scene_folder(body.scene_folder)
    scans = scan_nitf_folder(folder)
    if not scans:
        raise HTTPException(400, "No .ntf/.nitf files found in folder or its subfolders")

    pid = uuid.uuid4().hex[:12]
    profile = default_project_profile(
        modality="AERIAL_EO",
        georeferencing="SENSOR_GEO",
        annotation_mode=body.annotation_mode,
    )
    create_project_root(pid, body.name, body.project_location)

    # Ingest is GDAL-heavy (transcode + overviews + hash) and releases the GIL, so
    # run scenes in parallel off the event loop. Each scene writes its own dir, so
    # there is no shared mutable state to guard.
    def _ingest_one(nitf_path: Path, package_id: str | None) -> tuple[str | None, str, str | None]:
        relative = nitf_path.name if package_id is None else f"{package_id}/{nitf_path.name}"
        sid = _scene_id(relative)
        try:
            create_nitf_scene(pid, sid, nitf_path, package_id=package_id)
            return sid, relative, None
        except Exception as exc:  # noqa: BLE001 — isolate per-scene ingest failures
            return None, relative, str(exc)

    loop = asyncio.get_running_loop()
    max_workers = min(8, (os.cpu_count() or 4))
    created: list[str] = []
    errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="nitf-ingest") as pool:
        futures = [loop.run_in_executor(pool, _ingest_one, path, pkg) for path, pkg in scans]
        for sid, relative, err in await asyncio.gather(*futures):
            if err is None:
                created.append(sid)  # type: ignore[arg-type]
            else:
                errors.append({"file": relative, "error": err})
    if not created:
        delete_project_dir(pid)
        first = errors[0]["error"] if errors else "unknown"
        raise HTTPException(400, f"No NITF scenes could be ingested ({first})")

    classes = []
    if body.classes_file:
        classes_path = _resolve_classes_file(body.classes_file)
        if not classes_path:
            raise HTTPException(400, "classes_file not found")
        with open(classes_path, "r", encoding="utf-8") as f:
            classes = json.load(f)

    project = Project(
        id=pid,
        name=body.name,
        scene_folder=body.scene_folder,
        project_root=str(project_dir(pid)),
        created_in_appdata=body.project_location is None,
        profile=profile,
        scene_count=len(created),
    )
    save_json(pid, "project", project.model_dump())
    save_json(pid, "classes", classes)
    save_json(pid, "tiling_config", {"tile_size": 640, "buffer": 0})
    save_json(pid, "dataset_config", {
        "train_ratio": 0.7, "val_ratio": 0.2, "test_ratio": 0.1,
        "min_box_fraction": 0.3, "negative_ratio": 0.1,
        "split_mode": profile.default_split_strategy,
        "split_seed": 42,
        "block_size_tiles": 5,
        "preprocessing_profile_id": profile.default_preprocessing_profile,
    })
    ensure_preprocessing_profiles(pid)
    rebuild_scenes_index(pid)

    result = _project_with_display(project.model_dump())
    result["created_scenes"] = len(created)
    result["errors"] = errors
    return result


@router.post("/import-backup")
def import_project_backup(body: ProjectImportBackup):
    backup_path = Path(body.backup_file).expanduser()
    if not backup_path.is_absolute() or not backup_path.is_file():
        raise HTTPException(400, "backup_file not found")

    try:
        with zipfile.ZipFile(backup_path, "r") as zf:
            manifest = _read_backup_json(zf, "backup_manifest.json")
            if manifest.get("format_version") != BACKUP_FORMAT_VERSION:
                raise HTTPException(400, "Unsupported backup format")

            source_project = _read_backup_json(zf, "project.json")
            if source_project.get("source_type") == "basemap_geo":
                # Funkcja "projekt Basemap GEO" zostala usunieta. Stare archiwum ma byc
                # ODRZUCONE z czytelnym komunikatem — awaria w polowie odtwarzania
                # wyglada gorzej niz jawny brak obslugi.
                raise HTTPException(
                    400,
                    "Basemap GEO projects are no longer supported; this backup cannot be "
                    "imported. Use a version of the application that still provides them.",
                )
            has_scene_sources = "scene_sources.json" in zf.namelist()

            scenes = manifest.get("scenes", [])
            if has_scene_sources:
                missing = [scene.get("filename", "") for scene in scenes]
                found_scenes = scenes
            else:
                if not body.scene_folder:
                    raise HTTPException(400, "scene_folder is required for local-scene backups")
                folder = _resolve_scene_folder(body.scene_folder)
                missing = [
                    scene.get("filename", "")
                    for scene in scenes
                    if not (folder / scene.get("filename", "")).is_file()
                ]
                found_scenes = [
                    scene for scene in scenes
                    if (folder / scene.get("filename", "")).is_file()
                ]
                if not found_scenes:
                    raise HTTPException(400, "No backup scenes found in selected scene folder")

            pid = uuid.uuid4().hex[:12]
            project_data = dict(source_project)
            original_name = project_data.get("name", manifest.get("project_name", "Imported project"))
            project_data["id"] = pid
            project_data["name"] = body.name or f"{original_name} (imported)"
            project_data["scene_count"] = len(found_scenes)
            project_data["scene_folder"] = body.scene_folder
            save_json(pid, "project", project_data)

            if has_scene_sources:
                scene_sources = _read_backup_json(zf, "scene_sources.json")
                for source in scene_sources.get("sources") or []:
                    source["last_scan_status"] = "missing_source"
                    source["last_scan_at"] = None
                save_json(pid, "scene_sources", scene_sources)
                _restore_processing_manifests(zf, project_dir(pid))

            for name, default in [
                ("classes", []),
                ("tiling_config", {"tile_size": 640, "buffer": 0}),
                ("dataset_config", {
                    "train_ratio": 0.7,
                    "val_ratio": 0.2,
                    "test_ratio": 0.1,
                    "min_box_fraction": 0.3,
                    "negative_ratio": 0.1,
                }),
                ("preprocessing_profiles", {}),
            ]:
                save_json(pid, name, _read_optional_backup_json(zf, f"{name}.json", default))

            for scene in found_scenes:
                sid = scene["id"]
                scene_data = _read_backup_json(zf, f"scenes/{sid}/scene.json")
                scene_data["id"] = sid
                save_scene_json(pid, sid, "scene", scene_data)
                save_scene_json(
                    pid,
                    sid,
                    "annotations",
                    _read_optional_backup_json(zf, f"scenes/{sid}/annotations.json", []),
                )
                scene_manifest = _read_optional_backup_json(
                    zf, f"scenes/{sid}/scene_manifest.json", None
                )
                if scene_manifest is not None:
                    scene_manifest["project_id"] = pid
                    scene_manifest["scene_id"] = sid
                    scene_manifest["source_path"] = None
                    scene_manifest["source_exists"] = False
                    if has_scene_sources:
                        scene_manifest.setdefault("working_view", {})["preparation_status"] = "missing_source"
                    save_scene_json(pid, sid, "scene_manifest", scene_manifest)
                    if has_scene_sources:
                        scene_data["preparation_status"] = "missing_source"
                        save_scene_json(pid, sid, "scene", scene_data)
                tiles = _read_optional_backup_json(zf, f"scenes/{sid}/tiles.json", None)
                if tiles is not None:
                    save_scene_json(pid, sid, "tiles", tiles)

            tile_catalog_restored = False
            if len(found_scenes) == len(scenes):
                tile_catalog_restored = _restore_tile_catalogs(zf, project_dir(pid))

            rebuild_scenes_index(pid)
            ensure_preprocessing_profiles(pid)
            return {
                "project_id": pid,
                "name": project_data["name"],
                "imported_scenes": len(found_scenes),
                "missing_scenes": missing,
                "total_scenes": len(scenes),
                "tile_catalog_restored": tile_catalog_restored,
            }
    except KeyError as exc:
        raise HTTPException(400, f"Backup is missing required file: {exc}")
    except zipfile.BadZipFile:
        raise HTTPException(400, "Invalid backup ZIP")


@router.post("/import-folder")
def import_project_folder(body: ProjectImportFolder):
    source_project_dir = Path(body.project_folder).expanduser()
    if not source_project_dir.is_absolute() or not source_project_dir.is_dir():
        raise HTTPException(400, "project_folder not found")

    source_project_json = source_project_dir / "project.json"
    source_scenes_dir = source_project_dir / "scenes"
    if not source_project_json.is_file() or not source_scenes_dir.is_dir():
        raise HTTPException(400, "Selected folder is not a GeoTile Label project folder")

    folder = _resolve_scene_folder(body.scene_folder)
    source_project_data = _read_json_file(source_project_json)
    source_scenes = _read_project_folder_scenes(source_scenes_dir)

    missing = [
        scene.get("filename", "")
        for scene in source_scenes
        if not (folder / scene.get("filename", "")).is_file()
    ]
    found_scenes = [
        scene for scene in source_scenes
        if (folder / scene.get("filename", "")).is_file()
    ]
    if not found_scenes:
        raise HTTPException(400, "No project scenes found in selected scene folder")

    pid = uuid.uuid4().hex[:12]
    original_name = source_project_data.get("name", "Imported project")
    project_data = dict(source_project_data)
    project_data["id"] = pid
    project_data["name"] = body.name or f"{original_name} (imported)"
    project_data["scene_folder"] = body.scene_folder
    project_data["scene_count"] = len(found_scenes)
    save_json(pid, "project", project_data)

    for name, default in [
        ("classes", []),
        ("tiling_config", {"tile_size": 640, "buffer": 0}),
        ("dataset_config", {
            "train_ratio": 0.7,
            "val_ratio": 0.2,
            "test_ratio": 0.1,
            "min_box_fraction": 0.3,
            "negative_ratio": 0.1,
        }),
        ("preprocessing_profiles", {}),
    ]:
        save_json(
            pid,
            name,
            _read_optional_json_file(source_project_dir / f"{name}.json", default),
        )

    for scene in found_scenes:
        sid = scene["id"]
        source_scene_dir = Path(scene.pop("_source_scene_dir", source_scenes_dir / sid))
        scene_data = dict(scene)
        scene_data["id"] = sid
        scene_data.pop("scene_info", None)
        scene_data.pop("scene_info_version", None)
        scene_data.pop("scene_info_error", None)
        save_scene_json(pid, sid, "scene", scene_data)
        save_scene_json(
            pid,
            sid,
            "annotations",
            _read_optional_json_file(source_scene_dir / "annotations.json", []),
        )
        scene_manifest = _read_optional_json_file(
            source_scene_dir / "scene_manifest.json", None
        )
        if scene_manifest is not None:
            scene_manifest["project_id"] = pid
            scene_manifest["scene_id"] = sid
            scene_manifest["source_path"] = None
            scene_manifest["source_exists"] = False
            save_scene_json(pid, sid, "scene_manifest", scene_manifest)
        tiles = _read_optional_json_file(source_scene_dir / "tiles.json", None)
        if tiles is not None:
            save_scene_json(pid, sid, "tiles", tiles)

    rebuild_scenes_index(pid)
    ensure_preprocessing_profiles(pid)
    return {
        "project_id": pid,
        "name": project_data["name"],
        "imported_scenes": len(found_scenes),
        "missing_scenes": missing,
        "total_scenes": len(source_scenes),
    }


@router.get("/{project_id}/backup/download")
async def download_project_backup(project_id: str):
    archive_path, filename = await run_blocking(_prepare_project_backup, project_id)
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


@router.post("/{project_id}/backup/jobs")
def start_project_backup_job(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    try:
        return submit_job(
            project_id,
            JobCreateRequest(
                job_type=JobType.PROJECT_BACKUP,
                resource_class=ResourceClass.IO_HEAVY,
                priority_class=PriorityClass.USER_BACKGROUND,
                payload={},
                dedupe_key="project_backup",
            ),
        )
    except JobStoreError as exc:
        raise HTTPException(409, str(exc)) from exc


def _prepare_project_backup(project_id: str) -> tuple[Path, str]:
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")

    project_data = load_json(project_id, "project")
    scene_ids = list_scene_ids(project_id)
    scenes = []
    for sid in scene_ids:
        scene_data = load_scene_json(project_id, sid, "scene", default={})
        if scene_data:
            scenes.append({"id": sid, "filename": scene_data.get("filename", "")})

    manifest = {
        "format_version": BACKUP_FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
        "project_name": project_data.get("name", "project"),
        "scene_folder": project_data.get("scene_folder", ""),
        "scenes": scenes,
        "tile_catalogs_included": (project_dir(project_id) / "tile_catalogs" / "index.json").is_file(),
    }

    def write_backup(zf: zipfile.ZipFile) -> None:
        _write_backup_json(zf, "backup_manifest.json", manifest)
        _write_backup_json(zf, "project.json", project_data)
        scene_sources_path = project_dir(project_id) / "scene_sources.json"
        if scene_sources_path.is_file():
            zf.write(scene_sources_path, "scene_sources.json")
        scenes_index = project_dir(project_id) / "scenes_index.json"
        if scenes_index.exists():
            zf.write(scenes_index, "scenes_index.json")
        for name in ("classes", "tiling_config", "dataset_config", "preprocessing_profiles"):
            value = load_json(project_id, name, default={})
            _write_backup_json(zf, f"{name}.json", value)

        pdir = project_dir(project_id)
        for sid in scene_ids:
            sdir = pdir / "scenes" / sid
            for filename in ("scene.json", "scene_manifest.json", "annotations.json", "tiles.json"):
                path = sdir / filename
                if path.exists():
                    zf.write(path, f"scenes/{sid}/{filename}")

        catalogs_dir = pdir / "tile_catalogs"
        if catalogs_dir.is_dir():
            for path in catalogs_dir.rglob("*"):
                if not path.is_file() or "preview_cache" in path.parts:
                    continue
                if path.suffix.lower() not in {".json", ".parquet"}:
                    continue
                zf.write(path, path.relative_to(pdir).as_posix())

        derived_dir = pdir / "derived_scenes"
        if derived_dir.is_dir():
            for path in derived_dir.rglob("processing_manifest.json"):
                zf.write(path, path.relative_to(pdir).as_posix())

    safe_name = "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in project_data.get("name", "project")
    )
    filename = f"{safe_name}_backup.zip"
    archive_path = temporary_archive_path(f"{safe_name}_backup")
    write_zip_atomic(
        archive_path,
        write_backup,
    )
    return archive_path, filename


@router.get("/{project_id}")
def get_project(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    project_data = load_json(project_id, "project")
    return _project_with_display(project_data)


@router.get("/{project_id}/scene-working-storage")
def get_scene_working_storage(
    project_id: str,
    largest_limit: int = Query(10, ge=0, le=25),
):
    """Rozmiar `derived_scenes` projektu — wylacznie odczyt.

    Swiadomie synchroniczne `def`: FastAPI wykona skan w puli watkow, wiec chodzenie po
    katalogu nie blokuje petli zdarzen. Brak parametru `refresh` — kazde wywolanie jest
    swiadomym odczytem, panel woła je dopiero po rozwinieciu i po kliknieciu „Odswiez".
    """

    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    return summarize_scene_working_storage(project_id, largest_limit=largest_limit)


@router.delete("/{project_id}")
def delete_project(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    delete_project_dir(project_id)
    return {"status": "deleted"}


def _rescan_nitf_project(project_id: str, project_data: dict) -> dict:
    """Rescan a NITF project's folder: ingest new .ntf files, drop vanished ones.

    Uses the same scan + scene-id scheme as ``create_nitf_project`` so ids are stable.
    Existing scenes are left untouched (no re-transcode); only new files are ingested.
    """
    folder = _resolve_scene_folder(project_data["scene_folder"])
    wanted: dict = {}
    for nitf_path, package_id in scan_nitf_folder(folder):
        relative = nitf_path.name if package_id is None else f"{package_id}/{nitf_path.name}"
        wanted[_scene_id(relative)] = (nitf_path, package_id)

    existing_ids = set(list_scene_ids(project_id))
    added = 0
    errors: list[dict] = []
    for sid, (nitf_path, package_id) in wanted.items():
        if sid in existing_ids:
            continue
        try:
            create_nitf_scene(project_id, sid, nitf_path, package_id=package_id)
            added += 1
        except Exception as exc:  # noqa: BLE001 — isolate per-scene ingest failures
            errors.append({"scene_id": sid, "file": nitf_path.name, "error": str(exc)})

    removed = 0
    for sid in existing_ids:
        if sid not in wanted:
            if delete_scene_data(project_id, sid):
                removed += 1

    project_data["scene_count"] = len(wanted)
    save_json(project_id, "project", project_data)
    rebuild_scenes_index(project_id)
    return {"added": added, "removed": removed, "total": len(wanted), "errors": errors}


@router.post("/{project_id}/scan")
def scan_project(project_id: str):
    """Rescan scene folder for new/removed files."""
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")

    project_data = load_json(project_id, "project")
    # NITF (sensor-geometry) projects hold .ntf files, which the generic image-extension
    # scan ignores — rescanning them with the folder scanner would delete every scene.
    profile = project_data.get("profile") or {}
    if profile.get("georeferencing") == "SENSOR_GEO" or profile.get("modality") == "AERIAL_EO":
        return _rescan_nitf_project(project_id, project_data)

    folder = _resolve_scene_folder(project_data["scene_folder"])
    filenames = _scan_folder(folder)

    existing_ids = set(list_scene_ids(project_id))
    new_ids = {_scene_id(f) for f in filenames}

    # Add new scenes
    added = 0
    for fname in filenames:
        sid = _scene_id(fname)
        if sid not in existing_ids:
            scene = Scene(id=sid, filename=fname)
            save_scene_json(project_id, sid, "scene", scene.model_dump())
            save_scene_json(project_id, sid, "annotations", [])
            added += 1

    # Remove scenes that no longer exist
    removed = 0
    for sid in existing_ids:
        if sid not in new_ids:
            if delete_scene_data(project_id, sid):
                removed += 1

    # Update project count
    project_data["scene_count"] = len(filenames)
    save_json(project_id, "project", project_data)
    identity_complete = 0
    identity_errors = []
    for fname in filenames:
        sid = _scene_id(fname)
        scene_data = load_scene_json(project_id, sid, "scene", default={})
        scene_path = folder / fname
        try:
            if not scene_data.get("scene_info"):
                scene_data["scene_info"] = get_scene_info(scene_path).model_dump()
                save_scene_json(project_id, sid, "scene", scene_data)
            manifest = refresh_scene_identity(
                project_id,
                sid,
                project_data,
                scene_data,
                scene_path,
            )
            if manifest.get("source_identity_status") == "complete":
                identity_complete += 1
            else:
                identity_errors.append({
                    "scene_id": sid,
                    "filename": fname,
                    "error": manifest.get("source_identity_error"),
                })
        except Exception as exc:
            identity_errors.append({
                "scene_id": sid,
                "filename": fname,
                "error": str(exc),
            })
    rebuild_scenes_index(project_id)

    return {
        "added": added,
        "removed": removed,
        "total": len(filenames),
        "identity_complete": identity_complete,
        "identity_errors": identity_errors,
    }


def _restore_tile_catalogs(zf: zipfile.ZipFile, destination_root: Path) -> bool:
    allowed_catalog_files = {
        "tile_catalog_manifest.json",
        "tiles.parquet",
        "tile_annotation_links.parquet",
        "review_state.json",
        "catalog_statistics.json",
    }
    restored = False
    for member in zf.infolist():
        parts = member.filename.replace("\\", "/").strip("/").split("/")
        if member.is_dir() or not parts or parts[0] != "tile_catalogs":
            continue
        if parts == ["tile_catalogs", "index.json"]:
            pass
        elif (
            len(parts) == 3
            and parts[2] in allowed_catalog_files
            and parts[1]
            and all(character.isalnum() or character in "-_" for character in parts[1])
        ):
            pass
        else:
            continue
        output = destination_root.joinpath(*parts)
        output.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member, "r") as source, output.open("wb") as target:
            shutil.copyfileobj(source, target)
        restored = True
    return restored


def _restore_processing_manifests(zf: zipfile.ZipFile, destination_root: Path) -> bool:
    restored = False
    for member in zf.infolist():
        parts = member.filename.replace("\\", "/").strip("/").split("/")
        if (
            member.is_dir()
            or len(parts) != 4
            or parts[0] != "derived_scenes"
            or parts[-1] != "processing_manifest.json"
            or not all(all(char.isalnum() or char in "-_" for char in value) for value in parts[1:3])
        ):
            continue
        output = destination_root.joinpath(*parts)
        output.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member, "r") as source, output.open("wb") as target:
            shutil.copyfileobj(source, target)
        restored = True
    return restored


def _write_backup_json(zf: zipfile.ZipFile, name: str, data: object) -> None:
    zf.writestr(name, json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _read_backup_json(zf: zipfile.ZipFile, name: str):
    with zf.open(name) as f:
        return json.loads(f.read().decode("utf-8"))


def _read_optional_backup_json(zf: zipfile.ZipFile, name: str, default):
    try:
        return _read_backup_json(zf, name)
    except KeyError:
        return default


def _write_json_file(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def _read_json_file(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"Invalid JSON file: {path.name}") from exc


def _read_optional_json_file(path: Path, default):
    if not path.exists():
        return default
    return _read_json_file(path)


def _read_project_folder_scenes(source_scenes_dir: Path) -> list[dict]:
    scenes = []
    for source_scene_dir in sorted(source_scenes_dir.iterdir(), key=lambda p: p.name):
        if not source_scene_dir.is_dir():
            continue
        scene_json = source_scene_dir / "scene.json"
        if not scene_json.is_file():
            continue
        scene_data = _read_json_file(scene_json)
        filename = scene_data.get("filename")
        if not filename:
            continue
        scene_data["id"] = scene_data.get("id") or source_scene_dir.name
        scene_data["_source_scene_dir"] = str(source_scene_dir)
        scenes.append(scene_data)
    return scenes
