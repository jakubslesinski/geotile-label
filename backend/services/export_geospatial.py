"""Spatial export of source annotations to GeoJSON / GeoPackage (EPSG:4326).

For airborne NITF (SENSOR_GEO) scenes the polygon is the densified GCP-TPS
footprint of the annotation (computed in the attribute engine); every feature is
explicitly flagged ``approximate=true`` / ``orthorectified=false`` with its
``transform_model``. Classification/distribution markings are deliberately NOT
emitted here (spec §7 — no leaking in an unauthorized export).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.storage import load_json, project_exists
from models.project import APP_VERSION
from services.source_annotation_export import (
    build_source_annotation_rows,
    build_source_scene_cache,
)

GEOSPATIAL_EXPORT_VERSION = 1
CRS84_URN = "urn:ogc:def:crs:OGC:1.3:CRS84"

# Feature property whitelist — geo/provenance only, never classification markings.
FEATURE_PROPERTIES = [
    "source_annotation_id",
    "scene_id",
    "source_scene_uid",
    "source_filename",
    "class_id",
    "class_name",
    "geometry_type",
    "orientation_geo_deg",
    "centroid_lon",
    "centroid_lat",
    "area_m2",
    "major_axis_m",
    "minor_axis_m",
    "transform_model",
    "approximate",
    "orthorectified",
    "georeferencing",
    "modality",
    "sensor",
    "provider",
    "acquisition_datetime_utc",
    "annotation_source",
    "annotator_email",
]


def build_feature_collection(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a GeoJSON FeatureCollection (WGS84) from source-annotation rows."""
    features: list[dict[str, Any]] = []
    for row in rows:
        geometry = row.get("geometry_wgs84")
        if not geometry:
            continue
        features.append({
            "type": "Feature",
            "geometry": geometry,  # attribute engine already returns a GeoJSON Polygon
            "properties": {key: row.get(key) for key in FEATURE_PROPERTIES},
        })
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": CRS84_URN}},
        "features": features,
    }


def write_geojson(path: Path, feature_collection: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(feature_collection, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return path


def write_geopackage(path: Path, rows: list[dict[str, Any]], layer_name: str = "annotations") -> Path | None:
    """Write a GeoPackage layer via OGR; returns None if OGR is unavailable."""
    try:
        from osgeo import ogr, osr
    except Exception:  # noqa: BLE001 — OGR optional; GeoJSON remains the primary product
        return None

    path.parent.mkdir(parents=True, exist_ok=True)
    driver = ogr.GetDriverByName("GPKG")
    if driver is None:
        return None
    if path.exists():
        driver.DeleteDataSource(str(path))

    datasource = driver.CreateDataSource(str(path))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    layer = datasource.CreateLayer(layer_name, srs, ogr.wkbPolygon)

    string_fields = {
        "source_annotation_id", "scene_id", "source_scene_uid", "source_filename",
        "class_name", "geometry_type", "transform_model", "georeferencing",
        "modality", "sensor", "provider", "acquisition_datetime_utc",
        "annotation_source", "annotator_email",
    }
    real_fields = {
        "orientation_geo_deg", "centroid_lon", "centroid_lat",
        "area_m2", "major_axis_m", "minor_axis_m",
    }
    bool_fields = {"approximate", "orthorectified"}
    for name in FEATURE_PROPERTIES:
        if name in real_fields:
            layer.CreateField(ogr.FieldDefn(name, ogr.OFTReal))
        elif name in bool_fields or name == "class_id":
            layer.CreateField(ogr.FieldDefn(name, ogr.OFTInteger))
        elif name in string_fields:
            layer.CreateField(ogr.FieldDefn(name, ogr.OFTString))
        else:
            layer.CreateField(ogr.FieldDefn(name, ogr.OFTString))

    layer_defn = layer.GetLayerDefn()
    for row in rows:
        geometry = row.get("geometry_wgs84")
        if not geometry:
            continue
        feature = ogr.Feature(layer_defn)
        feature.SetGeometry(ogr.CreateGeometryFromJson(json.dumps(geometry)))
        for name in FEATURE_PROPERTIES:
            value = row.get(name)
            if value is None:
                continue
            if name in bool_fields:
                feature.SetField(name, int(bool(value)))
            elif name in real_fields:
                feature.SetField(name, float(value))
            elif name == "class_id":
                try:
                    feature.SetField(name, int(value))
                except (TypeError, ValueError):
                    pass
            else:
                feature.SetField(name, str(value))
        layer.CreateFeature(feature)
        feature = None
    datasource = None
    return path


def resolve_geospatial_paths(output_path: str | Path) -> tuple[Path, Path, Path]:
    selected = Path(output_path).expanduser()
    if not selected.is_absolute():
        raise ValueError("output_path must be an absolute path")
    if selected.suffix.lower() not in {".geojson", ".json"}:
        selected = selected.with_suffix(".geojson")
    selected.parent.mkdir(parents=True, exist_ok=True)
    stem = selected.stem
    gpkg_path = selected.with_name(f"{stem}.gpkg")
    summary_name = "annotation_geospatial_summary.json" if stem == "annotations" else f"{stem}_summary.json"
    return selected, gpkg_path, selected.with_name(summary_name)


def export_source_annotations_geospatial(
    project_id: str,
    output_path: str | Path,
) -> dict[str, Any]:
    """Write GeoJSON + GeoPackage of source annotations with a JSON summary."""
    if not project_exists(project_id):
        raise ValueError("Project not found")

    geojson_path, gpkg_path, summary_path = resolve_geospatial_paths(output_path)
    project = load_json(project_id, "project", default={})
    classes = load_json(project_id, "classes", default=[])
    class_names = {
        int(item["id"]): item.get("name")
        for item in classes
        if isinstance(item, dict) and item.get("id") is not None
    }
    scene_cache = build_source_scene_cache(project_id)
    rows = build_source_annotation_rows(scene_cache, class_names)

    feature_collection = build_feature_collection(rows)
    write_geojson(geojson_path, feature_collection)
    written_gpkg = write_geopackage(gpkg_path, rows)

    geo_count = len(feature_collection["features"])
    approximate_count = sum(1 for f in feature_collection["features"] if f["properties"].get("approximate"))
    summary = {
        "schema_name": "geotile_annotation_geospatial_summary",
        "schema_version": GEOSPATIAL_EXPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app_version": APP_VERSION,
        "project_id": project_id,
        "project_name": project.get("name"),
        "crs": "EPSG:4326",
        "source_annotation_count": len(rows),
        "exported_feature_count": geo_count,
        "omitted_without_geometry": len(rows) - geo_count,
        "approximate_feature_count": approximate_count,
        "transform_models": sorted(
            {str(f["properties"].get("transform_model")) for f in feature_collection["features"]}
        ),
        "files": {
            "geojson": str(geojson_path),
            "geopackage": str(written_gpkg) if written_gpkg else None,
            "summary": str(summary_path),
        },
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return summary
