"""Statystyki BIEŻĄCEGO WIDOKU mapy dla zakresu rozciągnięcia „Widok” (DESIGN_DECISIONS.md, display-stretch D).

Odpowiednik QGIS *Statistics extent: Current/Updated canvas*: progi rozciągnięcia liczone
z pikseli, które użytkownik akurat ogląda, i nakładane na WSZYSTKIE kafle widoku. Dzięki
temu obraz dopasowuje się do fragmentu sceny bez szwów, które dawało rozciąganie liczone
osobno na każdym kaflu (zmierzone: skok 19–41 poziomów na granicy kafli wobec ~7 w środku).

Odczyt jest zdziesiątkowany z tego samego grafu co kafle pikselowe (piramida wyświetlania
albo źródło) — GDAL sam wybiera poziom piramidy. Piksele bez danych (nodata, NaN, zera
przezroczyste na mapie) nie wchodzą do próbki, więc brzeg obróconego footprintu nie ściąga
dolnego progu do zera. Maskę liczymy z wartości, a nie `masked=True`: dekodowany
zdziesiątkowany odczyt JP2 z pasmem alfa potrafił się zawiesić.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from services.scene_histogram import DEFAULT_BINS, _empty_histogram, summarize_values

VIEW_STATS_VERSION = 1
#: 512 x 512 pikseli wystarcza na stabilne p0,1 i p99,9, a odczyt z piramidy mieści się
#: w czasie jednego kafla.
SAMPLE_LIMIT = 262_144
#: Poniżej tego widok nie daje wiarygodnych statystyk — frontend zostaje przy poprzednich
#: progach (albo progach sceny).
MIN_VALID_SAMPLES = 2_000
MIN_VALID_FRACTION = 0.05
#: Bez piramidy zdziesiątkowany odczyt dużego okna oznacza odczyt pełnej rozdzielczości.
#: Powyżej tej krotności nie ryzykujemy — kafle takiej sceny i tak czekają na piramidę.
MAX_DECIMATION_WITHOUT_OVERVIEWS = 4.0


def display_band_indexes(band_count: int) -> list[int]:
    """Pasma, które trafiają na ekran — ta sama reguła co `ensure_rgb_uint8`."""
    if band_count >= 3:
        return [1, 2, 3]
    return [1]


def _pixel_valid_mask(data: np.ndarray, nodata: float | None) -> np.ndarray:
    finite = np.isfinite(data)
    if nodata is not None and not (isinstance(nodata, float) and math.isnan(nodata)):
        valid = finite & (data != nodata)
    else:
        # Jak w kaflu geo bez maski: zero jest przezroczyste, więc nie należy do widoku.
        valid = finite & (np.abs(data) > 1e-12)
    return np.any(valid, axis=0)


def insufficient_view_stats(
    window: list[float] | None,
    *,
    reason: str,
    display_mode: str | None = None,
    bins: int = DEFAULT_BINS,
) -> dict[str, Any]:
    return {
        **_empty_histogram(bins),
        "scope": "view",
        "view_stats_version": VIEW_STATS_VERSION,
        "window": window,
        "sample_shape": None,
        "sample_factor": None,
        "valid_fraction": 0.0,
        "insufficient": True,
        "reason": reason,
        "domain": "display" if display_mode else "source",
        "display_mode": display_mode,
    }


def compute_window_stats(
    path: str | Path,
    window: tuple[float, float, float, float],
    *,
    coordinate_factor: float = 1.0,
    display_mode: str | None = None,
    bins: int = DEFAULT_BINS,
    sample_limit: int = SAMPLE_LIMIT,
) -> dict[str, Any]:
    """Statystyki okna `(x0, y0, x1, y1)` w pikselach siatki referencyjnej sceny.

    Okno musi być już przycięte do sceny. `coordinate_factor` przelicza współrzędne na
    piksele czytanego pliku (podgląd piramidy ma mniejszą rozdzielczość niż siatka sceny).
    """
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import Window

    from utils.image import _apply_display_mode

    x0, y0, x1, y1 = (float(value) for value in window)
    width_px, height_px = x1 - x0, y1 - y0
    scale = min(1.0, math.sqrt(sample_limit / max(1.0, width_px * height_px)))
    out_w = max(1, int(round(width_px * scale)))
    out_h = max(1, int(round(height_px * scale)))
    factor = float(coordinate_factor) or 1.0
    decimation = 1.0 / scale / factor
    window_list = [x0, y0, x1, y1]

    with rasterio.open(Path(path)) as src:
        if decimation > MAX_DECIMATION_WITHOUT_OVERVIEWS and not src.overviews(1):
            return insufficient_view_stats(
                window_list, reason="no_pyramid", display_mode=display_mode, bins=bins
            )
        indexes = display_band_indexes(src.count)
        data = src.read(
            indexes=indexes,
            window=Window(x0 / factor, y0 / factor, width_px / factor, height_px / factor),
            out_shape=(len(indexes), out_h, out_w),
            resampling=Resampling.nearest,
        )
        nodata = src.nodata

    values = data.astype(np.float32)
    pixel_valid = _pixel_valid_mask(values, nodata)
    transformed = _apply_display_mode(values, display_mode)
    mask = np.broadcast_to(~pixel_valid, transformed.shape)
    summary = summarize_values(np.ma.masked_array(transformed, mask=mask), bins)

    valid_count = int(pixel_valid.sum())
    valid_fraction = float(pixel_valid.mean()) if pixel_valid.size else 0.0
    insufficient = valid_count < MIN_VALID_SAMPLES or valid_fraction < MIN_VALID_FRACTION
    return {
        **summary,
        "scope": "view",
        "view_stats_version": VIEW_STATS_VERSION,
        "window": window_list,
        "sample_shape": [out_h, out_w],
        "sample_factor": round(1.0 / scale, 4),
        "valid_fraction": round(valid_fraction, 4),
        "insufficient": insufficient,
        "reason": "too_few_valid_pixels" if insufficient else None,
        "domain": "display" if display_mode else "source",
        "display_mode": display_mode,
    }


def snap_window(
    window: tuple[float, float, float, float],
    width: float,
    height: float,
    divisions: int = 64,
) -> tuple[float, float, float, float] | None:
    """Przytnij okno do sceny i przyciągnij do siatki 1/64 jego rozmiaru (potęga dwójki).

    Dwa prawie identyczne widoki (drobne przesunięcie) dają ten sam klucz cache, a zmiana
    statystyk poniżej 1/64 okna i tak jest pomijalna.
    """
    x0, y0, x1, y1 = window
    x0, y0 = max(0.0, float(x0)), max(0.0, float(y0))
    x1, y1 = min(float(width), float(x1)), min(float(height), float(y1))
    if x1 <= x0 or y1 <= y0:
        return None
    step = 2.0 ** math.floor(math.log2(max(1.0, max(x1 - x0, y1 - y0) / divisions)))
    sx0, sy0 = math.floor(x0 / step) * step, math.floor(y0 / step) * step
    sx1 = min(float(width), math.ceil(x1 / step) * step)
    sy1 = min(float(height), math.ceil(y1 / step) * step)
    return sx0, sy0, sx1, sy1
