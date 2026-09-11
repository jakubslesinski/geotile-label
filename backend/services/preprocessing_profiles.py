from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from db.storage import load_json, save_json
from models.preprocessing import PreprocessingProfile, PreprocessingProfilesFile

PROFILE_SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_preprocessing_profiles() -> list[PreprocessingProfile]:
    return [
        with_profile_hash(PreprocessingProfile(
            profile_id="eo_rgb_percentile",
            name="EO RGB percentile 2-98",
            description="Linear RGB conversion with per-band percentile stretch.",
            modality="EO",
            input_quantity="RGB reflectance or DN",
            radiometric_transform="linear",
            percentile_stretch=True,
            stretch_low=2.0,
            stretch_high=98.0,
            gamma=1.0,
            brightness=1.0,
            contrast=1.0,
            rgb_conversion="native_rgb",
            builtin=True,
        )),
        with_profile_hash(PreprocessingProfile(
            profile_id="eo_linear_full_range",
            name="EO linear full range",
            description="Linear EO conversion using the full numeric range.",
            modality="EO",
            input_quantity="RGB reflectance or DN",
            radiometric_transform="linear",
            percentile_stretch=False,
            stretch_low=0.0,
            stretch_high=100.0,
            gamma=1.0,
            brightness=1.0,
            contrast=1.0,
            rgb_conversion="native_rgb",
            builtin=True,
        )),
        with_profile_hash(PreprocessingProfile(
            profile_id="pan_uint16_percentile",
            name="Aerial pan UInt16 percentile 2-98",
            description="Panchromatic 10/16-bit airborne NITF rendered as grayscale with a percentile stretch.",
            modality="AERIAL_EO",
            input_quantity="panchromatic DN (UInt16)",
            radiometric_transform="linear",
            percentile_stretch=True,
            stretch_low=2.0,
            stretch_high=98.0,
            gamma=1.0,
            brightness=1.0,
            contrast=1.0,
            rgb_conversion="grayscale_rgb",
            builtin=True,
        )),
        with_profile_hash(PreprocessingProfile(
            profile_id="sar_log_percentile",
            name="SAR log percentile 2-98",
            description="Log-compressed single-band SAR rendered as three-channel grayscale.",
            modality="SAR",
            input_quantity="amplitude or intensity",
            radiometric_transform="log1p",
            percentile_stretch=True,
            stretch_low=2.0,
            stretch_high=98.0,
            gamma=0.85,
            brightness=1.0,
            contrast=1.1,
            rgb_conversion="grayscale_rgb",
            builtin=True,
        )),
        with_profile_hash(PreprocessingProfile(
            profile_id="sar_linear_percentile",
            name="SAR linear percentile 2-98",
            description="Linear percentile stretch for SAR products already expressed in dB.",
            modality="SAR",
            input_quantity="calibrated backscatter or dB",
            radiometric_transform="linear",
            percentile_stretch=True,
            stretch_low=2.0,
            stretch_high=98.0,
            gamma=1.0,
            brightness=1.0,
            contrast=1.0,
            rgb_conversion="grayscale_rgb",
            builtin=True,
        )),
    ]


def profile_processing_payload(profile: PreprocessingProfile | dict[str, Any]) -> dict[str, Any]:
    value = profile.model_dump() if isinstance(profile, PreprocessingProfile) else dict(profile)
    return {
        key: value.get(key)
        for key in (
            "profile_id",
            "profile_version",
            "processor_version",
            "modality",
            "input_quantity",
            "radiometric_transform",
            "percentile_stretch",
            "percentile_scope",
            "stretch_low",
            "stretch_high",
            "gamma",
            "brightness",
            "contrast",
            "rgb_conversion",
            "output_dtype",
        )
    }


def compute_profile_hash(profile: PreprocessingProfile | dict[str, Any]) -> str:
    payload = profile_processing_payload(profile)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def with_profile_hash(profile: PreprocessingProfile) -> PreprocessingProfile:
    return profile.model_copy(update={"profile_hash": compute_profile_hash(profile)})


