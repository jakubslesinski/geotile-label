"""Faza 1 statystyk datasetu: raport niezbalansowania (tiles/scenes/share/rel/split%/missing).

Testuje `_build_class_stats` na syntetycznym `stats_state` — bez pełnego buildu.
Run: python backend/tests/test_dataset_stats_imbalance.py
"""

import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.dataset_builder import _build_class_stats


def _state():
    return {
        "dataset_counts": {0: 10, 1: 184},
        "dataset_counts_by_name": {"ship": 10, "unknown": 184},
        "split_stats": {
            "train": {"images": 100, "per_class": {"ship": 8, "unknown": 150}},
            "val": {"images": 30, "per_class": {"ship": 2, "unknown": 34}},
            "test": {"images": 20, "per_class": {}},  # niepusty, ale bez tych klas
        },
        "tiles_by_class": {"ship": 8, "unknown": 20},
        "scene_dataset_classes": {
            "s1": {"ship", "unknown"}, "s2": {"ship"}, "s3": {"ship"},
            "s4": {"ship"}, "s5": {"ship"},
        },
    }


def test_imbalance_metrics():
    rows = _build_class_stats({0: "ship", 1: "unknown"}, {}, _state())
    by = {r.name: r for r in rows}
    ship, unknown = by["ship"], by["unknown"]

    # #kafli i #scen — sedno pytania „184 z ilu scen?"
    assert ship.tiles == 8 and ship.scenes == 5
    assert unknown.tiles == 20 and unknown.scenes == 1  # 184 adnotacji, ale z JEDNEJ sceny

    # udział % i relacja do największej (largest = 184)
    assert unknown.share_pct == round(100 * 184 / 194, 2)
    assert unknown.rel_to_largest == 1.0
    assert ship.rel_to_largest == round(10 / 184, 4)

    # rozkład klasy po splitach (train/(train+val+test))
    assert ship.train_pct == 80.0 and ship.val_pct == 20.0 and ship.test_pct == 0.0
    assert unknown.train_pct == round(100 * 150 / 184, 1)

    # brak w niepustym splicie (test ma obrazy, ale 0 tych klas)
    assert ship.missing_in_splits == ["test"], ship.missing_in_splits
    assert unknown.missing_in_splits == ["test"], unknown.missing_in_splits


def test_empty_splits_are_not_flagged_missing():
    st = _state()
    st["split_stats"]["test"]["images"] = 0  # test pusty → nie zgłaszamy „brak"
    rows = _build_class_stats({0: "ship", 1: "unknown"}, {}, st)
    for r in rows:
        assert "test" not in r.missing_in_splits


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK {name}")
    print("all dataset-stats imbalance tests passed")
