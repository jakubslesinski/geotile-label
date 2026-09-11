"""Parser adnotacji DOTA (OBB txt) → kanoniczne rekordy GeoTile (czysty, bez backendu).

Format DOTA labelTxt (jedna linia = jeden obiekt):

    x1 y1 x2 y2 x3 y3 x4 y4 category difficult

Pliki z oficjalnego devkitu mogą zaczynać się dwoma liniami nagłówka
(`imagesource:...`, `gsd:...`) — są pomijane (nie parsują się jako 8 liczb).
Geometria współdzielona z importerem FAIR1M (moduł `fair1m.geometry`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fair1m.geometry import Polygon, polygon_bbox, rotated_bbox_from_polygon, rounded_polygon


def parse_dota_txt(path: str | Path) -> list[dict[str, Any]]:
    """Zwraca listę {class_name, polygon:[(x,y)*4], difficult:bool} z jednego pliku."""
    out: list[dict[str, Any]] = []
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 9:  # puste, nagłówki (imagesource:/gsd:), niepełne
            continue
        try:
            coords = [float(v) for v in parts[:8]]
        except ValueError:
            continue  # linia nagłówka
        category = parts[8]
        difficult = parts[9] if len(parts) > 9 else "0"
        polygon = [
            (coords[0], coords[1]),
            (coords[2], coords[3]),
            (coords[4], coords[5]),
            (coords[6], coords[7]),
        ]
        out.append({
            "class_name": category,
            "polygon": polygon,
            "difficult": difficult.strip() in {"1", "true", "True"},
        })
    return out


def collect_class_names(annotation_dirs: list[Path]) -> list[str]:
    """Posortowany zbiór kategorii ze wszystkich plików w podanych katalogach."""
    names: set[str] = set()
    for ann_dir in annotation_dirs:
        for txt in sorted(Path(ann_dir).glob("*.txt")):
            for obj in parse_dota_txt(txt):
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
    """Jeden obiekt DOTA → rekord annotations.json (reszta pól dopełni storage)."""
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
        "difficult": bool(obj.get("difficult")),
        "annotation_source": "import",
        "annotator_email": annotator_email,
        "import_id": None,
    }
