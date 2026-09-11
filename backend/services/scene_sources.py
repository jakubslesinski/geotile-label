"""Persistence, migration and safe path resolution for scene sources."""

from __future__ import annotations

import hashlib
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.storage import list_scene_ids, load_json, load_scene_json, project_dir, save_json, save_scene_json
from utils.scene_paths import resolve_scene_folder
from db.storage import SCENES_ROOT


SCENE_SOURCES_SCHEMA_VERSION = 1

# Prefiks sciezki rozszerzonej Windows. Bez niego Win32 tnie sciezki na 260 znakach:
# `os.scandir` zwraca jeszcze nazwy, ale `is_file()`, `exists()` i `stat()` na pelnej
# sciezce zawodza z WinError 3 — pliki znikaja z inwentarza CICHO, bez bledu. Realny
# przypadek: dostawa PNEO, w ktorej cztery rastry mialy 282 znaki i resolver proponowal
# do wyboru wylacznie logotypy (jedyne pliki ponizej limitu). Rejestrowe
# `LongPathsEnabled` jest domyslnie wylaczone, wiec nie mozna na nim polegac.
_EXTENDED_PREFIX = "\\\\?\\"                        # \\?\
_EXTENDED_UNC_PREFIX = _EXTENDED_PREFIX + "UNC\\"   # \\?\UNC\


def extended_path(path: Path | str) -> Path:
    """Sciezka w formie rozszerzonej — omija MAX_PATH. Poza Windows bez zmian.

    Stosowana przy WEJSCIU w korzen zrodla: wszystkie sciezki potomne dziedzicza wtedy
    prefiks, a `relative_to(root)` nadal zwraca czyste sciezki wzgledne do manifestow.
    """
    text = str(path)
    if os.name != "nt" or text.startswith(_EXTENDED_PREFIX) or not Path(text).is_absolute():
        return Path(text)
    if text.startswith("\\\\"):
        return Path(_EXTENDED_UNC_PREFIX + text[2:])
    return Path(_EXTENDED_PREFIX + text)


def plain_path(path: Path | str) -> str:
    """Sciezka bez prefiksu rozszerzonego — do ZAPISU i pokazania uzytkownikowi.

    Prefiks jest szczegolem dostepu do systemu plikow, nie czescia tozsamosci sceny.
    W manifestach i w interfejsie ma sie nie pojawiac.
    """
    text = str(path)
    if text.startswith(_EXTENDED_UNC_PREFIX):
        return "\\\\" + text[len(_EXTENDED_UNC_PREFIX):]
    if text.startswith(_EXTENDED_PREFIX):
        return text[len(_EXTENDED_PREFIX):]
    return text


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_source_id(provider: str, root_path: str) -> str:
    seed = f"{provider}:{Path(root_path).expanduser().resolve(strict=False)}:{utc_now()}"
    return f"src_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:12]}"


def canonical_source_root(root_path: str | Path) -> str:
    """Return the comparison key used to prevent duplicate source roots.

    The persisted path remains human-readable; this key only normalises aliases such
    as ``folder/.`` and Windows drive/path casing.
    """

    resolved = Path(root_path).expanduser().resolve(strict=False)
    return os.path.normcase(str(resolved)).casefold()


def load_scene_sources(project_id: str, *, migrate: bool = True) -> dict[str, Any]:
    data = load_json(project_id, "scene_sources", default={})
    if data:
        data.setdefault("schema_name", "geotile_scene_sources")
        data.setdefault("schema_version", SCENE_SOURCES_SCHEMA_VERSION)
        data.setdefault("sources", [])
        for source in data["sources"]:
            source.setdefault(
                "canonical_root",
                canonical_source_root(str(source.get("root_path") or "")),
            )
        return data
    if not migrate:
        return _empty_sources()
    project = load_json(project_id, "project", default={})
    if not project:
        return _empty_sources()
    # NITF (sensor-geometry) projects are folder-based but must NOT be migrated to a
    # generic scene source: the generic pipeline rebuilds their manifests without the
    # GCP-TPS geometry block or NITF metadata (breaks sensor geo and the metadata tab).
    profile = project.get("profile") or {}
    if profile.get("georeferencing") == "SENSOR_GEO" or profile.get("modality") == "AERIAL_EO":
        return _empty_sources()
    scene_folder = project.get("scene_folder")
    if not scene_folder:
        return _empty_sources()
    source = {
        "source_id": new_source_id("generic", scene_folder),
        "provider": "generic",
        "root_path": scene_folder,
        "canonical_root": canonical_source_root(scene_folder),
        "enabled": True,
        "added_at": utc_now(),
        "last_scan_at": None,
        "last_scan_status": "legacy_migrated",
    }
    result = _empty_sources()
    result["sources"] = [source]
    _snapshot_legacy_project(project_id)
    save_scene_sources(project_id, result)
    _migrate_legacy_scene_refs(project_id, source)
    return result


