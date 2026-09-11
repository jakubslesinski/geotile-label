"""GSD-normalized tiling + resampled window read (opt-in dataset generation).

Run: python backend/tests/test_gsd_tiler.py
"""

import pathlib
import sys
import tempfile

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from models.tiling_config import TilingConfig
from services.gsd_tiler import gsd_normalized_scene_tiles
from services.preprocessing_profiles import default_preprocessing_profiles, read_scene_window


def test_gsd_tiling_window_and_grid():
    # scene 0.5 m/px, target 1.0 m/px -> window 320 px, resampled to 640 output.
    config = TilingConfig(tile_size=640, buffer=0)
    tiles, _anns, _links = gsd_normalized_scene_tiles(
        "s1", "scene", 1280, 1280, 0.5, 1.0, config, [], min_box_fraction=0.3
    )
    assert all(t.window_px == 320 for t in tiles)
    assert len(tiles) == 16  # 1280/320 = 4 x 4
    assert all(t.reviewed and not t.excluded for t in tiles)  # review flags don't apply


def test_gsd_tiling_rederives_annotations():
    config = TilingConfig(tile_size=640, buffer=0)
    annotation = {"id": "a1", "class_id": 0, "geometry_type": "bbox", "bbox": [100, 100, 200, 200]}
    tiles, tile_annotations, _links = gsd_normalized_scene_tiles(
        "s1", "scene", 1280, 1280, 0.5, 1.0, config, [annotation], min_box_fraction=0.3
    )
    # window 320: the box sits fully inside tile 1_1 (x0=0,y0=0).
    anns = tile_annotations.get("1_1_scene.png")
    assert anns and len(anns) == 1
    cls, xc, yc, w, h = anns[0]
    assert cls == 0
    # YOLO normalized to the 320-px window: center (150,150), size 100 -> /320.
    assert abs(xc - 150 / 320) < 1e-6 and abs(yc - 150 / 320) < 1e-6
    assert abs(w - 100 / 320) < 1e-6 and abs(h - 100 / 320) < 1e-6


def test_read_scene_window_resamples_to_tile_size():
    import numpy as np
    from osgeo import gdal

    gdal.UseExceptions()
    folder = pathlib.Path(tempfile.mkdtemp(prefix="geotile-gsd-"))
    raster = folder / "scene.tif"
    dataset = gdal.GetDriverByName("GTiff").Create(str(raster), 640, 640, 1, gdal.GDT_Byte)
    ramp = np.tile(np.linspace(0, 255, 640, dtype=np.uint8), (640, 1))  # horizontal gradient
    dataset.GetRasterBand(1).WriteArray(ramp)
    dataset = None

    profile = {p.profile_id: p for p in default_preprocessing_profiles()}["eo_rgb_percentile"]
    # Read a 320-px window (top-left quarter) resampled to 640 output.
    arr, valid = read_scene_window(raster, 0, 0, 640, profile, window_px=320)
    assert arr.shape[0] == 640 and arr.shape[1] == 640  # output is tile_size
    assert valid.shape == (640, 640)
    # Native read of the same window (no resample) is half the source width.
    native, _ = read_scene_window(raster, 0, 0, 320, profile)
    assert native.shape[0] == 320 and native.shape[1] == 320


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
