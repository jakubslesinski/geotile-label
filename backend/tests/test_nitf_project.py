"""M5a: wiring NITF ingest into project/scene storage.

Run: python backend/tests/test_nitf_project.py
(needs PROJ_LIB/GDAL_DATA on the backend-env — see backend-tests-proj-env).
"""

import os
import pathlib
import sys
import tempfile

# DATA_DIR must be set BEFORE importing db.storage (module-level root).
_TEMP_DATA = pathlib.Path(tempfile.mkdtemp(prefix="geotile-nitf-data-"))
os.environ["DATA_DIR"] = str(_TEMP_DATA)
os.environ.setdefault("SCENES_ROOT", str(_TEMP_DATA / "scenes_root"))

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db.storage import create_project_root, load_scene_json, save_json
from services.nitf.project import create_nitf_scene, scan_nitf_folder
from services.scene_raster_resolver import resolve_scene_raster
from services.sensor_geometry import SceneGeoModel
from services.tile_catalog import tile_geospatial_fields

# Sciezke do referencyjnego NITF-a podaje sie przez GEOTILE_TEST_NITF. Domyslnej
# wartosci nie ma celowo: wskazywalaby na konkretna dostawe na dysku autora, a testy
# ktore tego pliku potrzebuja i tak same sie pomijaja, gdy go nie ma.
REFERENCE_NITF = pathlib.Path(os.environ.get("GEOTILE_TEST_NITF", "reference.ntf"))


class SkipTest(Exception):
    pass


def test_scan_nitf_folder_files_and_packages():
    root = pathlib.Path(tempfile.mkdtemp(prefix="geotile-nitf-scan-"))
    (root / "a.ntf").write_bytes(b"")
    (root / "b.NITF").write_bytes(b"")
    (root / "notes.txt").write_bytes(b"")
    pkg = root / "package1"
    pkg.mkdir()
    (pkg / "c.ntf").write_bytes(b"")
    scans = scan_nitf_folder(root)
    names = {(p.name, package) for p, package in scans}
    assert ("a.ntf", None) in names
    assert ("b.NITF", None) in names
    assert ("c.ntf", "package1") in names
    assert all(not p.name.endswith(".txt") for p, _ in scans)


def _make_project():
    pid = "nitftest01"
    create_project_root(pid, "NITF test")
    save_json(pid, "project", {
        "id": pid, "name": "NITF test", "source_type": "local_scenes",
        "profile": {"modality": "AERIAL_EO", "georeferencing": "SENSOR_GEO"},
    })
    return pid


def test_create_nitf_scene_persists_manifest_and_raster():
    if not REFERENCE_NITF.is_file():
        raise SkipTest(f"reference NITF absent: {REFERENCE_NITF}")
    pid = _make_project()
    sid = "scene1"
    record = create_nitf_scene(pid, sid, REFERENCE_NITF)

    assert record["raster_kind"] == "direct"
    assert record["modality"] == "AERIAL_EO"
    assert record["preparation_status"] == "ready"

    # B1/B5 surfaced onto the scene record for the scene list.
    assert record["display_name"]
    assert record.get("gsd_m") and record["gsd_m"] > 0

    manifest = load_scene_json(pid, sid, "scene_manifest", default={})
    assert manifest["geometry"]["model"] == "gcp_tps"
    assert manifest["geospatial"]["has_geo"] is False
    assert manifest["georeferencing"] == "SENSOR_GEO"
    assert manifest["image"]["dtype"] == "UInt16"
    assert manifest["image"]["nbits"] == 10  # B3 radiometry
    assert manifest["display_name"] == record["display_name"]  # B1
    assert manifest["metadata"]["sensor_model"]["sensra_raw"]  # B2 retention
    assert manifest["working_view"]["raster_ref"]["storage"] == "project"

    # Identity must read as complete (file-based fallback), else the dataset build
    # rewrites the manifest every run and downstream identity gates fail.
    from services.scene_identity import identity_is_complete
    assert identity_is_complete(manifest) is True

    # Raster resolves to the materialized sensor working GeoTIFF.
    raster_path = resolve_scene_raster(pid, sid)
    assert raster_path.is_file() and raster_path.suffix == ".tif"

    # Geometry model + tile footprint work off the persisted manifest.
    model = SceneGeoModel.from_manifest(manifest)
    assert model is not None and model.kind == "gcp_tps"
    fields = tile_geospatial_fields(manifest, 0, 0, 640, 640)
    lon_min, lat_min, lon_max, lat_max = fields["bbox_lonlat"]
    assert lon_min < lon_max and lat_min < lat_max
    footprint = manifest["geometry"]["footprint_wgs84"]
    f_lons = [p[0] for p in footprint]
    f_lats = [p[1] for p in footprint]
    eps = 1e-6
    assert min(f_lons) - eps <= lon_min and lon_max <= max(f_lons) + eps
    assert min(f_lats) - eps <= lat_min and lat_max <= max(f_lats) + eps