def save_scene_sources(project_id: str, data: dict[str, Any]) -> None:
    payload = {
        "schema_name": "geotile_scene_sources",
        "schema_version": SCENE_SOURCES_SCHEMA_VERSION,
        "sources": data.get("sources") or [],
    }
    save_json(project_id, "scene_sources", payload)


def source_by_id(project_id: str, source_id: str) -> dict[str, Any] | None:
    for source in load_scene_sources(project_id).get("sources", []):
        if source.get("source_id") == source_id:
            return source
    return None


def resolve_source_root(source: dict[str, Any]) -> Path | None:
    """Korzen zrodla w formie rozszerzonej — patrz `extended_path`.

    Prefiks nakladamy PO `resolve()`, bo normalizacja sciezki potrafi go zdjac.
    """
    value = str(source.get("root_path") or "")
    direct = Path(value).expanduser()
    if direct.is_absolute() and direct.is_dir():
        return extended_path(direct.resolve(strict=False))
    folder = resolve_scene_folder(SCENES_ROOT, value)
    return extended_path(folder) if folder is not None else None


def resolve_source_asset(project_id: str, source_id: str, relative_path: str) -> Path | None:
    source = source_by_id(project_id, source_id)
    if not source:
        return None
    root = resolve_source_root(source)
    if root is None:
        return None
    # Bez `resolve()` na kandydacie: normalizacja zdejmuje prefiks rozszerzony i plik
    # powyzej 260 znakow znow staje sie niewidoczny. Ucieczke z korzenia sprawdzamy
    # na skladnikach sciezki, co zalatwia takze `..` w `relative_path`.
    parts = Path(relative_path).parts
    if any(part == ".." for part in parts) or Path(relative_path).is_absolute():
        return None
    candidate = root.joinpath(*parts)
    return candidate if candidate.is_file() else None


def _empty_sources() -> dict[str, Any]:
    return {
        "schema_name": "geotile_scene_sources",
        "schema_version": SCENE_SOURCES_SCHEMA_VERSION,
        "sources": [],
    }


def _snapshot_legacy_project(project_id: str) -> None:
    root = project_dir(project_id)
    backup = root / ".migration_backups" / "scene_sources_v1"
    if backup.exists():
        return
    backup.mkdir(parents=True, exist_ok=True)
    for name in ("project.json", "scenes_index.json"):
        source = root / name
        if source.is_file():
            shutil.copy2(source, backup / name)
    for scene_id in list_scene_ids(project_id):
        source_dir = root / "scenes" / scene_id
        target_dir = backup / "scenes" / scene_id
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in ("scene.json", "scene_manifest.json"):
            source = source_dir / name
            if source.is_file():
                shutil.copy2(source, target_dir / name)


def _migrate_legacy_scene_refs(project_id: str, source: dict[str, Any]) -> None:
    root = resolve_source_root(source)
    if root is None:
        return
    for scene_id in list_scene_ids(project_id):
        scene = load_scene_json(project_id, scene_id, "scene", default={})
        filename = str(scene.get("filename") or "")
        candidate = (root / filename).resolve(strict=False)
        if not filename or not candidate.is_file():
            continue
        relative = candidate.relative_to(root).as_posix()
        scene.update({
            "source_id": source["source_id"],
            "package_id": f"pkg_{hashlib.sha256(f'generic:{relative.casefold()}'.encode()).hexdigest()[:12]}",
            "raster_ref": {"storage": "source", "source_id": source["source_id"], "relative_path": relative},
            "raster_kind": "direct",
            "preparation_status": "ready",
        })
        save_scene_json(project_id, scene_id, "scene", scene)
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        if manifest:
            manifest.setdefault("working_view", {
                "variant_id": "variant_legacy_direct",
                "raster_kind": "direct",
                "raster_ref": scene["raster_ref"],
                "working_grid_uid": None,
                "preparation_status": "ready",
                "locked": False,
                "locked_at": None,
                "lock_reason": None,
                "processing_manifest": None,
            })
            save_scene_json(project_id, scene_id, "scene_manifest", manifest)
