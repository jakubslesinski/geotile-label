"""Per-scene value histogram + statistics for the display panel.

A deterministic sub-sampled read of the scene raster yields the distribution and
robust statistics the tonal-stretch UI needs (bins, min/max, mean/std, common
percentiles). Cached per scene keyed by the raster's mtime — histograms don't
change unless the raster does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from db.storage import scene_dir
from services.scene_raster_resolver import resolve_scene_raster

#: v2 dokłada krzywe PER PASMO, krzywą luminancji i pole `domain`. v3 zagęszcza ogony
#: percentyli (p0.1, p0.5, p99.5, p99.8, p99.9). Podbicie wersji samo unieważnia cache,
#: bo `get_scene_histogram` porównuje `schema_version`.
HISTOGRAM_VERSION = 3
DEFAULT_BINS = 128
SAMPLE_LIMIT = 1_500_000  # ~deterministic; caps read cost on huge scenes
#: Gęste ogony są potrzebne nastawie SAR 1–99,8% (DESIGN_DECISIONS.md, display-stretch B). Przy samych
#: p99 i p100 próg 99,8 wychodził z interpolacji liniowej między p99 a MAKSIMUM sceny —
#: pojedynczym punktem odstającym — i obraz robił się za ciemny.
PERCENTILE_POINTS = [
    0.0, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 25.0, 50.0, 75.0, 90.0, 95.0, 98.0, 99.0,
    99.5, 99.8, 99.9, 100.0,
]


def percentile_key(point: float) -> str:
    """`p2`, `p99.8` — klucz z częścią dziesiętną. Dawne `p{int(point)}` zlewało 99,5 z 99."""
    return f"p{float(point):g}"
#: Wagi luminancji (Rec. 601) — jedna krzywa dla scen wielopasmowych, odpowiadająca temu,
#: co widzi oko. Zlanie wszystkich wartości w jeden ciąg (wariant v1) daje wykres
#: zdominowany przez pasmo o najszerszym rozkładzie i nie mówi nic o ekspozycji.
LUMINANCE_WEIGHTS = (0.299, 0.587, 0.114)


def _as_float_with_nan(data: np.ndarray) -> np.ndarray:
    """Tablica float z maską zamienioną na NaN.

    `filled(np.nan)` na tablicy całkowitoliczbowej rzuca `TypeError` — rzutowanie na
    float MUSI być pierwsze, inaczej maskowany uint16 (typowy dla SAR) wywraca odczyt.
    """
    if np.ma.isMaskedArray(data):
        return np.ma.filled(data.astype(np.float64), np.nan)
    return np.asarray(data, dtype=np.float64)


def _band_series(data: np.ndarray) -> list[np.ndarray]:
    """Wartości skończone każdego pasma osobno, jako lista 1-D."""
    series: list[np.ndarray] = []
    for index in range(data.shape[0]):
        band = np.asarray(data[index], dtype=np.float64).ravel()
        series.append(band[np.isfinite(band)])
    return series


def _curve(values: np.ndarray, low: float, high: float, bins: int) -> dict[str, Any]:
    """Krzywa dla jednej serii, binowana we WSPÓLNYM zakresie osi.

    Wspólny zakres jest warunkiem czytelności: trzy krzywe pasm mają sens tylko wtedy,
    gdy leżą na tej samej osi wartości. Percentyle liczone są z pełnej serii, nie
    z przyciętej — przycięcie dotyczy wyłącznie rysunku.
    """
    if values.size == 0:
        return {
            "bins": [0] * bins,
            "min": low,
            "max": high,
            "mean": 0.0,
            "std": 0.0,
            "percentiles": {percentile_key(point): 0.0 for point in PERCENTILE_POINTS},
            "sample_count": 0,
        }
    counts, _edges = np.histogram(np.clip(values, low, high), bins=bins, range=(low, high))
    percentile_values = np.percentile(values, PERCENTILE_POINTS)
    return {
        "bins": [int(count) for count in counts],
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "mean": float(np.mean(values)),
        "std": float(np.std(values)),
        "percentiles": {
            percentile_key(point): float(value)
            for point, value in zip(PERCENTILE_POINTS, percentile_values)
        },
        "sample_count": int(values.size),
    }


def compute_raster_histogram(
    path: str | Path,
    bins: int = DEFAULT_BINS,
    display_mode: str | None = None,
) -> dict[str, Any]:
    """Histogram + statistics from a deterministic sub-sample of the raster.

    ``display_mode`` przenosi CAŁY histogram do dziedziny wyświetlania. Bez tego dla
    scen SAR wykres pokazuje rozkład surowych DN, a ekran — ten sam rozkład po `log1p`:
    zmierzone na Capelli szczyt wypada wtedy w binie 6/128 zamiast 66/128, czyli wykres
    jest przyklejony do lewej krawędzi, choć obraz ma masę tonalną pośrodku. Percentyle
    są niezmiennicze względem przekształcenia monotonicznego, więc uchwyty tną te same
    piksele — kłamie wyłącznie kształt, na którym użytkownik opiera decyzję.
    """
    import rasterio

    path = Path(path)
    with rasterio.open(path) as src:
        band_count = min(3, src.count)
        width, height = src.width, src.height
        total = max(1, width * height)
        # Even decimation so the sample is spread across the whole scene.
        scale = min(1.0, (SAMPLE_LIMIT / (total * band_count)) ** 0.5)
        out_w = max(1, int(width * scale))
        out_h = max(1, int(height * scale))
        data = src.read(
            indexes=list(range(1, band_count + 1)),
            out_shape=(band_count, out_h, out_w),
            masked=True,
        )

    if display_mode:
        from utils.image import _apply_display_mode

        transformed = _apply_display_mode(_as_float_with_nan(data).astype(np.float32), display_mode)
        data = (
            np.ma.masked_array(transformed, mask=np.ma.getmaskarray(data))
            if np.ma.isMaskedArray(data)
            else transformed
        )

    summary = summarize_values(data, bins)
    return {
        **summary,
        #: W jakiej dziedzinie sa wszystkie wartosci powyzej. `display` znaczy, ze
        #: przeksztalcenie `display_mode` zostalo juz nalozone i NIE wolno go nakladac
        #: drugi raz przy przeliczaniu progow.
        "domain": "display" if display_mode else "source",
        "display_mode": display_mode,
    }


def summarize_values(data: np.ndarray, bins: int = DEFAULT_BINS) -> dict[str, Any]:
    """Statystyki panelu z tablicy `(pasma, wiersze, kolumny)` JUŻ w docelowej dziedzinie.

    Wspólne dla histogramu sceny i statystyk bieżącego widoku (`scene_view_stats`), żeby
    oba zakresy mówiły tą samą liczbą. Wartości zamaskowane i nieskończone są pomijane.
    """
    if np.ma.isMaskedArray(data):
        values = data.compressed().astype(np.float64)
    else:
        values = np.asarray(data, dtype=np.float64).ravel()
    values = values[np.isfinite(values)]

    if values.size == 0:
        return _empty_histogram(bins)

    minimum = float(np.min(values))
    maximum = float(np.max(values))
    percentile_values = np.percentile(values, PERCENTILE_POINTS)
    percentiles = {percentile_key(point): float(value) for point, value in zip(PERCENTILE_POINTS, percentile_values)}

    # Bin over a robust range (p1..p99) so a few outliers don't flatten the plot;
    # min/max are still reported for full-range presets.
    low = percentiles["p1"]
    high = percentiles["p99"]
    if not (high > low):
        low, high = minimum, maximum
    if not (high > low):
        high = low + 1.0
    counts, edges = np.histogram(np.clip(values, low, high), bins=bins, range=(low, high))

    # Krzywe per pasmo. Nie kosztuja ani jednego dodatkowego odczytu — te same trzy pasma
    # byly juz wczytane, tylko zlewane w jeden ciag przed policzeniem statystyk.
    series = _band_series(_as_float_with_nan(data))
    band_curves = [
        {"index": position + 1, **_curve(band, low, high, bins)}
        for position, band in enumerate(series)
    ]

    luminance_curve = None
    if len(series) >= 3:
        shortest = min(band.size for band in series[:3])
        if shortest:
            weighted = sum(
                weight * band[:shortest]
                for weight, band in zip(LUMINANCE_WEIGHTS, series[:3])
            )
            luminance_curve = _curve(np.asarray(weighted), low, high, bins)

    return {
        "schema_version": HISTOGRAM_VERSION,
        # Poziom glowny zostaje ZLANY, bo to z niego renderer bierze progi rozciagniecia.
        # Zmiana tego zrodla przestawilaby obraz wszystkim scenom — to nalezy do osobnej
        # decyzji o rozciaganiu per pasmo, nie do zmiany wykresu.
        "bins": [int(count) for count in counts],
        "bin_low": float(low),
        "bin_high": float(high),
        "min": minimum,
        "max": maximum,
        "mean": float(np.mean(values)),
        "std": float(np.std(values)),
        "percentiles": percentiles,
        "sample_count": int(values.size),
        "band_count": len(series),
        "bands": band_curves,
        "luminance": luminance_curve,
    }


def percentile_to_value(histogram: dict[str, Any], percentile: float) -> float | None:
    """Percentyl -> wartosc DN, interpolowana liniowo miedzy punktami histogramu.

    Ten sam przepis, ktorego uzywa panel do napisu „DN a-b" (`HistogramStretch.tsx`).
    Dzieki temu liczba pokazana uzytkownikowi i prog uzyty przez renderer to ta sama
    wartosc — wczesniej etykieta i faktyczne odwzorowanie rozjezdzaly sie.
    """
    points: list[tuple[float, float]] = []
    for key, value in (histogram.get("percentiles") or {}).items():
        try:
            points.append((float(str(key).lstrip("p")), float(value)))
        except (TypeError, ValueError):
            continue
    if not points:
        return None
    points.sort()
    target = float(percentile)
    if target <= points[0][0]:
        return points[0][1]
    if target >= points[-1][0]:
        return points[-1][1]
    for index in range(1, len(points)):
        left_pct, left_value = points[index - 1]
        right_pct, right_value = points[index]
        if target <= right_pct:
            span = right_pct - left_pct
            if span <= 0:
                return right_value
            ratio = (target - left_pct) / span
            return left_value + (right_value - left_value) * ratio
    return points[-1][1]


def _empty_histogram(bins: int) -> dict[str, Any]:
    return {
        "schema_version": HISTOGRAM_VERSION,
        "bins": [0] * bins,
        "bin_low": 0.0,
        "bin_high": 1.0,
        "min": 0.0,
        "max": 1.0,
        "mean": 0.0,
        "std": 0.0,
        "percentiles": {percentile_key(point): 0.0 for point in PERCENTILE_POINTS},
        "sample_count": 0,
        "band_count": 0,
        "bands": [],
        "luminance": None,
        "domain": "source",
        "display_mode": None,
    }


def _scene_display_mode(project_id: str, scene_id: str) -> str | None:
    from db.storage import load_scene_json

    scene = load_scene_json(project_id, scene_id, "scene", default={}) or {}
    return (scene.get("scene_info") or {}).get("display_mode") or None


def get_scene_histogram(project_id: str, scene_id: str, bins: int = DEFAULT_BINS) -> dict[str, Any]:
    """Return the cached scene histogram, computing it on a miss."""
    raster_path = resolve_scene_raster(project_id, scene_id)
    display_mode = _scene_display_mode(project_id, scene_id)
    cache_path = scene_dir(project_id, scene_id) / "histogram.json"
    try:
        raster_mtime = raster_path.stat().st_mtime_ns
    except OSError:
        raster_mtime = 0

    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if (
                cached.get("schema_version") == HISTOGRAM_VERSION
                and cached.get("_raster_mtime") == raster_mtime
                and len(cached.get("bins") or []) == bins
                # Zmiana profilu wyswietlania zmienia dziedzine wykresu, wiec stary
                # histogram przestaje pasowac do osi.
                and cached.get("display_mode") == display_mode
            ):
                return cached
        except (json.JSONDecodeError, OSError):
            pass

    try:
        histogram = compute_raster_histogram(raster_path, bins=bins, display_mode=display_mode)
    except Exception:  # noqa: BLE001 — nie kazde zrodlo sceny jest prawdziwym rastrem
        histogram = _empty_histogram(bins)
    histogram["_raster_mtime"] = raster_mtime
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(histogram), encoding="utf-8")
    except OSError:
        pass
    return histogram
