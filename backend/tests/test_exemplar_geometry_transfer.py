"""„Znajdz podobne" ma dawac te sama geometrie co SAM: prostokat NA MAPIE.

Sciezka wzorcowa przenosila `rotated_bbox` czysto w pikselach — przesuniecie srodka,
mnozenie bokow przez skale, dodanie kata dopasowania — wiec miala komplet objawow,
ktore naprawiono dla SAM: rownolegloboczne ramki, przekoszona strzalka i obrot przodu
niebedacy obrotem o 90 stopni na mapie.

WYBOR ROZWIAZANIA BYL POMIAREM, nie zalozeniem. Trzy warianty na scenie EPSG:4326
na szerokosci 60 stopni, szesc przypadkow (obrot 0-90 stopni, przesuniecie do 2500 px,
skala 1,5):

    wariant                                  skos [st]     blad boku
    A: prostokat pikselowy (stan dotychczasowy) 1,7-36,2    do 74%
    B: narozniki przez to samo podobienstwo     0,0-58,1    do 31%
    D: przebudowa w przestrzeni wyswietlania    0,000       do 0,01%

Wariant B wygladal na naturalny (przeniesc ksztalt, nie parametry), ale przy obrocie
wypada GORZEJ niz stan dotychczasowy, bo obrot pikselowy nie jest obrotem na mapie.

Run: python backend/tests/test_exemplar_geometry_transfer.py
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

TEST_ROOT = pathlib.Path(tempfile.mkdtemp(prefix="geotile-exemplar-"))
os.environ["DATA_DIR"] = str(TEST_ROOT / "data")

from db.storage import save_json, save_scene_json  # noqa: E402
from services.exemplar_assist import transfer_exemplar_geometry  # noqa: E402
from services.sensor_geometry import (  # noqa: E402
    SceneGeoModel,
    display_to_pixel,
    pixel_to_display,
)

# Rozdzielczosc jak w produkcie o wysokiej rozdzielczosci (~0,2 m/px): obiekt 120 m ma
# wtedy kilkaset pikseli i zaokraglenie poligonu do 0,001 px nie wchodzi w tolerancje.
# Anizotropia zalezy od szerokosci geograficznej, nie od rozdzielczosci, wiec skos
# mierzony przez ten test pozostaje ten sam.
TRANSFORM = [2.0e-6, 0.0, 20.0, 0.0, -2.0e-6, 60.0]
GEO_MANIFEST = {"geospatial": {"has_geo": True, "transform": TRANSFORM, "crs": "EPSG:4326"}}
PROJECT = "exemplar-geo"
SCENE = "scene"
MODEL = SceneGeoModel.from_manifest(GEO_MANIFEST)


def _project(project_id: str, manifest: dict) -> None:
    save_json(project_id, "project", {"id": project_id, "name": project_id})
    save_scene_json(project_id, SCENE, "scene", {"id": SCENE, "filename": "scene.tif"})
    save_scene_json(project_id, SCENE, "scene_manifest", manifest)


_project(PROJECT, GEO_MANIFEST)
_project("exemplar-nogeo", {"geospatial": {"has_geo": False}})


def _map_rectangle_px(centre_px, width_m, height_m, angle_deg):
    cx, cy = pixel_to_display(MODEL, [centre_px])[0]
    rad = math.radians(angle_deg)
    ax, ay = math.cos(rad), math.sin(rad)
    nx, ny = -ay, ax
    hw, hh = width_m / 2.0, height_m / 2.0
    corners = [
        [cx - ax * hw - nx * hh, cy - ay * hw - ny * hh],
        [cx + ax * hw - nx * hh, cy + ay * hw - ny * hh],
        [cx + ax * hw + nx * hh, cy + ay * hw + ny * hh],
        [cx - ax * hw + nx * hh, cy - ay * hw + ny * hh],
    ]
    return [list(point) for point in display_to_pixel(MODEL, corners)]


EXEMPLAR_POLYGON = _map_rectangle_px([500.0, 400.0], 120.0, 48.0, 30.0)
EXEMPLAR_BBOX = [
    min(p[0] for p in EXEMPLAR_POLYGON), min(p[1] for p in EXEMPLAR_POLYGON),
    max(p[0] for p in EXEMPLAR_POLYGON), max(p[1] for p in EXEMPLAR_POLYGON),
]
EXEMPLAR_ROTATED = {"cx": 500.0, "cy": 400.0, "width": 120.0, "height": 48.0, "angle_deg": 30.0}


def _corner_angles(points_display):
    angles = []
    for index in range(4):
        prev, cur, nxt = (
            points_display[(index - 1) % 4],
            points_display[index],
            points_display[(index + 1) % 4],
        )
        ax, ay = prev[0] - cur[0], prev[1] - cur[1]
        bx, by = nxt[0] - cur[0], nxt[1] - cur[1]
        cosine = (ax * bx + ay * by) / (math.hypot(ax, ay) * math.hypot(bx, by))
        angles.append(math.degrees(math.acos(max(-1.0, min(1.0, cosine)))))
    return angles


def _transfer(project_id, matches, polygon=EXEMPLAR_POLYGON):
    return transfer_exemplar_geometry(
        [dict(item) for item in matches],
        exemplar_bbox=EXEMPLAR_BBOX,
        geometry_type="rotated_bbox",
        exemplar_rotated_bbox=EXEMPLAR_ROTATED,
        exemplar_front_vector=None,
        exemplar_polygon_scene_px=polygon,
        project_id=project_id,
        scene_id=SCENE,
    )


def _match(cx, cy, angle_deg=0.0, scale=1.0):
    return {
        "bbox": [cx - 60.0, cy - 30.0, cx + 60.0, cy + 30.0],
        "_match_angle_deg": angle_deg,
        "_match_scale": scale,
    }


def test_matches_are_rectangles_on_the_map():
    matches = [
        _match(1400.0, 1100.0, angle_deg=45.0),
        _match(3000.0, 2200.0, angle_deg=90.0),
        _match(700.0, 500.0, angle_deg=15.0, scale=1.5),
    ]
    for proposal in _transfer(PROJECT, matches):
        polygon = proposal["polygon_scene_px"]
        assert polygon is not None and len(polygon) == 4
        skew = max(abs(angle - 90.0) for angle in _corner_angles(pixel_to_display(MODEL, polygon)))
        assert skew < 0.01, (proposal["bbox"], skew)


def test_matches_keep_the_exemplar_size_on_the_ground():
    exemplar_display = pixel_to_display(MODEL, EXEMPLAR_POLYGON)
    width = math.dist(exemplar_display[0], exemplar_display[1])
    height = math.dist(exemplar_display[1], exemplar_display[2])

    for scale in (1.0, 1.5):
        proposal = _transfer(PROJECT, [_match(2000.0, 1500.0, angle_deg=60.0, scale=scale)])[0]
        display = pixel_to_display(MODEL, proposal["polygon_scene_px"])
        assert abs(math.dist(display[0], display[1]) / (width * scale) - 1.0) < 0.001
        assert abs(math.dist(display[1], display[2]) / (height * scale) - 1.0) < 0.001


def test_front_vector_follows_the_polygon_edge():
    proposal = _transfer(PROJECT, [_match(1400.0, 1100.0, angle_deg=45.0)])[0]
    polygon = proposal["polygon_scene_px"]
    vector = proposal["front_vector_scene_px"]
    assert vector is not None
    edge = math.degrees(math.atan2(polygon[1][1] - polygon[0][1], polygon[1][0] - polygon[0][0]))
    assert abs(math.degrees(math.atan2(vector[1], vector[0])) - edge) < 1e-6


def test_pixel_path_would_have_been_skewed():
    """Kontrola mocy testu: bez poligonu wzorca wracamy na sciezke pikselowa i skos jest."""
    proposal = _transfer(PROJECT, [_match(1400.0, 1100.0, angle_deg=45.0)], polygon=None)[0]
    assert proposal.get("polygon_scene_px") is None
    box = proposal["rotated_bbox"]
    angle = math.radians(box["angle_deg"])
    ax, ay = math.cos(angle), math.sin(angle)
    nx, ny = -ay, ax
    hw, hh = box["width"] / 2.0, box["height"] / 2.0
    corners = [
        [box["cx"] - ax * hw - nx * hh, box["cy"] - ay * hw - ny * hh],
        [box["cx"] + ax * hw - nx * hh, box["cy"] + ay * hw - ny * hh],
        [box["cx"] + ax * hw + nx * hh, box["cy"] + ay * hw + ny * hh],
        [box["cx"] - ax * hw + nx * hh, box["cy"] - ay * hw + ny * hh],
    ]
    skew = max(abs(angle - 90.0) for angle in _corner_angles(pixel_to_display(MODEL, corners)))
    assert skew > 5.0, skew


def test_scene_without_geo_keeps_the_pixel_path():
    proposal = _transfer("exemplar-nogeo", [_match(1400.0, 1100.0, angle_deg=45.0)])[0]
    assert proposal.get("polygon_scene_px") is None
    assert proposal["rotated_bbox"]["width"] > 0


def test_axis_aligned_geometry_is_left_alone():
    matches = [_match(1400.0, 1100.0)]
    out = transfer_exemplar_geometry(
        [dict(item) for item in matches],
        exemplar_bbox=EXEMPLAR_BBOX,
        geometry_type="bbox",
        exemplar_rotated_bbox=None,
        exemplar_front_vector=None,
        exemplar_polygon_scene_px=EXEMPLAR_POLYGON,
        project_id=PROJECT,
        scene_id=SCENE,
    )
    assert "rotated_bbox" not in out[0]


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
