"""Czysta geometria OBB dla importu FAIR1M (bez zależności od backendu).

FAIR1M niesie obiekt jako obrócony prostokąt = 4 rogi (w XML zamknięte 5. punktem).
Kanoniczny model GeoTile niesie `polygon_scene_px` (4 rogi) jako geometrię wiodącą
(patrz memory rotated-bbox-map-vs-pixel-frame) oraz pomocniczy `rotated_bbox`
{cx, cy, width, height, angle_deg}. Ten moduł liczy jedno z drugiego bez utraty kształtu.
"""

from __future__ import annotations

import math

Point = tuple[float, float]
Polygon = list[Point]

# Zaokrąglenia spójne z fixturą SAR_test (piksele 3 miejsca, kąt 6 miejsc).
_COORD_NDIGITS = 3
_ANGLE_NDIGITS = 6


def _dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def polygon_bbox(polygon: Polygon) -> list[float]:
    """Osiowo-równoległy bbox [minx, miny, maxx, maxy]."""
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return [
        round(min(xs), _COORD_NDIGITS),
        round(min(ys), _COORD_NDIGITS),
        round(max(xs), _COORD_NDIGITS),
        round(max(ys), _COORD_NDIGITS),
    ]


def rounded_polygon(polygon: Polygon) -> list[list[float]]:
    return [[round(x, _COORD_NDIGITS), round(y, _COORD_NDIGITS)] for x, y in polygon]


def rotated_bbox_from_polygon(polygon: Polygon) -> dict[str, float]:
    """Zorientowany prostokąt z 4 uporządkowanych rogów.

    Krawędź p0->p1 wyznacza `width` i `angle_deg`; krawędź p1->p2 wyznacza `height`.
    Uśredniamy przeciwległe krawędzie, bo FAIR1M bywa lekko niedomknięty numerycznie.
    """
    if len(polygon) != 4:
        raise ValueError(f"OBB wymaga 4 rogów, otrzymano {len(polygon)}")
    p0, p1, p2, p3 = polygon
    cx = sum(p[0] for p in polygon) / 4.0
    cy = sum(p[1] for p in polygon) / 4.0
    width = (_dist(p0, p1) + _dist(p2, p3)) / 2.0
    height = (_dist(p1, p2) + _dist(p3, p0)) / 2.0
    angle = math.degrees(math.atan2(p1[1] - p0[1], p1[0] - p0[0]))
    return {
        "cx": round(cx, _COORD_NDIGITS),
        "cy": round(cy, _COORD_NDIGITS),
        "width": round(width, _COORD_NDIGITS),
        "height": round(height, _COORD_NDIGITS),
        "angle_deg": round(angle, _ANGLE_NDIGITS),
    }


def polygon_area(polygon: Polygon) -> float:
    """Pole wielokąta (shoelace) — do testów IoU/round-trip."""
    n = len(polygon)
    s = 0.0
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0
