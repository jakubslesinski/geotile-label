"""M4: spatial export (GeoJSON/GeoPackage) + NITF geo provenance.

Run: python backend/tests/test_export_geospatial.py
(needs PROJ_LIB/GDAL_DATA on the backend-env — see backend-tests-proj-env).
"""

import json
import pathlib
import sys
import tempfile

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.attribute_engine.engine import compute_source_attributes
from services.export_geospatial import (
    FEATURE_PROPERTIES,
    build_feature_collection,
    write_geojson,
    write_geopackage,
)
from services.source_annotation_export import build_source_annotation_rows

GCPS_STRIP = [
    {"pixel": 0.5, "line": 0.5, "lon": 19.000, "lat": 50.000},
    {"pixel": 2999.5, "line": 0.5, "lon": 18.980, "lat": 50.004},
    {"pixel": 2999.5, "line": 11999.5, "lon": 18.996, "lat": 50.170},
    {"pixel": 0.5, "line": 11999.5, "lon": 19.016, "lat": 50.166},
]
AFFINE_TRANSFORM = [1.0, 0.0, 500000.0, 0.0, -1.0, 5_960_000.0]


def _tps_manifest():
    return {
        "modality": "AERIAL_EO",
        "georeferencing": "SENSOR_GEO",
        "sensor": "SENSOR-X",
        "geospatial": {"has_geo": False},
        "geometry": {"model": "gcp_tps", "gcp_crs": "EPSG:4326", "gcps": GCPS_STRIP},
    }


def _affine_manifest():
    return {
        "modality": "EO",
        "georeferencing": "GEO",
        "geospatial": {"has_geo": True, "transform": AFFINE_TRANSFORM, "crs": "EPSG:32634"},
    }


def _scene_cache(manifest, bbox, ann_id="a1"):
    annotation = {"id": ann_id, "class_id": 0, "geometry_type": "bbox", "bbox": bbox}
    annotation["attributes"] = compute_source_attributes(annotation, manifest, "vehicle")
    return {"s1": {"scene": {"filename": "scene.ntf"}, "manifest": manifest, "annotations": {ann_id: annotation}}}


def _rows(manifest, bbox):
    return build_source_annotation_rows(_scene_cache(manifest, bbox), {0: "vehicle"})


# --- provenance in rows ------------------------------------------------------

def test_tps_row_provenance():
    row = _rows(_tps_manifest(), [1000, 6000, 1120, 6100])[0]
    assert row["has_geo"] is True
    assert row["transform_model"] == "gcp_tps"
    assert row["approximate"] is True
    assert row["orthorectified"] is False
    assert row["georeferencing"] == "SENSOR_GEO"
    assert row["geometry_wgs84"] is not None


def test_affine_row_provenance():
    row = _rows(_affine_manifest(), [10, 10, 110, 110])[0]
    assert row["transform_model"] == "affine"
    assert row["approximate"] is False


# --- GeoJSON -----------------------------------------------------------------

def test_feature_collection_shape_and_props():
    rows = _rows(_tps_manifest(), [1000, 6000, 1120, 6100])
    fc = build_feature_collection(rows)
    assert fc["type"] == "FeatureCollection"
    assert "CRS84" in fc["crs"]["properties"]["name"]
    assert len(fc["features"]) == 1
    feature = fc["features"][0]
    assert feature["geometry"]["type"] == "Polygon"
    props = feature["properties"]
    assert props["transform_model"] == "gcp_tps"
    assert props["approximate"] is True and props["orthorectified"] is False
    assert props["sensor"] == "SENSOR-X"
    # centroid must be in Europe (axis order sane)
    assert 18.9 < props["centroid_lon"] < 19.1 and 50.0 < props["centroid_lat"] < 50.2


def test_classification_never_exported():
    # Classification keys must not appear in the feature property whitelist.
    forbidden = {"file_class", "classification", "file_class_system", "file_category"}
    assert forbidden.isdisjoint(set(FEATURE_PROPERTIES))


def test_write_geojson_roundtrip():
    rows = _rows(_tps_manifest(), [1000, 6000, 1120, 6100])
    fc = build_feature_collection(rows)
    out = pathlib.Path(tempfile.mkdtemp(prefix="geotile-geojson-")) / "annotations.geojson"
    write_geojson(out, fc)
    reloaded = json.loads(out.read_text(encoding="utf-8"))
    assert reloaded["type"] == "FeatureCollection" and len(reloaded["features"]) == 1


# --- GeoPackage --------------------------------------------------------------

def test_write_geopackage_features():
    from osgeo import ogr

    rows = _rows(_tps_manifest(), [1000, 6000, 1120, 6100])
    out = pathlib.Path(tempfile.mkdtemp(prefix="geotile-gpkg-")) / "annotations.gpkg"
    written = write_geopackage(out, rows)
    assert written is not None and out.is_file()
    ds = ogr.Open(str(out))
    try:
        layer = ds.GetLayer(0)
        assert layer.GetFeatureCount() == 1
        feature = layer.GetNextFeature()
        assert feature.GetField("transform_model") == "gcp_tps"
        assert int(feature.GetField("approximate")) == 1
    finally:
        ds = None


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