def ensure_preprocessing_profiles(project_id: str) -> dict[str, Any]:
    data = load_json(project_id, "preprocessing_profiles", default={})
    existing = data.get("profiles", []) if isinstance(data, dict) else []
    profiles: list[PreprocessingProfile] = []
    known_ids: set[str] = set()

    for item in existing:
        try:
            profile = with_profile_hash(PreprocessingProfile(**item))
        except Exception:
            continue
        profiles.append(profile)
        known_ids.add(profile.profile_id)

    changed = not isinstance(data, dict) or not data.get("schema_name")
    for profile in default_preprocessing_profiles():
        if profile.profile_id not in known_ids:
            profiles.append(profile)
            changed = True

    result = PreprocessingProfilesFile(
        schema_version=PROFILE_SCHEMA_VERSION,
        updated_at=data.get("updated_at", utc_now()) if isinstance(data, dict) else utc_now(),
        profiles=sorted(profiles, key=lambda item: (item.modality, item.profile_id)),
    ).model_dump()
    if changed or result.get("profiles") != existing:
        result["updated_at"] = utc_now()
        save_json(project_id, "preprocessing_profiles", result)
    return result


def get_preprocessing_profile(project_id: str, profile_id: str) -> PreprocessingProfile | None:
    for item in ensure_preprocessing_profiles(project_id).get("profiles", []):
        if item.get("profile_id") == profile_id:
            return PreprocessingProfile(**item)
    return None


def resolve_preprocessing_profile(
    project_id: str,
    profile_id: str | None,
    project_profile: dict[str, Any] | None = None,
) -> PreprocessingProfile:
    project_profile = project_profile or {}
    requested = profile_id or project_profile.get("default_preprocessing_profile")
    profiles = [
        PreprocessingProfile(**item)
        for item in ensure_preprocessing_profiles(project_id).get("profiles", [])
    ]
    if requested:
        for profile in profiles:
            if profile.profile_id == requested:
                return profile

    modality = project_profile.get("modality", "EO")
    for profile in profiles:
        if profile.modality == modality:
            return profile
    raise ValueError("No preprocessing profile is available for this project")


def upsert_preprocessing_profile(project_id: str, profile: PreprocessingProfile) -> PreprocessingProfile:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", profile.profile_id):
        raise ValueError("profile_id may contain only letters, digits, underscores and hyphens")

    data = ensure_preprocessing_profiles(project_id)
    existing = next(
        (item for item in data.get("profiles", []) if item.get("profile_id") == profile.profile_id),
        None,
    )
    incoming = profile.model_copy(update={"builtin": bool(existing and existing.get("builtin"))})
    incoming_hash = compute_profile_hash(incoming)
    if existing and existing.get("profile_hash") != incoming_hash:
        incoming = incoming.model_copy(update={
            "profile_version": max(int(existing.get("profile_version", 1)) + 1, incoming.profile_version),
        })
    incoming = with_profile_hash(incoming)

    profiles = [item for item in data.get("profiles", []) if item.get("profile_id") != incoming.profile_id]
    profiles.append(incoming.model_dump())
    data["profiles"] = sorted(profiles, key=lambda item: (item.get("modality", ""), item.get("profile_id", "")))
    data["updated_at"] = utc_now()
    save_json(project_id, "preprocessing_profiles", data)
    return incoming


