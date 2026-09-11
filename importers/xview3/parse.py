"""Parser etykiet xView3 (CSV per-scena) → kanoniczne rekordy GeoTile (czysty, bez backendu).

labels/<split>.csv, kolumny m.in.:
    detect_lat, detect_lon, vessel_length_m, source, detect_scene_row, detect_scene_column,
    is_vessel, is_fishing, distance_from_shore_km, scene_id, confidence,
    top, left, bottom, right, detect_id

xView3 to detekcje PUNKTOWE (row/col) — część ma HBB (top/left/bottom/right). Bez orientacji,
więc geometry_type="bbox" (AABB): jeśli jest HBB → z niej; inaczej mały box wokół punktu
(rozmiar z vessel_length_m / GSD, z minimum). `detect_lat/lon` i metadane trafiają do
`attributes` (pod v11 dokładność geo i v12 stratyfikację).
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Any

GSD_M = 10.0          # Sentinel-1 GRD, 10 m/piksel
DEFAULT_BOX_PX = 20.0  # gdy brak długości i brak HBB
MIN_HALF_PX = 5.0

# Stały zbiór klas (stabilne id niezależnie od --limit).
CLASS_ORDER = ["fishing_vessel", "non_fishing_vessel", "vessel", "non_vessel", "unknown"]


def _tri(value: str | None) -> bool | None:
    s = (value or "").strip().lower()
    if s in {"true", "1"}:
        return True
    if s in {"false", "0"}:
        return False
    return None


def classify(is_vessel: str | None, is_fishing: str | None) -> str:
    v = _tri(is_vessel)
    if v is False:
        return "non_vessel"
    if v is None:
        return "unknown"
    f = _tri(is_fishing)
    if f is True:
        return "fishing_vessel"
    if f is False:
        return "non_fishing_vessel"
    return "vessel"


def build_classes() -> tuple[list[dict[str, Any]], dict[str, int]]:
    import hashlib
    classes = [
        {
            "id": i,
            "name": name,
            "color": f"#{hashlib.sha256(name.encode()).hexdigest()[:6].upper()}",
            "hotkey": i + 1,
        }
        for i, name in enumerate(CLASS_ORDER)
    ]
    return classes, {name: i for i, name in enumerate(CLASS_ORDER)}


def load_detections_by_scene(csv_path: str | Path) -> dict[str, list[dict[str, str]]]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            groups[row["scene_id"]].append(row)
    return groups


def _f(value: str | None) -> float | None:
    s = (value or "").strip()
    if s in {"", "null", "None", "nan", "NaN"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _corners(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [
        [round(x0, 3), round(y0, 3)],
        [round(x1, 3), round(y0, 3)],
        [round(x1, 3), round(y1, 3)],
        [round(x0, 3), round(y1, 3)],
    ]


def to_canonical(row: dict[str, str], *, scene_id: str, class_id: int, index: int) -> dict[str, Any] | None:
    top, left = _f(row.get("top")), _f(row.get("left"))
    bottom, right = _f(row.get("bottom")), _f(row.get("right"))
    if None not in (top, left, bottom, right):
        x0, y0, x1, y1 = left, top, right, bottom  # HBB: left/top/right/bottom
    else:
        col, rr = _f(row.get("detect_scene_column")), _f(row.get("detect_scene_row"))
        if col is None or rr is None:
            return None  # brak i HBB, i punktu — pomiń
        length = _f(row.get("vessel_length_m"))
        half = max((length / GSD_M / 2.0) if length else DEFAULT_BOX_PX / 2.0, MIN_HALF_PX)
        x0, y0, x1, y1 = col - half, rr - half, col + half, rr + half

    return {
        "scene_id": scene_id,
        "source_annotation_id": (row.get("detect_id") or f"{scene_id}-{index:05d}"),
        "class_id": class_id,
        "geometry_type": "bbox",
        "bbox": [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)],
        "polygon_scene_px": _corners(x0, y0, x1, y1),
        "is_negative": False,
        "annotation_source": "import",
        "import_id": None,
        "attributes": {
            "detect_id": row.get("detect_id"),
            "detect_lat": _f(row.get("detect_lat")),
            "detect_lon": _f(row.get("detect_lon")),
            "detect_scene_row": _f(row.get("detect_scene_row")),
            "detect_scene_column": _f(row.get("detect_scene_column")),
            "is_vessel": row.get("is_vessel"),
            "is_fishing": row.get("is_fishing"),
            "vessel_length_m": _f(row.get("vessel_length_m")),
            "distance_from_shore_km": _f(row.get("distance_from_shore_km")),
            "confidence": row.get("confidence"),
            "source": row.get("source"),
        },
    }
