"""Testy parsera DOTA (czyste, bez backendu) — feed pod v10/v02."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dota.parse import parse_dota_txt, to_canonical  # noqa: E402

# Plik z dwoma liniami nagłówka (oficjalny devkit) + 2 obiekty.
SAMPLE = """imagesource:GoogleEarth
gsd:0.146
2244.0 1791.0 2254.0 1795.0 2245.0 1813.0 2238.0 1809.0 small-vehicle 1
100.0 100.0 200.0 100.0 200.0 150.0 100.0 150.0 ship 0
"""

# Wariant bez nagłówka (mirror HF).
SAMPLE_NO_HEADER = """100.0 100.0 200.0 100.0 200.0 150.0 100.0 150.0 plane 0
"""


def test_parse_skips_header_and_reads_objects(tmp_path):
    p = tmp_path / "P0000.txt"
    p.write_text(SAMPLE, encoding="utf-8")
    objs = parse_dota_txt(p)
    assert len(objs) == 2  # nagłówki pominięte
    assert objs[0]["class_name"] == "small-vehicle"
    assert objs[0]["difficult"] is True
    assert objs[1]["class_name"] == "ship"
    assert objs[1]["difficult"] is False
    assert len(objs[0]["polygon"]) == 4


def test_parse_without_header(tmp_path):
    p = tmp_path / "P1.txt"
    p.write_text(SAMPLE_NO_HEADER, encoding="utf-8")
    objs = parse_dota_txt(p)
    assert len(objs) == 1 and objs[0]["class_name"] == "plane"


def test_to_canonical_axis_aligned_and_difficult():
    obj = {
        "class_name": "ship",
        "polygon": [(100.0, 100.0), (200.0, 100.0), (200.0, 150.0), (100.0, 150.0)],
        "difficult": True,
    }
    rec = to_canonical(obj, scene_id="s", class_id=7, index=2, stem="P0000")
    assert rec["geometry_type"] == "rotated_bbox"
    assert rec["class_id"] == 7
    assert rec["difficult"] is True
    assert rec["source_annotation_id"] == "P0000-00002"
    assert rec["bbox"] == [100.0, 100.0, 200.0, 150.0]
    assert rec["rotated_bbox"]["width"] == 100.0 and rec["rotated_bbox"]["height"] == 50.0
    assert rec["annotation_source"] == "import"
