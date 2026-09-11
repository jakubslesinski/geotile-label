"""Tests for services.sensor_geometry.SceneGeoModel (affine + GCP TPS).

Runnable either under pytest or directly with the backend Python:
    python backend/tests/test_sensor_geometry.py
"""

import math
import pathlib
import sys

# Bootstrap sys.path for both pytest and direct-script execution.
BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.attribute_engine.engine import pixel_to_native, transform_points
from services.sensor_geometry import (
    SceneGeoModel,
    SceneGeometryError,
    densify_polyline_px,
)

# --- Fixtures: a synthetic airborne strip ------------------------------------
# Cztery narozne GCP-y wymyslonej smugi. Wartosci sa zmyslone celowo - testy
# sprawdzaja matematyke modelu, nie to, ktore zobrazowanie posluzylo za wzor.
GCPS_STRIP = [
    {"pixel": 0.5, "line": 0.5, "lon": 19.000, "lat": 50.000},
    {"pixel": 2999.5, "line": 0.5, "lon": 18.980, "lat": 50.004},
    {"pixel": 2999.5, "line": 11999.5, "lon": 18.996, "lat": 50.170},
    {"pixel": 0.5, "line": 11999.5, "lon": 19.016, "lat": 50.166},
]
WIDTH_STRIP, HEIGHT_STRIP = 3000, 12000

# A north-up UTM affine transform standing in for a satellite scene.
AFFINE_TRANSFORM = [1.0, 0.0, 500000.0, 0.0, -1.0, 5_960_000.0]
AFFINE_CRS = "EPSG:32634"


def _affine_manifest():
    return {
        "geospatial": {
            "has_geo": True,
            "transform": AFFINE_TRANSFORM,
            "crs": AFFINE_CRS,
        }
    }


def _tps_manifest():
    return {
        "geometry": {
            "model": "gcp_tps",
            "gcp_crs": "EPSG:4326",
            "gcps": GCPS_STRIP,
        }
    }


def _corners(width, height):
    return [[0, 0], [width, 0], [width, height], [0, height], [0, 0]]


# --- affine branch: must be bit-for-bit identical to the existing pipeline ---

def test_affine_matches_existing_pipeline_exactly():
    model = SceneGeoModel.from_manifest(_affine_manifest())
    assert model is not None and model.kind == "affine"
    ring = [[0, 0], [640, 0], [640, 640], [0, 640], [0, 0]]

    got = model.pixel_to_wgs84(ring)
    expected = transform_points(
        [pixel_to_native(point, AFFINE_TRANSFORM) for point in ring],
        AFFINE_CRS,
        "EPSG:4326",
    )
    assert got == expected, "affine branch must reproduce pixel_to_native+transform_points bit-for-bit"


def test_affine_roundtrip():
    model = SceneGeoModel.from_manifest(_affine_manifest())
    pixels = [[10, 20], [1000, 5], [640, 640], [0.5, 0.5]]
    back = model.wgs84_to_pixel(model.pixel_to_wgs84(pixels))
    for original, restored in zip(pixels, back):
        assert math.hypot(restored[0] - original[0], restored[1] - original[1]) < 1e-6


# --- densification helper ----------------------------------------------------

def test_densify_preserves_corners_and_bounds_step():
    ring = [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]
    dense = densify_polyline_px(ring, max_step_px=32.0)
    assert dense[0] == [0.0, 0.0] and dense[-1] == [0.0, 0.0]
    assert len(dense) > len(ring)
    for a, b in zip(dense, dense[1:]):
        assert math.hypot(b[0] - a[0], b[1] - a[1]) <= 32.0 + 1e-9


def test_densify_noop_for_short_segments():
    ring = [[0, 0], [5, 0], [5, 5]]
    assert densify_polyline_px(ring, max_step_px=32.0) == [[0.0, 0.0], [5.0, 0.0], [5.0, 5.0]]


# --- TPS branch --------------------------------------------------------------

def test_tps_passes_through_gcps():
    model = SceneGeoModel.from_manifest(_tps_manifest())
    assert model is not None and model.kind == "gcp_tps"
    for gcp in GCPS_STRIP:
        (lon, lat), = model.pixel_to_wgs84([[gcp["pixel"], gcp["line"]]])
        assert abs(lon - gcp["lon"]) < 1e-6, f"lon off at GCP {gcp}"
        assert abs(lat - gcp["lat"]) < 1e-6, f"lat off at GCP {gcp}"


def test_tps_roundtrip_within_footprint():
    model = SceneGeoModel.from_manifest(_tps_manifest())
    samples = [
        [WIDTH_STRIP / 2, HEIGHT_STRIP / 2],
        [10, 10],
        [WIDTH_STRIP - 10, HEIGHT_STRIP - 10],
        [WIDTH_STRIP - 10, 10],
    ]
    back = model.wgs84_to_pixel(model.pixel_to_wgs84(samples))
    for original, restored in zip(samples, back):
        assert math.hypot(restored[0] - original[0], restored[1] - original[1]) < 1e-3


def test_tps_axis_order_stays_in_europe():
    # Guards the classic lat/lon swap: the center must land inside the strip.
    model = SceneGeoModel.from_manifest(_tps_manifest())
    (lon, lat), = model.pixel_to_wgs84([[WIDTH_STRIP / 2, HEIGHT_STRIP / 2]])
    assert 18.9 < lon < 19.1, f"longitude {lon} out of expected band (axis swapped?)"
    assert 50.0 < lat < 50.2, f"latitude {lat} out of expected band (axis swapped?)"


def test_tps_densified_footprint_bounds():
    model = SceneGeoModel.from_manifest(_tps_manifest())
    ring = _corners(WIDTH_STRIP, HEIGHT_STRIP)
    dense = model.pixel_to_wgs84(ring, densify_px=64.0)
    assert len(dense) > len(ring)
    lons = [p[0] for p in dense]
    lats = [p[1] for p in dense]
    # Zmierzone na tej fiksturze: lon 18.979996..19.016004, lat 49.999992..50.170008.
    assert 18.978 < min(lons) < 18.982 and 19.014 < max(lons) < 19.018
    assert 49.998 < min(lats) < 50.002 and 50.168 < max(lats) < 50.172


# --- model selection ---------------------------------------------------------

def test_from_manifest_prefers_tps_and_handles_missing():
    assert SceneGeoModel.from_manifest(_tps_manifest()).kind == "gcp_tps"
    assert SceneGeoModel.from_manifest(_affine_manifest()).kind == "affine"
    assert SceneGeoModel.from_manifest({}) is None
    assert SceneGeoModel.from_manifest({"geospatial": {"has_geo": False}}) is None


def test_tps_requires_four_gcps():
    try:
        SceneGeoModel.from_gcps(GCPS_STRIP[:3])
    except SceneGeometryError:
        return
    raise AssertionError("expected SceneGeometryError for fewer than four GCPs")


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:  # noqa: BLE001 — standalone runner
            failures += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
