"""Single-pass raster structure, spectral semantics and display characterization."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from services.scene_name_metadata import infer_scene_name_metadata
from services.scene_overviews import describe_open_dataset_overviews


CHARACTERIZATION_VERSION = 1
DISPLAY_PROFILE_VERSION = 2
SAMPLE_GRID_SIZE = 5
SAMPLE_WINDOW_SIZE = 128
_PERCENTILES = (0.1, 1.0, 2.0, 50.0, 98.0, 99.0, 99.9)


def color_interpretations(src) -> list[str]:
    try:
        return [item.name for item in src.colorinterp]
    except Exception:
        return []


def data_band_indexes(src) -> list[int]:
    names = color_interpretations(src)
    indexes = [index + 1 for index, name in enumerate(names) if name != "alpha"]
    return indexes or list(range(1, src.count + 1))


def _window_offsets(length: int, window_size: int, grid_size: int) -> list[int]:
    size = min(max(1, window_size), length)
    maximum = max(0, length - size)
    if maximum == 0:
        return [0]
    return sorted({int(round(value)) for value in np.linspace(0, maximum, grid_size)})


def _sample_band_values(
    src,
    indexes: list[int],
    *,
    grid_size: int = SAMPLE_GRID_SIZE,
    window_size: int = SAMPLE_WINDOW_SIZE,
) -> tuple[list[np.ndarray], int]:
    from rasterio.windows import Window

    if not indexes:
        return [], 0
    # Never read alpha/mask while characterizing. In large JPEG2000 products the
    # alpha band may not use native overviews and can force a full-resolution decode.
    # Orthorectification padding is handled by nodata and the all-zero-block rule.
    read_indexes = indexes
    nodata_values = list(getattr(src, "nodatavals", ()) or ())
    chunks: list[list[np.ndarray]] = [[] for _ in indexes]
    width = min(max(1, window_size), src.width)
    height = min(max(1, window_size), src.height)
    read_count = 0

    for y in _window_offsets(src.height, height, grid_size):
        for x in _window_offsets(src.width, width, grid_size):
            data = src.read(read_indexes, window=Window(x, y, width, height))
            read_count += 1
            raster_data = data[: len(indexes)]
            # Completely empty edge blocks are typical orthorectification padding.
            # Skip the block as a whole; zero pixels inside a real block remain valid.
            if not np.any(raster_data):
                continue
            for position, band_index in enumerate(indexes):
                values = np.asarray(raster_data[position], dtype=np.float32)
                valid = np.isfinite(values)
                nodata = nodata_values[band_index - 1] if band_index - 1 < len(nodata_values) else src.nodata
                if nodata is not None and np.isfinite(nodata):
                    valid &= values != np.float32(nodata)
                selected = values[valid]
                if selected.size:
                    chunks[position].append(selected)

    return (
        [
            np.concatenate(band_chunks) if band_chunks else np.empty(0, dtype=np.float32)
            for band_chunks in chunks
        ],
        read_count,
    )


def _sample_overview_values(
    src,
    indexes: list[int],
    max_dimension: int = 1024,
) -> tuple[list[np.ndarray], int]:
    """Sample the full footprint through native resolution levels (not base windows)."""
    from rasterio.enums import Resampling

    if not indexes:
        return [], 0
    scale = min(1.0, max_dimension / max(src.width, src.height))
    out_width = max(1, round(src.width * scale))
    out_height = max(1, round(src.height * scale))
    try:
        data = src.read(
            indexes,
            out_shape=(len(indexes), out_height, out_width),
            resampling=Resampling.nearest,
        )
    except Exception:
        return [np.empty(0, dtype=np.float32) for _ in indexes], 1

    names = color_interpretations(src)
    has_alpha = "alpha" in names
    nodata_values = list(getattr(src, "nodatavals", ()) or ())
    samples: list[np.ndarray] = []
    for position, band_index in enumerate(indexes):
        values = np.asarray(data[position], dtype=np.float32)
        valid = np.isfinite(values)
        nodata = nodata_values[band_index - 1] if band_index - 1 < len(nodata_values) else src.nodata
        if nodata is not None and np.isfinite(nodata):
            valid &= values != np.float32(nodata)
        # Alpha itself is deliberately not read. In ortho products its transparent
        # rim is represented by zero data values, which are irrelevant to stretch.
        if has_alpha:
            valid &= values != 0
        samples.append(values[valid])
    return samples, 1


def _summarize(values: np.ndarray, band_index: int) -> dict[str, Any]:
    if not values.size:
        return {"band": band_index, "sample_count": 0}
    points = np.percentile(values, _PERCENTILES)
    return {
        "band": band_index,
        "sample_count": int(values.size),
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "mean": float(np.mean(values)),
        "std": float(np.std(values)),
        **{
            name: float(value)
            for name, value in zip(("p0_1", "p1", "p2", "p50", "p98", "p99", "p99_9"), points)
        },
    }


def _spectral_classification(
    *,
    data_band_count: int,
    color_names: list[str],
    modality: str | None,
    product_type: str | None,
    name_metadata: dict[str, Any],
) -> dict[str, Any]:
    normalized_product = str(product_type or "").upper()
    effective_modality = str(modality or name_metadata.get("modality") or "").upper()
    hint = name_metadata.get("spectral_layout_hint")
    processing_hint = name_metadata.get("spectral_processing_hint")

    if effective_modality == "SAR":
        return {
            "spectral_layout": "sar",
            "spectral_processing": "native",
            "classification_source": "modality",
            "classification_confidence": 1.0,
        }
    if "PANSHARP" in normalized_product or normalized_product in {"PMS-FS_RGB", "MS-FS_RGB+PAN", "MUL+PAN"}:
        return {
            "spectral_layout": "rgb",
            "spectral_processing": "pansharpened",
            "classification_source": "product_type",
            "classification_confidence": 1.0,
        }
    if hint == "rgb" and data_band_count >= 3:
        return {
            "spectral_layout": "rgb",
            "spectral_processing": processing_hint or "unknown",
            "classification_source": "filename+raster",
            "classification_confidence": 0.95,
        }
    if hint == "panchromatic" and data_band_count == 1:
        return {
            "spectral_layout": "panchromatic",
            "spectral_processing": "native",
            "classification_source": "filename+raster",
            "classification_confidence": 0.95,
        }
    if hint == "multispectral" and data_band_count > 1:
        return {
            "spectral_layout": "multispectral",
            "spectral_processing": "native",
            "classification_source": "filename+raster",
            "classification_confidence": 0.9,
        }
    if data_band_count == 1 and effective_modality in {"EO", "AERIAL_EO"}:
        return {
            "spectral_layout": "panchromatic",
            "spectral_processing": "unknown",
            "classification_source": "modality+raster",
            "classification_confidence": 0.8,
        }
    rgb_roles = {"red", "green", "blue"}
    if data_band_count == 3 and rgb_roles.issubset(set(color_names)):
        return {
            "spectral_layout": "rgb",
            "spectral_processing": "unknown",
            "classification_source": "colorinterp",
            "classification_confidence": 0.85,
        }
    if data_band_count == 3:
        return {
            "spectral_layout": "rgb",
            "spectral_processing": "unknown",
            "classification_source": "raster_structure",
            "classification_confidence": 0.6,
        }
    if data_band_count > 3:
        return {
            "spectral_layout": "multispectral",
            "spectral_processing": "unknown",
            "classification_source": "raster_structure",
            "classification_confidence": 0.7,
        }
    return {
        "spectral_layout": "unknown",
        "spectral_processing": "unknown",
        "classification_source": "raster_structure",
        "classification_confidence": 0.3,
    }


def _display_profile(
    dtype: str,
    classification: dict[str, Any],
    values: list[np.ndarray],
) -> tuple[float | None, float | None, str | None]:
    if dtype == "uint8":
        return None, None, None
    layout = classification.get("spectral_layout")
    if not values or not values[0].size:
        if layout == "sar" and dtype == "uint16":
            return None, None, "uint16_log"
        if layout == "sar" and dtype in {"float32", "float64"}:
            return None, None, "sar_db"
        if dtype == "uint16":
            return None, None, "linear_robust"
        return None, None, None
    first = values[0]
    mode: str | None = None
    transformed = first
    if layout == "sar" and dtype in {"float32", "float64"}:
        p2, p50, p98 = np.percentile(first, [2, 50, 98])
        if p2 >= 0.0 and p50 < 0.2 and p98 <= 1.0:
            mode = "sar_db"
            transformed = 10.0 * np.log10(np.clip(first, 1e-8, None))
    elif layout == "sar" and dtype == "uint16":
        mode = "uint16_log"
        transformed = np.log1p(np.clip(first, 0.0, float(np.iinfo(np.uint16).max)))

    if mode == "sar_db":
        low, high = np.percentile(transformed, [0.1, 99.9])
        low -= 6.0
        high += 3.0
    elif mode == "uint16_log":
        low, high = np.percentile(transformed, [0.1, 99.9])
        low -= 1.0
        high += 1.0
    else:
        low, high = np.percentile(transformed, [2, 98])
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return None, None, mode
    return float(low), float(high), mode


def characterize_raster(
    src,
    *,
    filename: str | None = None,
    source_path: Path | None = None,
    modality: str | None = None,
    product_type: str | None = None,
    selected_sensors: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Characterize an already open dataset with at most one bounded sample pass."""
    name_metadata = infer_scene_name_metadata(
        filename or Path(str(getattr(src, "name", ""))).name,
        selected_sensors,
    )
    effective_modality = modality or name_metadata.get("modality")
    color_names = color_interpretations(src)
    indexes = data_band_indexes(src)
    classification = _spectral_classification(
        data_band_count=len(indexes),
        color_names=color_names,
        modality=effective_modality,
        product_type=product_type,
        name_metadata=name_metadata,
    )
    dtype = str(src.dtypes[0]).lower()
    source_overviews = describe_open_dataset_overviews(src, source_path)
    native_overview_factors = list(source_overviews.get("factors") or [])
    driver = str(getattr(src, "driver", "") or "")
    if dtype == "uint8":
        values = []
        sample_method = None
        sample_read_count = 0
    elif driver == "JP2OpenJPEG":
        # OpenJPEG can allocate/decode gigabytes even when reading its smallest
        # virtual overview. Import remains metadata-only; robust rendering is
        # deferred to the display-preparation phase.
        values = []
        sample_method = "deferred_jp2"
        sample_read_count = 0
    elif native_overview_factors:
        values, sample_read_count = _sample_overview_values(src, indexes[:3])
        sample_method = "native_overview"
    else:
        values, sample_read_count = _sample_band_values(src, indexes[:3])
        sample_method = "block_grid"
    display_min, display_max, display_mode = _display_profile(dtype, classification, values)
    return {
        "characterization_version": CHARACTERIZATION_VERSION,
        "characterization_open_count": 1,
        "display_profile_version": DISPLAY_PROFILE_VERSION,
        "color_interpretation": color_names,
        "data_band_indexes": indexes,
        "native_overviews": bool(native_overview_factors),
        "source_overviews": source_overviews,
        "name_metadata": name_metadata,
        **classification,
        "display_min": display_min,
        "display_max": display_max,
        "display_mode": display_mode,
        "display_stats": {
            "status": "ready" if any(item.size for item in values) else "deferred",
            "method": sample_method,
            "grid_size": SAMPLE_GRID_SIZE,
            "window_size": SAMPLE_WINDOW_SIZE,
            "native_overview_factors": native_overview_factors,
            "read_count": sample_read_count,
            "bands": [_summarize(item, indexes[position]) for position, item in enumerate(values)],
        } if dtype != "uint8" else None,
    }
