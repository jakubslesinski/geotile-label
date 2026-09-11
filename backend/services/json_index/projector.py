"""Build JSON read models exclusively from authoritative project documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from db.storage import list_scene_ids, load_scene_json, project_paths
from services.annotation_summary import compute_project_annotation_summary
from services.json_index.schema import (
    ANNOTATION_SUMMARY_SCHEMA,
    INDEX_SCHEMA_VERSION,
    SCENES_INDEX_SCHEMA,
    SCENE_SUMMARY_SCHEMA,
    utc_now,
)
from services.scene_manifest import scene_index_entry


@dataclass(frozen=True)
class ProjectProjection:
    scenes_index: dict[str, Any]
    annotation_summary: dict[str, Any]
    scene_summaries: dict[str, dict[str, Any]]


def _fallback_manifest(scene_id: str, scene: dict[str, Any]) -> dict[str, Any]:
    info = scene.get("scene_info") or {}
    return {
        "scene_id": scene_id,
        "filename": scene.get("filename"),
        "modality": scene.get("modality"),
        "georeferencing": scene.get("georeferencing"),
        "sensor": scene.get("sensor"),
        "provider": scene.get("provider"),
        "acquisition_datetime_utc": scene.get("acquisition_datetime_utc"),
        "metadata_status": scene.get("metadata_status"),
        "image": {
            "width": info.get("width") or scene.get("width"),
            "height": info.get("height") or scene.get("height"),
            "channels": info.get("channels") or scene.get("channels"),
            "dtype": info.get("dtype") or scene.get("dtype"),
        },
        "geospatial": {
            "has_geo": info.get("has_geo") or scene.get("has_geo"),
            "crs": info.get("crs") or scene.get("crs"),
            "bounds_wgs84": info.get("bounds") or scene.get("bbox_lonlat"),
        },
    }


def build_project_projection(project_id: str, source_revision: int) -> ProjectProjection:
    paths = project_paths(project_id)
    entries: list[dict[str, Any]] = []
    for scene_id in list_scene_ids(project_id, paths=paths):
        scene = load_scene_json(project_id, scene_id, "scene", default={}, paths=paths)
        if not isinstance(scene, dict) or not scene:
            continue
        manifest = load_scene_json(
            project_id,
            scene_id,
            "scene_manifest",
            default={},
            paths=paths,
        )
        if not isinstance(manifest, dict) or not manifest:
            manifest = _fallback_manifest(scene_id, scene)
        entry = scene_index_entry(manifest, scene)
        entry["scene_id"] = scene_id
        entry["filename"] = entry.get("filename") or scene.get("filename")
        entries.append(entry)

    entries.sort(key=lambda item: (str(item.get("filename") or "").casefold(), str(item["scene_id"])))
    built_at = utc_now()
    scenes_index = {
        "schema_name": SCENES_INDEX_SCHEMA,
        "schema_version": INDEX_SCHEMA_VERSION,
        "source_revision": int(source_revision),
        "project_id": project_id,
        "built_at": built_at,
        "scene_count": len(entries),
        "scenes": entries,
    }

    full_summary = compute_project_annotation_summary(project_id, use_cache=False)
    annotation_summary = {
        **full_summary,
        "schema_name": ANNOTATION_SUMMARY_SCHEMA,
        "schema_version": INDEX_SCHEMA_VERSION,
        "source_revision": int(source_revision),
        "built_at": built_at,
    }
    per_scene_annotations = {
        str(item.get("scene_id")): item
        for item in full_summary.get("per_scene") or []
        if item.get("scene_id")
    }
    scene_summaries = {
        str(entry["scene_id"]): {
            "schema_name": SCENE_SUMMARY_SCHEMA,
            "schema_version": INDEX_SCHEMA_VERSION,
            "source_revision": int(source_revision),
            "built_at": built_at,
            "project_id": project_id,
            "scene_id": str(entry["scene_id"]),
            "scene": entry,
            "annotations": per_scene_annotations.get(str(entry["scene_id"]), {
                "scene_id": str(entry["scene_id"]),
                "annotation_count": 0,
                "class_ids": [],
                "class_names": [],
                "authors": [],
                "sources": [],
                "import_ids": [],
                "package_ids": [],
            }),
        }
        for entry in entries
    }
    return ProjectProjection(scenes_index, annotation_summary, scene_summaries)
