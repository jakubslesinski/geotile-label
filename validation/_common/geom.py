"""Geometria wielokątów wypukłych bez zależności zewnętrznych.

Środowisko backendu nie ma shapely (transformacje CRS idą przez rasterio.warp),
więc IoU liczymy sami. OBB/quady benchmarków są WYPUKŁE, więc część wspólną daje
klipowanie Sutherland–Hodgman, a pole — wzór na sznurowadło. To wystarcza do dowodu
wierności importu (wartości bliskie 1.0), gdzie liczy się dokładność, nie ogólność.
"""

from __future__ import annotations

from typing import Sequence

Point = Sequence[float]
Polygon = Sequence[Point]


def _signed_area(poly: Polygon) -> float:
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def polygon_area(poly: Polygon) -> float:
    return abs(_signed_area(poly))


def _convex_ccw_order(poly: Polygon) -> list[list[float]]:
    """Uporządkuj wierzchołki wypukłego wielokąta kątowo wokół centroidu (CCW).

    Benchmarki potrafią listować rogi OBB w kolejności, która jako surowy ring jest
    „muszką" (samoprzecięcie) — a wtedy klipowanie S-H zawodzi. Dla wielokąta WYPUKŁEGO
    sortowanie po kącie względem centroidu odtwarza właściwy ring, więc IoU staje się
    niezależne od kolejności wejścia (a IoU kształtu z samym sobą == 1.0).
    """
    import math

    pts = [[float(p[0]), float(p[1])] for p in poly]
    if len(pts) < 3:
        return pts
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    pts.sort(key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
    return pts


def _clip_convex(subject: list[list[float]], clip: list[list[float]]) -> list[list[float]]:
    """Część wspólna: przytnij ``subject`` półpłaszczyznami wypukłego ``clip`` (CCW)."""
    output = subject
    n = len(clip)
    for i in range(n):
        a = clip[i]
        b = clip[(i + 1) % n]
        if not output:
            break
        inp = output
        output = []
        # Punkt jest "wewnątrz" krawędzi a->b, gdy leży po jej lewej stronie (CCW).
        def inside(p: list[float]) -> bool:
            return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) >= 0.0

        def intersect(s: list[float], e: list[float]) -> list[float]:
            # Przecięcie prostej krawędzi klipu (a->b) z odcinkiem s->e.
            # t = [(a-s) × d_c] / [d_s × d_c], gdzie d_c=b-a, d_s=e-s.
            dx_c, dy_c = b[0] - a[0], b[1] - a[1]
            dx_s, dy_s = e[0] - s[0], e[1] - s[1]
            denom = dx_s * dy_c - dy_s * dx_c
            if denom == 0.0:
                return e
            t = ((a[0] - s[0]) * dy_c - (a[1] - s[1]) * dx_c) / denom
            return [s[0] + t * dx_s, s[1] + t * dy_s]

        prev = inp[-1]
        prev_in = inside(prev)
        for cur in inp:
            cur_in = inside(cur)
            if cur_in:
                if not prev_in:
                    output.append(intersect(prev, cur))
                output.append(cur)
            elif prev_in:
                output.append(intersect(prev, cur))
            prev, prev_in = cur, cur_in
    return output


def polygon_iou(poly_a: Polygon, poly_b: Polygon) -> float:
    """IoU dwóch wypukłych wielokątów. Zwraca 0.0, gdy któryś jest zdegenerowany."""
    a = _convex_ccw_order(poly_a)
    b = _convex_ccw_order(poly_b)
    area_a = polygon_area(a)
    area_b = polygon_area(b)
    if area_a <= 0.0 or area_b <= 0.0:
        return 0.0
    inter_poly = _clip_convex(a, b)
    inter = polygon_area(inter_poly) if len(inter_poly) >= 3 else 0.0
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


def is_convex(poly: Polygon) -> bool:
    """Czy prosty wielokąt jest wypukły (spójny znak iloczynu wektorowego na rogach).

    Nasze IoU zakłada wypukłość (klip S-H). Benchmarki sporadycznie niosą quady
    niewypukłe/samoprzecinające się (błąd adnotacji) — tam IoU nie jest miarodajne,
    a wierność importu i tak orzeka odchylenie wierzchołków.
    """
    n = len(poly)
    if n < 3:
        return False
    got_pos = got_neg = False
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        cx, cy = poly[(i + 2) % n]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if cross > 0:
            got_pos = True
        elif cross < 0:
            got_neg = True
        if got_pos and got_neg:
            return False
    return True


def polygon_vertex_deviation(a: Polygon, b: Polygon) -> float | None:
    """Maks. odległość między odpowiadającymi wierzchołkami (w ZACHOWANEJ kolejności).

    Importer zachowuje kolejność rogów (``rounded_polygon`` tylko zaokrągla), więc dla
    bezstratnego importu odchylenie == błąd zaokrąglenia. Odporne na figury zdegenerowane
    (zerowe pole — przycięte przy krawędzi obrazu), gdzie IoU jest nieokreślone.
    Zwraca ``None``, gdy liczba wierzchołków się nie zgadza.
    """
    import math

    if len(a) != len(b):
        return None
    if not a:
        return 0.0
    return max(math.hypot(pa[0] - pb[0], pa[1] - pb[1]) for pa, pb in zip(a, b))


def percentile(values: Sequence[float], q: float) -> float:
    """Prosty percentyl (nearest-rank) — bez numpy, dla raportów p50/p95."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return float(ordered[idx])
