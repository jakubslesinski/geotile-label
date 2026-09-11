"""Parser adnotacji FAIR1M → kanoniczne rekordy GeoTile (czysty, bez backendu).

Format FAIR1M (data/labelXmls/<id>.xml):

    <annotation>
      <size><width/><height/></size>
      <objects>
        <object>
          <coordinate>pixel</coordinate>
          <possibleresult><name>Liquid Cargo Ship</name></possibleresult>
          <points>
            <point>x1,y1</point> ... <point>x5,y5</point>   # 5. punkt domyka ring
          </points>
        </object>
      </objects>
    </annotation>

Wynik parsera to lista obiektów {class_name, polygon(4 rogi)}; `to_canonical`
zamienia je na rekordy zgodne z annotations.json (polygon_scene_px + rotated_bbox).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .geometry import Polygon, polygon_bbox, rotated_bbox_from_polygon, rounded_polygon


class Fair1mParseError(ValueError):
    pass


def _parse_points(points_el: ET.Element) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for pt in points_el.findall("point"):
        text = (pt.text or "").strip()
        if not text:
            continue
        try:
            x_str, y_str = text.split(",")
            pts.append((float(x_str), float(y_str)))
        except ValueError as exc:
            raise Fair1mParseError(f"Zły punkt '{text}'") from exc
    return pts


def parse_fair1m_xml(xml_path: str | Path) -> list[dict[str, Any]]:
    """Zwraca listę obiektów {class_name, polygon:[(x,y)*4]} z jednego XML.

    Obiekty inne niż czworokątne (rzadkie/uszkodzone) są pomijane — importer je zliczy.
    """
    root = ET.parse(str(xml_path)).getroot()
    objects_el = root.find("objects")
    out: list[dict[str, Any]] = []
    if objects_el is None:
        return out
    for obj in objects_el.findall("object"):
        name_el = obj.find("possibleresult/name")
        name = (name_el.text or "").strip() if name_el is not None else ""
        points_el = obj.find("points")
        if points_el is None or not name:
            continue
        pts = _parse_points(points_el)
        # FAIR1M domyka ring (pierwszy == ostatni punkt) — usuń duplikat.
        if len(pts) >= 2 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) != 4:
            continue
        out.append({"class_name": name, "polygon": pts})
    return out


def collect_class_names(label_dir: str | Path) -> list[str]:
    """Przejrzyj wszystkie XML i zwróć posortowany zbiór nazw klas (stabilne id)."""
    names: set[str] = set()
    for xml_path in sorted(Path(label_dir).glob("*.xml")):
        for obj in parse_fair1m_xml(xml_path):
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
    """Jeden obiekt FAIR1M → rekord annotations.json (reszta pól dopełni storage)."""
    polygon: Polygon = obj["polygon"]
    rbox = rotated_bbox_from_polygon(polygon)
    return {
        "scene_id": scene_id,
        "source_annotation_id": f"{stem}-{index:05d}",
        "class_id": class_id,
        "geometry_type": "rotated_bbox",
        "bbox": polygon_bbox(polygon),
        "rotated_bbox": rbox,
        "polygon_scene_px": rounded_polygon(polygon),
        "orientation_angle_deg": rbox["angle_deg"],
        "is_negative": False,
        "annotation_source": "import",
        "annotator_email": annotator_email,
        "import_id": None,
    }
