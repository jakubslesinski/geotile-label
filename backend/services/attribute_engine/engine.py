"""Deterministic geometry and geospatial attributes for annotations."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

import os
from pathlib import Path

from db.storage import (
    list_scene_ids,
    load_json,
    load_scene_json,
    mutate_scene_json,
    project_paths,
)

ATTRIBUTE_VERSION = 2
EARTH_RADIUS_M = 6_371_008.8
OBJECT_TPS_DENSIFY_PX = 32.0


def compute_source_attributes(
    annotation: dict[str, Any],
    scene_manifest: dict[str, Any] | None,
    class_name: str | None = None,
) -> dict[str, Any]:
    manifest = scene_manifest or {}
    errors: list[str] = []
    warnings: list[str] = []
    computed_at = datetime.now(timezone.utc).isoformat()

    try:
        bbox = _validated_bbox(annotation.get("bbox"))
        polygon = annotation_polygon_scene_px(annotation, bbox)
        centroid = polygon_centroid(polygon)
        bbox_width = bbox[2] - bbox[0]
        bbox_height = bbox[3] - bbox[1]
        edge_lengths_px = polygon_edge_lengths(polygon)
        major_axis_px = max(edge_lengths_px) if edge_lengths_px else max(bbox_width, bbox_height)
        minor_axis_px = min((value for value in edge_lengths_px if value > 0), default=min(bbox_width, bbox_height))
        aspect_ratio_px = major_axis_px / minor_axis_px if minor_axis_px > 0 else None

        orientation_px, orientation_source = _pixel_orientation(annotation)
        geospatial = compute_geospatial_attributes(
            polygon,
            centroid,
            annotation,
            manifest,
            errors,
            warnings,
        )
        has_geo = bool((manifest.get("geospatial") or {}).get("has_geo"))
        status = "partial" if has_geo and not geospatial.get("available") else "computed"

        attributes = {
            "attribute_version": ATTRIBUTE_VERSION,
            "attribute_status": status,
            "attribute_computed_at": computed_at,
            "attribute_errors": errors,
            "attribute_warnings": warnings,
            "attribute_input_hash": attribute_input_hash(annotation, manifest, class_name),
            "identification": {
                "source_annotation_id": annotation.get("source_annotation_id") or annotation.get("id"),
                "scene_id": annotation.get("scene_id") or manifest.get("scene_id"),
                "source_scene_uid": manifest.get("source_scene_uid"),
                "class_id": annotation.get("class_id"),
                "class_name": class_name,
                "annotation_source": annotation.get("annotation_source") or "manual",
                "annotator_email": annotation.get("annotator_email"),
            },
            "geometry": {
                "geometry_type": annotation.get("geometry_type") or "bbox",
                "bbox_scene_px": bbox,
                "centroid_scene_px": centroid,
                "bbox_width_px": bbox_width,
                "bbox_height_px": bbox_height,
                "bbox_area_px": bbox_width * bbox_height,
                "geometry_area_px": abs(polygon_area(polygon)),
                "aspect_ratio_px": aspect_ratio_px,
                "major_axis_px": major_axis_px,
                "minor_axis_px": minor_axis_px,
                "geometry_scene_px": polygon,
            },
            "orientation": {
                "orientation_px_deg": orientation_px,
                "orientation_source": orientation_source,
                "front_vector_scene_px": annotation.get("front_vector_scene_px"),
            },
            "geospatial": geospatial,
            "scene_metadata": {
                "provider": manifest.get("provider"),
                "sensor": manifest.get("sensor"),
                "modality": manifest.get("modality"),
                "acquisition_datetime_utc": manifest.get("acquisition_datetime_utc"),
                "crs": (manifest.get("geospatial") or {}).get("crs"),
                "scene_manifest_version": manifest.get("schema_version"),
                "source_scene_uid": manifest.get("source_scene_uid"),
            },
        }
        if errors and status == "computed":
            attributes["attribute_status"] = "partial"
        return attributes
    except Exception as exc:
        return {
            "attribute_version": ATTRIBUTE_VERSION,
            "attribute_status": "error",
            "attribute_computed_at": computed_at,
            "attribute_errors": [str(exc)],
            "attribute_warnings": warnings,
            "attribute_input_hash": attribute_input_hash(annotation, manifest, class_name),
            "identification": {
                "source_annotation_id": annotation.get("source_annotation_id") or annotation.get("id"),
                "scene_id": annotation.get("scene_id") or manifest.get("scene_id"),
                "source_scene_uid": manifest.get("source_scene_uid"),
                "class_id": annotation.get("class_id"),
                "class_name": class_name,
            },
            "geometry": {},
            "orientation": {},
            "geospatial": {"available": False, "reason": "attribute_computation_failed"},
            "scene_metadata": {},
        }


def compute_geospatial_attributes(
    polygon_scene_px: list[list[float]],
    centroid_scene_px: list[float],
    annotation: dict[str, Any],
    scene_manifest: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    # Sensor-geometry scenes (NITF): non-linear GCP TPS instead of an affine
    # transform. Geometry is derived to WGS84 and metric attributes measured on a
    # local tangent plane; results are explicitly approximate.
    if (scene_manifest.get("geometry") or {}).get("model") == "gcp_tps":
        return compute_geospatial_attributes_tps(
            polygon_scene_px, centroid_scene_px, annotation, scene_manifest, errors, warnings
        )

    geospatial = scene_manifest.get("geospatial") or {}
    if not geospatial.get("has_geo"):
        return {"available": False, "reason": "scene_not_georeferenced"}

    transform_values = geospatial.get("transform")
    crs = geospatial.get("crs")
    if not transform_values or len(transform_values) < 6 or not crs:
        errors.append("Scene CRS or affine transform is unavailable")
        return {"available": False, "reason": "missing_crs_or_transform"}

    try:
        polygon_native = [pixel_to_native(point, transform_values) for point in polygon_scene_px]
        centroid_native = pixel_to_native(centroid_scene_px, transform_values)
        polygon_wgs84 = transform_points(polygon_native, crs, "EPSG:4326")
        centroid_wgs84 = transform_points([centroid_native], crs, "EPSG:4326")[0]
        local_polygon = lonlat_to_local_meters(polygon_wgs84, centroid_wgs84)
        area_m2 = abs(polygon_area(local_polygon))
        edge_lengths_m = polygon_edge_lengths(local_polygon)
        major_axis_m = max(edge_lengths_m) if edge_lengths_m else None
        minor_axis_m = min((value for value in edge_lengths_m if value > 0), default=None)
        orientation_geo = compute_orientation_geo(annotation, centroid_scene_px, transform_values, crs)
        bbox_lonlat = [
            min(point[0] for point in polygon_wgs84),
            min(point[1] for point in polygon_wgs84),
            max(point[0] for point in polygon_wgs84),
            max(point[1] for point in polygon_wgs84),
        ]
        return {
            "available": True,
            "crs": crs,
            "geometry_geo": geojson_polygon(polygon_native),
            "geometry_wgs84": geojson_polygon(polygon_wgs84),
            "centroid_lon": centroid_wgs84[0],
            "centroid_lat": centroid_wgs84[1],
            "bbox_lonlat": bbox_lonlat,
            "area_m2": area_m2,
            "major_axis_m": major_axis_m,
            "minor_axis_m": minor_axis_m,
            "equivalent_diameter_m": math.sqrt(4 * area_m2 / math.pi) if area_m2 > 0 else 0.0,
            "aspect_ratio_m": major_axis_m / minor_axis_m if major_axis_m and minor_axis_m else None,
            "orientation_geo_deg": orientation_geo,
            "metric_method": "local_tangent_plane_wgs84",
        }
    except Exception as exc:
        errors.append(f"Geospatial attribute computation failed: {exc}")
        return {"available": False, "reason": "geospatial_transform_failed"}


def compute_geospatial_attributes_tps(
    polygon_scene_px: list[list[float]],
    centroid_scene_px: list[float],
    annotation: dict[str, Any],
    scene_manifest: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    # Lazy import breaks the engine <-> sensor_geometry import cycle.
    from services.sensor_geometry import try_scene_geo_model

    model = try_scene_geo_model(scene_manifest)
    if model is None:
        errors.append("TPS geometry model unavailable")
        return {"available": False, "reason": "missing_gcp_tps_model"}
    try:
        polygon_wgs84 = model.pixel_to_wgs84(polygon_scene_px, densify_px=OBJECT_TPS_DENSIFY_PX)
        centroid_wgs84 = model.pixel_to_wgs84([centroid_scene_px])[0]
        local_polygon = lonlat_to_local_meters(polygon_wgs84, centroid_wgs84)
        area_m2 = abs(polygon_area(local_polygon))
        edge_lengths_m = polygon_edge_lengths(local_polygon)
        major_axis_m = max(edge_lengths_m) if edge_lengths_m else None
        minor_axis_m = min((value for value in edge_lengths_m if value > 0), default=None)
        orientation_geo = _orientation_geo_tps(annotation, centroid_scene_px, model)
        bbox_lonlat = [
            min(point[0] for point in polygon_wgs84),
            min(point[1] for point in polygon_wgs84),
            max(point[0] for point in polygon_wgs84),
            max(point[1] for point in polygon_wgs84),
        ]
        return {
            "available": True,
            "crs": "EPSG:4326",
            "approximate": True,
            "orthorectified": False,
            "transform_model": "gcp_tps",
            "geometry_geo": None,  # no metric native CRS for sensor geometry
            "geometry_wgs84": geojson_polygon(polygon_wgs84),
            "centroid_lon": centroid_wgs84[0],
            "centroid_lat": centroid_wgs84[1],
            "bbox_lonlat": bbox_lonlat,
            "area_m2": area_m2,
            "major_axis_m": major_axis_m,
            "minor_axis_m": minor_axis_m,
            "equivalent_diameter_m": math.sqrt(4 * area_m2 / math.pi) if area_m2 > 0 else 0.0,
            "aspect_ratio_m": major_axis_m / minor_axis_m if major_axis_m and minor_axis_m else None,
            "orientation_geo_deg": orientation_geo,
            "metric_method": "local_tangent_plane_wgs84_tps",
        }
    except Exception as exc:
        errors.append(f"TPS geospatial attribute computation failed: {exc}")
        return {"available": False, "reason": "gcp_tps_transform_failed"}


def _orientation_geo_tps(
    annotation: dict[str, Any],
    centroid_scene_px: list[float],
    model: Any,
) -> float | None:
    orientation, _source = _pixel_orientation(annotation)
    if orientation is None:
        return None
    vector = annotation.get("front_vector_scene_px")
    if not isinstance(vector, list) or len(vector) < 2:
        angle = math.radians(orientation)
        vector = [math.cos(angle), math.sin(angle)]
    endpoint = [centroid_scene_px[0] + float(vector[0]), centroid_scene_px[1] + float(vector[1])]
    wgs84 = model.pixel_to_wgs84([centroid_scene_px, endpoint])
    return initial_bearing(wgs84[0], wgs84[1])


def compute_tile_attributes(
    source_annotation: dict[str, Any] | Any,
    tile: dict[str, Any] | Any,
    tile_size: int,
    min_box_fraction: float,
) -> dict[str, Any] | None:
    annotation = _as_dict(source_annotation)
    tile_data = _as_dict(tile)
    try:
        bbox = _validated_bbox(annotation.get("bbox"))
    except ValueError:
        # Zdegenerowana/niepoprawna ramka (np. zerowe pole po konwersji quad→AABB przy
        # imporcie FAIR1M/DOTA/DIOR-R). Pomijamy tę adnotację — pojedynczy zły box nie może
        # wywrócić budowy katalogu/datasetu dla całego projektu (spójne z resztą „return None").
        return None
    source_polygon = annotation_polygon_scene_px(annotation, bbox)
    source_area = abs(polygon_area(source_polygon))
    if source_area <= 0:
        return None

    x0 = float(tile_data["x0"])
    y0 = float(tile_data["y0"])
    clipped_scene = clip_polygon_to_rect(source_polygon, x0, y0, x0 + tile_size, y0 + tile_size)
    if len(clipped_scene) < 3:
        return None

    clipped_area = abs(polygon_area(clipped_scene))
    if clipped_area <= 0:
        return None
    visible_fraction = min(1.0, clipped_area / source_area)
    clipped_tile = [[point[0] - x0, point[1] - y0] for point in clipped_scene]
    bbox_tile = polygon_bbox(clipped_tile)
    width = bbox_tile[2] - bbox_tile[0]
    height = bbox_tile[3] - bbox_tile[1]
    exportable = visible_fraction >= min_box_fraction and width > 0 and height > 0
    source_id = annotation.get("source_annotation_id") or annotation.get("id")
    scene_id = annotation.get("scene_id")
    tile_filename = str(tile_data.get("filename") or "")
    tile_id = compute_tile_id(scene_id, tile_filename, tile_data.get("x0"), tile_data.get("y0"), tile_size)
    tile_annotation_id = stable_id(source_id, tile_id)
    tolerance = 1e-6
    geometry_type = annotation.get("geometry_type") or "bbox"
    obb_tile = minimum_rotated_box(clipped_tile) if geometry_type == "rotated_bbox" else [
        [bbox_tile[0], bbox_tile[1]],
        [bbox_tile[2], bbox_tile[1]],
        [bbox_tile[2], bbox_tile[3]],
        [bbox_tile[0], bbox_tile[3]],
    ]

    return {
        "attribute_version": ATTRIBUTE_VERSION,
        "tile_annotation_id": tile_annotation_id,
        "source_annotation_id": source_id,
        "tile_id": tile_id,
        "tile_filename": tile_filename,
        "scene_id": scene_id,
        "class_id": annotation.get("class_id"),
        "geometry_type": geometry_type,
        "bbox_tile_px": bbox_tile,
        "geometry_tile_px": clipped_tile,
        "obb_tile_px": obb_tile,
        "orientation_angle_deg": annotation.get("orientation_angle_deg"),
        "front_vector_scene_px": annotation.get("front_vector_scene_px"),
        "bbox_yolo_norm": [
            ((bbox_tile[0] + bbox_tile[2]) / 2) / tile_size,
            ((bbox_tile[1] + bbox_tile[3]) / 2) / tile_size,
            width / tile_size,
            height / tile_size,
        ],
        "bbox_coco_xywh": [bbox_tile[0], bbox_tile[1], width, height],
        "visible_fraction": visible_fraction,
        "is_clipped": visible_fraction < 1.0 - tolerance,
        "touches_tile_border": (
            bbox_tile[0] <= tolerance
            or bbox_tile[1] <= tolerance
            or bbox_tile[2] >= tile_size - tolerance
            or bbox_tile[3] >= tile_size - tolerance
        ),
        "exportable_yolo": exportable,
        "exportable_coco": exportable,
        "exportable_yolo_obb": exportable and len(obb_tile) == 4,
    }


def minimum_rotated_box(points: list[list[float]]) -> list[list[float]]:
    if len(points) < 3:
        return []
    try:
        import cv2
        import numpy as np

        contour = np.asarray(points, dtype=np.float32)
        rectangle = cv2.minAreaRect(contour)
        return [[float(x), float(y)] for x, y in cv2.boxPoints(rectangle)]
    except Exception:
        bbox = polygon_bbox(points)
        return [
            [bbox[0], bbox[1]],
            [bbox[2], bbox[1]],
            [bbox[2], bbox[3]],
            [bbox[0], bbox[3]],
        ]


def enrich_source_annotation(project_id: str, scene_id: str, annotation: dict[str, Any]) -> dict[str, Any]:
    manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
    classes = load_json(project_id, "classes", default=[])
    class_names = {int(item["id"]): item.get("name") for item in classes if "id" in item}
    enriched = dict(annotation)
    enriched["attributes"] = compute_source_attributes(
        enriched,
        manifest,
        class_names.get(int(enriched.get("class_id", -1))),
    )
    return enriched


# --- Bramka przeliczania atrybutow (DESIGN_DECISIONS.md, performance-roadmap triage LabelView) ---
#
# `recompute_scene_attributes()` liczy `attribute_input_hash` DLA KAZDEJ adnotacji, zeby
# stwierdzic, ze nic sie nie zmienilo. Na scenie DOTA z 10 206 adnotacjami kosztuje to ~1,8 s
# i — co gorsza — jest wolane z `GET /annotations`, wiec placi je kazde wejscie w widok ORAZ
# kazde odswiezenie po edycji. Pomiar: 1909 / 1768 / 1788 ms w trzech kolejnych przebiegach,
# czyli bez zadnego cache'u.
#
# Wynik petli zalezy wylacznie od: tresci adnotacji, manifestu sceny, klas projektu i
# ATTRIBUTE_VERSION. Jesli zaden z tych czterech wejsc sie nie zmienil, petla jest z definicji
# no-opem. Zapamietujemy wiec ich odcisk w maleńkim sidecarze i pomijamy cala prace.
#
# Odcisk uzywa `size + mtime_ns` plikow zrodlowych, a nie licznika rewizji: dzieki temu
# unieważnia sie takze przy zapisie z pominieciem `db.storage` (np. edycja z zewnatrz), a nie
# tylko przy mutacji przez API. Usuniecie sidecara jest bezpieczne — wymusza jedno przeliczenie.
_ATTRIBUTES_STATE_NAME = "attributes_state"
_ATTRIBUTES_STATE_SCHEMA = 1


def _file_key(path: Path) -> list[int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return [st.st_size, st.st_mtime_ns]


def _attributes_fingerprint(paths, scene_id: str) -> dict[str, Any]:
    return {
        "attribute_version": ATTRIBUTE_VERSION,
        "annotations": _file_key(paths.scene_json(scene_id, "annotations")),
        "scene_manifest": _file_key(paths.scene_json(scene_id, "scene_manifest")),
        "classes": _file_key(paths.project_json("classes")),
    }


def _read_attributes_state(state_path: Path) -> dict[str, Any] | None:
    try:
        document = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    if document.get("schema_version") != _ATTRIBUTES_STATE_SCHEMA:
        return None
    return document


def _write_attributes_state(state_path: Path, fingerprint: dict[str, Any], total: int) -> None:
    """Zapisz znacznik atomowo. Porazka zapisu jest nieszkodliwa — kolejne wywolanie po prostu
    przeliczy ponownie, wiec nie moze przerwac zadania odczytu adnotacji."""
    payload = json.dumps(
        {
            "schema_name": "geotile_scene_attributes_state",
            "schema_version": _ATTRIBUTES_STATE_SCHEMA,
            "fingerprint": fingerprint,
            "total": total,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    temp = state_path.with_name(f".{state_path.name}.{os.getpid()}.tmp")
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(payload)
        os.replace(temp, state_path)
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def recompute_scene_attributes(project_id: str, scene_id: str) -> dict[str, int]:
    paths = project_paths(project_id)
    state_path = paths.scene_json(scene_id, _ATTRIBUTES_STATE_NAME)
    fingerprint = _attributes_fingerprint(paths, scene_id)
    cached = _read_attributes_state(state_path)
    if cached is not None and cached.get("fingerprint") == fingerprint:
        # Adnotacje, manifest, klasy i wersja algorytmu sa te same co przy ostatnim
        # przeliczeniu, wiec petla per-adnotacja nie moze niczego zmienic. Pomijamy ja.
        return {"total": int(cached.get("total") or 0), "updated": 0}

    manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
    classes = load_json(project_id, "classes", default=[])
    class_names = {int(item["id"]): item.get("name") for item in classes if "id" in item}
    updated = 0

    def recompute(annotations: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        nonlocal updated
        normalized: list[dict[str, Any]] = []
        for annotation in annotations:
            item = dict(annotation)
            class_name = class_names.get(int(item.get("class_id", -1)))
            expected_hash = attribute_input_hash(item, manifest, class_name)
            current = item.get("attributes") or {}
            if (
                current.get("attribute_version") != ATTRIBUTE_VERSION
                or current.get("attribute_input_hash") != expected_hash
            ):
                item["attributes"] = compute_source_attributes(item, manifest, class_name)
                updated += 1
            normalized.append(item)
        return normalized if updated else None

    annotations, _revision = mutate_scene_json(
        project_id,
        scene_id,
        "annotations",
        recompute,
        default=[],
    )
    # Odcisk liczymy PO mutacji: gdy petla cos zapisala, `annotations.json` ma juz nowy
    # mtime i zapisanie odcisku sprzed zapisu wymuszaloby przeliczanie w kolko.
    _write_attributes_state(state_path, _attributes_fingerprint(paths, scene_id), len(annotations))
    return {"total": len(annotations), "updated": updated}


def recompute_project_attributes(project_id: str) -> dict[str, int]:
    total = 0
    updated = 0
    for scene_id in list_scene_ids(project_id):
        result = recompute_scene_attributes(project_id, scene_id)
        total += result["total"]
        updated += result["updated"]
    return {"total": total, "updated": updated}


def attribute_input_hash(
    annotation: dict[str, Any],
    scene_manifest: dict[str, Any],
    class_name: str | None,
) -> str:
    payload = {
        "attribute_version": ATTRIBUTE_VERSION,
        "annotation": {
            key: annotation.get(key)
            for key in (
                "id",
                "source_annotation_id",
                "scene_id",
                "class_id",
                "geometry_type",
                "bbox",
                "rotated_bbox",
                "polygon_scene_px",
                "front_vector_scene_px",
                "orientation_angle_deg",
                "annotation_source",
                "annotator_email",
            )
        },
        "class_name": class_name,
        "scene": {
            "schema_version": scene_manifest.get("schema_version"),
            "source_scene_uid": scene_manifest.get("source_scene_uid"),
            "provider": scene_manifest.get("provider"),
            "sensor": scene_manifest.get("sensor"),
            "modality": scene_manifest.get("modality"),
            "acquisition_datetime_utc": scene_manifest.get("acquisition_datetime_utc"),
            "geospatial": scene_manifest.get("geospatial"),
            "geometry": scene_manifest.get("geometry"),
        },
    }
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def annotation_polygon_scene_px(annotation: dict[str, Any], bbox: list[float] | None = None) -> list[list[float]]:
    polygon = annotation.get("polygon_scene_px")
    if isinstance(polygon, list) and len(polygon) >= 3:
        return [[float(point[0]), float(point[1])] for point in polygon if len(point) >= 2]

    rotated = annotation.get("rotated_bbox")
    if isinstance(rotated, dict):
        cx = float(rotated["cx"])
        cy = float(rotated["cy"])
        half_width = float(rotated["width"]) / 2
        half_height = float(rotated["height"]) / 2
        angle = math.radians(float(rotated.get("angle_deg", 0)))
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        return [
            [cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a]
            for dx, dy in (
                (-half_width, -half_height),
                (half_width, -half_height),
                (half_width, half_height),
                (-half_width, half_height),
            )
        ]

    resolved_bbox = bbox or _validated_bbox(annotation.get("bbox"))
    x0, y0, x1, y1 = resolved_bbox
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def clip_polygon_to_rect(
    polygon: list[list[float]],
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
) -> list[list[float]]:
    result = polygon
    result = _clip_edge(result, lambda point: point[0] >= xmin, lambda a, b: _intersect_vertical(a, b, xmin))
    result = _clip_edge(result, lambda point: point[0] <= xmax, lambda a, b: _intersect_vertical(a, b, xmax))
    result = _clip_edge(result, lambda point: point[1] >= ymin, lambda a, b: _intersect_horizontal(a, b, ymin))
    result = _clip_edge(result, lambda point: point[1] <= ymax, lambda a, b: _intersect_horizontal(a, b, ymax))
    return result


def _clip_edge(polygon, inside, intersection):
    if not polygon:
        return []
    output: list[list[float]] = []
    previous = polygon[-1]
    previous_inside = inside(previous)
    for current in polygon:
        current_inside = inside(current)
        if current_inside:
            if not previous_inside:
                output.append(intersection(previous, current))
            output.append(current)
        elif previous_inside:
            output.append(intersection(previous, current))
        previous = current
        previous_inside = current_inside
    return output


def _intersect_vertical(a: list[float], b: list[float], x: float) -> list[float]:
    dx = b[0] - a[0]
    if abs(dx) < 1e-12:
        return [x, a[1]]
    ratio = (x - a[0]) / dx
    return [x, a[1] + ratio * (b[1] - a[1])]


def _intersect_horizontal(a: list[float], b: list[float], y: float) -> list[float]:
    dy = b[1] - a[1]
    if abs(dy) < 1e-12:
        return [a[0], y]
    ratio = (y - a[1]) / dy
    return [a[0] + ratio * (b[0] - a[0]), y]


def pixel_to_native(point: list[float], transform_values: list[Any]) -> list[float]:
    a, b, c, d, e, f = [float(value) for value in transform_values[:6]]
    x, y = float(point[0]), float(point[1])
    return [a * x + b * y + c, d * x + e * y + f]


def transform_points(points: list[list[float]], source_crs: str, target_crs: str) -> list[list[float]]:
    from rasterio.warp import transform

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    out_xs, out_ys = transform(source_crs, target_crs, xs, ys)
    return [[float(x), float(y)] for x, y in zip(out_xs, out_ys)]


def compute_orientation_geo(
    annotation: dict[str, Any],
    centroid_scene_px: list[float],
    transform_values: list[Any],
    crs: str,
) -> float | None:
    orientation, _source = _pixel_orientation(annotation)
    if orientation is None:
        return None
    vector = annotation.get("front_vector_scene_px")
    if not isinstance(vector, list) or len(vector) < 2:
        angle = math.radians(orientation)
        vector = [math.cos(angle), math.sin(angle)]
    endpoint = [centroid_scene_px[0] + float(vector[0]), centroid_scene_px[1] + float(vector[1])]
    native = [pixel_to_native(centroid_scene_px, transform_values), pixel_to_native(endpoint, transform_values)]
    wgs84 = transform_points(native, crs, "EPSG:4326")
    return initial_bearing(wgs84[0], wgs84[1])


def lonlat_to_local_meters(points: list[list[float]], origin: list[float]) -> list[list[float]]:
    lon0 = math.radians(origin[0])
    lat0 = math.radians(origin[1])
    cos_lat = max(abs(math.cos(lat0)), 1e-12)
    return [
        [
            EARTH_RADIUS_M * (math.radians(point[0]) - lon0) * cos_lat,
            EARTH_RADIUS_M * (math.radians(point[1]) - lat0),
        ]
        for point in points
    ]


def initial_bearing(start: list[float], end: list[float]) -> float:
    lon1, lat1 = map(math.radians, start)
    lon2, lat2 = map(math.radians, end)
    delta_lon = lon2 - lon1
    x = math.sin(delta_lon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(delta_lon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def polygon_area(polygon: list[list[float]]) -> float:
    if len(polygon) < 3:
        return 0.0
    return 0.5 * sum(
        polygon[index][0] * polygon[(index + 1) % len(polygon)][1]
        - polygon[(index + 1) % len(polygon)][0] * polygon[index][1]
        for index in range(len(polygon))
    )


def polygon_centroid(polygon: list[list[float]]) -> list[float]:
    area = polygon_area(polygon)
    if abs(area) < 1e-12:
        return [
            sum(point[0] for point in polygon) / len(polygon),
            sum(point[1] for point in polygon) / len(polygon),
        ]
    factor = 1 / (6 * area)
    cx = 0.0
    cy = 0.0
    for index, point in enumerate(polygon):
        next_point = polygon[(index + 1) % len(polygon)]
        cross = point[0] * next_point[1] - next_point[0] * point[1]
        cx += (point[0] + next_point[0]) * cross
        cy += (point[1] + next_point[1]) * cross
    return [cx * factor, cy * factor]


def polygon_edge_lengths(polygon: list[list[float]]) -> list[float]:
    if len(polygon) < 2:
        return []
    return [
        math.hypot(
            polygon[(index + 1) % len(polygon)][0] - point[0],
            polygon[(index + 1) % len(polygon)][1] - point[1],
        )
        for index, point in enumerate(polygon)
    ]


def polygon_bbox(polygon: list[list[float]]) -> list[float]:
    return [
        min(point[0] for point in polygon),
        min(point[1] for point in polygon),
        max(point[0] for point in polygon),
        max(point[1] for point in polygon),
    ]


def geojson_polygon(points: list[list[float]]) -> dict[str, Any]:
    ring = [list(point) for point in points]
    if ring and ring[0] != ring[-1]:
        ring.append(list(ring[0]))
    return {"type": "Polygon", "coordinates": [ring]}


def stable_id(*parts: Any) -> str:
    value = "|".join(str(part) for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def compute_tile_id(scene_id: Any, tile_filename: Any, x0: Any, y0: Any, tile_size: Any) -> str:
    return stable_id(scene_id, tile_filename, x0, y0, tile_size)


def _validated_bbox(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("Annotation bbox must contain four coordinates")
    bbox = [float(item) for item in value]
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        raise ValueError("Annotation bbox has non-positive dimensions")
    return bbox


def _pixel_orientation(annotation: dict[str, Any]) -> tuple[float | None, str]:
    geometry_type = annotation.get("geometry_type") or "bbox"
    if geometry_type == "bbox":
        return None, "not_available_for_axis_aligned_bbox"
    value = annotation.get("orientation_angle_deg")
    if value is None and isinstance(annotation.get("rotated_bbox"), dict):
        value = annotation["rotated_bbox"].get("angle_deg")
    if value is None:
        return None, "orientation_not_provided"
    return float(value) % 360.0, "front_vector_or_rotated_bbox"


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    raise TypeError(f"Unsupported value type: {type(value)!r}")
