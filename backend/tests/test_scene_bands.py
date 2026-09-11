"""RGB band selection for local multiband scenes.

Run: python backend/tests/test_scene_bands.py
(needs PROJ_LIB/GDAL_DATA on the backend-env — see backend-tests-proj-env).
"""

import os
import pathlib
import sys
import tempfile

_TEMP_DATA = pathlib.Path(tempfile.mkdtemp(prefix="geotile-bands-data-"))
os.environ["DATA_DIR"] = str(_TEMP_DATA)
os.environ["SCENES_ROOT"] = str(_TEMP_DATA / "scenes_root")

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db.storage import create_project_root, load_scene_json, save_json, save_scene_json
from services.scene_bands import set_local_scene_rgb_bands
from services.scene_raster_resolver import resolve_scene_raster


def _make_4band_raster(path: pathlib.Path):
    import numpy as np
    from osgeo import gdal

    gdal.UseExceptions()
    dataset = gdal.GetDriverByName("GTiff").Create(str(path), 16, 16, 4, gdal.GDT_Byte)
    for band in range(1, 5):
        dataset.GetRasterBand(band).WriteArray(np.full((16, 16), band * 10, np.uint8))
    dataset = None


def test_local_multiband_band_selection():
    folder = pathlib.Path(os.environ["SCENES_ROOT"]) / "scenes"
    folder.mkdir(parents=True, exist_ok=True)
    _make_4band_raster(folder / "ms.tif")

    pid, sid = "bands01", "scene1"
    create_project_root(pid, "Bands")
    save_json(pid, "project", {
        "id": pid, "name": "Bands", "source_type": "local_scenes", "scene_folder": str(folder),
        "profile": {"modality": "EO", "georeferencing": "NO_GEO"},
    })
    save_scene_json(pid, sid, "scene", {"id": sid, "filename": "ms.tif"})

    result = set_local_scene_rgb_bands(pid, sid, [3, 2, 1])
    assert result["band_count"] == 4 and result["rgb_bands"] == [3, 2, 1]

    # The scene now resolves to the band-selecting VRT with the chosen order.
    raster = resolve_scene_raster(pid, sid)
    assert raster.suffix == ".vrt" and raster.is_file()

    import rasterio

    with rasterio.open(raster) as src:
        assert src.count == 3
        # VRT band 1 = source band 3 (=30), band 2 = source band 2 (=20), band 3 = source band 1 (=10)
        assert [int(src.read(b)[0, 0]) for b in (1, 2, 3)] == [30, 20, 10]

    scene = load_scene_json(pid, sid, "scene", default={})
    assert scene["rgb_bands"] == [3, 2, 1] and "scene_info" not in scene  # forced recompute


def test_band_selection_rejects_bad_input():
    folder = pathlib.Path(os.environ["SCENES_ROOT"]) / "scenes2"
    folder.mkdir(parents=True, exist_ok=True)
    _make_4band_raster(folder / "ms.tif")
    pid, sid = "bands02", "scene1"
    create_project_root(pid, "Bands2")
    save_json(pid, "project", {"id": pid, "name": "B", "source_type": "local_scenes", "scene_folder": str(folder), "profile": {}})
    save_scene_json(pid, sid, "scene", {"id": sid, "filename": "ms.tif"})
    for bad in ([1, 2], [1, 2, 9], [0, 1, 2]):
        try:
            set_local_scene_rgb_bands(pid, sid, bad)
        except ValueError:
            continue
        raise AssertionError(f"expected rejection for rgb_bands={bad}")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
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