def profile_warnings(profile: PreprocessingProfile, project_profile: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    modality = project_profile.get("modality")
    if modality and profile.modality != modality:
        warnings.append(
            f"Profile modality {profile.modality} does not match project modality {modality}."
        )
    return warnings


def write_preprocessed_tile(
    scene_path: str | Path,
    x0: int,
    y0: int,
    tile_size: int,
    profile: PreprocessingProfile | dict[str, Any],
    output_path: str | Path,
    *,
    window_px: int | None = None,
) -> None:
    profile_model = profile if isinstance(profile, PreprocessingProfile) else PreprocessingProfile(**profile)
    raw, valid_mask = read_scene_window(
        scene_path,
        x0,
        y0,
        tile_size,
        profile_model,
        window_px=window_px,
    )
    processed = apply_preprocessing_profile(raw, profile_model, valid_mask)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(processed, mode="RGB").save(output, "PNG")


class SceneSourceReader:
    """Otwarte źródło sceny do WIELOKROTNEGO wycinania kafli — otwarcie/dekodowanie raz na
    scenę zamiast raz na kafel (patrz `open_scene_source`). Zwalnia zasoby przez `close()`.

    - kind="pil": pełna zdekodowana tablica obrazu w pamięci (PNG/JPG),
    - kind="tiff": otwarty uchwyt rasterio (leniwe odczyty okien),
    """

    def __init__(self, kind: str, *, path: Path | None = None, array: np.ndarray | None = None,
                 dataset: Any = None, band_count: int = 0,
                 nodata: Any = None) -> None:
        self.kind = kind
        self.path = path
        self.array = array
        self.dataset = dataset
        self.band_count = band_count
        self.nodata = nodata

    def close(self) -> None:
        if self.dataset is not None:
            try:
                self.dataset.close()
            finally:
                self.dataset = None

    def __enter__(self) -> "SceneSourceReader":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


def open_scene_source(
    scene_path: str | Path,
    profile: PreprocessingProfile | dict[str, Any],
) -> SceneSourceReader:
    """Otwórz źródło sceny RAZ do wycinania wielu kafli. Dla PNG/JPG dekoduje cały obraz do
    pamięci; dla GeoTIFF trzyma otwarty uchwyt rasterio."""
    profile_model = profile if isinstance(profile, PreprocessingProfile) else PreprocessingProfile(**profile)
    path = Path(scene_path)

    # Czytnik kafli musi obsłużyć te same formaty co reszta pipeline'u (get_scene_info),
    # w tym working-raster VRT (SAR Float16 → Float32) oraz JP2/NITF — inaczej ".vrt" spadał
    # do PIL i wywalał build ("cannot identify image file").
    from services.scene_loader import GDAL_RASTER_EXTENSIONS

    if path.suffix.lower() in GDAL_RASTER_EXTENSIONS:
        import rasterio

        src = rasterio.open(path)
        band_count = 1 if profile_model.rgb_conversion == "grayscale_rgb" else min(3, src.count)
        return SceneSourceReader("tiff", path=path, dataset=src, band_count=band_count, nodata=src.nodata)

    with Image.open(path) as source:
        if source.mode in {"I", "I;16", "I;16B", "I;16L", "F"}:
            source_array = np.asarray(source)
            if source_array.ndim == 2:
                source_array = source_array[:, :, None]
        else:
            source_array = np.asarray(source.convert("RGB"))
    return SceneSourceReader("pil", path=path, array=source_array)


def read_tile_from_source(
    reader: SceneSourceReader,
    x0: int,
    y0: int,
    tile_size: int,
    *,
    window_px: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Wytnij jeden kafel z już otwartego źródła (`open_scene_source`). Logika okna/paddingu/
    valid-mask identyczna jak przy odczycie per-kafel."""
    if reader.kind == "tiff":
        from rasterio.windows import Window

        # GSD normalization: read a larger/smaller source window (window_px) and resample
        # to tile_size. window_px None (or == tile_size) → native read.
        read_w = int(window_px) if window_px else tile_size
        band_count = reader.band_count
        read_kwargs = dict(
            indexes=list(range(1, band_count + 1)),
            window=Window(x0, y0, read_w, read_w),
            boundless=True,
            masked=True,
            fill_value=reader.nodata if reader.nodata is not None else 0,
        )
        if read_w != tile_size:
            from rasterio.enums import Resampling

            read_kwargs["out_shape"] = (band_count, tile_size, tile_size)
            read_kwargs["resampling"] = Resampling.bilinear
        data = reader.dataset.read(**read_kwargs)
        if np.ma.isMaskedArray(data):
            mask = np.ma.getmaskarray(data)
            valid = ~np.all(mask, axis=0)
            values = data.filled(0)
        else:
            values = np.asarray(data)
            valid = np.ones(values.shape[1:], dtype=bool)
        return np.transpose(values, (1, 2, 0)), valid

    # PIL: wytnij kafel z pełnej zdekodowanej tablicy (bez ponownego dekodowania obrazu).
    source_array = reader.array
    canvas = np.zeros(
        (tile_size, tile_size, source_array.shape[2]),
        dtype=source_array.dtype,
    )
    valid = np.zeros((tile_size, tile_size), dtype=bool)
    src_x1 = min(source_array.shape[1], x0 + tile_size)
    src_y1 = min(source_array.shape[0], y0 + tile_size)
    width = max(0, src_x1 - x0)
    height = max(0, src_y1 - y0)
    if width and height:
        canvas[:height, :width] = source_array[y0:src_y1, x0:src_x1]
        valid[:height, :width] = True
    return canvas, valid


def read_scene_window(
    scene_path: str | Path,
    x0: int,
    y0: int,
    tile_size: int,
    profile: PreprocessingProfile,
    *,
    window_px: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Odczyt pojedynczego kafla (podglądy). Build używa `open_scene_source` + `read_tile_from_source`
    (dekod raz na scenę)."""
    reader = open_scene_source(scene_path, profile)
    try:
        return read_tile_from_source(reader, x0, y0, tile_size, window_px=window_px)
    finally:
        reader.close()


def apply_preprocessing_profile(
    image: np.ndarray,
    profile: PreprocessingProfile | dict[str, Any],
    valid_mask: np.ndarray | None = None,
) -> np.ndarray:
    profile_model = profile if isinstance(profile, PreprocessingProfile) else PreprocessingProfile(**profile)
    source_dtype = image.dtype
    values = np.asarray(image)
    if values.ndim == 2:
        values = values[:, :, None]
    values = np.nan_to_num(values.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    if valid_mask is None:
        valid_mask = np.ones(values.shape[:2], dtype=bool)
    else:
        valid_mask = np.asarray(valid_mask, dtype=bool)

    if profile_model.rgb_conversion == "grayscale_rgb":
        values = values[:, :, :1]
    elif values.shape[2] > 3:
        values = values[:, :, :3]

    if profile_model.radiometric_transform == "log1p":
        values = np.log1p(np.clip(values, 0.0, None))
    elif profile_model.radiometric_transform == "db10":
        values = 10.0 * np.log10(np.clip(values, 1e-8, None))

    stretched = np.zeros_like(values, dtype=np.float32)
    per_band = profile_model.rgb_conversion != "grayscale_rgb"
    if per_band:
        for band in range(values.shape[2]):
            stretched[:, :, band] = _stretch_band(
                values[:, :, band], valid_mask, source_dtype, profile_model
            )
    else:
        stretched[:, :, 0] = _stretch_band(
            values[:, :, 0], valid_mask, source_dtype, profile_model
        )

    stretched *= float(profile_model.brightness)
    if profile_model.contrast != 1.0:
        stretched = 127.5 + float(profile_model.contrast) * (stretched - 127.5)
    if profile_model.gamma != 1.0:
        stretched = np.power(
            np.clip(stretched, 0.0, 255.0) / 255.0,
            1.0 / float(profile_model.gamma),
        ) * 255.0

    if stretched.shape[2] == 1:
        stretched = np.repeat(stretched, 3, axis=2)
    elif stretched.shape[2] == 2:
        stretched = np.repeat(stretched[:, :, :1], 3, axis=2)
    stretched[~valid_mask] = 0.0
    return np.clip(stretched[:, :, :3], 0.0, 255.0).astype(np.uint8)


def _stretch_band(
    band: np.ndarray,
    valid_mask: np.ndarray,
    source_dtype: np.dtype,
    profile: PreprocessingProfile,
) -> np.ndarray:
    finite_mask = valid_mask & np.isfinite(band)
    valid_values = band[finite_mask]
    if valid_values.size == 0:
        return np.zeros_like(band, dtype=np.float32)

    if profile.percentile_stretch:
        low, high = np.percentile(valid_values, [profile.stretch_low, profile.stretch_high])
    elif np.issubdtype(source_dtype, np.integer) and profile.radiometric_transform in {"none", "linear"}:
        info = np.iinfo(source_dtype)
        low, high = float(info.min), float(info.max)
    else:
        low, high = float(np.min(valid_values)), float(np.max(valid_values))

    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return np.zeros_like(band, dtype=np.float32)
    return (band - float(low)) / (float(high) - float(low)) * 255.0
