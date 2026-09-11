"""Tests for services.nitf.ingest on a reference airborne NITF scene.

Runnable under pytest or directly:
    python backend/tests/test_nitf_ingest.py
Requires PROJ_LIB/GDAL_DATA pointed at the backend-env (see backend-tests-proj-env).
Skips the file-dependent tests when the reference NITF is absent.
"""

import math
import os
import pathlib
import re
import sys
import tempfile

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.nitf.ingest import NitfIngestError, ingest_nitf
from services.nitf.metadata import _nitf_datetime_to_iso
from services.preprocessing_profiles import default_preprocessing_profiles

# Sciezke do referencyjnego NITF-a podaje sie przez GEOTILE_TEST_NITF. Domyslnej
# wartosci nie ma celowo: wskazywalaby na konkretna dostawe na dysku autora, a testy
# ktore tego pliku potrzebuja i tak same sie pomijaja, gdy go nie ma.
REFERENCE_NITF = pathlib.Path(os.environ.get("GEOTILE_TEST_NITF", "reference.ntf"))


class SkipTest(Exception):
    pass


# --- pure-unit tests (no file needed) ----------------------------------------

def test_nitf_datetime_parsing():
    assert _nitf_datetime_to_iso("20250204063858") == "2025-02-04T06:38:58Z"
    assert _nitf_datetime_to_iso("202502040549") == "2025-02-04T05:49:00Z"
    assert _nitf_datetime_to_iso("") is None
    assert _nitf_datetime_to_iso("garbage") is None


def test_pan_profile_registered():
    profiles = {p.profile_id: p for p in default_preprocessing_profiles()}
    assert "pan_uint16_percentile" in profiles
    profile = profiles["pan_uint16_percentile"]
    assert profile.modality == "AERIAL_EO"
    assert profile.rgb_conversion == "grayscale_rgb"
    assert profile.percentile_stretch and profile.stretch_low == 2.0 and profile.stretch_high == 98.0


# --- ingest on the real scene ------------------------------------------------

def _ingest_reference():
    if not REFERENCE_NITF.is_file():
        raise SkipTest(f"reference NITF absent: {REFERENCE_NITF}")
    workspace = pathlib.Path(tempfile.mkdtemp(prefix="geotile-nitf-test-"))
    return ingest_nitf(REFERENCE_NITF, workspace)


def test_ingest_manifest_geometry_and_metadata():
    result = _ingest_reference()
    manifest = result.manifest

    assert manifest["raster_kind"] == "nitf_sensor"
    assert manifest["modality"] == "AERIAL_EO"
    assert manifest["georeferencing"] == "SENSOR_GEO"
    assert manifest["geospatial"] == {"has_geo": False}

    image = manifest["image"]
    assert image["width"] > 0 and image["height"] > 0
    assert image["bands"] == 1 and image["dtype"] == "UInt16" and image["abpp"] == 10

    geometry = manifest["geometry"]
    assert geometry["model"] == "gcp_tps"
    assert geometry["gcp_crs"] == "EPSG:4326"
    assert geometry["approximate"] is True and geometry["orthorectified"] is False
    assert len(geometry["gcps"]) == 4
    footprint = geometry["footprint_wgs84"]
    assert len(footprint) > 5  # densified beyond four corners
    lons = [p[0] for p in footprint]
    lats = [p[1] for p in footprint]
    assert -180.0 <= min(lons) and max(lons) <= 180.0
    assert -90.0 <= min(lats) and max(lats) <= 90.0
    assert max(lons) > min(lons) and max(lats) > min(lats)

    acquisition = manifest["metadata"]["acquisition"]
    assert acquisition["acquisition_datetime_utc"].endswith("Z")
    assert acquisition["sensor"]
    # Classification carried, not dropped (spec §7).
    assert manifest["metadata"]["classification"]["file_class"] == "U"
    assert len(manifest["source_file_sha256"]) == 64


def test_ingest_extended_metadata_b1_b2_b3_b5():
    m = _ingest_reference().manifest

    # B1 — provenance / identity (surfaced + block)
    assert m["display_name"]
    assert m["production_datetime_utc"].endswith("Z")
    assert m["mission_id"]
    assert isinstance(m["scene_number"], int) and m["scene_number"] >= 0
    assert m["target_area_id"]
    assert m["focal_length_mm"] > 0
    assert m["platform_altitude_m"] > 0
    provenance = m["metadata"]["provenance"]
    # Ten sam identyfikator obrazu musi wyjsc z naglowka i z bloku pochodzenia.
    assert provenance["image_id"] == m["display_name"]
    assert provenance["product_lineage"]
    acquisition = m["metadata"]["acquisition"]
    # Start poprzedza zobrazowanie, a zobrazowanie poprzedza produkcje.
    assert acquisition["takeoff_datetime_utc"] <= acquisition["acquisition_datetime_utc"]
    assert acquisition["acquisition_datetime_utc"] <= m["production_datetime_utc"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", acquisition["calibration_date"])

    # B2 — retained sensor model
    sensor_model = m["metadata"]["sensor_model"]
    assert sensor_model["focal_length_mm"] == m["focal_length_mm"]
    assert -90.0 <= sensor_model["entry_location"]["lat"] <= 90.0
    assert len(sensor_model["sensra_raw"]) == 3
    # Nazwa sensora z naglowka musi wystapic w surowym rekordzie SENSRA.
    assert m["metadata"]["acquisition"]["sensor"] in sensor_model["sensra_raw"][0]

    # B3 — band radiometry on image
    image = m["image"]
    assert image["nbits"] == 10
    assert image["mask_flags"] == 1
    assert image["color_interpretation"] == "Gray"
    assert "nodata" in image and "scale" in image and "unit" in image

    # B5 — approximate GSD (anisotropic: across-track finer than along-track)
    assert m["gsd_approximate"] is True
    assert 0.3 < m["gsd_m"] < 2.0
    assert m["gsd_col_m"] < m["gsd_row_m"]


def test_ingest_working_raster_is_tiled_uint16_with_overviews_and_gcps():
    from osgeo import gdal

    result = _ingest_reference()
    path = result.working_raster
    assert pathlib.Path(path).is_file()
    ds = gdal.Open(path)
    try:
        assert ds.GetDriver().ShortName == "GTiff"
        band = ds.GetRasterBand(1)
        assert band.DataType == gdal.GDT_UInt16
        assert band.GetBlockSize() == [512, 512]
        assert band.GetOverviewCount() >= 1
        assert len(ds.GetGCPs()) == 4  # GCPs preserved for QGIS / TPS rebuild
    finally:
        ds = None


def test_ingest_rejects_wrong_source(tmp_path=None):
    workspace = pathlib.Path(tempfile.mkdtemp(prefix="geotile-nitf-neg-"))
    bogus = workspace / "not_a_scene.ntf"
    bogus.write_bytes(b"not a nitf file")
    try:
        ingest_nitf(bogus, workspace)
    except Exception:  # noqa: BLE001 — rejection via NitfIngestError or a GDAL error both count
        return
    raise AssertionError("expected ingest to reject a non-NITF file")


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    passed = skipped = failed = 0
    for test in tests:
        try:
            test()
            passed += 1
            print(f"PASS {test.__name__}")
        except SkipTest as exc:
            skipped += 1
            print(f"SKIP {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{passed} passed, {skipped} skipped, {failed} failed")
    sys.exit(1 if failed else 0)
