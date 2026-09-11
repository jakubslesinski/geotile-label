"""Provider sidecar metadata parsing for scene manifests."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from services.scene_packages.contracts import graph_v2_enabled

PARSER_VERSION = 1

#: Status sceny, ktorej sidecary sa ze soba sprzeczne. Sekcja 4.3 roadmapy odnotowala, ze
#: `metadata_status` pozostawal `ok` mimo 914 konfliktow — czyli jedyny sygnal, jaki mial
#: uzytkownik, milczal dokladnie wtedy, gdy metadane byly niewiarygodne.
STATUS_METADATA_CONFLICTS = "metadata_conflicts"


def parse_scene_metadata(
    scene_path: Path | None,
    metadata_files: list[str],
    filename: str = "",
) -> dict[str, Any]:
    diagnostics = {"warnings": [], "errors": [], "metadata_conflicts": []}
    sources: list[dict[str, Any]] = []
    parsed_results: list[tuple[Path, dict[str, Any]]] = []

    for metadata_file in sorted(metadata_files, key=_metadata_priority, reverse=True):
        path = Path(metadata_file)
        if not path.exists():
            diagnostics["warnings"].append(f"Metadata file does not exist: {metadata_file}")
            continue

        try:
            parsed = _parse_metadata_file(path, scene_path, filename)
        except Exception as exc:
            diagnostics["errors"].append(f"{path.name}: {exc}")
            continue

        if not parsed:
            continue

        sources.append({"path": str(path.resolve(strict=False)), "parser": parsed["parser_name"]})
        parsed_results.append((path, parsed))

    if parsed_results:
        primary_path, merged = parsed_results[0]
        for path, parsed in parsed_results[1:]:
            _merge_metadata(merged, parsed, str(primary_path), str(path), diagnostics["metadata_conflicts"])
        merged["metadata_sources"] = sources
        merged["parser_diagnostics"] = diagnostics
        if diagnostics["errors"]:
            merged["metadata_status"] = "partial_metadata"
        elif diagnostics["metadata_conflicts"] and graph_v2_enabled():
            merged["metadata_status"] = STATUS_METADATA_CONFLICTS
        else:
            merged["metadata_status"] = "ok"
        return merged

    if metadata_files:
        diagnostics["warnings"].append("No supported provider metadata sidecar matched this scene")

    return {
        "provider": None,
        "parser_name": None,
        "parser_version": PARSER_VERSION,
        "metadata_sources": sources,
        "metadata_status": "no_parser_match" if metadata_files else "raster_only",
        "parser_diagnostics": diagnostics,
        "modality": None,
        "sensor": None,
        "acquisition_datetime_utc": None,
        "sar": None,
        "eo": None,
    }


def _metadata_priority(value: str) -> tuple[int, str]:
    name = Path(value).name.casefold()
    score = 0
    if "extended" in name:
        score += 40
    if name.startswith("dim_") or name.endswith(".imd"):
        score += 30
    if "metadata" in name:
        score += 20
    return score, name


def _merge_metadata(
    target: dict[str, Any],
    incoming: dict[str, Any],
    target_source: str,
    incoming_source: str,
    conflicts: list[dict[str, Any]],
    prefix: str = "",
) -> None:
    ignored = {"parser_name", "parser_version", "metadata_sources", "parser_diagnostics", "metadata_status"}
    for key, value in incoming.items():
        if key in ignored or value in (None, "", [], {}):
            continue
        field = f"{prefix}.{key}" if prefix else key
        current = target.get(key)
        if current in (None, "", [], {}):
            target[key] = value
        elif isinstance(current, dict) and isinstance(value, dict):
            _merge_metadata(current, value, target_source, incoming_source, conflicts, field)
        elif current != value:
            conflicts.append({
                "field": field,
                "selected_value": current,
                "selected_source": target_source,
                "conflicting_value": value,
                "conflicting_source": incoming_source,
            })


def _parse_metadata_file(path: Path, scene_path: Path | None, filename: str) -> dict[str, Any] | None:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return _parse_json_metadata(path)
    if suffix in {".xml", ".dim"}:
        return _parse_xml_metadata(path)
    if suffix == ".imd":
        return _parse_imd_metadata(path, scene_path, filename)
    return None


def _parse_json_metadata(path: Path) -> dict[str, Any] | None:
    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        return None
    return _parse_umbra_json(data) or _parse_capella_json(data) or _parse_blacksky_json(data)


def _parse_umbra_json(data: dict[str, Any]) -> dict[str, Any] | None:
    properties = data.get("properties") or {}
    constellation = str(properties.get("constellation") or "")
    platform = str(properties.get("platform") or "")
    if (
        data.get("type") == "Feature"
        and (constellation.casefold() == "umbra" or platform.upper().startswith("UMBRA"))
    ):
        polarizations = properties.get("sar:polarizations")
        if isinstance(polarizations, list):
            polarization = "/".join(str(value) for value in polarizations)
        else:
            polarization = str(polarizations) if polarizations else None
        return _base_result(
            provider="UMBRA",
            parser_name="umbra_stac_v2",
            modality="SAR",
            sensor="UMBRA",
            acquisition_datetime_utc=_clean_datetime(
                properties.get("start_datetime") or properties.get("datetime")
            ),
            sar={
                "satellite": platform or None,
                "collect_id": properties.get("umbra:collect_id") or data.get("id"),
                "imaging_mode": properties.get("sar:instrument_mode"),
                "product_type": properties.get("sar:product_type"),
                "polarization": polarization,
                "radar_band": properties.get("sar:frequency_band"),
                "incidence_angle_deg": _to_float(properties.get("view:incidence_angle")),
                "look_direction": properties.get("sar:observation_direction"),
                "orbit_direction": properties.get("sat:orbit_state"),
                "range_resolution_m": _to_float(properties.get("sar:resolution_range")),
                "azimuth_resolution_m": _to_float(properties.get("sar:resolution_azimuth")),
                "heading_deg": _to_float(properties.get("view:azimuth")),
            },
        )

    vendor = str(data.get("vendor") or "")
    satellite = data.get("umbraSatelliteName")
    if "UMBRA" not in vendor.upper() and not satellite:
        return None

    collect = _first_list_item(data.get("collects")) or {}
    derived = data.get("derivedProducts") or {}
    product = _first_dict_value(derived) or {}
    polarization = collect.get("polarizations")
    if isinstance(polarization, list):
        polarization = "/".join(str(value) for value in polarization)

    return _base_result(
        provider="UMBRA",
        parser_name="umbra_json_v1",
        modality="SAR",
        sensor="UMBRA",
        acquisition_datetime_utc=_clean_datetime(collect.get("startAtUTC")),
        sar={
            "satellite": satellite,
            "imaging_mode": collect.get("imagingMode") or data.get("imagingMode"),
            "product_type": product.get("productType") or data.get("productSku"),
            "polarization": polarization,
            "radar_band": collect.get("radarBand"),
            "incidence_angle_deg": _to_float(collect.get("angleIncidenceDegrees")),
            "look_direction": collect.get("observationDirection"),
            "orbit_direction": collect.get("satelliteTrack"),
            "pixel_spacing_m": _first_float(
                product.get("groundResolution"),
                product.get("rangeResolutionMeters"),
                product.get("azimuthResolutionMeters"),
            ),
            "center_lonlat": _lonlat_from_lla(collect.get("sceneCenterPointLla")),
        },
    )


def _parse_capella_json(data: dict[str, Any]) -> dict[str, Any] | None:
    collect = data.get("collect") or {}
    platform = str(collect.get("platform") or data.get("platform") or "")
    if "CAPELLA" not in platform.upper() and not collect.get("radar"):
        return None

    image = collect.get("image") or {}
    center = image.get("center_pixel") or {}
    radar = collect.get("radar") or {}
    tx_pol = radar.get("transmit_polarization")
    rx_pol = radar.get("receive_polarization")
    polarization = "/".join(value for value in (tx_pol, rx_pol) if value) or None

    return _base_result(
        provider="Capella",
        parser_name="capella_extended_json_v1",
        modality="SAR",
        sensor="Capella",
        acquisition_datetime_utc=_clean_datetime(collect.get("start_timestamp")),
        sar={
            "satellite": platform or None,
            "collect_id": collect.get("collect_id"),
            "imaging_mode": collect.get("mode"),
            "product_type": data.get("product_type"),
            "polarization": polarization,
            "radar_band": radar.get("band"),
            "incidence_angle_deg": _to_float(center.get("incidence_angle")),
            "look_angle_deg": _to_float(center.get("look_angle")),
            "squint_angle_deg": _to_float(center.get("squint_angle")),
            "look_direction": collect.get("look_direction"),
            "orbit_direction": collect.get("orbit_direction"),
            "pixel_spacing_row_m": _to_float(image.get("pixel_spacing_row")),
            "pixel_spacing_col_m": _to_float(image.get("pixel_spacing_column")),
            # Capella deklaruje radiometrie wprost — w korpusie `sigma_nought` z pelna
            # kalibracja. To jest druga strona porownania z ICEYE, ktore dostarcza DN.
            "radiometry": image.get("radiometry"),
            "calibration": image.get("calibration"),
            "calibration_id": image.get("calibration_id"),
            "scale_factor": _to_float(image.get("scale_factor")),
        },
    )


def _parse_blacksky_json(data: dict[str, Any]) -> dict[str, Any] | None:
    sensor_name = str(data.get("sensorName") or "")
    scene_id = str(data.get("id") or "")
    if "BLACKSKY" not in sensor_name.upper() and not scene_id.upper().startswith("BSG-"):
        return None

    return _base_result(
        provider="BlackSky",
        parser_name="blacksky_json_v1",
        modality="EO",
        sensor="BlackSky",
        acquisition_datetime_utc=_clean_datetime(data.get("acquisitionDate")),
        eo={
            "satellite": sensor_name or None,
            "product_type": data.get("productType") or data.get("productLevel"),
            "gsd_m": _to_float(data.get("gsd") or data.get("gsdGroundPlane")),
            "cloud_cover_percent": _to_float(data.get("cloudCoverPercent")),
            "sun_elevation_deg": _to_float(data.get("sunElevation")),
            "sun_azimuth_deg": _to_float(data.get("sunAzimuth")),
            "off_nadir_angle_deg": _to_float(data.get("offNadirAngle")),
            "satellite_elevation_deg": _to_float(data.get("satelliteElevation")),
            "satellite_azimuth_deg": _to_float(data.get("satelliteAzimuth")),
            "bits_per_pixel": _to_int(data.get("bitsPerPixel")),
            "orthorectified": data.get("orthorectified"),
            "georeferenced": data.get("georeferenced"),
        },
    )


def _parse_xml_metadata(path: Path) -> dict[str, Any] | None:
    root = ET.parse(path).getroot()
    return _parse_iceye_xml(root) or _parse_pleiades_dim(root) or _parse_worldview_xml(root)


def _parse_iceye_xml(root: ET.Element) -> dict[str, Any] | None:
    satellite = _find_text(root, "satellite_name")
    product_name = _find_text(root, "product_name")
    if not satellite and "ICEYE" not in str(product_name).upper():
        return None

    return _base_result(
        provider="ICEYE",
        parser_name="iceye_xml_v1",
        modality="SAR",
        sensor="ICEYE",
        acquisition_datetime_utc=_clean_datetime(_find_text(root, "acquisition_start_utc")),
        sar={
            "satellite": satellite,
            "product_name": product_name,
            "product_level": _find_text(root, "product_level"),
            "product_type": _find_text(root, "product_type"),
            "polarization": _find_text(root, "polarization"),
            "look_direction": _find_text(root, "look_side"),
            "orbit_direction": _find_text(root, "orbit_direction"),
            "incidence_angle_deg": _to_float(_find_text(root, "incidence_center")),
            "heading_deg": _to_float(_find_text(root, "heading")),
            "range_spacing_m": _to_float(_find_text(root, "range_spacing")),
            "azimuth_spacing_m": _to_float(_find_text(root, "azimuth_spacing")),
            "center_lonlat": _iceye_center(root),
            # Obecnosc `calibration_factor` znaczy, ze produkt NIE jest skalibrowany:
            # dostawca podaje wspolczynnik, ktorym uzytkownik moze przeliczyc DN na sigma0.
            "calibration_factor": _to_float(_find_text(root, "calibration_factor")),
        },
    )


#: Misje Airbusa w formacie DIMAP. Klucz to zawartosc `<MISSION>`, wartosc — nazwa dostawcy.
_AIRBUS_MISSIONS = {"PNEO": "Pleiades Neo", "PHR": "Pleiades"}


def _parse_pleiades_dim(root: ET.Element) -> dict[str, Any] | None:
    """Odczytaj DIMAP Airbusa: zarowno PNEO, jak i PHR.

    Poprzednia wersja wymagala tokenu `PNEO` albo `PLEIADES` w `DATASET_NAME` lub
    `PRODUCT_INFO`. Pomiar na rzeczywistych dostawach pokazal, ze pliki PHR maja
    `DATASET_NAME = DS_PHR1B_...` i PUSTY `PRODUCT_INFO`, wiec **zadna z dziesieciu dostaw
    PHR w korpusie nie byla parsowana** — wszystkie konczyly jako `no_parser_match`.

    Rozpoznanie idzie teraz po polu `<MISSION>`, ktore obie misje wypelniaja jednoznacznie
    (`PNEO` albo `PHR`), a sensor sklada sie z `MISSION` + `MISSION_INDEX` — czyli `PNEO4`
    i `PHR1B`, a nie ogolne „Pleiades Neo".
    """
    mission = (_find_text(root, "MISSION") or "").strip().upper()
    if mission not in _AIRBUS_MISSIONS:
        # Starsze dostawy moga nie miec `<MISSION>`; zostaje rozpoznanie po nazwie zbioru.
        joined = f"{_find_text(root, 'DATASET_NAME') or ''} {_find_text(root, 'PRODUCT_INFO') or ''}".upper()
        if "PNEO" in joined:
            mission = "PNEO"
        elif "PLEIADES" in joined or "PHR" in joined:
            mission = "PHR"
        else:
            return None

    mission_index = (_find_text(root, "MISSION_INDEX") or "").strip().upper()
    satellite = f"{mission}{mission_index}" if mission_index else mission
    dataset_name = _find_text(root, "DATASET_NAME")
    bands = _unique_texts(root, "BAND_NAME") or _unique_texts(root, "BAND_ID")

    return _base_result(
        provider=_AIRBUS_MISSIONS[mission],
        parser_name="airbus_dimap_v2",
        modality="EO",
        sensor=satellite,
        acquisition_datetime_utc=_clean_datetime(
            _find_text(root, "TIME") or _find_text(root, "PRODUCTION_DATE")
        ),
        eo={
            "satellite": satellite,
            "mission": mission,
            "dataset_name": dataset_name,
            # `PRODUCT_INFO` bywa samym slowem „PNEO" i nie jest typem produktu.
            # Typ opisuje `PRODUCT_TYPE` (np. `STANDARD`), a wariant spektralny
            # `SPECTRAL_PROCESSING` (`PMS-FS` dla PNEO, `PMS` dla PHR).
            "product_type": _find_text(root, "PRODUCT_TYPE") or _find_text(root, "PRODUCT_INFO"),
            "spectral_processing": _find_text(root, "SPECTRAL_PROCESSING"),
            "dataset_type": _find_text(root, "DATASET_TYPE"),
            "bands": bands,
            "band_count": _to_int(_find_text(root, "NBANDS")),
            # Kolejnosc kanalow deklaruje sam DIMAP — nie zgadujemy jej z konwencji.
            "band_display_order": _airbus_display_order(root),
            # Wartosci specjalne: dostawa deklaruje `NODATA`, ktorego sam raster juz nie
            # niesie. Bez tego ramka geokodowania (37% powierzchni w PNEO, 54% w PHR)
            # liczy sie jako prawidlowe zera.
            **_airbus_special_values(root),
            "cloud_cover_percent": _to_float(_find_text(root, "CLOUD_COVERAGE")),
            "sun_elevation_deg": _to_float(_find_text(root, "SUN_ELEVATION")),
            "sun_azimuth_deg": _to_float(_find_text(root, "SUN_AZIMUTH")),
            "viewing_angle_deg": _to_float(_find_text(root, "VIEWING_ANGLE")),
            "incidence_angle_deg": _to_float(_find_text(root, "INCIDENCE_ANGLE")),
            # `DISPLAY` to produkt gotowy do pokazania (w korpusie 8-bitowy), a nie
            # radiancja czy reflektancja. Roznica jest istotna przy laczeniu scen w dataset.
            "radiometric_processing": _find_text(root, "RADIOMETRIC_PROCESSING"),
            "processing_level": _find_text(root, "PROCESSING_LEVEL"),
            "bits_per_pixel": _to_int(_find_text(root, "NBITS")),
        },
    )


def _airbus_special_values(root: ET.Element) -> dict[str, Any]:
    """`NODATA` i `SATURATED` zadeklarowane przez dostawce, o ile sa spojne w dostawie."""
    from services.scene_packages.dimap import parse_dimap_bands

    bands = parse_dimap_bands(ET.tostring(root, encoding="unicode"))
    values: dict[str, Any] = {}
    if bands.nodata() is not None:
        values["nodata"] = bands.nodata()
    if bands.saturated() is not None:
        values["saturated"] = bands.saturated()
    return values


def _airbus_display_order(root: ET.Element) -> list[str] | None:
    """Identyfikatory pasm przypisane do kanalow R, G i B przez `<Band_Display_Order>`.

    PHR podaje `B2/B1/B0`, PNEO — `R/G/B` dla pliku RGB. Zwracamy identyfikatory, a nie
    indeksy: przelozenie na numery pasm zalezy od PLIKU i nalezy do resolvera.
    """
    for node in root.iter():
        if _local_name(node.tag).upper() != "BAND_DISPLAY_ORDER":
            continue
        channels = {_local_name(child.tag).upper(): (child.text or "").strip() for child in node}
        ordered = [channels.get(name) for name in ("RED_CHANNEL", "GREEN_CHANNEL", "BLUE_CHANNEL")]
        if all(ordered):
            return [str(value) for value in ordered]
    return None


def _parse_worldview_xml(root: ET.Element) -> dict[str, Any] | None:
    satid = _find_text(root, "SATID")
    root_name = _local_name(root.tag).upper()
    if not satid and root_name not in {"ISD", "IMD"}:
        return None
    if satid and not satid.upper().startswith("WV"):
        return None

    return _base_result(
        provider="WorldView",
        parser_name="worldview_xml_v1",
        modality="EO",
        sensor="WorldView",
        acquisition_datetime_utc=_clean_datetime(
            _find_text(root, "FIRSTLINETIME") or _find_text(root, "EARLIESTACQTIME")
        ),
        eo={
            "satellite": satid,
            "band_id": _find_text(root, "BANDID"),
            "product_type": _find_text(root, "PRODUCTLEVEL") or _find_text(root, "PRODUCTTYPE"),
            "gsd_m": _to_float(_find_text(root, "PRODUCTGSD") or _find_text(root, "MEANCOLLECTEDGSD")),
            "cloud_cover_percent": _to_percent(_find_text(root, "CLOUDCOVER")),
            "sun_elevation_deg": _to_float(_find_text(root, "MEANSUNEL")),
            "sun_azimuth_deg": _to_float(_find_text(root, "MEANSUNAZ")),
            "off_nadir_angle_deg": _to_float(_find_text(root, "MEANOFFNADIRVIEWANGLE")),
        },
    )


def _parse_imd_metadata(path: Path, scene_path: Path | None, filename: str) -> dict[str, Any] | None:
    keys = _parse_imd_key_values(path)
    lower = {key.lower(): value for key, value in keys.items()}
    text_hint = f"{path.name} {scene_path.name if scene_path else ''} {filename}".upper()
    satid = _ci_get(lower, "satid")
    if not (str(satid or "").upper().startswith("WV") or "WORLDVIEW" in text_hint or "-P2AS" in text_hint or "-M2AS" in text_hint):
        return None

    return _base_result(
        provider="WorldView",
        parser_name="worldview_imd_v1",
        modality="EO",
        sensor="WorldView",
        acquisition_datetime_utc=_clean_datetime(
            _ci_get(lower, "firstlinetime")
            or _ci_get(lower, "earliestacqtime")
            or _ci_get(lower, "generationtime")
        ),
        eo={
            "satellite": satid,
            "band_id": _ci_get(lower, "bandid"),
            "product_type": _ci_get(lower, "productlevel"),
            "gsd_m": _to_float(_ci_get(lower, "productgsd") or _ci_get(lower, "meancollectedgsd")),
            "cloud_cover_percent": _to_percent(_ci_get(lower, "cloudcover")),
            "sun_elevation_deg": _to_float(_ci_get(lower, "meansunel")),
            "sun_azimuth_deg": _to_float(_ci_get(lower, "meansunaz")),
            "off_nadir_angle_deg": _to_float(_ci_get(lower, "meanoffnadirviewangle")),
            "bits_per_pixel": _to_int(_ci_get(lower, "bitsperpixel")),
            "catalog_id": _ci_get(lower, "productcatalogid") or _ci_get(lower, "childcatalogid"),
            # `radiometricLevel`/`radiometricEnhancement` rozstrzygaja, czym sa piksele:
            # w korpusie WV2 to `Corrected` + `ACOMP`, czyli produkt po korekcji
            # atmosferycznej, a nie surowe DN.
            "radiometric_level": _ci_get(lower, "radiometriclevel"),
            "radiometric_enhancement": _ci_get(lower, "radiometricenhancement"),
            "abs_cal_factor": _to_float(_ci_get(lower, "abscalfactor")),
        },
    )


def _base_result(
    provider: str,
    parser_name: str,
    modality: str,
    sensor: str,
    acquisition_datetime_utc: str | None,
    sar: dict[str, Any] | None = None,
    eo: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "parser_name": parser_name,
        "parser_version": PARSER_VERSION,
        "modality": modality,
        "sensor": sensor,
        "acquisition_datetime_utc": acquisition_datetime_utc,
        "sar": _compact(sar) if sar else None,
        "eo": _compact(eo) if eo else None,
    }


def _parse_imd_key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    pattern = re.compile(r'^\s*([A-Za-z0-9_]+)\s*=\s*"?([^";]+)"?\s*;?')
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            match = pattern.match(line)
            if match:
                values[match.group(1)] = match.group(2).strip()
    return values


def _find_text(root: ET.Element, name: str) -> str | None:
    target = name.upper()
    for element in root.iter():
        if _local_name(element.tag).upper() == target and element.text:
            text = element.text.strip()
            if text:
                return text
    return None


def _unique_texts(root: ET.Element, name: str) -> list[str]:
    target = name.upper()
    values: list[str] = []
    for element in root.iter():
        if _local_name(element.tag).upper() == target and element.text:
            text = element.text.strip()
            if text and text not in values:
                values.append(text)
    return values


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_list_item(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None


def _first_dict_value(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        for item in value.values():
            if isinstance(item, dict):
                return item
    return None


def _first_float(*values: Any) -> float | None:
    for value in values:
        parsed = _to_float(value)
        if parsed is not None:
            return parsed
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _to_percent(value: Any) -> float | None:
    parsed = _to_float(value)
    if parsed is None:
        return None
    return parsed * 100 if 0 <= parsed <= 1 else parsed


def _clean_datetime(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    if re.fullmatch(r"20\d{12}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}T{text[8:10]}:{text[10:12]}:{text[12:14]}+00:00"
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?", text):
        text = f"{text}+00:00"
    return text


def _lonlat_from_lla(value: Any) -> list[float] | None:
    if not isinstance(value, dict):
        return None
    lon = _to_float(value.get("longitude"))
    lat = _to_float(value.get("latitude"))
    if lon is None or lat is None:
        return None
    return [lon, lat]


def _iceye_center(root: ET.Element) -> list[float] | None:
    coord_center = None
    for element in root.iter():
        if _local_name(element.tag).upper() == "COORD_CENTER":
            coord_center = element
            break
    if coord_center is None:
        return None
    lon = _to_float(_find_text(coord_center, "longitude"))
    lat = _to_float(_find_text(coord_center, "latitude"))
    if lon is None or lat is None:
        return None
    return [lon, lat]


def _ci_get(values: dict[str, str], key: str) -> str | None:
    return values.get(key.lower())


def _first_regex(value: str, pattern: str) -> str | None:
    match = re.search(pattern, value)
    return match.group(1) if match else None


def _compact(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}
