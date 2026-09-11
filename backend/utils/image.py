"""Image utility helpers."""

import numpy as np
from PIL import Image


def _finite_percentiles(arr: np.ndarray, low_pct: float = 2.0, high_pct: float = 98.0) -> tuple[float, float] | None:
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return None
    low, high = np.percentile(finite, [low_pct, high_pct])
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return None
    return float(low), float(high)


def _scale_to_uint8(arr: np.ndarray, low: float, high: float) -> np.ndarray:
    out = (arr.astype(np.float32) - float(low)) / (float(high) - float(low)) * 255.0
    return np.clip(out, 0.0, 255.0).astype(np.uint8)


def _apply_display_mode(arr: np.ndarray, display_mode: str | None) -> np.ndarray:
    arr_f = np.nan_to_num(arr.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    if display_mode == "sar_db":
        return 10.0 * np.log10(np.clip(arr_f, 1e-8, None))
    if display_mode == "uint16_log":
        max_u16 = float(np.iinfo(np.uint16).max)
        return np.log1p(np.clip(arr_f, 0.0, max_u16))
    return arr_f


def scene_display_window(
    low_value: float,
    high_value: float,
    display_min: float | None,
    display_max: float | None,
    display_mode: str | None,
    dtype_is_uint8: bool,
    already_transformed: bool = False,
) -> tuple[float, float] | None:
    """Progi sceny (wartosci z histogramu) -> okno konwersji do uint8 w DZIEDZINIE WYSWIETLANIA.

    Dziedzina wyswietlania to wartosci po `display_mode` (SAR: log1p albo dB); dla scen bez
    trybu to wartosci zrodla. Okno trafia do `render_display_window` i ZASTEPUJE okno bazowe
    sceny, wiec obraz jest kwantowany do uint8 tylko raz. Wczesniej prog przenoszono do 0-255
    i rozciagano obraz JUZ skwantowany oknem bazowym: dla SAR dane zajmowaly ~64 poziomy,
    a rozciagniecie x4 zostawialo co piaty (zmierzone na Capelli, DESIGN_DECISIONS.md, display-stretch A).

    `already_transformed` znaczy, ze wartosci pochodza z histogramu policzonego JUZ w
    dziedzinie wyswietlania (`domain: "display"`) — nakladanie `display_mode` drugi raz
    dawaloby podwojny logarytm.

    `None` znaczy „scena nie ma deterministycznej konwersji" — dotyczy galezi, w ktorych
    `ensure_rgb_uint8` liczy percentyle NA KAFLU (`linear_robust`, `uint16_log` bez okna,
    float bez trybu). Wolajacy zostaje wtedy przy dotychczasowym zachowaniu.
    """
    has_base_window = (
        display_min is not None and display_max is not None and display_max > display_min
    )
    if has_base_window:
        if already_transformed or not display_mode:
            low, high = float(low_value), float(high_value)
        else:
            transformed = _apply_display_mode(
                np.asarray([[float(low_value), float(high_value)]], dtype=np.float32),
                display_mode,
            )
            low, high = float(transformed[0][0]), float(transformed[0][1])
    elif dtype_is_uint8 and not display_mode:
        # `ensure_rgb_uint8` nie rusza uint8 — dziedzina zrodla JEST dziedzina wyswietlania.
        low, high = float(low_value), float(high_value)
    else:
        return None
    if not (np.isfinite(low) and np.isfinite(high) and high > low):
        return None
    return low, high


def _display_channels(arr: np.ndarray) -> np.ndarray:
    """Kanaly, ktore trafiaja na ekran — ta sama regula co w `ensure_rgb_uint8`, ale PRZED
    rozmnozeniem jednego pasma na trzy, zeby arytmetyka szla na jednym kanale."""
    if arr.ndim == 3 and arr.shape[2] in (1, 2):
        return arr[:, :, 0]
    if arr.ndim == 3 and arr.shape[2] > 3:
        return arr[:, :, :3]
    return arr


def _stack_rgb(arr: np.ndarray) -> np.ndarray:
    if arr.ndim == 2:
        return np.stack([arr, arr, arr], axis=-1)
    return arr


def render_display_window(
    arr: np.ndarray,
    display_mode: str | None,
    window: tuple[float, float],
    brightness: float = 1.0,
    contrast: float = 1.0,
    gamma: float = 1.0,
) -> np.ndarray:
    """Surowe piksele -> RGB uint8 z JEDNA kwantyzacja.

    Okno rozciagniecia (w dziedzinie wyswietlania), jasnosc, kontrast i gamma sa liczone
    razem we float; konwersja do uint8 jest ostatnim krokiem. Dla zrodel uint8 bez trybu to
    dokladnie ta sama arytmetyka co LUT w `apply_display_params`, wiec wynik jest identyczny.
    """
    from services.image_preprocessor import apply_display_params

    low, high = float(window[0]), float(window[1])
    channels = _display_channels(arr)
    if channels.dtype == np.uint8 and not display_mode:
        # Zrodlo juz jest uint8: 256-elementowy LUT to ta sama arytmetyka, a nie liczy
        # float na calym kaflu (EO RGB z nastawa rozciagniecia).
        return _stack_rgb(
            apply_display_params(
                channels, brightness, contrast, gamma, stretch_bounds=(low, high)
            )
        )
    values = _apply_display_mode(channels, display_mode)
    scaled = (values - low) / (high - low) * 255.0
    return _stack_rgb(apply_display_params(scaled, brightness, contrast, gamma))


def _uint16_to_uint8(arr: np.ndarray) -> np.ndarray:
    """Convert uint16 imagery to uint8 for display.

    For single-band scenes (typical SAR), apply log compression to avoid
    near-binary visualization when values occupy a small part of uint16 range.
    For multiband scenes, keep linear scaling.
    """
    arr_f = np.nan_to_num(arr.astype(np.float32), copy=False)

    is_single_band = arr_f.ndim == 2 or (arr_f.ndim == 3 and arr_f.shape[2] == 1)
    if is_single_band:
        max_u16 = float(np.iinfo(np.uint16).max)
        arr_f = np.log1p(np.clip(arr_f, 0.0, max_u16))
        arr_f = arr_f / np.log1p(max_u16) * 255.0
        return np.clip(arr_f, 0.0, 255.0).astype(np.uint8)

    # Multiband 16-bit (e.g., RGB/NIR) - preserve relative radiometry linearly.
    arr_f = np.clip(arr_f, 0.0, float(np.iinfo(np.uint16).max))
    arr_f = arr_f / float(np.iinfo(np.uint16).max) * 255.0
    return arr_f.astype(np.uint8)


def _float_to_uint8(arr: np.ndarray) -> np.ndarray:
    arr_f = np.nan_to_num(arr.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    finite = arr_f[np.isfinite(arr_f)]
    if finite.size == 0:
        return np.zeros(arr.shape, dtype=np.uint8)

    p2, p50, p98 = np.percentile(finite, [2, 50, 98])
    is_single_band = arr_f.ndim == 2 or (arr_f.ndim == 3 and arr_f.shape[2] == 1)
    if is_single_band and p2 >= 0.0 and p50 < 0.2 and p98 <= 1.0:
        arr_f = 10.0 * np.log10(np.clip(arr_f, 1e-8, None))
        pct = _finite_percentiles(arr_f, 2, 98)
        if pct:
            return _scale_to_uint8(arr_f, *pct)

    if p2 >= 0.0 and p98 <= 1.0 and p50 >= 0.2:
        return np.clip(arr_f * 255.0, 0.0, 255.0).astype(np.uint8)

    pct = _finite_percentiles(arr_f, 2, 98)
    if pct:
        return _scale_to_uint8(arr_f, *pct)
    return np.clip(arr_f, 0.0, 255.0).astype(np.uint8)


def ensure_rgb_uint8(
    arr: np.ndarray,
    display_min: float | None = None,
    display_max: float | None = None,
    display_mode: str | None = None,
) -> np.ndarray:
    """Convert array to RGB uint8 for display/saving."""
    if display_min is not None and display_max is not None and display_max > display_min:
        arr = _scale_to_uint8(_apply_display_mode(arr, display_mode), display_min, display_max)
    elif arr.dtype == np.uint16:
        if display_mode == "linear_robust":
            pct = _finite_percentiles(arr, 2, 98)
            arr = _scale_to_uint8(arr, *pct) if pct else np.zeros(arr.shape, dtype=np.uint8)
        elif display_mode == "uint16_log":
            transformed = _apply_display_mode(arr, display_mode)
            pct = _finite_percentiles(transformed, 2, 98)
            arr = _scale_to_uint8(transformed, *pct) if pct else _uint16_to_uint8(arr)
        else:
            arr = _uint16_to_uint8(arr)
    elif arr.dtype == np.float32 or arr.dtype == np.float64:
        arr = _float_to_uint8(arr)
    elif arr.dtype != np.uint8:
        arr = arr.astype(np.uint8)

    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=-1)
    elif arr.ndim == 3 and arr.shape[2] == 1:
        arr = np.repeat(arr, 3, axis=2)
    elif arr.ndim == 3 and arr.shape[2] == 2:
        arr = np.repeat(arr[:, :, :1], 3, axis=2)
    elif arr.ndim == 3 and arr.shape[2] == 4:
        arr = arr[:, :, :3]
    elif arr.ndim == 3 and arr.shape[2] > 4:
        arr = arr[:, :, :3]

    return arr


def make_thumbnail(img: np.ndarray, max_width: int = 1000) -> Image.Image:
    """Create a thumbnail PIL Image."""
    img = ensure_rgb_uint8(img)
    pil = Image.fromarray(img)
    if pil.width > max_width:
        ratio = max_width / pil.width
        new_h = int(pil.height * ratio)
        pil = pil.resize((max_width, new_h), Image.LANCZOS)
    return pil
