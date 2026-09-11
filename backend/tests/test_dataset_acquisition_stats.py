"""Faza 4 statystyk: metadane akwizycji (GSD/sensor/modality/sezon/SAR incidence) per split.

Run: python backend/tests/test_dataset_acquisition_stats.py
"""

import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from models.tiling_config import TileInfo
from services.dataset_builder import _build_acquisition_stats, _season_from_iso


def _tile(name: str) -> TileInfo:
    return TileInfo(filename=name, col=0, row=0, x0=0, y0=0)


def test_season_mapping():
    assert _season_from_iso("2023-07-15T10:00:00Z") == "summer"
    assert _season_from_iso("2023-01-10") == "winter"
    assert _season_from_iso("2023-04-01") == "spring"
    assert _season_from_iso("2023-10-20") == "autumn"
    assert _season_from_iso(None) is None
    assert _season_from_iso("bad") is None


def test_acquisition_distributions_by_tile_and_split():
    tiles = [_tile("sA__t1.png"), _tile("sA__t2.png"), _tile("sB__t3.png"), _tile("sC__t4.png")]
    splits = {"sA__t1.png": "train", "sA__t2.png": "val", "sB__t3.png": "train", "sC__t4.png": "test"}
    scene_context = {
        "sA": {"gsd_m": 0.5, "sensor": "WV2", "modality": "EO", "acquisition_datetime_utc": "2023-07-15T00:00:00Z"},
        "sB": {"gsd_m": 0.3, "sensor": "PHRNEO", "modality": "EO", "acquisition_datetime_utc": "2023-01-10T00:00:00Z"},
        "sC": {"modality": "SAR", "incidence_angle_deg": 35.0},  # brak gsd/sensor/date
    }
    a = _build_acquisition_stats(tiles, splits, scene_context)
    assert a is not None
    assert a.total_tiles == 4 and a.weighting == "by_tile"

    # braki (ważone kaflami): sC bez gsd/sensor/date
    assert a.missing == {"gsd": 1, "sensor": 1, "date": 1}, a.missing

    # sensor: WV2 x2 (t1,t2), PHRNEO x1 (t3); rozbicie per split
    assert a.sensor.overall == {"WV2": 2, "PHRNEO": 1}
    assert a.sensor.train == {"WV2": 1, "PHRNEO": 1}
    assert a.sensor.val == {"WV2": 1}
    assert a.sensor.test == {}

    # modality: EO x3, SAR x1
    assert a.modality.overall == {"EO": 3, "SAR": 1}

    # sezon: summer x2 (sA lipiec), winter x1 (sB styczeń)
    assert a.season.overall == {"summer": 2, "winter": 1}

    # GSD hist: 3 wartości (sA,sA,sB); histogram niepusty; sumy per split spójne
    assert a.gsd_hist is not None
    assert sum(a.gsd_hist.counts) == 3
    assert sum(a.gsd_hist.train) == 2 and sum(a.gsd_hist.val) == 1 and sum(a.gsd_hist.test) == 0

    # SAR incidence: 1 wartość (sC=35) w splicie test
    assert a.incidence_hist is not None
    assert sum(a.incidence_hist.counts) == 1
    assert sum(a.incidence_hist.test) == 1


def test_acquisition_none_without_context():
    assert _build_acquisition_stats([_tile("s__t.png")], {"s__t.png": "train"}, {}) is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK {name}")
    print("all dataset acquisition stats tests passed")
