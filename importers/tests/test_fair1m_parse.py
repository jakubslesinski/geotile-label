"""Testy parsera/geometrii FAIR1M (czyste, bez backendu).

Sprawdzają rdzeń, którego dotyka v10 (import fidelity): XML → polygon_scene_px + rotated_bbox
bez utraty kształtu, oraz round-trip polygon → rotated_bbox → polygon (IoU≈1).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fair1m.geometry import (  # noqa: E402
    polygon_area,
    polygon_bbox,
    rotated_bbox_from_polygon,
)
from fair1m.parse import parse_fair1m_xml, to_canonical  # noqa: E402

SAMPLE_XML = """<?xml version="1.0" encoding="utf-8"?>
<annotation>
  <size><width>1500</width><height>1500</height></size>
  <objects>
    <object>
      <coordinate>pixel</coordinate>
      <possibleresult><name>Liquid Cargo Ship</name></possibleresult>
      <points>
        <point>100.0,100.0</point>
        <point>200.0,100.0</point>
        <point>200.0,150.0</point>
        <point>100.0,150.0</point>
        <point>100.0,100.0</point>
      </points>
    </object>
    <object>
      <coordinate>pixel</coordinate>
      <possibleresult><name>Passenger Ship</name></possibleresult>
      <points>
        <point>0.0,0.0</point>
        <point>10.0,10.0</point>
        <point>0.0,20.0</point>
        <point>-10.0,10.0</point>
        <point>0.0,0.0</point>
      </points>
    </object>
  </objects>
</annotation>
"""


def _write(tmp_path: Path) -> Path:
    p = tmp_path / "0.xml"
    p.write_text(SAMPLE_XML, encoding="utf-8")
    return p


def test_parse_extracts_quads_and_drops_closing_point(tmp_path):
    objs = parse_fair1m_xml(_write(tmp_path))
    assert len(objs) == 2
    assert objs[0]["class_name"] == "Liquid Cargo Ship"
    assert len(objs[0]["polygon"]) == 4  # domykający 5. punkt usunięty


def test_axis_aligned_box_geometry():
    poly = [(100.0, 100.0), (200.0, 100.0), (200.0, 150.0), (100.0, 150.0)]
    rb = rotated_bbox_from_polygon(poly)
    assert rb["cx"] == 150.0 and rb["cy"] == 125.0
    assert rb["width"] == 100.0 and rb["height"] == 50.0
    assert abs(rb["angle_deg"]) < 1e-9  # pozioma
    assert polygon_bbox(poly) == [100.0, 100.0, 200.0, 150.0]


def test_rotated_diamond_geometry():
    # kwadrat obrócony o 45° (przekątne osiowe)
    poly = [(0.0, 0.0), (10.0, 10.0), (0.0, 20.0), (-10.0, 10.0)]
    rb = rotated_bbox_from_polygon(poly)
    assert rb["cx"] == 0.0 and rb["cy"] == 10.0
    # width/height są zaokrąglone do 3 miejsc → tolerancja rzędu zaokrąglenia
    assert abs(rb["width"] - math.hypot(10, 10)) < 1e-2
    assert abs(rb["height"] - math.hypot(10, 10)) < 1e-2
    assert abs(rb["angle_deg"] - 45.0) < 1e-6


def test_to_canonical_shape():
    poly = [(100.0, 100.0), (200.0, 100.0), (200.0, 150.0), (100.0, 150.0)]
    rec = to_canonical(
        {"class_name": "Liquid Cargo Ship", "polygon": poly},
        scene_id="abc123", class_id=3, index=0, stem="0",
    )
    assert rec["geometry_type"] == "rotated_bbox"
    assert rec["class_id"] == 3
    assert rec["annotation_source"] == "import"
    assert rec["source_annotation_id"] == "0-00000"
    assert rec["polygon_scene_px"] == [[100.0, 100.0], [200.0, 100.0], [200.0, 150.0], [100.0, 150.0]]
    assert rec["orientation_angle_deg"] == rec["rotated_bbox"]["angle_deg"]


def test_polygon_roundtrip_area_preserved():
    # v10: polygon niesiony wprost → pole zachowane (brak skewu przy odbudowie)
    poly = [(0.0, 0.0), (10.0, 10.0), (0.0, 20.0), (-10.0, 10.0)]
    rec = to_canonical({"class_name": "X", "polygon": poly}, scene_id="s", class_id=0, index=0, stem="t")
    out = [(x, y) for x, y in rec["polygon_scene_px"]]
    assert abs(polygon_area(out) - polygon_area(poly)) < 1e-6
