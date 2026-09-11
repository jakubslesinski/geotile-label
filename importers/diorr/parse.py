"""Parser adnotacji DIOR-R (VOC-XML OBB) → kanoniczne rekordy GeoTile (czysty, bez backendu).

Annotations/Oriented Bounding Boxes/<id>.xml:

    <object>
      <name>golffield</name>
      <type>robndbox</type>
      <difficult>0</difficult>
      <robndbox>
        <x_left_top/><y_left_top/> <x_right_top/><y_right_top/>
        <x_right_bottom/><y_right_bottom/> <x_left_bottom/><y_left_bottom/>
      </robndbox>
      <angle>0</angle>
    </object>

`robndbox` daje 4 jawne rogi zorientowanego prostokąta → wprost `polygon_scene_px`
(kąt wyliczamy z rogów, jak dla FAIR1M). Obiekty HBB (`bndbox`) → geometry_type="bbox".
Geometria współdzielona z `fair1m.geometry`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from fair1m.geometry import Polygon, polygon_bbox, rotated_bbox_from_polygon, rounded_polygon

_ROBND_CORNERS = [
    ("x_left_top", "y_left_top"),
    ("x_right_top", "y_right_top"),
    ("x_right_bottom", "y_right_bottom"),
    ("x_left_bottom", "y_left_bottom"),
]


def _difficult(obj: ET.Element) -> bool:
    return (obj.findtext("difficult") or "0").strip() in {"1", "true", "True"}


def parse_diorr_xml(path: str | Path) -> list[dict[str, Any]]:
    """Zwraca [{class_name, polygon(4), difficult, geometry}] z jednego XML."""
    root = ET.parse(str(path)).getroot()
    out: list[dict[str, Any]] = []
    for obj in root.findall("object"):
        name = (obj.findtext("name") or "").strip()
        if not name:
            continue
        rb = obj.find("robndbox")
        if rb is not None:
            try:
                poly = [(float(rb.findtext(kx)), float(rb.findtext(ky))) for kx, ky in _ROBND_CORNERS]
            except (TypeError, ValueError):
                continue
            out.append({"class_name": name, "polygon": poly, "difficult": _difficult(obj), "geometry": "rotated_bbox"})
            continue
        bb = obj.find("bndbox")
        if bb is not None:
            try:
                xmin = float(bb.findtext("xmin")); ymin = float(bb.findtext("ymin"))
                xmax = float(bb.findtext("xmax")); ymax = float(bb.findtext("ymax"))
            except (TypeError, ValueError):
                continue
            poly = [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)]
            out.append({"class_name": name, "polygon": poly, "difficult": _difficult(obj), "geometry": "bbox"})
    return out


def collect_class_names(ann_dir: Path, ids: list[str]) -> list[str]:
    names: set[str] = set()
    for image_id in ids:
        xml = Path(ann_dir) / f"{image_id}.xml"
        if xml.is_file():
            for obj in parse_diorr_xml(xml):
                names.add(obj["class_name"])
    return sorted(names)


def to_canonical(
    obj: dict[str, Any],
    *,
    scene_id: str,
    class_id: int,
    index: int,
    stem: str,
    annotator_email: str | None = None,
) -> dict[str, Any]:
    polygon: Polygon = obj["polygon"]
    geom = obj.get("geometry", "rotated_bbox")
    rec: dict[str, Any] = {
        "scene_id": scene_id,
        "source_annotation_id": f"{stem}-{index:05d}",
        "class_id": class_id,
        "geometry_type": geom,
        "bbox": polygon_bbox(polygon),
        "polygon_scene_px": rounded_polygon(polygon),
        "difficult": bool(obj.get("difficult")),
        "is_negative": False,
        "annotation_source": "import",
        "annotator_email": annotator_email,
        "import_id": None,
    }
    if geom == "rotated_bbox":
        rbox = rotated_bbox_from_polygon(polygon)
        rec["rotated_bbox"] = rbox
        rec["orientation_angle_deg"] = rbox["angle_deg"]
    return rec
