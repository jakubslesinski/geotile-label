"""Histogram v2: krzywe per pasmo, luminancja i dziedzina wyswietlania.

Dwie rzeczy naraz:
  1. wykres dla scen wielopasmowych da sie rozdzielic na pasma (dzis jedna zlana krzywa
     ukrywa, ze pasma maja rozne rozklady — w QGIS widac to od razu);
  2. dla scen z `display_mode` (SAR) histogram liczy sie w dziedzinie WYSWIETLANIA.
     Zmierzone na Capelli: w dziedzinie surowej szczyt wypada w binie 6/128, po `log1p`
     w 66/128 — wykres byl przyklejony do lewej krawedzi, choc obraz ma mase tonalna
     posrodku.

Zadna z tych zmian NIE MOZE ruszyc renderowania: progi bierze sie z krzywej zlanej,
a percentyle sa niezmiennicze wzgledem przeksztalcenia monotonicznego.

Run: python backend/tests/test_scene_histogram_v2.py
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

TEST_ROOT = pathlib.Path(tempfile.mkdtemp(prefix="geotile-hist-v2-"))
os.environ["DATA_DIR"] = str(TEST_ROOT / "data")

import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402

from services.scene_histogram import compute_raster_histogram  # noqa: E402
from utils.image import scene_display_window  # noqa: E402


def _write_raster(name: str, data: np.ndarray) -> pathlib.Path:
    path = TEST_ROOT / name
    count, height, width = data.shape
    with rasterio.open(
        path, "w", driver="GTiff", width=width, height=height, count=count,
        dtype=data.dtype, transform=from_origin(20.0, 60.0, 1e-4, 1e-4), crs="EPSG:4326",
    ) as dst:
        dst.write(data)
    return path


def _multiband() -> pathlib.Path:
    """Trzy pasma o CELOWO roznych rozkladach — jak w realnej scenie PMS."""
    rng = np.random.default_rng(3)
    bands = [
        np.clip(rng.normal(40, 12, (256, 256)), 0, 255),
        np.clip(rng.normal(90, 12, (256, 256)), 0, 255),
        np.clip(rng.normal(150, 12, (256, 256)), 0, 255),
    ]
    return _write_raster("ms.tif", np.stack(bands).astype(np.uint8))


def _single_band_uint16() -> pathlib.Path:
    rng = np.random.default_rng(5)
    values = np.clip(rng.lognormal(7.0, 0.5, (256, 256)), 1, 65535)
    return _write_raster("sar.tif", values.astype(np.uint16)[np.newaxis, :, :])


def test_multiband_scene_exposes_one_curve_per_band():
    histogram = compute_raster_histogram(_multiband())
    assert histogram["band_count"] == 3
    assert len(histogram["bands"]) == 3
    assert all(len(band["bins"]) == len(histogram["bins"]) for band in histogram["bands"])

    medians = [band["percentiles"]["p50"] for band in histogram["bands"]]
    assert medians[0] < medians[1] < medians[2], medians
    # Krzywa zlana nie moze zdradzac tej struktury — po to sa krzywe pasm.
    assert abs(histogram["percentiles"]["p50"] - medians[1]) < 15


def test_band_curves_share_the_axis():
    """Trzy krzywe maja sens tylko na wspolnej osi wartosci."""
    histogram = compute_raster_histogram(_multiband())
    total = sum(sum(band["bins"]) for band in histogram["bands"])
    assert total > 0
    # Suma licznosci pasm odpowiada licznosci krzywej zlanej (te same probki).
    assert abs(total - sum(histogram["bins"])) / max(1, sum(histogram["bins"])) < 0.02


def test_luminance_curve_sits_between_the_bands():
    histogram = compute_raster_histogram(_multiband())
    luminance = histogram["luminance"]
    assert luminance is not None
    medians = [band["percentiles"]["p50"] for band in histogram["bands"]]
    assert medians[0] < luminance["percentiles"]["p50"] < medians[2]


def test_single_band_scene_has_no_luminance_curve():
    """PAN i SAR: przelacznik ma byc ukryty, wiec luminancji nie liczymy."""
    histogram = compute_raster_histogram(_single_band_uint16())
    assert histogram["band_count"] == 1
    assert histogram["luminance"] is None
    assert len(histogram["bands"]) == 1


def test_display_mode_moves_the_histogram_into_the_rendered_domain():
    path = _single_band_uint16()
    source = compute_raster_histogram(path)
    display = compute_raster_histogram(path, display_mode="uint16_log")

    assert source["domain"] == "source"
    assert display["domain"] == "display"
    assert display["display_mode"] == "uint16_log"
    # Rozklad lognormalny w dziedzinie surowej jest zepchniety w lewo, po logarytmie
    # symetryczny — szczyt musi sie przesunac wyraznie w prawo.
    assert int(np.argmax(display["bins"])) > int(np.argmax(source["bins"])) + 20


def test_percentiles_stay_invariant_so_the_render_does_not_move():
    path = _single_band_uint16()
    source = compute_raster_histogram(path)
    display = compute_raster_histogram(path, display_mode="uint16_log")

    p2_source = source["percentiles"]["p2"]
    p2_display = display["percentiles"]["p2"]
    assert abs(np.log1p(p2_source) - p2_display) < 1e-6
    p98_source = source["percentiles"]["p98"]
    p98_display = display["percentiles"]["p98"]

    low, high = 5.0, 10.0  # okno bazowe profilu SAR, w dziedzinie log
    before = scene_display_window(p2_source, p98_source, low, high, "uint16_log", False, False)
    after = scene_display_window(p2_display, p98_display, low, high, "uint16_log", False, True)
    assert before is not None and after is not None
    assert np.allclose(before, after, atol=1e-5), (before, after)


def test_tail_percentiles_keep_their_decimal_keys():
    """v3: p0.1 ... p99.9. Klucz `p{int(point)}` zlewal 99,5 z 99 i gubil punkty."""
    from services.scene_histogram import HISTOGRAM_VERSION, PERCENTILE_POINTS

    histogram = compute_raster_histogram(_single_band_uint16(), display_mode="uint16_log")
    assert histogram["schema_version"] == HISTOGRAM_VERSION >= 3
    keys = list(histogram["percentiles"])
    assert len(keys) == len(PERCENTILE_POINTS)
    for key in ("p0.1", "p0.5", "p99.5", "p99.8", "p99.9"):
        assert key in histogram["percentiles"], key
    values = [histogram["percentiles"][key] for key in keys]
    assert values == sorted(values)
    assert all(len(band["percentiles"]) == len(PERCENTILE_POINTS) for band in histogram["bands"])


def test_sar_upper_threshold_is_not_interpolated_towards_the_scene_maximum():
    """Nastawa SAR 1-99,8%: przy samych p99 i p100 prog wychodzil prawie przy maksimum."""
    from services.scene_histogram import percentile_to_value

    rng = np.random.default_rng(9)
    values = np.clip(rng.lognormal(7.0, 0.35, (256, 256)), 1, 65535).astype(np.uint16)
    values[0, 0] = 60000  # jeden jasny reflektor narozny — punkt odstajacy
    path = _write_raster("sar_outlier.tif", values[np.newaxis, :, :])
    histogram = compute_raster_histogram(path, display_mode="uint16_log")

    logs = np.log1p(values.astype(np.float64))
    exact = float(np.percentile(logs, 99.8))
    assert abs(percentile_to_value(histogram, 99.8) - exact) < 1e-6
    # Stara siatka punktow: interpolacja miedzy p99 a maksimum.
    p99, p100 = float(np.percentile(logs, 99)), float(logs.max())
    legacy = p99 + (p100 - p99) * 0.8
    assert legacy - exact > 1.0, (legacy, exact)


def test_masked_integer_raster_does_not_crash():
    """`filled(nan)` na uint16 rzuca TypeError — rzutowanie na float musi byc pierwsze."""
    path = TEST_ROOT / "masked.tif"
    values = np.full((1, 64, 64), 500, dtype=np.uint16)
    values[0, :10, :] = 0
    with rasterio.open(
        path, "w", driver="GTiff", width=64, height=64, count=1, dtype="uint16",
        transform=from_origin(20.0, 60.0, 1e-4, 1e-4), crs="EPSG:4326", nodata=0,
    ) as dst:
        dst.write(values)

    histogram = compute_raster_histogram(path, display_mode="uint16_log")
    assert histogram["sample_count"] > 0
    assert histogram["domain"] == "display"


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
