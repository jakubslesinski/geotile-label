"""Generate geospatial metadata sidecars alongside standard ML exports."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.storage import list_scene_ids, load_json, load_scene_json
from models.project import APP_VERSION
from services.attribute_engine import compute_tile_id, recompute_scene_attributes
from services.attribute_engine.engine import pixel_to_native, transform_points
from services.dataset_audit import generate_dataset_audit
from services.parquet_io import write_geoparquet, write_parquet
from services.sensor_geometry import try_scene_geo_model
from services.source_annotation_export import build_source_annotation_rows, geospatial_rows

SIDECAR_SCHEMA_VERSION = 1
TILE_TPS_DENSIFY_PX = 64.0

TILE_METADATA_FIELDS = [
    "tile_filename",
    "tile_id",
    "scene_id",
    "source_scene_uid",
    "source_scene_candidate_uid",
    "source_identity_method",
    "source_identity_strength",
    "source_scene_filename",
    "split",
    "tile_size",
    "source_x_min_px",
    "source_y_min_px",
    "source_x_max_px",
    "source_y_max_px",
    "tile_wgs84_west",
    "tile_wgs84_south",
    "tile_wgs84_east",
    "tile_wgs84_north",
    "crs",
    "preprocessing_profile",
    "preprocessing_profile_hash",
    "provider",
    "sensor",
    "modality",
    "acquisition_datetime_utc",
    "annotation_count",
    "is_positive",
    "is_negative",
    "review_status",
    "reviewed",
    "exclude_from_dataset",
    "excluded",
]

ANNOTATION_LINK_FIELDS = [
    "tile_annotation_id",
    "source_annotation_id",
    "tile_id",
    "tile_filename",
    "scene_id",
    "source_scene_uid",
    "source_scene_candidate_uid",
    "source_identity_method",
    "source_identity_strength",
    "split",
    "class_id",
    "class_name",
    "bbox_x_min_px",
    "bbox_y_min_px",
    "bbox_x_max_px",
    "bbox_y_max_px",
    "bbox_yolo_xc",
    "bbox_yolo_yc",
    "bbox_yolo_w",
    "bbox_yolo_h",
    "bbox_coco_x",
    "bbox_coco_y",
    "bbox_coco_w",
    "bbox_coco_h",
    "visible_fraction",
    "is_clipped",
    "touches_tile_border",
    "exportable_yolo",
    "exportable_yolo_obb",
    "exportable_coco",
    "geometry_type",
    "geometry_tile_px",
    "obb_tile_px",
    "orientation_angle_deg",
    "front_vector_scene_px",
    "centroid_lon",
    "centroid_lat",
    "area_m2",
    "major_axis_m",
    "minor_axis_m",
    "equivalent_diameter_m",
    "orientation_geo_deg",
    "attribute_status",
    "attribute_version",
    "annotation_source",
    "annotator_email",
]


def generate_export_sidecars(
    project_id: str,
    dataset_dir: str | Path,
    export_format: str | None = None,
    run_id: str | None = None,
    *,
    output_dir: str | Path | None = None,
    persist_run_manifest: bool = True,
) -> dict[str, Any]:
    dataset_path = Path(dataset_dir)
    output_root = Path(output_dir) if output_dir is not None else dataset_path
    metadata_dir = output_root / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)

    project = load_json(project_id, "project", default={})
    run_manifest = read_json_file(dataset_path / "dataset_run_manifest.json", {})
    profile = (run_manifest.get("project_profile") if run_manifest else None) or project.get("profile") or {}
    classes = (run_manifest.get("classes") if run_manifest else None) or load_json(project_id, "classes", default=[])
    class_names = {int(item["id"]): item.get("name") for item in classes if "id" in item}
    tiling_config = (run_manifest.get("tiling_config") if run_manifest else None) or load_json(project_id, "tiling_config", default={})
    dataset_config = (run_manifest.get("dataset_config") if run_manifest else None) or load_json(project_id, "dataset_config", default={})
    tile_size = int(tiling_config.get("tile_size", 640))
    tile_annotations = read_json_file(dataset_path / "tile_annotations.json", None)
    if tile_annotations is None:
        tile_annotations = load_json(project_id, "tile_annotations", default={})
    link_manifest = read_json_file(dataset_path / "tile_annotation_links.json", None)
    if link_manifest is None:
        link_manifest = load_json(project_id, "tile_annotation_links", default={})
    links = link_manifest.get("annotations", []) if isinstance(link_manifest, dict) else []
    split_map = dataset_split_map(dataset_path)

    scene_cache = build_run_scene_cache(dataset_path, run_manifest) if run_manifest else build_scene_cache(project_id)
    preprocessing = run_manifest.get("preprocessing_profile") or {}
    preprocessing_profile_id = (
        preprocessing.get("profile_id")
        or dataset_config.get("preprocessing_profile_id")
        or profile.get("default_preprocessing_profile")
    )
    preprocessing_profile_hash = preprocessing.get("profile_hash")
    tile_rows = build_tile_metadata_rows(
        split_map,
        scene_cache,
        tile_annotations,
        tile_size,
        preprocessing_profile_id,
        preprocessing_profile_hash,
    )
    annotation_rows = build_annotation_link_rows(
        links,
        split_map,
        scene_cache,
        class_names,
    )

    tile_metadata_path = metadata_dir / "tile_metadata.csv"
    annotation_links_path = metadata_dir / "annotation_links.csv"
    write_csv(tile_metadata_path, TILE_METADATA_FIELDS, tile_rows)
    write_csv(annotation_links_path, ANNOTATION_LINK_FIELDS, annotation_rows)
    tile_parquet_path = write_parquet(metadata_dir / "tile_metadata.parquet", tile_rows)
    annotation_parquet_path = write_parquet(
        metadata_dir / "annotation_links.parquet", annotation_rows
    )
    scene_rows = build_scene_rows(scene_cache)
    write_json_file(metadata_dir / "scenes_index.json", {
        "schema_name": "geotile_scenes_index",
        "schema_version": 1,
        "scenes": scene_rows,
    })
    scenes_parquet_path = write_parquet(metadata_dir / "scenes.parquet", scene_rows)
    annotation_geo_rows = build_source_annotation_rows(scene_cache, class_names)
    annotations_wgs84_path = write_geoparquet(
        metadata_dir / "annotations_wgs84.geoparquet",
        geospatial_rows(annotation_geo_rows, "geometry_wgs84", require_geometry=True),
    )
    annotations_native_path = write_geoparquet(
        metadata_dir / "annotations_native.geoparquet",
        geospatial_rows(annotation_geo_rows, "geometry_native", require_geometry=True),
        crs=None,
    )

    validation = validate_sidecars(
        dataset_path,
        split_map,
        tile_rows,
        annotation_rows,
        tile_annotations,
        tile_size,
        dataset_config.get("split_mode", "random_tile"),
        links_available=isinstance(link_manifest, dict) and link_manifest.get("schema_name") is not None,
    )
    dataset_validation = read_json_file(dataset_path / "validation_report.json", {})

    manifest_path = output_root / "geotile_export_manifest.json"
    existing = read_json_file(manifest_path, {})
    export_formats = set(existing.get("export_formats") or [])
    if export_format:
        export_formats.add(export_format)

    manifest = {
        "schema_name": "geotile_export_manifest",
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app_version": APP_VERSION,
        "project_id": project_id,
        "project_name": run_manifest.get("project_name") or project.get("name"),
        "run_id": run_id or run_manifest.get("run_id"),
        "export_formats": sorted(export_formats),
        "project_profile": {
            "modality": profile.get("modality"),
            "georeferencing": profile.get("georeferencing"),
            "sensors": profile.get("sensors") or [],
            "annotation_mode": profile.get("annotation_mode"),
            "default_preprocessing_profile": profile.get("default_preprocessing_profile"),
            "default_split_strategy": profile.get("default_split_strategy"),
        },
        "tiling": {
            "tile_size": tile_size,
            "buffer": tiling_config.get("buffer", 0),
        },
        "dataset": {
            "split_mode": dataset_config.get("split_mode", "random_tile"),
            "split_seed": dataset_config.get("split_seed", 42),
            "block_size_tiles": dataset_config.get("block_size_tiles"),
            "train_ratio": dataset_config.get("train_ratio"),
            "val_ratio": dataset_config.get("val_ratio"),
            "test_ratio": dataset_config.get("test_ratio"),
            "min_box_fraction": dataset_config.get("min_box_fraction"),
            "negative_ratio": dataset_config.get("negative_ratio"),
            "tile_selection": dataset_config.get("tile_selection", "reviewed_sampled"),
            "tile_count": len(tile_rows),
            "tile_annotation_count": len(annotation_rows),
        },
        "preprocessing_profile": preprocessing or {
            "profile_id": preprocessing_profile_id,
            "profile_hash": preprocessing_profile_hash,
        },
        "classes": sorted(classes, key=lambda item: int(item.get("id", 0))),
        "scenes": [scene_export_summary(scene_id, value) for scene_id, value in sorted(scene_cache.items())],
        "sidecars": {
            "tile_metadata": "metadata/tile_metadata.csv",
            "tile_metadata_parquet": "metadata/tile_metadata.parquet",
            "annotation_links": "metadata/annotation_links.csv",
            "annotation_links_parquet": "metadata/annotation_links.parquet",
            "scenes_index": "metadata/scenes_index.json",
            "scenes_parquet": "metadata/scenes.parquet",
            "annotations_wgs84": "metadata/annotations_wgs84.geoparquet",
            "annotations_native": "metadata/annotations_native.geoparquet",
        },
        "validation": validation,
        "dataset_validation": dataset_validation,
    }
    write_json_file(manifest_path, manifest)
    audit = generate_dataset_audit(
        project_id,
        dataset_path,
        persist=True,
        sidecar_dir=output_root,
        persist_dir=output_root,
    )
    manifest["audit_summary"] = audit.get("summary") or {}
    write_json_file(manifest_path, manifest)
    if run_manifest and persist_run_manifest:
        run_manifest["audit_summary"] = audit.get("summary") or {}
        write_json_file(dataset_path / "dataset_run_manifest.json", run_manifest)
    return {
        "manifest_path": str(manifest_path),
        "tile_metadata_path": str(tile_metadata_path),
        "annotation_links_path": str(annotation_links_path),
        "tile_metadata_parquet_path": str(tile_parquet_path),
        "annotation_links_parquet_path": str(annotation_parquet_path),
        "scenes_parquet_path": str(scenes_parquet_path),
        "annotations_wgs84_path": str(annotations_wgs84_path),
        "annotations_native_path": str(annotations_native_path),
        "validation": validation,
        "audit": audit.get("summary") or {},
    }


def dataset_split_map(dataset_dir: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for split in ("train", "val", "test"):
        images_dir = dataset_dir / split / "images"
        if not images_dir.exists():
            continue
        for image_path in images_dir.iterdir():
            if image_path.is_file():
                result[image_path.name] = split
    return result


def build_scene_cache(project_id: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for scene_id in list_scene_ids(project_id):
        recompute_scene_attributes(project_id, scene_id)
        scene = load_scene_json(project_id, scene_id, "scene", default={})
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        tiles = load_scene_json(project_id, scene_id, "tiles", default=[])
        annotations = load_scene_json(project_id, scene_id, "annotations", default=[])
        result[scene_id] = {
            "scene": scene,
            "manifest": manifest,
            "tiles": {item.get("filename"): item for item in tiles if item.get("filename")},
            "annotations": {
                item.get("source_annotation_id") or item.get("id"): item
                for item in annotations
                if item.get("source_annotation_id") or item.get("id")
            },
        }
    return result


def build_run_scene_cache(dataset_dir: Path, run_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    scene_manifests = read_json_file(
        dataset_dir / "dataset_scene_manifests.json",
        read_json_file(dataset_dir / "scene_manifests.json", {}),
    )
    source_annotations = read_json_file(dataset_dir / "source_annotations.json", {})
    tiles = read_json_file(dataset_dir / "tiles.json", [])
    scene_summaries = {
        item.get("scene_id"): item
        for item in run_manifest.get("scenes", [])
        if item.get("scene_id")
    }
    tile_maps: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for tile in tiles:
        scene_id, tile_filename = split_dataset_tile_name(str(tile.get("filename") or ""))
        if scene_id and tile_filename:
            normalized = dict(tile)
            normalized["filename"] = tile_filename
            tile_maps[scene_id][tile_filename] = normalized

    scene_ids = set(scene_manifests) | set(source_annotations) | set(scene_summaries) | set(tile_maps)
    result: dict[str, dict[str, Any]] = {}
    for scene_id in scene_ids:
        annotations = source_annotations.get(scene_id, [])
        result[scene_id] = {
            "scene": scene_summaries.get(scene_id, {}),
            "manifest": scene_manifests.get(scene_id, {}),
            "tiles": tile_maps.get(scene_id, {}),
            "annotations": {
                item.get("source_annotation_id") or item.get("id"): item
                for item in annotations
                if item.get("source_annotation_id") or item.get("id")
            },
        }
    return result


def build_tile_metadata_rows(
    split_map: dict[str, str],
    scene_cache: dict[str, dict[str, Any]],
    tile_annotations: dict[str, list],
    tile_size: int,
    preprocessing_profile: str | None,
    preprocessing_profile_hash: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    geo_model_cache: dict[str, Any] = {}  # one model per scene (TPS owns a transformer)
    for dataset_filename, split in sorted(split_map.items(), key=lambda item: (item[1], item[0])):
        scene_id, tile_filename = split_dataset_tile_name(dataset_filename)
        context = scene_cache.get(scene_id, {})
        scene = context.get("scene") or {}
        manifest = context.get("manifest") or {}
        tile = (context.get("tiles") or {}).get(tile_filename, {})
        x0 = int(tile.get("x0", 0))
        y0 = int(tile.get("y0", 0))
        image = manifest.get("image") or {}
        x1 = min(x0 + tile_size, int(image.get("width") or x0 + tile_size))
        y1 = min(y0 + tile_size, int(image.get("height") or y0 + tile_size))
        if scene_id not in geo_model_cache:
            geo_model_cache[scene_id] = try_scene_geo_model(manifest)
        bounds_wgs84 = tile_bounds_wgs84([x0, y0, x1, y1], manifest, geo_model_cache[scene_id])
        annotations = tile_annotations.get(dataset_filename, [])
        review_status = str(tile.get("review_status") or ("reviewed" if tile.get("reviewed") else "unreviewed"))
        exclude_from_dataset = bool(tile.get("exclude_from_dataset", tile.get("excluded", False)))
        row = {
            "tile_filename": dataset_filename,
            "tile_id": compute_tile_id(scene_id, tile_filename, x0, y0, tile_size),
            "scene_id": scene_id,
            "source_scene_uid": manifest.get("source_scene_uid"),
            "source_scene_candidate_uid": manifest.get("source_scene_candidate_uid"),
            "source_identity_method": manifest.get("source_identity_method"),
            "source_identity_strength": manifest.get("source_identity_strength"),
            "source_scene_filename": scene.get("filename") or manifest.get("filename"),
            "split": split,
            "tile_size": tile_size,
            "source_x_min_px": x0,
            "source_y_min_px": y0,
            "source_x_max_px": x1,
            "source_y_max_px": y1,
            "tile_wgs84_west": bounds_wgs84[0] if bounds_wgs84 else None,
            "tile_wgs84_south": bounds_wgs84[1] if bounds_wgs84 else None,
            "tile_wgs84_east": bounds_wgs84[2] if bounds_wgs84 else None,
            "tile_wgs84_north": bounds_wgs84[3] if bounds_wgs84 else None,
            "crs": (manifest.get("geospatial") or {}).get("crs"),
            "preprocessing_profile": preprocessing_profile,
            "preprocessing_profile_hash": preprocessing_profile_hash,
            "provider": manifest.get("provider"),
            "sensor": manifest.get("sensor"),
            "modality": manifest.get("modality"),
            "acquisition_datetime_utc": manifest.get("acquisition_datetime_utc"),
            "annotation_count": len(annotations),
            "is_positive": bool(annotations),
            "is_negative": not bool(annotations),
            "review_status": review_status,
            "reviewed": review_status == "reviewed",
            "exclude_from_dataset": exclude_from_dataset,
            "excluded": exclude_from_dataset,
        }
        rows.append(row)
    return rows


def build_annotation_link_rows(
    links: list[dict[str, Any]],
    split_map: dict[str, str],
    scene_cache: dict[str, dict[str, Any]],
    class_names: dict[int, str | None],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for link in links:
        dataset_filename = link.get("dataset_tile_filename")
        split = split_map.get(dataset_filename)
        if not dataset_filename or not split:
            continue
        scene_id = link.get("scene_id")
        scene_manifest = ((scene_cache.get(scene_id) or {}).get("manifest") or {})
        source_id = link.get("source_annotation_id")
        source = ((scene_cache.get(scene_id) or {}).get("annotations") or {}).get(source_id, {})
        attributes = source.get("attributes") or {}
        geospatial = attributes.get("geospatial") or {}
        identification = attributes.get("identification") or {}
        bbox = padded_values(link.get("bbox_tile_px"), 4)
        yolo = padded_values(link.get("bbox_yolo_norm"), 4)
        coco = padded_values(link.get("bbox_coco_xywh"), 4)
        class_id = link.get("class_id")
        rows.append({
            "tile_annotation_id": link.get("tile_annotation_id"),
            "source_annotation_id": source_id,
            "tile_id": link.get("tile_id"),
            "tile_filename": dataset_filename,
            "scene_id": scene_id,
            "source_scene_uid": scene_manifest.get("source_scene_uid"),
            "source_scene_candidate_uid": scene_manifest.get("source_scene_candidate_uid"),
            "source_identity_method": scene_manifest.get("source_identity_method"),
            "source_identity_strength": scene_manifest.get("source_identity_strength"),
            "split": split,
            "class_id": class_id,
            "class_name": class_names.get(int(class_id)) if class_id is not None else None,
            "bbox_x_min_px": bbox[0],
            "bbox_y_min_px": bbox[1],
            "bbox_x_max_px": bbox[2],
            "bbox_y_max_px": bbox[3],
            "bbox_yolo_xc": yolo[0],
            "bbox_yolo_yc": yolo[1],
            "bbox_yolo_w": yolo[2],
            "bbox_yolo_h": yolo[3],
            "bbox_coco_x": coco[0],
            "bbox_coco_y": coco[1],
            "bbox_coco_w": coco[2],
            "bbox_coco_h": coco[3],
            "visible_fraction": link.get("visible_fraction"),
            "is_clipped": link.get("is_clipped"),
            "touches_tile_border": link.get("touches_tile_border"),
            "exportable_yolo": link.get("exportable_yolo"),
            "exportable_yolo_obb": link.get("exportable_yolo_obb"),
            "exportable_coco": link.get("exportable_coco"),
            "geometry_type": link.get("geometry_type") or source.get("geometry_type") or "bbox",
            "geometry_tile_px": link.get("geometry_tile_px"),
            "obb_tile_px": link.get("obb_tile_px"),
            "orientation_angle_deg": link.get("orientation_angle_deg") or source.get("orientation_angle_deg"),
            "front_vector_scene_px": link.get("front_vector_scene_px") or source.get("front_vector_scene_px"),
            "centroid_lon": geospatial.get("centroid_lon"),
            "centroid_lat": geospatial.get("centroid_lat"),
            "area_m2": geospatial.get("area_m2"),
            "major_axis_m": geospatial.get("major_axis_m"),
            "minor_axis_m": geospatial.get("minor_axis_m"),
            "equivalent_diameter_m": geospatial.get("equivalent_diameter_m"),
            "orientation_geo_deg": geospatial.get("orientation_geo_deg"),
            "attribute_status": attributes.get("attribute_status"),
            "attribute_version": attributes.get("attribute_version"),
            "annotation_source": identification.get("annotation_source") or source.get("annotation_source"),
            "annotator_email": identification.get("annotator_email") or source.get("annotator_email"),
        })
    return rows


def build_scene_rows(scene_cache: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for scene_id, context in sorted(scene_cache.items()):
        scene = context.get("scene") or {}
        manifest = context.get("manifest") or {}
        image = manifest.get("image") or {}
        geospatial = manifest.get("geospatial") or {}
        geometry = manifest.get("geometry") or {}
        transform_model = geometry.get("model") or ("affine" if geospatial.get("has_geo") else None)
        rows.append({
            "scene_id": scene_id,
            "source_scene_uid": manifest.get("source_scene_uid"),
            "source_scene_candidate_uid": manifest.get("source_scene_candidate_uid"),
            "source_identity_method": manifest.get("source_identity_method"),
            "source_identity_strength": manifest.get("source_identity_strength"),
            "source_file_sha256": manifest.get("source_file_sha256"),
            "filename": scene.get("filename") or manifest.get("filename"),
            "provider": manifest.get("provider"),
            "sensor": manifest.get("sensor"),
            "modality": manifest.get("modality"),
            "georeferencing": manifest.get("georeferencing"),
            "acquisition_datetime_utc": manifest.get("acquisition_datetime_utc"),
            # Airborne NITF provenance (None for other scenes) — dataset-queryable.
            "display_name": manifest.get("display_name"),
            "production_datetime_utc": manifest.get("production_datetime_utc"),
            "mission_id": manifest.get("mission_id"),
            "target_area_id": manifest.get("target_area_id"),
            "platform_altitude_m": manifest.get("platform_altitude_m"),
            "focal_length_mm": manifest.get("focal_length_mm"),
            "gsd_m": manifest.get("gsd_m") or (manifest.get("eo") or {}).get("gsd_m"),
            "gsd_approximate": manifest.get("gsd_approximate"),
            "width": image.get("width"),
            "height": image.get("height"),
            "bands": image.get("bands"),
            "dtype": image.get("dtype"),
            "has_geo": geospatial.get("has_geo", False) or geometry.get("model") == "gcp_tps",
            "crs": geospatial.get("crs") or (geometry.get("gcp_crs") if geometry.get("model") == "gcp_tps" else None),
            "transform_model": transform_model,
            "approximate": bool(geometry.get("approximate", False)),
            "orthorectified": geometry.get("orthorectified"),
            "transform": geospatial.get("transform"),
            "bounds": geospatial.get("bounds"),
            "annotation_count": len(context.get("annotations") or {}),
            "tile_count": len(context.get("tiles") or {}),
        })
    return rows


def validate_sidecars(
    dataset_dir: Path,
    split_map: dict[str, str],
    tile_rows: list[dict[str, Any]],
    annotation_rows: list[dict[str, Any]],
    tile_annotations: dict[str, list],
    tile_size: int,
    split_mode: str,
    links_available: bool,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not links_available:
        errors.append("tile_annotation_links.json is missing; regenerate the dataset to restore annotation provenance")
    if len(tile_rows) != len(split_map):
        errors.append("tile_metadata.csv does not contain exactly one row per dataset image")

    for row in annotation_rows:
        if not row.get("source_annotation_id"):
            errors.append(f"Missing source_annotation_id for {row.get('tile_annotation_id')}")
        yolo = [row.get(key) for key in ("bbox_yolo_xc", "bbox_yolo_yc", "bbox_yolo_w", "bbox_yolo_h")]
        if row.get("exportable_yolo") and any(value is None or float(value) < 0 or float(value) > 1 for value in yolo):
            errors.append(f"YOLO bbox outside [0,1] for {row.get('tile_annotation_id')}")
        coco = [row.get(key) for key in ("bbox_coco_x", "bbox_coco_y", "bbox_coco_w", "bbox_coco_h")]
        if row.get("exportable_coco") and not coco_bbox_is_valid(coco, tile_size):
            errors.append(f"COCO bbox outside tile bounds for {row.get('tile_annotation_id')}")

    source_splits: dict[str, set[str]] = defaultdict(set)
    for row in annotation_rows:
        if row.get("source_annotation_id") and row.get("split") and row.get("exportable_yolo"):
            source_splits[str(row["source_annotation_id"])].add(str(row["split"]))
    leaking_sources = sorted(source_id for source_id, splits in source_splits.items() if len(splits) > 1)
    if leaking_sources:
        message = f"{len(leaking_sources)} source annotations occur in more than one split"
        if split_mode == "random_tile":
            warnings.append(message)
        else:
            errors.append(message)

    empty_label_errors = validate_empty_yolo_labels(dataset_dir, split_map, tile_annotations)
    errors.extend(empty_label_errors)
    unique_errors = sorted(set(errors))
    unique_warnings = sorted(set(warnings))
    return {
        "status": "error" if unique_errors else "warning" if unique_warnings else "ok",
        "errors": unique_errors,
        "warnings": unique_warnings,
        "tile_count": len(tile_rows),
        "annotation_link_count": len(annotation_rows),
        "cross_split_source_annotation_count": len(leaking_sources),
    }


def validate_empty_yolo_labels(
    dataset_dir: Path,
    split_map: dict[str, str],
    tile_annotations: dict[str, list],
) -> list[str]:
    errors: list[str] = []
    for filename, split in split_map.items():
        if tile_annotations.get(filename):
            continue
        label_path = dataset_dir / split / "labels" / f"{Path(filename).stem}.txt"
        if not label_path.exists():
            errors.append(f"Missing empty YOLO label for negative tile {filename}")
        elif label_path.read_text(encoding="utf-8").strip():
            errors.append(f"Negative tile has a non-empty YOLO label: {filename}")
    return errors


def tile_bounds_wgs84(
    bounds_px: list[int],
    manifest: dict[str, Any],
    geo_model: Any = None,
) -> list[float] | None:
    x0, y0, x1, y1 = bounds_px

    # Sensor-geometry scenes (NITF): non-linear GCP TPS with edge densification.
    if (manifest.get("geometry") or {}).get("model") == "gcp_tps":
        model = geo_model or try_scene_geo_model(manifest)
        if model is None:
            return None
        try:
            corners = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
            wgs84 = model.pixel_to_wgs84(corners, densify_px=TILE_TPS_DENSIFY_PX)
            return [
                min(point[0] for point in wgs84),
                min(point[1] for point in wgs84),
                max(point[0] for point in wgs84),
                max(point[1] for point in wgs84),
            ]
        except Exception:
            return None

    # --- affine scenes (satellite): unchanged ---
    geospatial = manifest.get("geospatial") or {}
    transform_values = geospatial.get("transform")
    crs = geospatial.get("crs")
    if not geospatial.get("has_geo") or not transform_values or not crs:
        return None
    polygon_px = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    try:
        native = [pixel_to_native(point, transform_values) for point in polygon_px]
        wgs84 = transform_points(native, crs, "EPSG:4326")
        return [
            min(point[0] for point in wgs84),
            min(point[1] for point in wgs84),
            max(point[0] for point in wgs84),
            max(point[1] for point in wgs84),
        ]
    except Exception:
        return None


def scene_export_summary(scene_id: str, context: dict[str, Any]) -> dict[str, Any]:
    scene = context.get("scene") or {}
    manifest = context.get("manifest") or {}
    geospatial = manifest.get("geospatial") or {}
    geometry = manifest.get("geometry") or {}
    return {
        "scene_id": scene_id,
        "source_scene_uid": manifest.get("source_scene_uid"),
        "filename": scene.get("filename") or manifest.get("filename"),
        "provider": manifest.get("provider"),
        "sensor": manifest.get("sensor"),
        "modality": manifest.get("modality"),
        "georeferencing": manifest.get("georeferencing"),
        "transform_model": geometry.get("model") or ("affine" if geospatial.get("has_geo") else None),
        "approximate": bool(geometry.get("approximate", False)),
        "acquisition_datetime_utc": manifest.get("acquisition_datetime_utc"),
        "crs": geospatial.get("crs") or (geometry.get("gcp_crs") if geometry.get("model") == "gcp_tps" else None),
    }


def split_dataset_tile_name(filename: str) -> tuple[str, str]:
    if "__" not in filename:
        return "", filename
    return tuple(filename.split("__", 1))


def padded_values(value: Any, length: int) -> list[Any]:
    result = list(value) if isinstance(value, (list, tuple)) else []
    return (result + [None] * length)[:length]


def coco_bbox_is_valid(values: list[Any], tile_size: int) -> bool:
    if any(value is None for value in values):
        return False
    x, y, width, height = [float(value) for value in values]
    return x >= 0 and y >= 0 and width > 0 and height > 0 and x + width <= tile_size + 1e-6 and y + height <= tile_size + 1e-6


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json_file(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, default=str)


def read_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)
