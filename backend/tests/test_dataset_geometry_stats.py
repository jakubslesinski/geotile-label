"""Faza 2 statystyk: rozmiary geometrii (AABB kubełki/metry + OBB kąt/boki).

Run: python backend/tests/test_dataset_geometry_stats.py
"""

import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from models.tiling_config import TileInfo
from services.dataset_builder import _build_geometry_stats


def _tile(filename: str) -> TileInfo:
    return TileInfo(filename=filename, col=0, row=0, x0=0, y0=0)


def test_geometry_aabb_and_obb():
    tiles = [_tile("sA__t1.png"), _tile("sB__t2.png")]
    tile_annotations = {
        "sA__t1.png": [
            [0, 0.5, 0.5, 0.1, 0.05],     # ship: 64x32 px, area 2048 -> medium, aspect 2.0
            [0, 0.5, 0.5, 0.025, 0.025],  # ship: 16x16 px, area 256 -> small
        ],
        "sB__t2.png": [
            [1, 0.5, 0.5, 0.15, 0.15],    # unknown: 96x96 px, area 9216 -> large
        ],
    }
    class_names = {0: "ship", 1: "unknown"}
    gsd_by_scene = {"sA": 0.5, "sB": None}   # ship ma GSD, unknown nie
    source_annotations = {
        "sA": [{"class_id": 0, "rotated_bbox": {"width": 64, "height": 32, "angle_deg": 30}}],
    }

    g = _build_geometry_stats(tiles, tile_annotations, class_names, 640, gsd_by_scene, source_annotations)
    assert g is not None
    assert g.mode == "rotated_bbox"
    assert g.total_annotations == 3
    assert g.tile_size == 640
    # pokrycie GSD: 2 z 3 adnotacji (ship z sA), unknown z sB bez GSD
    assert abs(g.gsd_coverage_frac - 2 / 3) < 1e-3, g.gsd_coverage_frac

    by = {c.name: c for c in g.per_class}
    ship, unknown = by["ship"], by["unknown"]

    # AABB ship: 2 sztuki, kubełki small=1 medium=1, mediana w = median(64,16)=40
    assert ship.count == 2
    assert ship.size_small == 1 and ship.size_medium == 1 and ship.size_large == 0
    assert ship.w_px_median == 40.0
    # metry policzone (GSD 0.5): w_m mediana = 40*0.5 = 20
    assert ship.w_m_median == 20.0 and ship.area_m2_median is not None

    # AABB unknown: large, brak metrów (brak GSD)
    assert unknown.count == 1 and unknown.size_large == 1
    assert unknown.w_m_median is None

    # OBB ship (ze źródła): kąt 30, long 64 short 32, near_square 0 (proporcja 2:1)
    assert ship.obb_count == 1
    assert ship.angle_median == 30.0
    assert ship.long_side_median == 64.0 and ship.short_side_median == 32.0
    assert ship.near_square_count == 0
    # unknown bez OBB źródłowego
    assert unknown.obb_count == 0 and unknown.angle_median is None

    # histogramy niepuste
    assert g.area_px_hist.counts and sum(g.area_px_hist.counts) >= 1
    assert g.angle_hist is not None and sum(g.angle_hist.counts) == 1


def test_geometry_none_when_no_annotations():
    assert _build_geometry_stats([_tile("s__t.png")], {}, {0: "ship"}, 640, {}, {}) is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK {name}")
    print("all dataset geometry stats tests passed")
