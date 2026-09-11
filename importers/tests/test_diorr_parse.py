"""Testy parsera DIOR-R (czyste, bez backendu) — feed pod v10/v09."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from diorr.parse import parse_diorr_xml, to_canonical  # noqa: E402

SAMPLE = """<annotation>
  <filename>00001.jpg</filename>
  <size><width>800</width><height>800</height><depth>3</depth></size>
  <object>
    <name>golffield</name>
    <type>robndbox</type>
    <difficult>0</difficult>
    <robndbox>
      <x_left_top>133</x_left_top><y_left_top>237</y_left_top>
      <x_right_top>684</x_right_top><y_right_top>237</y_right_top>
      <x_right_bottom>684</x_right_bottom><y_right_bottom>672</y_right_bottom>
      <x_left_bottom>133</x_left_bottom><y_left_bottom>672</y_left_bottom>
    </robndbox>
    <angle>0</angle>
  </object>
  <object>
    <name>ship</name>
    <type>bndbox</type>
    <difficult>1</difficult>
    <bndbox><xmin>10</xmin><ymin>20</ymin><xmax>50</xmax><ymax>80</ymax></bndbox>
  </object>
</annotation>
"""


def _write(tmp_path: Path) -> Path:
    p = tmp_path / "00001.xml"
    p.write_text(SAMPLE, encoding="utf-8")
    return p


def test_parse_robndbox_and_bndbox(tmp_path):
    objs = parse_diorr_xml(_write(tmp_path))
    assert len(objs) == 2
    a, b = objs
    assert a["class_name"] == "golffield" and a["geometry"] == "rotated_bbox"
    assert len(a["polygon"]) == 4 and a["polygon"][0] == (133.0, 237.0)
    assert a["difficult"] is False
    assert b["class_name"] == "ship" and b["geometry"] == "bbox"
    assert b["difficult"] is True
    assert b["polygon"] == [(10.0, 20.0), (50.0, 20.0), (50.0, 80.0), (10.0, 80.0)]


def test_to_canonical_robndbox():
    obj = {
        "class_name": "golffield", "geometry": "rotated_bbox", "difficult": False,
        "polygon": [(133.0, 237.0), (684.0, 237.0), (684.0, 672.0), (133.0, 672.0)],
    }
    rec = to_canonical(obj, scene_id="s", class_id=9, index=0, stem="00001")
    assert rec["geometry_type"] == "rotated_bbox"
    assert rec["class_id"] == 9
    assert rec["polygon_scene_px"][0] == [133.0, 237.0]
    assert rec["rotated_bbox"]["width"] == 551.0 and rec["rotated_bbox"]["height"] == 435.0
    assert rec["orientation_angle_deg"] == 0.0
    assert rec["bbox"] == [133.0, 237.0, 684.0, 672.0]


def test_to_canonical_bndbox_is_aabb():
    obj = {
        "class_name": "ship", "geometry": "bbox", "difficult": True,
        "polygon": [(10.0, 20.0), (50.0, 20.0), (50.0, 80.0), (10.0, 80.0)],
    }
    rec = to_canonical(obj, scene_id="s", class_id=13, index=2, stem="00001")
    assert rec["geometry_type"] == "bbox"
    assert "rotated_bbox" not in rec  # AABB nie dostaje rotated_bbox
    assert rec["difficult"] is True
    assert rec["source_annotation_id"] == "00001-00002"
