"""Rozciagniecie tonalne: jedna semantyka, progi wyznaczane RAZ dla sceny, jedna kwantyzacja.

Ta sama nastawa „2-98%" znaczyla wczesniej trzy rozne rzeczy: panel pokazywal zakres DN
z histogramu sceny, sciezka pikselowa liczyla percentyle NA KAFLU (zmierzone `1->153`
w srodku i `4->104` gdzie indziej tej samej sceny), a sciezka geo traktowala 2 i 98 jako
procent z 255, czyli praktycznie nie rozciagala. Te testy pilnuja, ze wszystkie trzy
mowia teraz o tej samej liczbie.

Druga czesc (DESIGN_DECISIONS.md, display-stretch A): okno sceny wchodzi do konwersji do uint8, a nie
na obraz juz skwantowany oknem bazowym. Dla SAR stara kolejnosc zostawiala ~35 odcieni
w kaflu z przerwa co piaty poziom.

Run: python backend/tests/test_display_stretch_semantics.py
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

TEST_ROOT = pathlib.Path(tempfile.mkdtemp(prefix="geotile-stretch-"))
os.environ["DATA_DIR"] = str(TEST_ROOT / "data")

import numpy as np  # noqa: E402

from services.image_preprocessor import apply_display_params  # noqa: E402
from services.scene_histogram import percentile_to_value  # noqa: E402
from utils.image import ensure_rgb_uint8, render_display_window, scene_display_window  # noqa: E402


HISTOGRAM = {
    "sample_count": 1000,
    "percentiles": {
        "p0": 0.0, "p1": 1.0, "p2": 1.0, "p5": 4.0, "p10": 8.0, "p25": 22.0,
        "p50": 47.0, "p75": 96.0, "p90": 142.0, "p95": 155.0, "p98": 162.0,
        "p99": 175.0, "p100": 255.0,
    },
}


def _tile(values: list[int]) -> np.ndarray:
    """Kafel 8x8x3 zlozony z podanych wartosci, powtarzanych cyklicznie."""
    flat = np.array((values * 200)[: 8 * 8 * 3], dtype=np.uint8)
    return flat.reshape(8, 8, 3)


def test_percentile_maps_to_the_value_shown_in_the_panel():
    assert percentile_to_value(HISTOGRAM, 2.0) == 1.0
    assert percentile_to_value(HISTOGRAM, 98.0) == 162.0
    # Interpolacja miedzy punktami, tak jak w panelu.
    middle = percentile_to_value(HISTOGRAM, 96.5)
    assert 155.0 < middle < 162.0


def test_uint8_scene_keeps_the_source_domain():
    assert scene_display_window(1.0, 162.0, None, None, None, True) == (1.0, 162.0)


def test_scene_window_is_in_the_display_domain():
    """Okno sceny zastepuje okno bazowe, wiec musi byc w tej samej dziedzinie co ono."""
    # Scena liniowa z oknem bazowym: dziedzina wyswietlania to DN zrodla.
    assert scene_display_window(300.0, 900.0, 100.0, 1100.0, None, False) == (300.0, 900.0)
    # SAR z histogramem w DN zrodla (stary cache): prog przechodzi przez log1p.
    low, high = scene_display_window(1000.0, 4000.0, 5.0, 10.0, "uint16_log", False)
    assert abs(low - np.log1p(1000.0)) < 1e-4 and abs(high - np.log1p(4000.0)) < 1e-4
    # SAR z histogramem juz w dziedzinie wyswietlania: bez drugiego logarytmu.
    assert scene_display_window(6.9, 8.3, 5.0, 10.0, "uint16_log", False, True) == (6.9, 8.3)


def test_non_deterministic_mapping_declines_instead_of_guessing():
    """Galezie liczace percentyle na kaflu nie daja sie przeliczyc — maja zwrocic None."""
    assert scene_display_window(100.0, 600.0, None, None, "linear_robust", False) is None
    assert scene_display_window(100.0, 600.0, None, None, None, False) is None
    assert scene_display_window(600.0, 600.0, 100.0, 1100.0, None, False) is None


def test_same_bounds_give_the_same_mapping_in_every_tile():
    """Sedno punktu 2: dwa rozne kafle, jedno odwzorowanie."""
    bright = _tile([120, 140, 160])
    dark = _tile([4, 8, 12])
    bounds = (1.0, 162.0)

    for tile in (bright, dark):
        out = apply_display_params(tile, stretch_bounds=bounds)
        # Odwzorowanie musi byc funkcja WARTOSCI, nie zawartosci kafla.
        for value in np.unique(tile):
            expected = np.clip((float(value) - 1.0) / (162.0 - 1.0) * 255.0, 0, 255)
            got = out[tile == value]
            assert np.allclose(got, round(float(expected)), atol=1), (value, expected, got[:3])


def test_per_tile_percentiles_did_differ_between_tiles():
    """Dowod, ze problem byl realny: bez progow sceny te same DN wychodza inaczej."""
    bright = _tile([120, 140, 160])
    dark = _tile([4, 8, 12])

    bright_out = apply_display_params(bright, stretch_low=2.0, stretch_high=98.0)
    dark_out = apply_display_params(dark, stretch_low=2.0, stretch_high=98.0)
    # Ta sama wartosc 8 nie wystepuje w obu kaflach, wiec porownujemy rozpietosc:
    # kazdy kafel rozciagnal sie na pelna skale niezaleznie od swojej jasnosci.
    assert bright_out.max() - bright_out.min() > 200
    assert dark_out.max() - dark_out.min() > 200
    # ...czyli ciemny kafel udaje jasny — dokladnie ten efekt usuwaja progi sceny.
    assert abs(int(dark_out.mean()) - int(bright_out.mean())) < 40


def test_bounds_take_precedence_over_percent_of_255_fallback():
    """Sedno punktu 1: sciezka geo nie moze juz czytac 2/98 jako procentu z 255."""
    tile = _tile([50, 100, 150])
    fallback = apply_display_params(
        tile, stretch_low=2.0, stretch_high=98.0, percentile_stretch=False
    )
    unified = apply_display_params(
        tile, stretch_low=2.0, stretch_high=98.0, percentile_stretch=False,
        stretch_bounds=(1.0, 162.0),
    )
    assert not np.array_equal(fallback, unified)
    # Stary wariant mapowal 5,1 -> 0 i 249,9 -> 255, czyli prawie nic nie zmienial.
    assert abs(int(fallback.mean()) - int(tile.mean())) < 6
    # Nowy realnie rozciaga: srodek histogramu ladnie w gore.
    assert int(unified.mean()) > int(tile.mean()) + 30


def test_router_resolves_bounds_from_the_scene_histogram():
    """Spiecie w routerze: histogram sceny -> okno. Cichy `None` cofnalby nas do stanu
    sprzed poprawki, wiec sama konwersja jednostek to za malo — testujemy droge."""
    import services.scene_histogram as histogram_module
    from routers.scenes import _compute_scene_display_window

    original = histogram_module.get_scene_histogram
    histogram_module.get_scene_histogram = lambda *args, **kwargs: HISTOGRAM
    try:
        window = _compute_scene_display_window("p", "s", {"dtype": "uint8"}, 2.0, 98.0)
    finally:
        histogram_module.get_scene_histogram = original

    assert window == (1.0, 162.0)


def test_router_window_for_sar_stays_in_the_log_domain():
    """Histogram SAR jest juz po log1p — okno ma byc wprost jego wartosciami."""
    import services.scene_histogram as histogram_module
    from routers.scenes import _compute_scene_display_window

    sar_histogram = {
        "sample_count": 1000,
        "domain": "display",
        "percentiles": {"p0": 5.9, "p2": 6.576, "p50": 7.006, "p98": 7.55, "p100": 9.8},
    }
    si = {"dtype": "uint16", "display_min": 5.373, "display_max": 9.275,
          "display_mode": "uint16_log"}
    original = histogram_module.get_scene_histogram
    histogram_module.get_scene_histogram = lambda *args, **kwargs: sar_histogram
    try:
        window = _compute_scene_display_window("p", "s-sar", si, 2.0, 98.0)
    finally:
        histogram_module.get_scene_histogram = original

    assert window == (6.576, 7.55)


def test_router_declines_when_the_scene_has_no_histogram():
    import services.scene_histogram as histogram_module
    from routers.scenes import _compute_scene_display_window

    original = histogram_module.get_scene_histogram
    histogram_module.get_scene_histogram = lambda *args, **kwargs: {"sample_count": 0}
    try:
        window = _compute_scene_display_window("p", "s", {"dtype": "uint8"}, 2.0, 98.0)
    finally:
        histogram_module.get_scene_histogram = original

    assert window is None


def test_full_range_setting_changes_nothing():
    tile = _tile([10, 120, 240])
    out = apply_display_params(tile, stretch_low=0.0, stretch_high=100.0)
    assert np.array_equal(out, tile)


# --- Poprawka A: jedna kwantyzacja -------------------------------------------------------

def _sar_tile(seed: int, sigma: float = 0.35) -> np.ndarray:
    """Amplituda SAR uint16 o rozkladzie log-normalnym, jak na scenie Capella."""
    rng = np.random.default_rng(seed)
    values = np.clip(rng.lognormal(7.0, sigma, (256, 256)), 1, 65535)
    return values.astype(np.uint16)[:, :, np.newaxis]


def _core_levels(gray: np.ndarray) -> tuple[int, int]:
    """(liczba uzywanych poziomow w srodkowych 90% jasnosci, najwieksza przerwa miedzy nimi)."""
    occupied = np.flatnonzero(np.bincount(gray.ravel(), minlength=256))
    low, high = np.percentile(gray, [5, 95])
    core = occupied[(occupied >= low) & (occupied <= high)]
    return int(core.size), int(np.max(np.diff(core))) if core.size > 1 else 0


def _sar_windows(raw: np.ndarray) -> tuple[tuple[float, float], tuple[float, float]]:
    """(okno bazowe jak w charakteryzacji sceny, okno nastawy 2-98%) — oba w log1p."""
    logs = np.log1p(raw.astype(np.float64))
    base = (float(np.percentile(logs, 0.1)) - 1.0, float(np.percentile(logs, 99.9)) + 1.0)
    stretch = (float(np.percentile(logs, 2)), float(np.percentile(logs, 98)))
    return base, stretch


def test_single_quantization_keeps_the_gradation_of_a_sar_tile():
    raw = _sar_tile(11)
    (base_low, base_high), (low, high) = _sar_windows(raw)

    # Stara kolejnosc: okno bazowe -> uint8 -> rozciagniecie LUT na skwantowanym obrazie.
    to_255 = lambda value: (value - base_low) / (base_high - base_low) * 255.0  # noqa: E731
    base = ensure_rgb_uint8(raw, base_low, base_high, "uint16_log")
    old = apply_display_params(base, 1.0, 1.1, 0.85, stretch_bounds=(to_255(low), to_255(high)))
    # Nowa: okno nastawy + regulacje tonalne profilu SAR we float, jedna kwantyzacja.
    new = render_display_window(raw, "uint16_log", (low, high), 1.0, 1.1, 0.85)

    old_levels, old_gap = _core_levels(old[:, :, 0])
    new_levels, new_gap = _core_levels(new[:, :, 0])
    assert old_levels < 100 and old_gap >= 3, (old_levels, old_gap)  # dowod starego problemu
    assert new_levels >= 180 and new_gap == 1, (new_levels, new_gap)
    assert new.shape == (256, 256, 3) and np.array_equal(new[:, :, 0], new[:, :, 2])


def test_window_render_maps_the_same_value_the_same_way_in_every_tile():
    """Okno jest wlasnoscia sceny: ten sam DN w dwoch roznych kaflach -> ta sama jasnosc."""
    bright, dark = _sar_tile(1, 0.2), _sar_tile(2, 0.6)
    shared = 1500
    bright[0, 0, 0] = dark[0, 0, 0] = shared
    window = (6.5, 7.8)
    out_bright = render_display_window(bright, "uint16_log", window, 1.0, 1.1, 0.85)
    out_dark = render_display_window(dark, "uint16_log", window, 1.0, 1.1, 0.85)
    assert np.array_equal(out_bright[0, 0], out_dark[0, 0])


def test_uint8_window_render_is_identical_to_the_lut():
    """EO uint8: nowa droga to ta sama arytmetyka co LUT, wiec obraz nie moze sie zmienic."""
    rng = np.random.default_rng(7)
    tile = rng.integers(0, 256, (64, 64, 3), dtype=np.uint8)
    for tonal in ((1.0, 1.0, 1.0), (1.2, 0.8, 1.4), (0.9, 1.3, 0.7)):
        for window in ((1.0, 162.0), (40.0, 90.0)):
            expected = apply_display_params(tile, *tonal, stretch_bounds=window)
            got = render_display_window(tile, None, window, *tonal)
            assert np.array_equal(got, expected), (tonal, window)


def test_geotiff_window_read_quantizes_once_and_keeps_padding_black():
    import rasterio
    from rasterio.transform import from_origin

    from routers.scenes import _read_geotiff_window

    raw = _sar_tile(21)
    path = TEST_ROOT / "sar_window.tif"
    with rasterio.open(
        path, "w", driver="GTiff", width=256, height=256, count=1, dtype="uint16",
        transform=from_origin(20.0, 60.0, 1e-4, 1e-4), crs="EPSG:4326",
    ) as dst:
        dst.write(raw[:, :, 0][np.newaxis])
    (base_low, base_high), window = _sar_windows(raw)
    si = {"display_min": base_low, "display_max": base_high, "display_mode": "uint16_log"}

    # Krawedz sceny: 200 x 200 px danych w kaflu 256 x 256.
    tile = _read_geotiff_window(path, si, 0, 0, 200, 200, 256, 200, 200,
                                display_window=window, tonal=(1.0, 1.1, 0.85))
    assert tile.shape == (256, 256, 3)
    assert not tile[:, 200:].any() and not tile[200:, :].any()
    levels, gap = _core_levels(tile[:200, :200, 0])
    assert levels >= 150 and gap == 1, (levels, gap)

    # Bez okna: dotychczasowa droga przez okno bazowe, bit w bit.
    plain = _read_geotiff_window(path, si, 0, 0, 200, 200, 256, 200, 200)
    expected = ensure_rgb_uint8(raw[:200, :200], base_low, base_high, "uint16_log")
    assert np.array_equal(plain[:200, :200], expected)


def test_tonal_only_adjustment_uses_the_base_window():
    """Same regulacje tonalne na scenie z oknem bazowym tez ida jedna kwantyzacja."""
    from routers.scenes import _tile_display_window

    with_window = {"si": {"display_min": 5.0, "display_max": 10.0}, "scene_mtime_key": 1}
    without_window = {"si": {"dtype": "uint8"}, "scene_mtime_key": 1}
    assert _tile_display_window("p", "t1", with_window, 1.0, 1.1, 0.85, 0.0, 100.0) == (5.0, 10.0)
    assert _tile_display_window("p", "t2", without_window, 1.0, 1.1, 0.85, 0.0, 100.0) is None
    assert _tile_display_window("p", "t3", with_window, 1.0, 1.0, 1.0, 0.0, 100.0) is None


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
