"""Testy parsera xView3 (czyste, bez backendu) — feed pod v11/v12."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xview3.parse import GSD_M, build_classes, classify, to_canonical  # noqa: E402


def test_classify():
    assert classify("True", "True") == "fishing_vessel"
    assert classify("True", "False") == "non_fishing_vessel"
    assert classify("True", "") == "vessel"
    assert classify("False", "") == "non_vessel"
    assert classify("", "") == "unknown"


def test_build_classes_stable():
    classes, lookup = build_classes()
    assert len(classes) == 5
    assert lookup["fishing_vessel"] == 0
    assert lookup["unknown"] == 4


def test_to_canonical_from_hbb():
    row = {
        "top": "100", "left": "50", "bottom": "140", "right": "90",
        "detect_id": "d1", "detect_lat": "5.5", "detect_lon": "4.8",
        "is_vessel": "True", "is_fishing": "False",
    }
    rec = to_canonical(row, scene_id="s", class_id=1, index=0)
    assert rec["geometry_type"] == "bbox"
    assert rec["bbox"] == [50.0, 100.0, 90.0, 140.0]  # [left,top,right,bottom]
    assert rec["polygon_scene_px"][0] == [50.0, 100.0]
    assert rec["source_annotation_id"] == "d1"
    assert rec["attributes"]["detect_lat"] == 5.5
    assert rec["attributes"]["is_fishing"] == "False"


def test_to_canonical_point_with_length():
    # brak HBB → box wokół punktu, rozmiar z długości (m) / GSD
    row = {
        "detect_scene_row": "1000", "detect_scene_column": "2000",
        "vessel_length_m": "100", "detect_id": "d2",
    }
    rec = to_canonical(row, scene_id="s", class_id=2, index=3)
    half = 100.0 / GSD_M / 2.0  # 5 px
    assert rec["bbox"] == [2000.0 - half, 1000.0 - half, 2000.0 + half, 1000.0 + half]


def test_to_canonical_point_default_size():
    row = {"detect_scene_row": "10", "detect_scene_column": "20"}  # brak długości i HBB
    rec = to_canonical(row, scene_id="s", class_id=2, index=0)
    # domyślny box 20 px → half 10; min_half=5 nie ogranicza
    assert rec["bbox"] == [10.0, 0.0, 30.0, 20.0]
    assert rec["source_annotation_id"] == "s-00000"


def test_missing_geometry_returns_none():
    assert to_canonical({"is_vessel": "True"}, scene_id="s", class_id=0, index=0) is None
