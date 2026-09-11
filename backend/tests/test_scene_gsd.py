"""Pixel spacing i GSD z pelnej transformacji (DESIGN_DECISIONS.md, scene-import P0.7).

Wiekszosc testow dotyczy czystych funkcji `pixel_spacing_from_transform()` i
`gsd_from_pixel_spacing()` — bez wejscia/wyjscia, wiec przypadki brzegowe (obrot, jednostki
CRS, anizotropia) sa sprawdzane deterministycznie i szybko. Na koncu jeden test integracyjny
przechodzi przez `get_scene_info()` na prawdziwym, malym GeoTIFF-ie.

Wartosci odniesienia dla CRS geograficznego sa niezalezne od implementacji: dlugosc stopnia
na rowniku wynika wprost z parametrow WGS84 (`a` i `a(1-e^2)`), a nie z tej samej formuly,
ktora testujemy.

Uruchomienie: pytest backend/tests/test_scene_gsd.py
"""

from __future__ import annotations

import math
import pathlib
import sys

import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(BACKEND_ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT / "tests"))

from services.scene_loader import (  # noqa: E402
    gsd_from_pixel_spacing,
    pixel_spacing_from_transform,
)

#: Wartosci wynikajace wprost z definicji WGS84, nie z testowanej implementacji.
DEGREE_LON_AT_EQUATOR_M = 6378137.0 * math.radians(1.0)              # ~111319.49
DEGREE_LAT_AT_EQUATOR_M = 6378137.0 * (1.0 - 0.00669437999014) * math.radians(1.0)  # ~110574.4


def _degree_lengths(latitude_deg: float) -> tuple[float, float]:
    """Dlugosc stopnia dlugosci i szerokosci [m] — niezalezna kopia definicji WGS84."""
    ecc_sq = 2 * (1 / 298.257223563) - (1 / 298.257223563) ** 2
    lat = math.radians(latitude_deg)
    w = math.sqrt(1 - ecc_sq * math.sin(lat) ** 2)
    return (
        math.radians(1.0) * (6378137.0 / w) * math.cos(lat),
        math.radians(1.0) * (6378137.0 * (1 - ecc_sq) / w ** 3),
    )


def _north_up(spacing: float) -> list[float]:
    """Transformacja bez obrotu: [a, b, c, d, e, f]."""
    return [spacing, 0.0, 0.0, 0.0, -spacing, 0.0]


def _rotated(spacing: float, degrees: float) -> list[float]:
    """Transformacja obrocona o zadany kat, o zachowanym rozmiarze piksela."""
    theta = math.radians(degrees)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    return [
        spacing * cos_t,
        spacing * sin_t,
        0.0,
        spacing * sin_t,
        -spacing * cos_t,
        0.0,
    ]


# --- Obrot (bramka P0.7: "transformacja obrocona daje poprawny spacing obu osi") -------


def test_rotated_transform_preserves_pixel_size_on_both_axes():
    """Obrot nie zmienia rozmiaru piksela — zmienia tylko jego orientacje."""
    spacing_x, spacing_y = pixel_spacing_from_transform(
        _rotated(0.25, 79.0), is_geographic=False, linear_unit_factor=1.0
    )
    assert math.isclose(spacing_x, 0.25, rel_tol=1e-9)
    assert math.isclose(spacing_y, 0.25, rel_tol=1e-9)


def test_ignoring_rotation_terms_would_underestimate_spacing():
    """Kontrola kierunku bledu: stara formula `abs(a)` zaniza wynik o `cos(obrotu)`.

    Test nie wywoluje starej implementacji — odtwarza ja jawnie, zeby udokumentowac skale
    problemu zmierzona na rzeczywistym ICEYE CSI (obrot 103,26 stopnia, blad -81,2%).
    """
    transform = _rotated(0.25, 79.0)
    legacy = min(abs(transform[0]), abs(transform[4]))
    spacing_x, _ = pixel_spacing_from_transform(
        transform, is_geographic=False, linear_unit_factor=1.0
    )
    assert legacy < spacing_x
    assert math.isclose(legacy / spacing_x, math.cos(math.radians(79.0)), rel_tol=1e-9)


def test_rotation_by_ninety_degrees_is_not_degenerate():
    """Przy obrocie 90 stopni `a` wynosi zero — stara formula dawalaby spacing 0."""
    transform = _rotated(0.5, 90.0)
    assert math.isclose(abs(transform[0]), 0.0, abs_tol=1e-12)
    spacing_x, spacing_y = pixel_spacing_from_transform(
        transform, is_geographic=False, linear_unit_factor=1.0
    )
    assert math.isclose(spacing_x, 0.5, rel_tol=1e-9)
    assert math.isclose(spacing_y, 0.5, rel_tol=1e-9)


# --- Jednostki CRS (bramka: "CRS geograficzny i projektowy w jednostkach innych niz metr") --