def test_audit_tile_center_is_tps_aware():
    # dataset_audit spatial-leakage positioning must work for NITF (TPS), not only affine.
    from services.dataset_audit import _tile_center_3857

    manifest = {
        "geospatial": {"has_geo": False},
        "geometry": {
            "model": "gcp_tps", "gcp_crs": "EPSG:4326",
            "gcps": [
                {"pixel": 0.5, "line": 0.5, "lon": 19.000, "lat": 50.000},
                {"pixel": 2999.5, "line": 0.5, "lon": 18.980, "lat": 50.004},
                {"pixel": 2999.5, "line": 11999.5, "lon": 18.996, "lat": 50.170},
                {"pixel": 0.5, "line": 11999.5, "lon": 19.016, "lat": 50.166},
            ],
        },
    }
    models: dict = {}
    result = _tile_center_3857({"scene_id": "s1", "x0": 1000, "y0": 6000}, {"s1": manifest}, 640, models)
    assert result is not None
    (x, y), span = result
    assert 2.1e6 < x < 2.2e6 and 6.4e6 < y < 6.5e6 and span > 0  # wewnatrz smugi w EPSG:3857
    assert list(models.keys()) == ["s1"]  # model cached once per scene


def test_rescan_nitf_restores_scenes_and_is_idempotent():
    if not REFERENCE_NITF.is_file():
        raise SkipTest(f"reference NITF absent: {REFERENCE_NITF}")
    import shutil as sh
    from db.storage import create_project_root, list_scene_ids, load_json, save_json
    from db.storage import scene_dir as _scene_dir
    from routers.projects import _rescan_nitf_project, scan_project

    folder = pathlib.Path(tempfile.mkdtemp(prefix="geotile-nitf-folder-"))
    sh.copy2(REFERENCE_NITF, folder / "scene01.ntf")
    pid = "nitfrescan01"
    create_project_root(pid, "NITF rescan")
    save_json(pid, "project", {
        "id": pid, "name": "NITF rescan", "source_type": "local_scenes",
        "scene_folder": str(folder),
        "profile": {"modality": "AERIAL_EO", "georeferencing": "SENSOR_GEO"},
    })

    # Empty project -> rescan ingests the folder's .ntf files.
    r1 = _rescan_nitf_project(pid, load_json(pid, "project"))
    assert r1["added"] == 1 and r1["total"] == 1 and r1["removed"] == 0
    assert len(list_scene_ids(pid)) == 1

    # Idempotent: a second rescan neither re-ingests nor deletes.
    r2 = _rescan_nitf_project(pid, load_json(pid, "project"))
    assert r2["added"] == 0 and r2["removed"] == 0 and r2["total"] == 1

    # scan_project must ROUTE to the NITF path (the generic folder scan would delete all).
    r3 = scan_project(pid)
    assert r3["total"] == 1 and len(list_scene_ids(pid)) == 1

    # Heal: a lost scene dir is re-ingested on the next rescan.
    sid = list_scene_ids(pid)[0]
    sh.rmtree(_scene_dir(pid, sid))
    r4 = _rescan_nitf_project(pid, load_json(pid, "project"))
    assert r4["added"] == 1 and len(list_scene_ids(pid)) == 1


def test_scene_sources_migration_skips_nitf():
    # A NITF project (SENSOR_GEO) must NOT be migrated to a generic scene source —
    # that migration rebuilds manifests generically and destroys the TPS geometry.
    from db.storage import create_project_root, load_json, save_json
    from services.scene_sources import load_scene_sources

    pid = "nitfsrc01"
    create_project_root(pid, "NITF src")
    save_json(pid, "project", {
        "id": pid, "name": "NITF src", "source_type": "local_scenes",
        "scene_folder": r"C:\some\ntf\folder",
        "profile": {"modality": "AERIAL_EO", "georeferencing": "SENSOR_GEO"},
    })
    result = load_scene_sources(pid, migrate=True)
    assert result["sources"] == []
    assert not load_json(pid, "scene_sources", default={})  # nothing written


def test_build_scene_manifest_preserves_nitf():
    # The generic manifest builder must never overwrite a NITF manifest.
    from services.scene_manifest import SCENE_MANIFEST_VERSION, build_scene_manifest

    existing = {
        "geometry": {"model": "gcp_tps", "gcps": [1, 2, 3, 4]},
        "georeferencing": "SENSOR_GEO",
        "modality": "AERIAL_EO",
        "display_name": "A001.F0012",
        "metadata": {"sensor_model": {"sensra_raw": ["x"]}, "acquisition": {"sensor": "SENSOR-X"}},
    }
    m = build_scene_manifest(
        "pidX", "sidX", {"profile": {}}, {"filename": "scene01.ntf"},
        None, existing_manifest=existing,
    )
    assert m["geometry"]["model"] == "gcp_tps"
    assert m["georeferencing"] == "SENSOR_GEO"
    assert m["display_name"] == "A001.F0012"
    assert m["metadata"]["acquisition"]["sensor"] == "SENSOR-X"
    assert m["schema_version"] == SCENE_MANIFEST_VERSION


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
