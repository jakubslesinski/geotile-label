"""Kierunek przodu ramki zorientowanej: krawedz poligonu, nie osobny wektor.

Zgloszenie z uzycia: ramka z SAM wychodzila prostokatna, ale kreska kierunku byla
przekoszona, a „Obroc kierunek przodu o 90 stopni" w tabeli propozycji dawalo inny
kierunek niz strzalka po zaakceptowaniu.

Zrodlo obu objawow: `front_vector_scene_px` byl wektorem PIKSELOWYM, a ramka po
dopasowaniu w przestrzeni wyswietlania jest prostokatem NA MAPIE. Na scenach
niekonforemnych wektor pikselowy o tym samym kacie renderuje sie pod innym katem niz
bok ramki, a obrot `(-y, x)` w pikselach nie jest obrotem o 90 stopni na mapie.

Run: python backend/tests/test_front_direction.py
"""

from __future__ import annotations

import math
import pathlib
import sys


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.sam_assist import front_vector_from_polygon  # noqa: E402
from services.sensor_geometry import rotated_bbox_from_polygon_px  # noqa: E402


def _polygon(width: float, height: float, angle_deg: float) -> list[list[float]]:
    angle = math.radians(angle_deg)
    ax, ay = math.cos(angle), math.sin(angle)
    nx, ny = -ay, ax
    half_w, half_h = width / 2.0, height / 2.0
    return [
        [500 - ax * half_w - nx * half_h, 400 - ay * half_w - ny * half_h],
        [500 + ax * half_w - nx * half_h, 400 + ay * half_w - ny * half_h],
        [500 + ax * half_w + nx * half_h, 400 + ay * half_w + ny * half_h],
        [500 - ax * half_w + nx * half_h, 400 - ay * half_w + ny * half_h],
    ]


def _rotate(polygon: list[list[float]]) -> list[list[float]]:
    """Ten sam przepis, co endpoint obrotu przodu."""
    return polygon[1:4] + polygon[:1]


def test_front_vector_follows_the_first_polygon_edge():
    polygon = _polygon(200.0, 80.0, 30.0)
    vector = front_vector_from_polygon(polygon)
    assert vector is not None
    edge_angle = math.degrees(math.atan2(polygon[1][1] - polygon[0][1], polygon[1][0] - polygon[0][0]))
    vector_angle = math.degrees(math.atan2(vector[1], vector[0]))
    assert abs(vector_angle - edge_angle) < 1e-6
    assert abs(math.hypot(*vector) - 1.0) < 1e-6


def test_rotation_cycles_through_all_four_edges_and_returns():
    polygon = _polygon(200.0, 80.0, 30.0)
    angles = []
    current = polygon
    for _ in range(4):
        vector = front_vector_from_polygon(current)
        assert vector is not None
        angles.append(math.degrees(math.atan2(vector[1], vector[0])))
        current = _rotate(current)

    # Po czterech obrotach wracamy do punktu wyjscia — CO DO WSPOLRZEDNYCH, nie tylko kata.
    assert current == polygon
    # Kazdy krok to inna krawedz.
    assert len({round(angle, 6) for angle in angles}) == 4


def test_rotation_does_not_move_the_box():
    polygon = _polygon(200.0, 80.0, 30.0)
    rotated = _rotate(polygon)
    assert sorted(map(tuple, rotated)) == sorted(map(tuple, polygon))


def test_summary_follows_the_rotated_polygon():
    """`rotated_bbox` jest podsumowaniem poligonu, wiec po obrocie boki sie zamieniaja."""
    polygon = _polygon(200.0, 80.0, 30.0)
    before = rotated_bbox_from_polygon_px(polygon)
    after = rotated_bbox_from_polygon_px(_rotate(polygon))
    assert before is not None and after is not None
    assert abs(before["width"] - after["height"]) < 1e-6
    assert abs(before["height"] - after["width"]) < 1e-6
    # Srodek zostaje na miejscu — obrot dotyczy KIERUNKU, nie polozenia.
    assert abs(before["cx"] - after["cx"]) < 1e-6
    assert abs(before["cy"] - after["cy"]) < 1e-6


def test_degenerate_polygon_declines():
    assert front_vector_from_polygon(None) is None
    assert front_vector_from_polygon([[1.0, 1.0]]) is None
    assert front_vector_from_polygon([[1.0, 1.0], [1.0, 1.0]]) is None


def _run() -> int:
    tests = [value for key, value in sorted(globals().items()) if key.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"[OK  ] {test.__name__}")
        except Exception as exc:  # noqa: BLE001 - raport testowy
            failed += 1
            print(f"[BLAD] {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} przeszlo")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())
