"""Durable, cacheable dataset-package artifacts for background jobs."""

from __future__ import annotations

import hashlib
import os
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterable

from db.storage import load_json, project_dir
from services.archive_io import write_zip_atomic
from services.dataset_package import (
    PACKAGE_EXPORTER_VERSION,
    DatasetPackageCancelled,
    build_dataset_package,
    normalize_package_formats,
)
from services.jobs.store import interprocess_lock, read_json, utc_now, write_json_atomic


ARTIFACT_SCHEMA_VERSION = 1
COPY_BUFFER_BYTES = 4 * 1024 * 1024
DISK_RESERVE_BYTES = 128 * 1024 * 1024
DEFAULT_CACHE_RETENTION_DAYS = 30
DEFAULT_PARTIAL_RETENTION_HOURS = 24


class ArtifactExportCancelled(RuntimeError):
    pass


class InsufficientExportSpace(RuntimeError):
    def __init__(self, *, path: Path, required: int, free: int):
        self.path = path
        self.required = required
        self.free = free
        super().__init__(
            f"Not enough free space for export at {path}: "
            f"required {required} bytes, available {free} bytes"
        )


def dataset_export_root(project_id: str) -> Path:
    path = project_dir(project_id) / "artifacts" / "dataset_exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def dataset_export_cache_key(
    dataset_run_id: str,
    formats: Iterable[str] | None,
) -> str:
    selected = normalize_package_formats(formats)
    identity = "\n".join((dataset_run_id, ",".join(selected), PACKAGE_EXPORTER_VERSION))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def estimate_dataset_export(
    dataset_dir: str | Path,
    formats: Iterable[str] | None,
) -> dict[str, int]:
    """Conservative disk estimate without materializing the package."""

    source = Path(dataset_dir)
    selected = normalize_package_formats(formats)
    image_bytes = 0
    image_count = 0
    for split in ("train", "val", "test"):
        images_dir = source / split / "images"
        if not images_dir.is_dir():
            continue
        for path in images_dir.iterdir():
            if not path.is_file():
                continue
            try:
                image_bytes += path.stat().st_size
                image_count += 1
            except OSError:
                continue

    metadata_bytes = 0
    for path in source.rglob("*"):
        if not path.is_file() or "images" in path.parts:
            continue
        try:
            metadata_bytes += path.stat().st_size
        except OSError:
            continue

    # PNG/JPEG tiles are already compressed and are copied into every selected
    # standard format, so assume close to their source size in the ZIP. The 15%
    # allowance covers generated labels, central-directory records and metadata.
    estimated_archive = int((image_bytes * len(selected) + metadata_bytes) * 1.15)
    estimated_archive = max(1024 * 1024, estimated_archive)
    required_free = estimated_archive + DISK_RESERVE_BYTES
    return {
        "image_count": image_count,
        "source_image_bytes": image_bytes,
        "metadata_bytes": metadata_bytes,
        "estimated_archive_bytes": estimated_archive,
        "required_free_bytes": required_free,
    }


def ensure_export_space(path: Path, required_free_bytes: int) -> int:
    path.mkdir(parents=True, exist_ok=True)
    free = int(shutil.disk_usage(path).free)
    if free < required_free_bytes:
        raise InsufficientExportSpace(path=path, required=required_free_bytes, free=free)
    return free


