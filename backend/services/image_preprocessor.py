"""Image preprocessing — brightness, gamma, sharpen (adapted from APP_GB_KK)."""

import numpy as np
import cv2


def _uint8_percentiles(
    img: np.ndarray, low_pct: float, high_pct: float
) -> tuple[float, float]:
    """Match NumPy's linear percentiles using a fixed-size uint8 histogram."""
    flat = np.asarray(img, dtype=np.uint8).reshape(-1)
    if flat.size == 0:
        return 0.0, 0.0
    cumulative = np.cumsum(np.bincount(flat, minlength=256), dtype=np.int64)

    def percentile(value: float) -> float:
        rank = (flat.size - 1) * float(value) / 100.0
        lower_rank = int(np.floor(rank))
        upper_rank = int(np.ceil(rank))
        lower = int(np.searchsorted(cumulative, lower_rank + 1, side="left"))
        upper = int(np.searchsorted(cumulative, upper_rank + 1, side="left"))
        return float(lower + (upper - lower) * (rank - lower_rank))

    return percentile(low_pct), percentile(high_pct)


def adjust_brightness(img: np.ndarray, factor: float = 1.0) -> np.ndarray:
    """Adjust brightness. factor > 1 = brighter, < 1 = darker."""
    return np.clip(img.astype(np.float32) * factor, 0, 255).astype(np.uint8)


def adjust_gamma(img: np.ndarray, gamma: float = 1.0) -> np.ndarray:
    """Apply gamma correction."""
    if gamma == 1.0:
        return img
    inv_gamma = 1.0 / gamma
    table = np.array(
        [(i / 255.0) ** inv_gamma * 255 for i in range(256)],
        dtype=np.uint8,
    )
    return cv2.LUT(img, table)


def adjust_contrast(img: np.ndarray, factor: float = 1.0) -> np.ndarray:
    """Adjust contrast around the mean. factor > 1 = more contrast."""
    if factor == 1.0:
        return img
    mean = np.mean(img, dtype=np.float32)
    return np.clip(mean + factor * (img.astype(np.float32) - mean), 0, 255).astype(np.uint8)


def histogram_stretch(img: np.ndarray, low_pct: float = 2.0, high_pct: float = 98.0) -> np.ndarray:
    """Percentile-based histogram stretch."""
    if low_pct == 0.0 and high_pct == 100.0:
        return img
    low_val = np.percentile(img, low_pct)
    high_val = np.percentile(img, high_pct)
    if high_val <= low_val:
        return img
    stretched = (img.astype(np.float32) - low_val) / (high_val - low_val) * 255.0
    return np.clip(stretched, 0, 255).astype(np.uint8)


def apply_display_params(
    img: np.ndarray,
    brightness: float = 1.0,
    contrast: float = 1.0,
    gamma: float = 1.0,
    stretch_low: float = 0.0,
    stretch_high: float = 100.0,
    percentile_stretch: bool = True,
    stretch_bounds: tuple[float, float] | None = None,
) -> np.ndarray:
    """Apply display adjustments in float space to reduce quantization artifacts.

    ``stretch_bounds`` to gotowa para progow w dziedzinie 0-255. Podanie jej jest
    PREFEROWANA droga dla widoku sceny: progi wyznacza sie raz dla calej sceny, wiec
    sasiednie kafle dostaja identyczne odwzorowanie. Liczenie percentyli tutaj oznacza
    liczenie ich NA KAFLU — zmierzone na scenie PHR1B dawalo `1->153` w srodku i `4->104`
    w innym miejscu tej samej sceny, czyli te same DN w roznej jasnosci zaleznie od
    polozenia. Zostaje jako zachowanie zapasowe tam, gdzie progu sceny nie da sie ustalic.
    """
    source_is_uint8 = img.dtype == np.uint8
    # Every operation below is point-wise after the two percentile values are
    # known. For uint8, transform a 256-value LUT instead of a full image-sized
    # float32 array, then apply it with one indexed copy.
    out = (
        np.arange(256, dtype=np.float32)
        if source_is_uint8
        else img.astype(np.float32)
    )

    if stretch_bounds is not None:
        low_val, high_val = float(stretch_bounds[0]), float(stretch_bounds[1])
        if high_val > low_val:
            out = (out - low_val) / (high_val - low_val) * 255.0
    elif stretch_low > 0.0 or stretch_high < 100.0:
        if percentile_stretch:
            if source_is_uint8:
                low_val, high_val = _uint8_percentiles(
                    img, stretch_low, stretch_high
                )
            else:
                low_val = np.percentile(out, stretch_low)
                high_val = np.percentile(out, stretch_high)
        else:
            low_val = float(stretch_low) / 100.0 * 255.0
            high_val = float(stretch_high) / 100.0 * 255.0
        if high_val > low_val:
            out = (out - low_val) / (high_val - low_val) * 255.0

    if brightness != 1.0:
        out *= float(brightness)

    if contrast != 1.0:
        mean = 127.5
        out = mean + float(contrast) * (out - mean)

    if gamma != 1.0:
        safe_gamma = max(float(gamma), 1e-6)
        out = np.power(np.clip(out, 0.0, 255.0) / 255.0, 1.0 / safe_gamma) * 255.0

    converted = np.clip(out, 0.0, 255.0).astype(np.uint8)
    return converted[img] if source_is_uint8 else converted


def unsharp_mask(img: np.ndarray, sigma: float = 1.0, strength: float = 1.5) -> np.ndarray:
    """Apply unsharp mask sharpening."""
    blurred = cv2.GaussianBlur(img, (0, 0), sigma)
    sharpened = cv2.addWeighted(img, 1.0 + strength, blurred, -strength, 0)
    return np.clip(sharpened, 0, 255).astype(np.uint8)