def test_projected_crs_in_metres():
    spacing_x, spacing_y = pixel_spacing_from_transform(
        _north_up(2.0), is_geographic=False, linear_unit_factor=1.0
    )
    assert (spacing_x, spacing_y) == (2.0, 2.0)


def test_projected_crs_in_us_survey_feet_is_converted():
    """CRS w stopach musi dac metry — inaczej GSD jest zawyzone ~3,28x."""
    foot = 0.30480060960121924
    spacing_x, spacing_y = pixel_spacing_from_transform(
        _north_up(1.0), is_geographic=False, linear_unit_factor=foot
    )
    assert math.isclose(spacing_x, foot, rel_tol=1e-12)
    assert math.isclose(spacing_y, foot, rel_tol=1e-12)


def test_geographic_crs_uses_ellipsoid_not_fixed_constants():
    """Na rowniku dlugosc stopnia wynika z parametrow WGS84, a nie ze stalych 111320/110540.

    Stala 110540 uzywana wczesniej dla osi polnoc-poludnie jest wartoscia SREDNIA i myli sie
    na rowniku o okolo 34 m na stopien — test pilnuje, zeby nie wrocila.
    """
    spacing_x, spacing_y = pixel_spacing_from_transform(
        _north_up(1.0), is_geographic=True, latitude_deg=0.0
    )
    assert math.isclose(spacing_x, DEGREE_LON_AT_EQUATOR_M, rel_tol=1e-9)
    assert math.isclose(spacing_y, DEGREE_LAT_AT_EQUATOR_M, rel_tol=1e-9)
    assert not math.isclose(spacing_y, 110540.0, rel_tol=1e-4)


def test_geographic_spacing_shrinks_with_latitude():
    """Oś wschod-zachod skraca sie jak `cos(szerokosci)`; polnoc-poludnie prawie nie."""
    equator_x, equator_y = pixel_spacing_from_transform(
        _north_up(1.0), is_geographic=True, latitude_deg=0.0
    )
    north_x, north_y = pixel_spacing_from_transform(
        _north_up(1.0), is_geographic=True, latitude_deg=60.0
    )
    ratio = north_x / equator_x
    # Stosunek NIE jest dokladnie `cos(60)`: promien poprzeczny elipsoidy rosnie z szerokoscia
    # (N(60)/N(0) = 1,0025), wiec wynik jest o okolo 0,25% wiekszy. Model sferyczny dalby
    # dokladnie 0,5 — ta roznica jest wlasnie tym, co odroznia elipsoide od stalych.
    assert 0.5 < ratio < 0.505, ratio
    assert math.isclose(ratio, math.cos(math.radians(60.0)), rel_tol=3e-3)
    assert 0.99 < north_y / equator_y < 1.01


def test_geographic_crs_without_latitude_returns_nothing():
    """Bez szerokosci nie da sie przeliczyc stopni — cicha zamiana na metry byla bledem."""
    assert pixel_spacing_from_transform(_north_up(1.0), is_geographic=True) == (None, None)


def test_projected_crs_without_unit_factor_returns_nothing():
    """Nieznana jednostka osi ma dac brak wyniku, nie zalozenie metrow."""
    assert pixel_spacing_from_transform(
        _north_up(1.0), is_geographic=False, linear_unit_factor=None
    ) == (None, None)


# --- Skalarny GSD i anizotropia -------------------------------------------------------


def test_scalar_gsd_keeps_backward_compatible_min_semantics():
    """`gsd_m` pozostaje `min(x, y)` — zmiana definicji to osobna decyzja migracyjna."""
    assert gsd_from_pixel_spacing(0.2159, 0.2998) == 0.2159
    assert gsd_from_pixel_spacing(2.0, 2.0) == 2.0


def test_scalar_gsd_ignores_missing_axis():
    assert gsd_from_pixel_spacing(0.5, None) == 0.5
    assert gsd_from_pixel_spacing(None, None) is None


def test_anisotropic_spacing_is_preserved_in_both_axes():
    """Piksel kwadratowy w stopniach jest anizotropowy w metrach — obie osie musza przetrwac.

    Na szerokosci 44 stopni stosunek osi to okolo `cos(44)`. Wczesniej `min()` zwracalo
    wylacznie mniejsza wartosc i informacja o drugiej osi znikala bezpowrotnie.
    """
    spacing_x, spacing_y = pixel_spacing_from_transform(
        _north_up(1.0), is_geographic=True, latitude_deg=44.0
    )
    assert math.isclose(spacing_x / spacing_y, math.cos(math.radians(44.0)), rel_tol=5e-3)
    assert gsd_from_pixel_spacing(spacing_x, spacing_y) == round(min(spacing_x, spacing_y), 4)


# --- SAR: brak zgadywania range/azimuth ----------------------------------------------


def test_sar_range_and_azimuth_are_not_inferred_from_the_transform():
    """Bramka P0.7: SAR nie moze dostac range/azimuth wymyslonych z transformacji.

    Dla produktu naziemnie zrzutowanego (GEO/GRD) osie transformacji to osie MAPY, a nie
    kierunki range i azimuth. Pola pozostaja puste, dopoki nie dostarczy ich dostawca.
    """
    from models.project import SceneInfo

    info = SceneInfo(width=8, height=8, channels=1, dtype="uint8")
    assert info.pixel_spacing_range_m is None
    assert info.pixel_spacing_azimuth_m is None


