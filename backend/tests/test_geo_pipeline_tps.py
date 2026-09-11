"""M3: geo-derivation pipeline routes through SceneGeoModel.

Verifies the affine (satellite) path is unchanged and the new GCP-TPS (NITF)
path yields geo for tile footprints, tile bounds and annotation attributes.

Run: python backend/tests/test_geo_pipeline_tps.py
(needs PROJ_LIB/GDAL_DATA on the backend-env — see backend-tests-proj-env).
"""

import math
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.attribute_engine.engine import (
    annotation_polygon_scene_px,
    compute_geospatial_attributes,
    pixel_to_native,
    polygon_centroid,
    transform_points,
)
from services.export_sidecars import tile_bounds_wgs84
from services.tile_catalog import tile_geospatial_fields

GCPS_STRIP = [
    {"pixel": 0.5, "line": 0.5, "lon": 19.000, "lat": 50.000},
    {"pixel": 2999.5, "line": 0.5, "lon": 18.980, "lat": 50.004},
    {"pixel": 2999.5, "line": 11999.5, "lon": 18.996, "lat": 50.170},
    {"pixel": 0.5, "line": 11999.5, "lon": 19.016, "lat": 50.166},
]
AFFINE_TRANSFORM = [1.0, 0.0, 500000.0, 0.0, -1.0, 5_960_000.0]
AFFINE_CRS = "EPSG:32634"


def _affine_manifest():
    return {"geospatial": {"has_geo": True, "transform": AFFINE_TRANSFORM, "crs": AFFINE_CRS}}


def _tps_manifest():
    return {
        "geospatial": {"has_geo": False},
        "geometry": {"model": "gcp_tps", "gcp_crs": "EPSG:4326", "gcps": GCPS_STRIP},
    }


def _in_footprint(lon, lat):
    return 18.978 < lon < 19.018 and 49.998 < lat < 50.172


# --- affine (satellite) regression -------------------------------------------

def test_affine_tile_bounds_unchanged():
    manifest = _affine_manifest()
    bounds = tile_bounds_wgs84([0, 0, 640, 640], manifest)
    corners = [[0, 0], [640, 0], [640, 640], [0, 640]]
    wgs84 = transform_points([pixel_to_native(p, AFFINE_TRANSFORM) for p in corners], AFFINE_CRS, "EPSG:4326")
    expected = [
        min(p[0] for p in wgs84), min(p[1] for p in wgs84),
        max(p[0] for p in wgs84), max(p[1] for p in wgs84),
    ]
    assert bounds == expected


def test_affine_tile_fields_keep_native():
    fields = tile_geospatial_fields(_affine_manifest(), 0, 0, 640, 640)
    assert fields["native_crs"] == AFFINE_CRS
    assert fields["geometry_native"] is not None
    assert fields["geometry_wgs84"] is not None
    assert fields["bbox_lonlat"] is not None


def test_affine_attributes_available():
    manifest = _affine_manifest()
    annotation = {"geometry_type": "bbox", "bbox": [10, 10, 110, 110]}
    polygon = annotation_polygon_scene_px(annotation)
    centroid = polygon_centroid(polygon)
    result = compute_geospatial_attributes(polygon, centroid, annotation, manifest, [], [])
    assert result["available"] is True
    assert result.get("area_m2", 0) > 0
    assert result["metric_method"] == "local_tangent_plane_wgs84"  # affine unchanged


# --- GCP-TPS (NITF) path -----------------------------------------------------

def test_tps_tile_fields_derive_wgs84_without_native():
    fields = tile_geospatial_fields(_tps_manifest(), 0, 0, 640, 640)
    assert fields["native_crs"] is None and fields["geometry_native"] is None
    assert fields["geometry_wgs84"] is not None and len(fields["geometry_wgs84"]) > 5
    lon_min, lat_min, lon_max, lat_max = fields["bbox_lonlat"]
    assert _in_footprint(lon_min, lat_min) and _in_footprint(lon_max, lat_max)
    assert _in_footprint(fields["centroid_lon"], fields["centroid_lat"])


def test_tps_tile_bounds_in_footprint():
    bounds = tile_bounds_wgs84([1000, 6000, 1640, 6640], _tps_manifest())
    assert bounds is not None
    assert _in_footprint(bounds[0], bounds[1]) and _in_footprint(bounds[2], bounds[3])


def test_tps_attributes_available_and_approximate():
    manifest = _tps_manifest()
    annotation = {"geometry_type": "bbox", "bbox": [1000, 6000, 1120, 6100]}
    polygon = annotation_polygon_scene_px(annotation)
    centroid = polygon_centroid(polygon)
    errors: list = []
    result = compute_geospatial_attributes(polygon, centroid, annotation, manifest, errors, [])
    assert result["available"] is True, f"errors: {errors}"
    assert result["approximate"] is True and result["transform_model"] == "gcp_tps"
    assert result["metric_method"] == "local_tangent_plane_wgs84_tps"
    assert result.get("area_m2", 0) > 0
    assert _in_footprint(result["centroid_lon"], result["centroid_lat"])
    assert result["geometry_geo"] is None  # no metric native for sensor geometry


def test_tps_rotated_bbox_has_geo_orientation():
    manifest = _tps_manifest()
    annotation = {
        "geometry_type": "rotated_bbox",
        "bbox": [1000, 6000, 1200, 6100],
        "rotated_bbox": {"cx": 1100, "cy": 6050, "width": 200, "height": 100, "angle_deg": 30.0},
        "orientation_angle_deg": 30.0,
        "front_vector_scene_px": [math.cos(math.radians(30)), math.sin(math.radians(30))],
    }
    polygon = annotation_polygon_scene_px(annotation)
    centroid = polygon_centroid(polygon)
    result = compute_geospatial_attributes(polygon, centroid, annotation, manifest, [], [])
    assert result["available"] is True
    assert result["orientation_geo_deg"] is not None
    assert 0.0 <= result["orientation_geo_deg"] < 360.0


def test_tps_without_model_reports_not_available():
    # geometry marked gcp_tps but no usable GCPs -> graceful "not available".
    manifest = {"geospatial": {"has_geo": False}, "geometry": {"model": "gcp_tps", "gcps": []}}
    result = compute_geospatial_attributes([[0, 0], [1, 0], [1, 1]], [0.5, 0.5], {}, manifest, [], [])
    assert result["available"] is False


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