def create_dataset_export_artifact(
    project_id: str,
    dataset_dir: str | Path,
    *,
    dataset_run_id: str,
    formats: Iterable[str] | None = None,
    output_path: str | Path | None = None,
    should_cancel: Callable[[], bool] | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    selected = normalize_package_formats(formats)
    cancel = should_cancel or (lambda: False)
    report = progress or (lambda _phase, _current, _total: None)
    root = dataset_export_root(project_id)
    cache_key = dataset_export_cache_key(dataset_run_id, selected)
    archive_path = root / f"{cache_key}.zip"
    manifest_path = root / f"{cache_key}.manifest.json"
    lock_path = root / f".{cache_key}.lock"
    estimate = estimate_dataset_export(dataset_dir, selected)
    cache_hit = False
    created_archive = False

    if cancel():
        raise ArtifactExportCancelled("Dataset export cancelled before start")
    report("estimating_disk", 0, 1)

    with interprocess_lock(lock_path, timeout_s=60.0) as acquired:
        if not acquired:
            raise TimeoutError("Timed out waiting for an identical dataset export")
        cached = read_json(manifest_path, default={}) or {}
        cache_hit = bool(
            archive_path.is_file()
            and cached.get("cache_key") == cache_key
            and cached.get("dataset_run_id") == dataset_run_id
            and tuple(cached.get("formats") or ()) == selected
            and cached.get("exporter_version") == PACKAGE_EXPORTER_VERSION
            and int(cached.get("archive_size_bytes") or -1) == archive_path.stat().st_size
        )
        if not cache_hit:
            free_before = ensure_export_space(root, estimate["required_free_bytes"])
            staging = root / f".{cache_key}.{uuid.uuid4().hex}.partial"
            try:
                report("building_package", 0, len(selected) + 1)
                build_dataset_package(
                    project_id,
                    dataset_dir,
                    run_id=dataset_run_id,
                    formats=selected,
                    package_dir=staging,
                    should_cancel=cancel,
                    progress=report,
                )
                if cancel():
                    raise ArtifactExportCancelled("Dataset export cancelled before archiving")
                files = sorted(path for path in staging.rglob("*") if path.is_file())

                def write_entries(archive: zipfile.ZipFile) -> None:
                    total = len(files)
                    for index, file_path in enumerate(files, start=1):
                        if cancel():
                            raise ArtifactExportCancelled("Dataset export cancelled while archiving")
                        archive.write(file_path, file_path.relative_to(staging))
                        report("writing_zip", index, total)

                write_zip_atomic(archive_path, write_entries)
                created_archive = True
                if cancel():
                    raise ArtifactExportCancelled("Dataset export cancelled before publication")
                report("checksumming_archive", 0, 1)
                archive_sha256 = sha256_file(archive_path, should_cancel=cancel)
                archive_size = archive_path.stat().st_size
                artifact_manifest = {
                    "schema_name": "geotile_dataset_export_artifact",
                    "schema_version": ARTIFACT_SCHEMA_VERSION,
                    "cache_key": cache_key,
                    "dataset_run_id": dataset_run_id,
                    "formats": list(selected),
                    "exporter_version": PACKAGE_EXPORTER_VERSION,
                    "created_at": utc_now(),
                    "archive_path": str(archive_path),
                    "archive_size_bytes": archive_size,
                    "archive_sha256": archive_sha256,
                    "checksum_strategy": "bounded_stream_after_atomic_publish",
                    "disk_estimate": estimate,
                    "free_bytes_before": free_before,
                }
                write_json_atomic(manifest_path, artifact_manifest)
                cached = artifact_manifest
            except DatasetPackageCancelled as exc:
                if created_archive:
                    archive_path.unlink(missing_ok=True)
                manifest_path.unlink(missing_ok=True)
                raise ArtifactExportCancelled(str(exc)) from exc
            except ArtifactExportCancelled:
                if created_archive:
                    archive_path.unlink(missing_ok=True)
                manifest_path.unlink(missing_ok=True)
                raise
            except Exception:
                if created_archive:
                    archive_path.unlink(missing_ok=True)
                manifest_path.unlink(missing_ok=True)
                raise
            finally:
                shutil.rmtree(staging, ignore_errors=True)

    if cancel():
        raise ArtifactExportCancelled("Dataset export cancelled")
    destination = None
    if output_path:
        destination = normalize_zip_destination(output_path)
        copy_archive_atomic(archive_path, destination, should_cancel=cancel, progress=report)

    project = load_json(project_id, "project", default={})
    project_name = safe_name(project.get("name") or "dataset")
    filename = f"{project_name}_{dataset_run_id}_{'-'.join(selected)}.zip"
    report("completed", 1, 1)
    return {
        "artifact_type": "dataset_export",
        "downloadable": True,
        "cache_hit": cache_hit,
        "cache_key": cache_key,
        "dataset_run_id": dataset_run_id,
        "formats": list(selected),
        "filename": filename,
        "archive_path": str(archive_path),
        "manifest_path": str(manifest_path),
        "archive_size_bytes": int(cached.get("archive_size_bytes") or archive_path.stat().st_size),
        "archive_sha256": cached.get("archive_sha256"),
        "output_path": str(destination) if destination else None,
        "disk_estimate": estimate,
    }


def normalize_zip_destination(value: str | Path) -> Path:
    destination = Path(value).expanduser()
    if not destination.is_absolute():
        raise ValueError("output_path must be an absolute path")
    if destination.suffix.lower() != ".zip":
        destination = destination.with_suffix(".zip")
    return destination.resolve(strict=False)


def copy_archive_atomic(
    source: Path,
    destination: Path,
    *,
    should_cancel: Callable[[], bool],
    progress: Callable[[str, int, int], None],
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    required = source.stat().st_size + DISK_RESERVE_BYTES
    ensure_export_space(destination.parent, required)
    partial = destination.with_name(f"{destination.name}.partial")
    copied = 0
    total = source.stat().st_size
    try:
        partial.unlink(missing_ok=True)
        with source.open("rb") as input_handle, partial.open("wb") as output_handle:
            while True:
                if should_cancel():
                    raise ArtifactExportCancelled("Dataset export cancelled while copying")
                chunk = input_handle.read(COPY_BUFFER_BYTES)
                if not chunk:
                    break
                output_handle.write(chunk)
                copied += len(chunk)
                progress("copying_archive", copied, total)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)


def sha256_file(
    path: Path,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> str:
    digest = hashlib.sha256()
    cancel = should_cancel or (lambda: False)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(COPY_BUFFER_BYTES), b""):
            if cancel():
                raise ArtifactExportCancelled("Dataset export cancelled while checksumming")
            digest.update(chunk)
    return digest.hexdigest()


def cleanup_export_artifacts(
    project_id: str,
    *,
    cache_retention_days: int = DEFAULT_CACHE_RETENTION_DAYS,
    partial_retention_hours: int = DEFAULT_PARTIAL_RETENTION_HOURS,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Delete only stale app-owned cache artifacts and partial staging entries."""

    root = dataset_export_root(project_id)
    backup_root = project_dir(project_id) / "artifacts" / "project_backups"
    cancel = should_cancel or (lambda: False)
    now = time.time()
    cache_cutoff = now - max(1, cache_retention_days) * 86400
    partial_cutoff = now - max(1, partial_retention_hours) * 3600
    removed_files = 0
    removed_dirs = 0
    reclaimed_bytes = 0

    roots = [root]
    if backup_root.is_dir():
        roots.append(backup_root)
    for artifact_root in roots:
        for path in list(artifact_root.iterdir()):
            if cancel():
                raise ArtifactExportCancelled("Artifact cleanup cancelled")
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            if path.is_dir() and path.name.endswith(".partial") and modified < partial_cutoff:
                reclaimed_bytes += directory_size(path)
                shutil.rmtree(path, ignore_errors=True)
                removed_dirs += 1
                continue
            if not path.is_file():
                continue
            if path.name.endswith(".partial") and modified < partial_cutoff:
                reclaimed_bytes += path.stat().st_size
                path.unlink(missing_ok=True)
                removed_files += 1
                continue
            if path.suffix.lower() == ".zip" and modified < cache_cutoff:
                reclaimed_bytes += path.stat().st_size
                path.unlink(missing_ok=True)
                path.with_suffix(".manifest.json").unlink(missing_ok=True)
                removed_files += 1

    # P2.3 training staging is another rebuildable artifact. Reuse the existing
    # maintenance job and retention knob instead of introducing a second cleanup
    # scheduler/API. Logical hardlink size is reported separately from estimated
    # reclaimable allocation.
    from services.training_dataset import cleanup_training_dataset_cache

    if cancel():
        raise ArtifactExportCancelled("Artifact cleanup cancelled")
    training_cleanup = cleanup_training_dataset_cache(
        project_id,
        retention_days=cache_retention_days,
    )
    removed_dirs += int(training_cleanup["removed_count"])
    reclaimed_bytes += int(training_cleanup["allocated_size_estimate_bytes"])

    return {
        "artifact_type": "artifact_cleanup",
        "downloadable": False,
        "removed_files": removed_files,
        "removed_directories": removed_dirs,
        "reclaimed_bytes": reclaimed_bytes,
        "cache_retention_days": cache_retention_days,
        "partial_retention_hours": partial_retention_hours,
        "training_dataset_cache": training_cleanup,
    }


def directory_size(path: Path) -> int:
    total = 0
    for candidate in path.rglob("*"):
        if candidate.is_file():
            try:
                total += candidate.stat().st_size
            except OSError:
                pass
    return total


def safe_name(value: Any) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in str(value)
    )
    return normalized.strip("_-") or "dataset"
