"""Ramka zorientowana z AI ma byc prostokatem NA MAPIE i nadal przylegac do obiektu.

Widok mapy jest konforemny wzgledem elipsoidy, piksele sceny w EPSG:4326 nie sa. Prostokat
o najmniejszym polu dopasowany W PIKSELACH renderuje sie wiec jako rownoleglobok — to jest
zglaszany blad „skoszonych ramek z SAM". Ramki rysowane recznie sa prostokatami na mapie,
stad roznica widoczna golym okiem.

Testy pilnuja OBU wlasnosci naraz, bo pierwsze podejscie do tej poprawki spelnialo tylko
pierwsza: „prostowalo" gotowy prostokat pikselowy, przez co na szerokosci 60 stopni ramka
rosla o polowe wysokosci i przestawala przylegac do obiektu.

Run: python backend/tests/test_map_faithful_obb.py
"""

from __future__ import annotations

import math
import os
import pathlib
import sys
import tempfile


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

TEST_ROOT = pathlib.Path(tempfile.mkdtemp(prefix="geotile-obb-display-"))
os.environ["DATA_DIR"] = str(TEST_ROOT / "data")

import numpy as np  # noqa: E402

from db.storage import save_json, save_scene_json  # noqa: E402
from services.sam_assist import fit_oriented_box, mask_to_rotated_bbox  # noqa: E402
from services.sensor_geometry import SceneGeoModel, pixel_to_display  # noqa: E402


# Scena w EPSG:4326 na szerokosci 60 stopni: stopien szerokosci jest tam na mapie okolo
# dwa razy „dluzszy" niz stopien dlugosci, wiec anizotropia jest wyrazna i mierzalna.
TRANSFORM = [2.0e-4, 0.0, 20.0, 0.0, -2.0e-4, 60.0]
GEO_MANIFEST = {"geospatial": {"has_geo": True, "transform": TRANSFORM, "crs": "EPSG:4326"}}
MASK_ANGLE_DEG = 35.0
MASK_HALF = (70.0, 22.0)  # polowa dlugosci i szerokosci prostokatnego „obiektu"


def _scene(project_id: str, manifest: dict) -> None:
    save_json(project_id, "project", {"id": project_id, "name": project_id})
    save_scene_json(project_id, "scene", "scene", {"id": "scene", "filename": "scene.tif"})
    save_scene_json(project_id, "scene", "scene_manifest", manifest)


def _rotated_mask(size: int = 320) -> np.ndarray:
    """Maska prostokatnego obiektu, obroconego W PRZESTRZENI WYSWIETLANIA.

    Obiekt na ziemi jest prostokatem, wiec jego obraz w pikselach EPSG:4326 jest
    rownoleglobokiem — mask musi to odwzorowywac, inaczej test sprawdzalby przypadek,
    ktory na scenie geo nie wystepuje.
    """
    model = SceneGeoModel.from_manifest(GEO_MANIFEST)
    assert model is not None
    center_px = [[size / 2.0, size / 2.0]]
    center = pixel_to_display(model, center_px)[0]
    angle = math.radians(MASK_ANGLE_DEG)
    ax, ay = math.cos(angle), math.sin(angle)

    ys, xs = np.mgrid[0:size, 0:size]
    flat = np.column_stack([xs.ravel().astype(float), ys.ravel().astype(float)])
    display = np.asarray(pixel_to_display(model, flat.tolist()), dtype=np.float64)
    dx = display[:, 0] - center[0]
    dy = display[:, 1] - center[1]
    # metry na piksel wzdluz osi X — skala, w ktorej podajemy polowy bokow obiektu
    origin_display, unit_x_display = pixel_to_display(model, [[0.0, 0.0], [1.0, 0.0]])
    scale = abs(unit_x_display[0] - origin_display[0])
    along = np.abs(dx * ax + dy * ay) <= MASK_HALF[0] * scale
    across = np.abs(-dx * ay + dy * ax) <= MASK_HALF[1] * scale
    return (along & across).reshape(size, size)