# --- Integracja z get_scene_info() ----------------------------------------------------


def test_get_scene_info_reports_spacing_for_a_rotated_raster(tmp_path):
    """Pelna sciezka: obrocony GeoTIFF w UTM ma poprawny spacing i oznaczona metode."""
    from fixtures.scene_packages import build_rotated_transform_raster
    from services.scene_loader import get_scene_info

    path = build_rotated_transform_raster(tmp_path, spacing=0.25, rotation_deg=79.0)
    info = get_scene_info(path)

    assert info.has_geo is True
    assert math.isclose(info.pixel_spacing_x_m, 0.25, rel_tol=1e-6)
    assert math.isclose(info.pixel_spacing_y_m, 0.25, rel_tol=1e-6)
    assert math.isclose(info.gsd_m, 0.25, rel_tol=1e-6)
    assert info.gsd_source == "transform"
    assert info.gsd_method == "affine_projected"


# --- Fixture liczbowe per dostawca (bramka P0.7) --------------------------------------
#
# Wspolrzedne pochodza z RZECZYWISTYCH rastrow czterech zrodel z sekcji 4.2 roadmapy,
# odczytane 2026-08-31. Same wspolczynniki transformacji i CRS nie sa danymi obrazowymi
# ani wlasnoscia dostawcy, wiec moga trafic do CI — w odroznieniu od samych rastrow.
# Dzieki temu bramka jest powtarzalna bez dostepu do udzialu sieciowego.

PROVIDER_TRANSFORM_CASES = [
    # (nazwa, transform, geograficzny, szerokosc, unit_factor, oczekiwany X, oczekiwany Y)
    (
        "iceye_csi_rotated",
        (-1.1830769562756764e-06, 3.7565223483989133e-06, 35.92729189808518,
         5.019187928441649e-06, 5.927112285162996e-07, 35.37698447742392),
        True, 35.40337183696798, None, 0.5671, 0.3475,
    ),
    (
        "capella_geo_utm36n",
        (0.35, 0.0, 561321.4016699266, 0.0, -0.35, 6860299.41494905),
        False, None, 1.0, 0.35, 0.35,
    ),
    (
        "worldview_mul_utm34n",
        (2.0, 0.0, 494900.0, 0.0, -2.0, 5778286.0),
        False, None, 1.0, 2.0, 2.0,
    ),
    (
        "pleiades_phr1a_wgs84",
        (4.629629629630647e-06, 0.0, 33.952444444444446,
         0.0, -4.629629629629926e-06, 45.13687037037038),
        True, 45.11667592592593, None, 0.3643, 0.5145,
    ),
]


@pytest.mark.parametrize(
    "name,transform,is_geographic,latitude,unit_factor,expected_x,expected_y",
    PROVIDER_TRANSFORM_CASES,
    ids=[case[0] for case in PROVIDER_TRANSFORM_CASES],
)
def test_provider_transform_yields_expected_spacing(
    name, transform, is_geographic, latitude, unit_factor, expected_x, expected_y
):
    spacing_x, spacing_y = pixel_spacing_from_transform(
        list(transform),
        is_geographic=is_geographic,
        linear_unit_factor=unit_factor,
        latitude_deg=latitude,
    )
    assert spacing_x == pytest.approx(expected_x, abs=5e-4), (name, spacing_x)
    assert spacing_y == pytest.approx(expected_y, abs=5e-4), (name, spacing_y)


def test_rotated_iceye_case_is_the_one_the_legacy_formula_broke():
    """Regresja konkretnego przypadku: obrocony ICEYE CSI zanizany o ponad 80%.

    Stara formula brala `min(abs(a), abs(e))` i dla tej transformacji dawala 0,0655 m
    zamiast 0,3475 m. Test pilnuje obu liczb naraz, zeby poprawka nie cofnela sie niepostrzezenie.
    """
    transform = list(PROVIDER_TRANSFORM_CASES[0][1])
    latitude = PROVIDER_TRANSFORM_CASES[0][3]
    lon_m, lat_m = _degree_lengths(latitude)
    legacy_x = abs(transform[0]) * lon_m
    legacy_y = abs(transform[4]) * lat_m
    legacy_gsd = round(min(legacy_x, legacy_y), 4)

    spacing_x, spacing_y = pixel_spacing_from_transform(
        transform, is_geographic=True, latitude_deg=latitude
    )
    corrected_gsd = gsd_from_pixel_spacing(spacing_x, spacing_y)

    assert legacy_gsd == pytest.approx(0.0655, abs=2e-3), legacy_gsd
    assert corrected_gsd == pytest.approx(0.3475, abs=2e-3), corrected_gsd
    assert corrected_gsd / legacy_gsd > 5.0
