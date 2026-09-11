"""Extract the ML-relevant metadata subset from an open NITF dataset.

Scope (decisions C3 + B1/B2/B3/B5): image structure and radiometry, acquisition,
provenance/identity, a retained sensor-model block for a future rigorous transform,
target, and — never silently dropped (spec §7) — classification markings. Raw,
undecoded records (e.g. SENSRA) are kept verbatim rather than discarded.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any


def _nitf_datetime_to_iso(value: str | None) -> str | None:
    """NITF ``YYYYMMDDHHMMSS`` (or ``YYYYMMDDHHMM``) -> ISO 8601 UTC."""
    if not value:
        return None
    digits = value.strip()
    if len(digits) < 8 or not digits[:8].isdigit():
        return None
    year, month, day = digits[0:4], digits[4:6], digits[6:8]
    hour = digits[8:10] if len(digits) >= 10 and digits[8:10].isdigit() else "00"
    minute = digits[10:12] if len(digits) >= 12 and digits[10:12].isdigit() else "00"
    second = digits[12:14] if len(digits) >= 14 and digits[12:14].isdigit() else "00"
    return f"{year}-{month}-{day}T{hour}:{minute}:{second}Z"


def _nitf_date_to_iso(value: str | None) -> str | None:
    """NITF ``YYYYMMDD`` -> ISO date ``YYYY-MM-DD``."""
    if not value:
        return None
    digits = value.strip()
    if len(digits) < 8 or not digits[:8].isdigit():
        return None
    return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"


def _parse_tre_fields(xml_tre: list[str] | None) -> dict[str, dict[str, str]]:
    """Return {TRE_NAME: {FIELD: value}} from the decoded ``xml:TRE`` domain."""
    result: dict[str, dict[str, str]] = {}
    if not xml_tre:
        return result
    try:
        root = ET.fromstring(xml_tre[0])
    except ET.ParseError:
        return result
    for tre in root.findall("tre"):
        name = tre.get("name")
        if not name:
            continue
        fields: dict[str, str] = {}
        for field in tre.findall("field"):
            key = field.get("name")
            if key is not None:
                fields[key] = (field.get("value") or "").strip()
        result[name] = fields
    return result


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _to_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_latlon(value: str | None) -> dict[str, float] | None:
    """Parse two concatenated signed decimals ``+LAT+LON`` (e.g. ACFTB ENTLOC)."""
    match = re.fullmatch(r"\s*([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)\s*", value or "")
    if not match:
        return None
    return {"lat": float(match.group(1)), "lon": float(match.group(2))}


def _parse_sensra_altitude_m(sensra_raw: list[str]) -> float | None:
    """Platform altitude from a raw SENSRA record (e.g. ``…G+07600m…``)."""
    for raw in sensra_raw:
        match = re.search(r"G([+-]?\d+)m", raw or "")
        if match:
            return float(match.group(1))
    return None


def _gdal_dtype_name(data_type: int) -> str:
    from osgeo import gdal

    return gdal.GetDataTypeName(data_type)


def _band_radiometry(dataset: Any) -> dict[str, Any]:
    from osgeo import gdal

    band = dataset.GetRasterBand(1)
    return {
        "nbits": _to_int(band.GetMetadataItem("NBITS", "IMAGE_STRUCTURE")),
        "color_interpretation": gdal.GetColorInterpretationName(band.GetColorInterpretation()),
        "nodata": band.GetNoDataValue(),
        "scale": band.GetScale(),
        "offset": band.GetOffset(),
        "unit": _clean(band.GetUnitType()) or "",
        "mask_flags": int(band.GetMaskFlags()),
    }


def _sensor_model(acftb: dict[str, str], sensra_raw: list[str]) -> dict[str, Any]:
    """Retained camera/flight-geometry block (B2) — not used in v1 (spec §23)."""
    return {
        "note": "Retencja pod przyszły dokładniejszy model transformacji (spec §23). Nie używane w v1.",
        "focal_length_mm": _to_float(acftb.get("FOCAL_LENGTH")),
        "row_spacing": _to_float(acftb.get("ROW_SPACING")),
        "col_spacing": _to_float(acftb.get("COL_SPACING")),
        "spacing_units": _clean(acftb.get("ROW_SPACING_UNITS")),
        "entry_location": {
            **(_parse_latlon(acftb.get("ENTLOC")) or {}),
            "elev_m": _to_float(acftb.get("ENTELV")),
        },
        "exit_location": {
            **(_parse_latlon(acftb.get("EXITLOC")) or {}),
            "elev_m": _to_float(acftb.get("EXITELV")),
        },
        "map_angle_deg": _to_float(acftb.get("TMAP")),
        "platform_altitude_m": _parse_sensra_altitude_m(sensra_raw),
        "acftb": {key: value for key, value in acftb.items() if value},
        "sensra_raw": sensra_raw,
    }


def extract_nitf_metadata(dataset: Any) -> dict[str, Any]:
    """Pull the ML-relevant metadata subset from an open GDAL NITF dataset."""
    md = dataset.GetMetadata("") or {}
    tre = _parse_tre_fields(dataset.GetMetadata("xml:TRE"))  # decoded AIMIDB/ACFTB/MSTGTA
    tre_raw = dataset.GetMetadata("TRE") or {}  # raw records incl. SENSRA*
    band = dataset.GetRasterBand(1)

    aimidb = tre.get("AIMIDB", {})
    acftb = tre.get("ACFTB", {})
    mstgta = tre.get("MSTGTA", {})
    sensra_raw = [tre_raw[key] for key in sorted(tre_raw) if key.startswith("SENSRA")]

    acquisition_iso = (
        _nitf_datetime_to_iso(aimidb.get("ACQUISITION_DATE"))
        or _nitf_datetime_to_iso(md.get("NITF_IDATIM"))
    )

    return {
        "image": {
            "width": int(dataset.RasterXSize),
            "height": int(dataset.RasterYSize),
            "bands": int(dataset.RasterCount),
            "dtype": _gdal_dtype_name(band.DataType),
            "abpp": _to_int(md.get("NITF_ABPP")),
            "block": list(band.GetBlockSize()),
            "irep": _clean(md.get("NITF_IREP")),
            "icat": _clean(md.get("NITF_ICAT")),
            "fbkgc": _clean(md.get("NITF_FBKGC")),
            **_band_radiometry(dataset),  # B3: nbits, nodata, scale, offset, unit, mask_flags, color_interp
        },
        "acquisition": {
            "acquisition_datetime_utc": acquisition_iso,
            "takeoff_datetime_utc": _nitf_datetime_to_iso(acftb.get("AC_TO")),
            "sensor": _clean(acftb.get("SENSOR_ID")),
            "sensor_type": _clean(acftb.get("SENSOR_ID_TYPE")),
            "platform": _clean(md.get("NITF_ISORCE")),
            "mission": _clean(aimidb.get("MISSION_IDENTIFICATION")),
            "country": _clean(aimidb.get("COUNTRY")),
            "calibration_date": _nitf_date_to_iso(acftb.get("CAL_DATE")),
        },
        # B1: identity / provenance
        "provenance": {
            "image_id": _clean(md.get("NITF_IID1")),
            "product_lineage": _clean(md.get("NITF_IID2")),
            "production_datetime_utc": _nitf_datetime_to_iso(md.get("NITF_FDT")),
            "processing_date": _nitf_date_to_iso(acftb.get("PDATE")),
            "mission_plan": _clean(acftb.get("MPLAN")),
            "scene_number": _clean(acftb.get("SCNUM")),
            "flight_no": _clean(aimidb.get("FLIGHT_NO")),
            "format_header": _clean(md.get("NITF_FHDR")),
            "originating_station": _clean(md.get("NITF_OSTAID")),
        },
        # B2: retained sensor model (future rigorous geometry)
        "sensor_model": _sensor_model(acftb, sensra_raw),
        "target": {
            "tgt_id": _clean(mstgta.get("TGT_ID")),
            "tgt_loc": _clean(mstgta.get("TGT_LOC")),
            "tgt_elev": _clean(mstgta.get("TGT_ELEV")),
            "tgt_elev_unit": _clean(mstgta.get("TGT_ELEV_UNIT")),
        },
        # Classification/distribution markings — carried, never silently dropped
        # (spec §7). Do not expose in unauthorized exports.
        "classification": {
            "file_class": _clean(md.get("NITF_FSCLAS")),
            "file_class_system": _clean(md.get("NITF_FSCLSY")),
            "file_category": _clean(md.get("NITF_FSCATP")),
            "image_class": _clean(md.get("NITF_ISCLAS")),
        },
        "tre_names": sorted(tre_raw.keys()),
        "metadata_domains": dataset.GetMetadataDomainList() or [],
    }
