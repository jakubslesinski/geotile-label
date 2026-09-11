"""Sanitized project diagnostics for support packages."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.storage import APP_VERSION, SCHEMA_VERSION, list_project_ids, list_scene_ids, load_json, load_scene_json, project_dir
from services.annotation_package import ANNOTATION_PACKAGE_SCHEMA_VERSION
from services.dataset_runs import DATASET_RUN_SCHEMA_VERSION
from services.scene_manifest import SCENE_MANIFEST_VERSION
from services.tile_catalog import TILE_CATALOG_SCHEMA_VERSION, legacy_cache_info

SENSITIVE_GEOMETRY_KEYS = {
    "attributes",
    "bbox",
    "bbox_lonlat",
    "bbox_scene_px",
    "bbox_tile_px",
    "bbox_yolo_norm",
    "front_edge_scene_px",
    "front_vector_scene_px",
    "geometry",
    "geometry_native",
    "geometry_scene_px",
    "geometry_tile_px",
    "geometry_wgs84",
    "obb_tile_px",
    "polygon_scene_px",
    "rotated_bbox",
}


def build_project_diagnostics() -> dict[str, Any]:
    projects = []
    for project_id in list_project_ids():
        try:
            projects.append(project_diagnostics(project_id))
        except Exception as exc:
            projects.append({"project_id": project_id, "status": "error", "error": str(exc)})
    return {
        "schema_name": "geotile_support_project_diagnostics",
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app_version": APP_VERSION,
        "supported_schema_versions": {
            "project": SCHEMA_VERSION,
            "scene_manifest": SCENE_MANIFEST_VERSION,
            "annotation_package": ANNOTATION_PACKAGE_SCHEMA_VERSION,
            "tile_catalog": TILE_CATALOG_SCHEMA_VERSION,
            "dataset_run": DATASET_RUN_SCHEMA_VERSION,
        },
        "project_count": len(projects),
        "projects": projects,
        "privacy": {
            "source_scenes_included": False,
            "annotations_included": False,
            "full_geometries_included": False,
        },
    }


def project_diagnostics(project_id: str) -> dict[str, Any]:
    project = load_json(project_id, "project", default={})
    root = project_dir(project_id)
    scene_ids = list_scene_ids(project_id)
    identity_issues: list[dict[str, Any]] = []
    identity_counts: Counter[str] = Counter()
    uid_to_scenes: dict[str, list[str]] = {}
    scene_manifest_versions: Counter[str] = Counter()

    for scene_id in scene_ids:
        scene = load_scene_json(project_id, scene_id, "scene", default={})
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        status = str(manifest.get("source_identity_status") or "missing")
        identity_counts[status] += 1
        version = manifest.get("schema_version")
        scene_manifest_versions[str(version if version is not None else "missing")] += 1
        uid = manifest.get("source_scene_uid")
        if uid:
            uid_to_scenes.setdefault(str(uid), []).append(scene_id)
        if not uid or status != "complete":
            identity_issues.append({
                "code": "incomplete_source_identity",
                "scene_id": scene_id,
                "filename": scene.get("filename"),
                "status": status,
                "error": manifest.get("source_identity_error"),
            })
        if manifest and manifest.get("scene_id") not in (None, scene_id):
            identity_issues.append({
                "code": "scene_id_mismatch",
                "scene_id": scene_id,
                "manifest_scene_id": manifest.get("scene_id"),
                "filename": scene.get("filename"),
            })
    for uid, duplicates in uid_to_scenes.items():
        if len(duplicates) > 1:
            identity_issues.append({"code": "duplicate_source_scene_uid", "source_scene_uid": uid, "scene_ids": duplicates})

    migration_state = read_json(root / ".migration_state.json", {})
    tile_catalog_versions = manifest_versions(root / "tile_catalogs", "tile_catalog_manifest.json")
    dataset_run_versions = manifest_versions(root / "dataset_runs", "dataset_run_manifest.json")
    return {
        "project_id": project_id,
        "name": project.get("name"),
        "status": "ok" if not identity_issues else "warning",
        "project_schema_version": project.get("schema_version"),
        "project_app_version": project.get("app_version"),
        "scene_count": len(scene_ids),
        "identity_status_counts": dict(sorted(identity_counts.items())),
        "identity_issue_count": len(identity_issues),
        "identity_issues": identity_issues,
        "schema_versions": {
            "scene_manifests": dict(sorted(scene_manifest_versions.items())),
            "tile_catalogs": tile_catalog_versions,
            "dataset_runs": dataset_run_versions,
        },
        "migration": sanitize_json(migration_state),
        "tile_storage": {
            "legacy": legacy_cache_info(project_id),
            "preview_cache": directory_stats(root / "tile_catalogs", directory_name="preview_cache"),
            "geo_tile_cache": directory_stats(root, directory_name="geo_tile_cache"),
        },
        "latest_annotation_import": latest_sanitized_import_report(root),
    }


def latest_sanitized_import_report(root: Path) -> dict[str, Any] | None:
    imports_root = root / "annotation_imports"
    if not imports_root.is_dir():
        return None
    reports = sorted(imports_root.glob("*/import_report.json"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    if not reports:
        return None
    report = read_json(reports[0], {})
    sanitized = sanitize_json(report)
    if isinstance(sanitized, dict):
        sanitized["report_id"] = reports[0].parent.name
    return sanitized


def sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: sanitize_json(item)
            for key, item in value.items()
            if key not in SENSITIVE_GEOMETRY_KEYS and not key.startswith("_")
        }
    if isinstance(value, list):
        return [sanitize_json(item) for item in value]
    return value


def manifest_versions(root: Path, filename: str) -> dict[str, int]:
    versions: Counter[str] = Counter()
    if root.is_dir():
        for path in root.glob(f"*/{filename}"):
            manifest = read_json(path, {})
            version = manifest.get("schema_version")
            versions[str(version if version is not None else "missing")] += 1
    return dict(sorted(versions.items()))


def directory_stats(root: Path, *, directory_name: str) -> dict[str, int]:
    files: list[Path] = []
    if root.is_dir():
        for directory in root.rglob(directory_name):
            if directory.is_dir():
                files.extend(path for path in directory.rglob("*") if path.is_file())
    return {"file_count": len(files), "bytes": sum(path.stat().st_size for path in files)}


def read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
