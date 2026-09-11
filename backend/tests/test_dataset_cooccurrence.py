"""Faza 3 statystyk: współwystępowanie klas (macierz class × class na kaflu).

Run: python backend/tests/test_dataset_cooccurrence.py
"""

import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from models.tiling_config import TileInfo
from services.dataset_builder import _empty_stats_state, _update_stats_state, _build_co_occurrence

CLASSES = {0: "ship", 1: "fishing", 2: "unknown"}


def _tile(name: str) -> TileInfo:
    return TileInfo(filename=name, col=0, row=0, x0=0, y0=0)


def _ann(cls: int):
    return [cls, 0.5, 0.5, 0.1, 0.1]


def _state_with_tiles():
    st = _empty_stats_state(CLASSES)
    # tile1,2: ship+fishing; tile3: ship; tile4: unknown; tile5: puste (negatyw)
    _update_stats_state(st, _tile("s__t1.png"), "train", [_ann(0), _ann(1)], CLASSES)
    _update_stats_state(st, _tile("s__t2.png"), "train", [_ann(0), _ann(1)], CLASSES)
    _update_stats_state(st, _tile("s__t3.png"), "val", [_ann(0)], CLASSES)
    _update_stats_state(st, _tile("s__t4.png"), "test", [_ann(2)], CLASSES)
    _update_stats_state(st, _tile("s__t5.png"), "train", [], CLASSES)
    return st


def test_cooccurrence_matrix():
    st = _state_with_tiles()
    assert st["co_tiles_total"] == 4  # 4 kafle z ≥1 klasą (t5 pusty)
    co = _build_co_occurrence(st)
    assert co is not None
    # kolejność wg #kafli malejąco: ship(3), fishing(2), unknown(1)
    assert co.classes == ["ship", "fishing", "unknown"], co.classes
    idx = {n: i for i, n in enumerate(co.classes)}
    # diagonala = #kafli z klasą
    assert co.counts[idx["ship"]][idx["ship"]] == 3
    assert co.counts[idx["fishing"]][idx["fishing"]] == 2
    assert co.counts[idx["unknown"]][idx["unknown"]] == 1
    # ship↔fishing na 2 kaflach; symetryczne
    assert co.counts[idx["ship"]][idx["fishing"]] == 2
    assert co.counts[idx["fishing"]][idx["ship"]] == 2
    # unknown z nikim nie współwystępuje
    assert co.counts[idx["unknown"]][idx["ship"]] == 0
    assert co.counts[idx["unknown"]][idx["fishing"]] == 0
    assert co.tiles_total == 4
    assert co.truncated is False


def test_cooccurrence_topk_truncates():
    st = _state_with_tiles()
    co = _build_co_occurrence(st, top_k=2)
    assert co.truncated is True
    assert co.classes == ["ship", "fishing"]      # dwie najczęstsze
    assert len(co.counts) == 2 and len(co.counts[0]) == 2


def test_cooccurrence_none_with_one_class():
    st = _empty_stats_state(CLASSES)
    _update_stats_state(st, _tile("s__t1.png"), "train", [_ann(0)], CLASSES)
    assert _build_co_occurrence(st) is None  # <2 klasy obecne


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK {name}")
    print("all dataset co-occurrence tests passed")
