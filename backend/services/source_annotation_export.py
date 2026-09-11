"""Export source-scene annotations without requiring tiling or a dataset run."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.storage import list_scene_ids, load_json, load_scene_json
from models.project import APP_VERSION
from services.attribute_engine import recompute_scene_attributes
from services.parquet_io import polygon_wkb, write_geoparquet

SOURCE_ANNOTATION_EXPORT_VERSION = 1


def export_source_annotations_geoparquet(
    project_id: str,
    output_path: str | Path,
) -> dict[str, Any]:
    """Write WGS84/native GeoParquet files and a JSON summary sidecar."""

    wgs84_path, native_path, summary_path = resolve_export_paths(output_path)
    project = load_json(project_id, "project", default={})
    classes = load_json(project_id, "classes", default=[])
    class_names = {
        int(item["id"]): item.get("name")
        for item in classes
        if isinstance(item, dict) and item.get("id") is not None
    }
    scene_cache = build_source_scene_cache(project_id)
    rows = build_source_annotation_rows(scene_cache, class_names)
    wgs84_rows = geospatial_rows(rows, "geometry_wgs84", require_geometry=True)
    native_rows = geospatial_rows(rows, "geometry_native", require_geometry=True)

    write_geoparquet(wgs84_path, wgs84_rows)
    write_geoparquet(native_path, native_rows, crs=None)

    summary = build_annotation_summary(
        project_id,
        project,
        scene_cache,
        rows,
        wgs84_rows,
        native_rows,
        wgs84_path,
        native_path,
        summary_path,
    )
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return summary


def resolve_export_paths(output_path: str | Path) -> tuple[Path, Path, Path]:
    selected = Path(output_path).expanduser()
    if not selected.is_absolute():
        raise ValueError("output_path must be an absolute path")
    if selected.suffix.lower() not in {".parquet", ".geoparquet"}:
        selected = selected.with_suffix(".geoparquet")

    selected.parent.mkdir(parents=True, exist_ok=True)
    stem = selected.stem
    prefix = stem[:-6] if stem.lower().endswith("_wgs84") else stem
    native_path = selected.with_name(f"{prefix}_native.geoparquet")
    summary_name = "annotation_summary.json" if prefix == "annotations" else f"{prefix}_summary.json"
    return selected, native_path, selected.with_name(summary_name)


def build_source_scene_cache(project_id: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for scene_id in list_scene_ids(project_id):
        recompute_scene_attributes(project_id, scene_id)
        annotations = load_scene_json(project_id, scene_id, "annotations", default=[])
        result[scene_id] = {
            "scene": load_scene_json(project_id, scene_id, "scene", default={}),
            "manifest": load_scene_json(project_id, scene_id, "scene_manifest", default={}),
            "annotations": {
                item.get("source_annotation_id") or item.get("id"): item
                for item in annotations
                if isinstance(item, dict) and (item.get("source_annotation_id") or item.get("id"))
            },
        }
    return result


def build_source_annotation_rows(
    scene_cache: dict[str, dict[str, Any]],
    class_names: dict[int, str | None],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scene_id, context in sorted(scene_cache.items()):
        scene = context.get("scene") or {}
        manifest = context.get("manifest") or {}
        native_crs = (manifest.get("geospatial") or {}).get("crs")
        source_filename = scene.get("filename") or manifest.get("filename")
        for source_id, annotation in sorted((context.get("annotations") or {}).items()):
            attributes = annotation.get("attributes") or {}
            geometry = attributes.get("geometry") or {}
            geospatial = attributes.get("geospatial") or {}
            identification = attributes.get("identification") or {}
            scene_metadata = attributes.get("scene_metadata") or {}
            class_id = annotation.get("class_id")
            # Geo provenance — derived here so affine (satellite) attributes stay untouched.
            transform_model = geospatial.get("transform_model")
            if transform_model is None and geospatial.get("available"):
                transform_model = "affine"
            rows.append({
                "source_annotation_id": source_id,
                "source_scene_uid": manifest.get("source_scene_uid"),
                "source_scene_candidate_uid": manifest.get("source_scene_candidate_uid"),
                "scene_id": scene_id,
                "source_filename": source_filename,
                "class_id": class_id,
                "class_name": _class_name(class_id, class_names, identification),
                "geometry_type": annotation.get("geometry_type") or "bbox",
                "bbox_scene_px": annotation.get("bbox") or geometry.get("bbox_scene_px"),
                "polygon_scene_px": annotation.get("polygon_scene_px") or geometry.get("geometry_scene_px"),
                "rotated_bbox": annotation.get("rotated_bbox"),
                "orientation_angle_deg": annotation.get("orientation_angle_deg"),
                "orientation_geo_deg": geospatial.get("orientation_geo_deg"),
                "centroid_lon": geospatial.get("centroid_lon"),
                "centroid_lat": geospatial.get("centroid_lat"),
                "bbox_lonlat": geospatial.get("bbox_lonlat"),
                "area_m2": geospatial.get("area_m2"),
                "major_axis_m": geospatial.get("major_axis_m"),
                "minor_axis_m": geospatial.get("minor_axis_m"),
                "annotation_source": annotation.get("annotation_source") or identification.get("annotation_source"),
                "annotator_email": annotation.get("annotator_email") or identification.get("annotator_email"),
                "created_by": annotation.get("created_by"),
                "updated_by": annotation.get("updated_by"),
                "created_at": annotation.get("created_at"),
                "updated_at": annotation.get("updated_at"),
                "import_id": annotation.get("import_id"),
                "source_package_id": annotation.get("source_package_id"),
                "provider": manifest.get("provider") or scene_metadata.get("provider"),
                "sensor": manifest.get("sensor") or scene_metadata.get("sensor"),
                "modality": manifest.get("modality") or scene_metadata.get("modality"),
                "acquisition_datetime_utc": manifest.get("acquisition_datetime_utc") or scene_metadata.get("acquisition_datetime_utc"),
                "source_identity_status": manifest.get("source_identity_status"),
                "source_identity_method": manifest.get("source_identity_method"),
                "source_identity_strength": manifest.get("source_identity_strength"),
                "source_file_sha256": manifest.get("source_file_sha256"),
                "source_file_content_signature": manifest.get("source_file_content_signature"),
                "has_geo": bool(geospatial.get("available")),
                "georeferencing": manifest.get("georeferencing"),
                "transform_model": transform_model,
                "approximate": bool(geospatial.get("approximate", False)),
                "orthorectified": geospatial.get("orthorectified"),
                "native_crs": native_crs,
                "attribute_status": attributes.get("attribute_status"),
                "attribute_version": attributes.get("attribute_version"),
                "geometry_wgs84": geospatial.get("geometry_wgs84"),
                "geometry_native": geospatial.get("geometry_geo"),
            })
    return rows


def geospatial_rows(
    rows: list[dict[str, Any]],
    geometry_field: str,
    *,
    require_geometry: bool = False,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        geometry = polygon_wkb(row.get(geometry_field))
        if require_geometry and geometry is None:
            continue
        item = {
            key: value
            for key, value in row.items()
            if key not in {"geometry_wgs84", "geometry_native"}
        }
        item["geometry"] = geometry
        result.append(item)
    return result


def build_annotation_summary(
    project_id: str,
    project: dict[str, Any],
    scene_cache: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
    wgs84_rows: list[dict[str, Any]],
    native_rows: list[dict[str, Any]],
    wgs84_path: Path,
    native_path: Path,
    summary_path: Path,
) -> dict[str, Any]:
    class_counts = Counter(str(row.get("class_name") or row.get("class_id")) for row in rows)
    author_counts = Counter(str(row.get("annotator_email") or "unknown") for row in rows)
    geometry_counts = Counter(str(row.get("geometry_type") or "bbox") for row in rows)
    scene_summaries = []
    scenes_without_annotations = []
    for scene_id, context in sorted(scene_cache.items()):
        scene = context.get("scene") or {}
        manifest = context.get("manifest") or {}
        scene_rows = [row for row in rows if row.get("scene_id") == scene_id]
        geo_count = sum(1 for row in scene_rows if row.get("geometry_wgs84"))
        if not scene_rows:
            scenes_without_annotations.append(scene_id)
        scene_summaries.append({
            "scene_id": scene_id,
            "source_scene_uid": manifest.get("source_scene_uid"),
            "source_scene_candidate_uid": manifest.get("source_scene_candidate_uid"),
            "source_identity_method": manifest.get("source_identity_method"),
            "source_identity_strength": manifest.get("source_identity_strength"),
            "source_filename": scene.get("filename") or manifest.get("filename"),
            "annotation_count": len(scene_rows),
            "georeferenced_annotation_count": geo_count,
            "omitted_from_wgs84_count": len(scene_rows) - geo_count,
            "native_crs": (manifest.get("geospatial") or {}).get("crs"),
        })

    return {
        "schema_name": "geotile_source_annotation_summary",
        "schema_version": SOURCE_ANNOTATION_EXPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app_version": APP_VERSION,
        "project_id": project_id,
        "project_name": project.get("name"),
        "project_profile": project.get("profile") or {},
        "scene_count": len(scene_cache),
        "scenes_with_annotations": sum(1 for item in scene_summaries if item["annotation_count"] > 0),
        "scenes_without_annotations": scenes_without_annotations,
        "source_annotation_count": len(rows),
        "wgs84_annotation_count": len(wgs84_rows),
        "native_annotation_count": len(native_rows),
        "omitted_from_wgs84_count": len(rows) - len(wgs84_rows),
        "class_counts": dict(sorted(class_counts.items())),
        "annotator_counts": dict(sorted(author_counts.items())),
        "geometry_type_counts": dict(sorted(geometry_counts.items())),
        "scenes": scene_summaries,
        "files": {
            "annotations_wgs84": str(wgs84_path),
            "annotations_native": str(native_path),
            "annotation_summary": str(summary_path),
        },
    }


def _class_name(
    class_id: Any,
    class_names: dict[int, str | None],
    identification: dict[str, Any],
) -> str | None:
    if class_id is not None:
        try:
            value = class_names.get(int(class_id))
            if value:
                return value
        except (TypeError, ValueError):
            pass
    return identification.get("class_name")