def _corner_angles_deg(points) -> list[float]:
    angles = []
    for index in range(4):
        previous, current, following = points[(index - 1) % 4], points[index], points[(index + 1) % 4]
        ax, ay = previous[0] - current[0], previous[1] - current[1]
        bx, by = following[0] - current[0], following[1] - current[1]
        cosine = (ax * bx + ay * by) / (math.hypot(ax, ay) * math.hypot(bx, by))
        angles.append(math.degrees(math.acos(max(-1.0, min(1.0, cosine)))))
    return angles


def _display_angles(polygon) -> list[float]:
    model = SceneGeoModel.from_manifest(GEO_MANIFEST)
    return _corner_angles_deg(pixel_to_display(model, polygon))


def _polygon_area(points) -> float:
    total = 0.0
    for index in range(len(points)):
        x0, y0 = points[index]
        x1, y1 = points[(index + 1) % len(points)]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


def test_pixel_fit_is_skewed_on_the_map():
    """Sanity dowodu: bez poprawki problem NAPRAWDE istnieje na tej scenie."""
    from services.sensor_geometry import rotated_corners_px

    mask = _rotated_mask()
    pixel_fit = mask_to_rotated_bbox(mask)
    assert pixel_fit is not None
    skew = max(abs(angle - 90.0) for angle in _display_angles(rotated_corners_px(pixel_fit)))
    assert skew > 5.0, f"scena testowa nie jest niekonforemna, skos {skew:.3f} st"


def test_display_fit_is_a_rectangle_on_the_map():
    _scene("obb-geo", GEO_MANIFEST)
    mask = _rotated_mask()
    rotated, polygon = fit_oriented_box(mask, 0.0, 0.0, "obb-geo", "scene")
    assert polygon is not None and len(polygon) == 4
    assert rotated is not None

    angles = _display_angles(polygon)
    assert max(abs(angle - 90.0) for angle in angles) < 0.01, angles


def test_display_fit_still_hugs_the_object():
    """Prostopadloscia nie wolno platnic rozmiarem — pierwsze podejscie tu polegalo."""
    _scene("obb-tight", GEO_MANIFEST)
    mask = _rotated_mask()
    _rotated, polygon = fit_oriented_box(mask, 0.0, 0.0, "obb-tight", "scene")
    assert polygon is not None

    mask_area = float(mask.sum())
    box_area = _polygon_area(polygon)
    # Prostokat o najmniejszym polu wokol prostokatnego obiektu to praktycznie ten obiekt.
    assert 0.95 < box_area / mask_area < 1.15, (box_area, mask_area)

    ys, xs = np.where(mask)
    assert abs(sum(point[0] for point in polygon) / 4.0 - float(xs.mean())) < 2.0
    assert abs(sum(point[1] for point in polygon) / 4.0 - float(ys.mean())) < 2.0


def test_window_offset_lands_in_scene_pixels():
    _scene("obb-offset", GEO_MANIFEST)
    mask = _rotated_mask()
    _rotated, base = fit_oriented_box(mask, 0.0, 0.0, "obb-offset", "scene")
    _shifted_rotated, shifted = fit_oriented_box(mask, 128.0, 64.0, "obb-offset", "scene")
    assert base is not None and shifted is not None
    dx = [after[0] - before[0] for before, after in zip(base, shifted)]
    dy = [after[1] - before[1] for before, after in zip(base, shifted)]
    assert all(abs(value - 128.0) < 1.5 for value in dx), dx
    assert all(abs(value - 64.0) < 1.5 for value in dy), dy


def test_scene_without_geo_keeps_the_pixel_fit():
    """Bez geo piksel JEST mapa — zmiana geometrii bylaby zmiana bez powodu."""
    _scene("obb-nogeo", {"geospatial": {"has_geo": False}})
    mask = _rotated_mask()
    rotated, polygon = fit_oriented_box(mask, 10.0, 20.0, "obb-nogeo", "scene")
    assert polygon is None
    expected = mask_to_rotated_bbox(mask)
    assert rotated is not None and expected is not None
    assert abs(rotated["cx"] - (expected["cx"] + 10.0)) < 1e-6
    assert abs(rotated["cy"] - (expected["cy"] + 20.0)) < 1e-6
    assert abs(rotated["width"] - expected["width"]) < 1e-6


def test_missing_project_context_falls_back_instead_of_raising():
    mask = _rotated_mask()
    rotated, polygon = fit_oriented_box(mask, 0.0, 0.0, None, None)
    assert polygon is None and rotated is not None


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
