"""Disk-backed, atomic ZIP creation and crash cleanup."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
import zipfile
from pathlib import Path
from typing import Callable

from db.storage import DATA_DIR


ARCHIVE_RUNTIME_DIR = DATA_DIR / "runtime" / "archives"
PARTIAL_REGISTRY_PATH = DATA_DIR / "runtime" / "archive_partials.json"
_REGISTRY_LOCK = threading.Lock()


def temporary_archive_path(prefix: str) -> Path:
    """Allocate an app-owned path for a download archive."""

    ARCHIVE_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    safe_prefix = re.sub(r"[^A-Za-z0-9._-]+", "_", prefix).strip("._-") or "archive"
    return ARCHIVE_RUNTIME_DIR / f"{safe_prefix}_{uuid.uuid4().hex}.zip"


def write_zip_atomic(
    destination: Path,
    write_entries: Callable[[zipfile.ZipFile], None],
) -> Path:
    """Build alongside the destination and publish with one ``os.replace``."""

    destination = destination.resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.partial")
    _register_partial(partial)
    try:
        partial.unlink(missing_ok=True)
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
            write_entries(archive)
        os.replace(partial, destination)
        return destination
    finally:
        partial.unlink(missing_ok=True)
        _unregister_partial(partial)


def remove_temporary_archive(path: str | Path) -> None:
    """Best-effort cleanup used after ``FileResponse`` finishes streaming."""

    candidate = Path(path).resolve(strict=False)
    try:
        candidate.relative_to(ARCHIVE_RUNTIME_DIR.resolve(strict=False))
    except ValueError:
        return
    try:
        candidate.unlink(missing_ok=True)
    except OSError:
        # A short-lived Windows file lock is harmless; startup cleanup will retry.
        pass


def cleanup_partial_archives() -> dict[str, int]:
    """Remove archive fragments recorded before a previous process stopped."""

    removed_partial = 0
    removed_temporary = 0
    with _REGISTRY_LOCK:
        tracked = _read_registry_unlocked()
        remaining: list[str] = []
        for raw_path in tracked:
            candidate = Path(raw_path)
            if candidate.name.endswith(".partial") and candidate.is_file():
                try:
                    candidate.unlink(missing_ok=True)
                    removed_partial += 1
                except OSError:
                    remaining.append(raw_path)
        _write_registry_unlocked(remaining)

    if ARCHIVE_RUNTIME_DIR.is_dir():
        for candidate in ARCHIVE_RUNTIME_DIR.iterdir():
            if not candidate.is_file():
                continue
            if candidate.name.endswith(".partial"):
                try:
                    candidate.unlink(missing_ok=True)
                    removed_partial += 1
                except OSError:
                    pass
            elif candidate.suffix.lower() == ".zip":
                try:
                    candidate.unlink(missing_ok=True)
                    removed_temporary += 1
                except OSError:
                    pass
    return {"partial": removed_partial, "temporary": removed_temporary}


def _register_partial(path: Path) -> None:
    with _REGISTRY_LOCK:
        paths = _read_registry_unlocked()
        resolved = str(path.resolve(strict=False))
        if resolved not in paths:
            paths.append(resolved)
            _write_registry_unlocked(paths)


def _unregister_partial(path: Path) -> None:
    with _REGISTRY_LOCK:
        resolved = str(path.resolve(strict=False))
        paths = [item for item in _read_registry_unlocked() if item != resolved]
        _write_registry_unlocked(paths)


def _read_registry_unlocked() -> list[str]:
    try:
        payload = json.loads(PARTIAL_REGISTRY_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return []
    return [str(item) for item in payload.get("paths", []) if isinstance(item, str)]


def _write_registry_unlocked(paths: list[str]) -> None:
    PARTIAL_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = PARTIAL_REGISTRY_PATH.with_name(f".{PARTIAL_REGISTRY_PATH.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps({"paths": paths}, indent=2), encoding="utf-8")
        os.replace(temp, PARTIAL_REGISTRY_PATH)
    finally:
        temp.unlink(missing_ok=True)
