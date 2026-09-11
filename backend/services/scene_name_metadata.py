"""Single, cached filename analysis shared by import and raster characterization."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable


KNOWN_SENSORS = (
    ("Capella", ("CAPELLA", "CAPELLA SPACE")),
    ("ICEYE", ("ICEYE",)),
    ("UMBRA", ("UMBRA",)),
    # Pleiades Neo must precede Pleiades-1: PHRNEO contains the shorter PHR token.
    ("Pleiades Neo", ("PLEIADES", "PNEO", "PNEO3", "PNEO4", "PHRNEO")),
    ("Pleiades", ("PHR1", "PHR-1")),
    ("GeoEye", ("GE1", "GEOEYE")),
    ("BlackSky", ("BLACKSKY", "BSG-")),
    ("WorldView", ("WORLDVIEW", "WV01", "WV02", "WV03", "WV04", "WV1", "WV2", "WV3", "WV4")),
)

_SAR_TOKENS = ("SAR", "GRD", "SLC", "SIGMA0", "GAMMA0")
_PANSHARP_PATTERN = re.compile(r"(?:^|[_\-.])(?:PANSHARP(?:ENED)?|PAN_SHARP|PSH|PMS)(?:[_\-.]|$)")
_PAN_PATTERN = re.compile(r"(?:^|[_\-.])PAN(?:[_\-.]|$)")
_RGB_PATTERN = re.compile(r"(?:^|[_\-.])RGB(?:[_\-.]|$)")
_MULTISPECTRAL_PATTERN = re.compile(r"(?:^|[_\-.])(?:MUL|MULTI|MS)(?:[_\-.]|$)")


def _normalize_selected(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value) for value in values if value and value != "Other"}))


@lru_cache(maxsize=4096)
def _infer_cached(filename: str, selected_sensors: tuple[str, ...]) -> tuple[tuple[str, Any], ...]:
    upper_name = Path(filename).name.upper()
    sensor = None
    for label, tokens in KNOWN_SENSORS:
        if any(token in upper_name for token in tokens):
            sensor = label
            break
    if sensor is None and len(selected_sensors) == 1:
        sensor = selected_sensors[0]

    if sensor in {"Capella", "ICEYE", "UMBRA"} or any(token in upper_name for token in _SAR_TOKENS):
        modality = "SAR"
    else:
        modality = "EO"

    pansharpened = bool(_PANSHARP_PATTERN.search(upper_name))
    panchromatic = bool(_PAN_PATTERN.search(upper_name)) and not pansharpened
    rgb = bool(_RGB_PATTERN.search(upper_name))
    multispectral = bool(_MULTISPECTRAL_PATTERN.search(upper_name))
    if modality == "SAR":
        spectral_layout_hint = "sar"
    elif pansharpened or rgb:
        spectral_layout_hint = "rgb"
    elif panchromatic:
        spectral_layout_hint = "panchromatic"
    elif multispectral:
        spectral_layout_hint = "multispectral"
    else:
        spectral_layout_hint = None

    value = {
        "sensor": sensor,
        "modality": modality,
        "acquisition_datetime_utc": _infer_acquisition_datetime(upper_name),
        "spectral_layout_hint": spectral_layout_hint,
        "spectral_processing_hint": "pansharpened" if pansharpened else None,
        "tokens": tuple(
            token
            for token, present in (
                ("SAR", modality == "SAR"),
                ("PAN", panchromatic),
                ("PANSHARP", pansharpened),
                ("RGB", rgb),
                ("MULTISPECTRAL", multispectral),
            )
            if present
        ),
    }
    return tuple(value.items())


def infer_scene_name_metadata(
    filename: str,
    selected_sensors: Iterable[str] = (),
) -> dict[str, Any]:
    """Analyze a name once; cached output is copied so callers may safely mutate it."""
    return dict(_infer_cached(str(filename), _normalize_selected(selected_sensors)))


def _infer_acquisition_datetime(filename: str) -> str | None:
    stem = Path(filename).stem
    patterns = (
        r"(20\d{2})[-_](\d{2})[-_](\d{2})[-_T](\d{2})[-_](\d{2})[-_](\d{2})",
        r"(20\d{2})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z?",
        r"(?<!\d)(20\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(?!\d)",
        r"(?<!\d)(20\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(?!\d)",
        r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)",
    )
    for pattern in patterns:
        match = re.search(pattern, stem)
        if not match:
            continue
        try:
            parts = [int(value) for value in match.groups()]
            parts += [0] * (6 - len(parts))
            return datetime(*parts[:6], tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return None


def infer_acquisition_datetime(filename: str) -> str | None:
    return infer_scene_name_metadata(filename).get("acquisition_datetime_utc")


def infer_sensor(filename: str, selected_sensors: Iterable[str]) -> str | None:
    return infer_scene_name_metadata(filename, selected_sensors).get("sensor")


def infer_modality(filename: str, sensor: str | None = None) -> str:
    if sensor in {"Capella", "ICEYE", "UMBRA"}:
        return "SAR"
    return str(infer_scene_name_metadata(filename, [sensor] if sensor else []).get("modality") or "EO")
