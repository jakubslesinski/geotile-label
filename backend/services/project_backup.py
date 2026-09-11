"""Disk-streamed project backup artifacts used by HTTP and durable jobs."""

from __future__ import annotations

import json
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from db.storage import list_scene_ids, load_json, load_scene_json, project_dir
from services.archive_io import write_zip_atomic
from services.artifact_exports import DISK_RESERVE_BYTES, InsufficientExportSpace, sha256_file
from services.jobs.store import utc_now, write_json_atomic


BACKUP_FORMAT_VERSION = 1


class ProjectBackupCancelled(RuntimeError):
    pass


def project_backup_root(project_id: str) -> Path:
    path = project_dir(project_id) / "artifacts" / "project_backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_project_backup_artifact(
    project_id: str,
    *,
    artifact_id: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    cancel = should_cancel or (lambda: False)
    report = progress or (lambda _phase, _current, _total: None)
    if cancel():
        raise ProjectBackupCancelled("Project backup cancelled before start")

    project_data = load_json(project_id, "project", default={})
    scene_ids = list_scene_ids(project_id)
    scenes = []
    for scene_id in scene_ids:
        scene_data = load_scene_json(project_id, scene_id, "scene", default={})
        if scene_data:
            scenes.append({"id": scene_id, "filename": scene_data.get("filename", "")})

    root = project_dir(project_id)
    manifest = {
        "format_version": BACKUP_FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
        "project_name": project_data.get("name", "project"),
        "scene_folder": project_data.get("scene_folder", ""),
        "scenes": scenes,
        "tile_catalogs_included": (root / "tile_catalogs" / "index.json").is_file(),
    }
    generated = {
        "backup_manifest.json": manifest,
        "project.json": project_data,
        **{
            f"{name}.json": load_json(project_id, name, default={})
            for name in ("classes", "tiling_config", "dataset_config", "preprocessing_profiles")
        },
    }
    files = backup_source_files(root, scene_ids)
    generated_bytes = sum(len(json_bytes(value)) for value in generated.values())
    source_bytes = sum(path.stat().st_size for path, _arcname in files)
    estimated_archive = max(1024 * 1024, int((generated_bytes + source_bytes) * 1.1))
    backup_root = project_backup_root(project_id)
    free_bytes = int(shutil.disk_usage(backup_root).free)
    required = estimated_archive + DISK_RESERVE_BYTES
    if free_bytes < required:
        raise InsufficientExportSpace(path=backup_root, required=required, free=free_bytes)

    safe_project_name = safe_name(project_data.get("name") or "project")
    identity = safe_name(artifact_id or f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}")
    archive_path = backup_root / f"{identity}.zip"
    artifact_manifest_path = backup_root / f"{identity}.manifest.json"
    total = len(generated) + len(files)

    def write_backup(archive: zipfile.ZipFile) -> None:
        current = 0
        for arcname, value in generated.items():
            if cancel():
                raise ProjectBackupCancelled("Project backup cancelled while writing metadata")
            archive.writestr(arcname, json_bytes(value))
            current += 1
            report("writing_backup", current, total)
        for path, arcname in files:
            if cancel():
                raise ProjectBackupCancelled("Project backup cancelled while archiving")
            archive.write(path, arcname)
            current += 1
            report("writing_backup", current, total)

    try:
        write_zip_atomic(archive_path, write_backup)
        if cancel():
            raise ProjectBackupCancelled("Project backup cancelled before publication")
        report("checksumming_archive", 0, 1)
        digest = sha256_file(archive_path, should_cancel=cancel)
        archive_size = archive_path.stat().st_size
        artifact_manifest = {
            "schema_name": "geotile_project_backup_artifact",
            "schema_version": 1,
            "created_at": utc_now(),
            "project_id": project_id,
            "filename": f"{safe_project_name}_backup.zip",
            "archive_path": str(archive_path),
            "archive_size_bytes": archive_size,
            "archive_sha256": digest,
            "source_bytes": source_bytes,
            "estimated_archive_bytes": estimated_archive,
            "free_bytes_before": free_bytes,
        }
        write_json_atomic(artifact_manifest_path, artifact_manifest)
    except Exception:
        archive_path.unlink(missing_ok=True)
        artifact_manifest_path.unlink(missing_ok=True)
        raise

    report("completed", 1, 1)
    return {
        "artifact_type": "project_backup",
        "downloadable": True,
        "filename": f"{safe_project_name}_backup.zip",
        "archive_path": str(archive_path),
        "manifest_path": str(artifact_manifest_path),
        "archive_size_bytes": archive_size,
        "archive_sha256": digest,
        "source_bytes": source_bytes,
        "estimated_archive_bytes": estimated_archive,
    }


def backup_source_files(root: Path, scene_ids: list[str]) -> list[tuple[Path, str]]:
    result: list[tuple[Path, str]] = []

    def include(path: Path, arcname: str) -> None:
        if path.is_file():
            result.append((path, arcname))

    include(root / "scene_sources.json", "scene_sources.json")
    include(root / "scenes_index.json", "scenes_index.json")
    for scene_id in scene_ids:
        scene_root = root / "scenes" / scene_id
        for filename in ("scene.json", "scene_manifest.json", "annotations.json", "tiles.json"):
            include(scene_root / filename, f"scenes/{scene_id}/{filename}")

    catalogs = root / "tile_catalogs"
    if catalogs.is_dir():
        for path in catalogs.rglob("*"):
            if not path.is_file() or "preview_cache" in path.parts:
                continue
            if path.suffix.lower() in {".json", ".parquet"}:
                result.append((path, path.relative_to(root).as_posix()))

    derived = root / "derived_scenes"
    if derived.is_dir():
        for path in derived.rglob("processing_manifest.json"):
            result.append((path, path.relative_to(root).as_posix()))
    return sorted(result, key=lambda item: item[1])


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def safe_name(value: Any) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in str(value)
    )
    return normalized.strip("_-") or "project"
