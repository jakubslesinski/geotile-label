"""Semantyka wartosci pikseli sceny (DESIGN_DECISIONS.md, scene-import P1.5).

Aplikacja nigdy nie przelicza radiometrii zrodla — kafle do etykietowania powstaja przez
rozciagniecie do 8 bitow w profilu przetwarzania, a wartosci uzywane do datasetu pochodza
z rastra takiego, jaki dostarczyl dostawca. Zeby dalo sie o tym cokolwiek powiedziec,
manifest musi ZAPISAC, czym te wartosci sa.

Modul nie zgaduje. Kazde pole ma zrodlo (`source`), a gdy metadane milcza, wynikiem jest
`unknown` — bo „nie wiemy" jest inna informacja niz „to sa surowe DN". Rozroznienie ma
znaczenie praktyczne: bramka P1.5 zabrania NIEJAWNEGO mieszania scen o roznej kalibracji,
a `unknown` jest osobnym stanem, nie jokerem pasujacym do wszystkiego.

Podstawa jest pomiar rzeczywistych metadanych z korpusu:

- ICEYE (`ICEYE_*.xml`) podaje `calibration_factor` — czyli piksele sa NIESKALIBROWANA
  amplituda DN, a dostawca daje wspolczynnik do przeliczenia na sigma0,
- Capella (`*_extended.json`) deklaruje wprost `radiometry=sigma_nought`,
  `calibration=full` i `scale_factor` — czyli produkt JEST skalibrowany,
- WorldView (`*.IMD`) podaje `radiometricLevel=Corrected` i `radiometricEnhancement=ACOMP`,
  czyli produkt po korekcji atmosferycznej, plus `bitsPerPixel=16`,
- Airbus (`DIM_*.XML`) podaje `RADIOMETRIC_PROCESSING=DISPLAY` i `NBITS=8`, czyli produkt
  gotowy do wyswietlenia, nie radiancje.

Zestawienie ICEYE z Capella w jednym projekcie SAR laczy wiec nieskalibrowana amplitude ze
skalibrowanym sigma0 — i to jest dokladnie ta sytuacja, ktorej ma zapobiec bramka.
"""

from __future__ import annotations

from typing import Any

# --- wielkosc fizyczna pikseli ---------------------------------------------------------
QUANTITY_DN = "dn"
QUANTITY_AMPLITUDE = "amplitude"
QUANTITY_INTENSITY = "intensity"
QUANTITY_SIGMA0 = "sigma0"
QUANTITY_BETA0 = "beta0"
QUANTITY_GAMMA0 = "gamma0"
QUANTITY_RADIANCE = "radiance"
QUANTITY_REFLECTANCE = "reflectance"
QUANTITY_SURFACE_REFLECTANCE = "surface_reflectance"
QUANTITY_DISPLAY = "display_ready"
QUANTITY_UNKNOWN = "unknown"

# --- stan kalibracji -------------------------------------------------------------------
CALIBRATION_CALIBRATED = "calibrated"
CALIBRATION_UNCALIBRATED = "uncalibrated"
CALIBRATION_UNKNOWN = "unknown"

# --- jednostka -------------------------------------------------------------------------
UNITS_LINEAR = "linear"
UNITS_DB = "db"
#: Produkt bez jednostki fizycznej: surowe DN albo obraz rozciagniety do wyswietlenia.
#: `linear` znaczy „liniowy wobec wielkosci fizycznej" i dla takiego produktu wprowadza w blad.
UNITS_NONE = "none"
UNITS_UNKNOWN = "unknown"

#: Wielkosci, ktore NIE maja jednostki fizycznej.
_UNITLESS = frozenset({QUANTITY_DN, QUANTITY_DISPLAY})

#: Nazwy radiometrii uzywane przez dostawcow SAR → nasza wielkosc.
_SAR_RADIOMETRY_NAMES = {
    "sigma_nought": QUANTITY_SIGMA0,
    "sigma0": QUANTITY_SIGMA0,
    "beta_nought": QUANTITY_BETA0,
    "beta0": QUANTITY_BETA0,
    "gamma_nought": QUANTITY_GAMMA0,
    "gamma0": QUANTITY_GAMMA0,
    "amplitude": QUANTITY_AMPLITUDE,
    "intensity": QUANTITY_INTENSITY,
}

#: Deklaracje przetwarzania radiometrycznego u dostawcow EO → nasza wielkosc.
_EO_PROCESSING_NAMES = {
    "display": QUANTITY_DISPLAY,
    "basic": QUANTITY_DN,
    "reflectance": QUANTITY_REFLECTANCE,
    "surface_reflectance": QUANTITY_SURFACE_REFLECTANCE,
    "linear_stretch": QUANTITY_DISPLAY,
}


def _unknown(note: str) -> dict[str, Any]:
    return {
        "quantity": QUANTITY_UNKNOWN,
        "calibration_state": CALIBRATION_UNKNOWN,
        "units": UNITS_UNKNOWN,
        "source": "unavailable",
        "note": note,
    }


def _describe_sar(sar: dict[str, Any]) -> dict[str, Any]:
    declared = str(sar.get("radiometry") or "").strip().lower()
    calibration = str(sar.get("calibration") or "").strip().lower()
    calibration_factor = sar.get("calibration_factor")

    if declared in _SAR_RADIOMETRY_NAMES:
        quantity = _SAR_RADIOMETRY_NAMES[declared]
        calibrated = quantity in {QUANTITY_SIGMA0, QUANTITY_BETA0, QUANTITY_GAMMA0}
        return {
            "quantity": quantity,
            # `calibration=full` jest deklaracja dostawcy; brak tego pola przy zadeklarowanym
            # sigma0 nadal znaczy „skalibrowany", bo sama wielkosc tego wymaga.
            "calibration_state": CALIBRATION_CALIBRATED if calibrated else CALIBRATION_UNCALIBRATED,
            "units": UNITS_LINEAR,
            "provider_calibration": calibration or None,
            "scale_factor": sar.get("scale_factor"),
            "calibration_id": sar.get("calibration_id"),
            "source": "provider_metadata",
        }

    if calibration_factor is not None:
        # Wspolczynnik podany OBOK obrazu znaczy, ze obraz go jeszcze nie zawiera.
        return {
            "quantity": QUANTITY_AMPLITUDE,
            "calibration_state": CALIBRATION_UNCALIBRATED,
            "units": UNITS_LINEAR,
            "calibration_factor": calibration_factor,
            "calibration_available": True,
            "source": "provider_metadata",
        }

    return _unknown("SAR metadata declares neither radiometry nor a calibration factor")


def _describe_eo(eo: dict[str, Any]) -> dict[str, Any]:
    processing = str(eo.get("radiometric_processing") or "").strip().lower()
    level = str(eo.get("radiometric_level") or "").strip().lower()
    enhancement = str(eo.get("radiometric_enhancement") or "").strip().upper()

    if processing in _EO_PROCESSING_NAMES:
        quantity = _EO_PROCESSING_NAMES[processing]
        return {
            "quantity": quantity,
            # Produkt „display" jest juz przetworzony do prezentacji — nie jest ani surowym DN,
            # ani wielkoscia fizyczna, wiec nie nazywamy go skalibrowanym.
            "calibration_state": (
                CALIBRATION_CALIBRATED
                if processing in {"reflectance", "surface_reflectance"}
                else CALIBRATION_UNCALIBRATED
            ),
            "units": UNITS_NONE if quantity in _UNITLESS else UNITS_LINEAR,
            "provider_processing": eo.get("radiometric_processing"),
            "source": "provider_metadata",
        }

    if level:
        atmospherically_compensated = enhancement in {"ACOMP", "AICOMP"}
        quantity = (
            QUANTITY_SURFACE_REFLECTANCE if atmospherically_compensated else QUANTITY_DN
        )
        return {
            "quantity": quantity,
            "calibration_state": (
                CALIBRATION_CALIBRATED if atmospherically_compensated else CALIBRATION_UNCALIBRATED
            ),
            "units": UNITS_NONE if quantity in _UNITLESS else UNITS_LINEAR,
            "provider_level": eo.get("radiometric_level"),
            "provider_enhancement": eo.get("radiometric_enhancement"),
            "abs_cal_factor": eo.get("abs_cal_factor"),
            "source": "provider_metadata",
        }

    return _unknown("EO metadata declares no radiometric processing level")


def describe(
    modality: str | None,
    provider_metadata: dict[str, Any] | None,
    scene_info: dict[str, Any] | None,
    working_view: dict[str, Any] | None = None,
    selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Zbuduj blok `radiometry` manifestu sceny.

    Wielkosc i kalibracja pochodza WYLACZNIE z metadanych dostawcy; glebia bitowa, nodata
    i uklad pasm — z odczytanego rastra. `applied_by_app` jest zawsze `false`, bo aplikacja
    nie przelicza radiometrii zrodla: rozciagniecie do 8 bitow dzieje sie dopiero przy
    budowie kafli i jest wlasnoscia PROFILU, nie sceny.
    """
    provider_metadata = provider_metadata or {}
    scene_info = scene_info or {}
    working_view = working_view or {}

    sar = provider_metadata.get("sar") or {}
    eo = provider_metadata.get("eo") or {}
    normalized_modality = str(modality or "").strip().upper()

    if normalized_modality == "SAR":
        values = _describe_sar(sar)
    elif normalized_modality.endswith("EO"):
        values = _describe_eo(eo)
    elif sar:
        values = _describe_sar(sar)
    elif eo:
        values = _describe_eo(eo)
    else:
        values = _unknown("no provider metadata for this scene")

    bit_depth = (
        eo.get("bits_per_pixel")
        or _bit_depth_from_dtype(scene_info.get("dtype"))
    )
    # Raster jest zrodlem nadrzednym — deklaracja dostawcy wchodzi TYLKO wtedy, gdy plik
    # sam nic nie mowi. Airbus jest dokladnie takim przypadkiem: DIMAP deklaruje `NODATA`,
    # a GeoTIFF nie niesie go wcale.
    raster_nodata = scene_info.get("nodata")
    declared_nodata = eo.get("nodata") if raster_nodata is None else None
    return {
        **values,
        "modality": normalized_modality or None,
        "dtype": scene_info.get("dtype"),
        "bit_depth": bit_depth,
        "nodata": raster_nodata if raster_nodata is not None else declared_nodata,
        "nodata_source": (
            "raster" if raster_nodata is not None
            else "provider_metadata" if declared_nodata is not None
            else None
        ),
        "saturated": eo.get("saturated"),
        "mask_flags": scene_info.get("mask_flags") or [],
        "band_count": scene_info.get("channels"),
        # To jest interpretacja kanalow zapisana W RASTRZE i bywa niezgodna z dostawca:
        # dla PHR GDAL raportuje `red, green, blue`, podczas gdy DIMAP deklaruje, ze
        # czerwien jest trzecim pasmem. Dlatego pole nazywa sie tak, jak to, czym jest.
        "color_interpretation": scene_info.get("color_interpretation") or [],
        # Odwzorowanie RGB faktycznie uzyte przez scene — pochodzi z deklaracji dostawcy.
        "rgb_bands": list((selection or {}).get("rgb_bands") or []) or None,
        "declared_band_order": list(eo.get("band_display_order") or []) or None,
        "data_band_indexes": scene_info.get("data_band_indexes") or [],
        "incidence_angle_deg": sar.get("incidence_angle_deg"),
        "applied_by_app": False,
        "derived_variant": working_view.get("raster_kind") == "derived",
        "source_components": sorted((working_view.get("mosaic_geometry") or {}).get("components") or [])
        or sorted(((working_view.get("variant_definition") or {}).get("source_components") or {})),
    }


def _bit_depth_from_dtype(dtype: str | None) -> int | None:
    mapping = {
        "uint8": 8, "int8": 8,
        "uint16": 16, "int16": 16,
        "uint32": 32, "int32": 32, "float32": 32,
        "float64": 64, "int64": 64, "uint64": 64,
    }
    return mapping.get(str(dtype or "").strip().lower())


def calibration_key(radiometry: dict[str, Any] | None) -> tuple[str, str, str]:
    """Klucz porownawczy uzywany do wykrywania mieszania niezgodnych radiometrii."""
    values = radiometry or {}
    return (
        str(values.get("quantity") or QUANTITY_UNKNOWN),
        str(values.get("calibration_state") or CALIBRATION_UNKNOWN),
        str(values.get("units") or UNITS_UNKNOWN),
    )


def summarize(scene_manifests: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Podsumowanie radiometrii scen wchodzacych do datasetu.

    `source_kind` odpowiada wprost na pytanie bramki „czy dataset uzywa zrodla, czy wariantu
    derived" — bez przekopywania sie przez pelny manifest kazdej sceny.
    """
    scenes: dict[str, dict[str, Any]] = {}
    entries: list[dict[str, Any]] = []
    for scene_id, manifest in sorted((scene_manifests or {}).items()):
        radiometry = (manifest or {}).get("radiometry") or {}
        working = (manifest or {}).get("working_view") or {}
        raster_kind = working.get("raster_kind")
        scenes[scene_id] = radiometry
        entries.append({
            "scene_id": scene_id,
            "modality": radiometry.get("modality"),
            "quantity": radiometry.get("quantity", QUANTITY_UNKNOWN),
            "calibration_state": radiometry.get("calibration_state", CALIBRATION_UNKNOWN),
            "units": radiometry.get("units", UNITS_UNKNOWN),
            "bit_depth": radiometry.get("bit_depth"),
            "nodata": radiometry.get("nodata"),
            "source_kind": "derived" if raster_kind == "derived" else "source",
            "raster_kind": raster_kind,
            "variant_id": working.get("variant_id"),
        })
    groups = incompatible_groups(scenes)
    return {
        "scenes": entries,
        "groups": [
            {"quantity": key[0], "calibration_state": key[1], "units": key[2], "scene_ids": value}
            for key, value in groups.items()
        ],
        "mixed": bool(groups),
        "conflicting_calibration": bool(conflicting_calibration(scenes)),
        "source_kinds": sorted({entry["source_kind"] for entry in entries}),
    }


class MixedRadiometryError(ValueError):
    """Dataset laczylby sceny o sprzecznej semantyce wartosci pikseli."""

    def __init__(self, summary: dict[str, Any]):
        groups = "; ".join(
            f"{group['quantity']}/{group['calibration_state']}: "
            + ", ".join(group["scene_ids"][:5])
            + (" …" if len(group["scene_ids"]) > 5 else "")
            for group in summary.get("groups") or []
        )
        super().__init__(
            "Dataset would mix scenes with different pixel value semantics "
            f"({groups}). Filter the scenes or set allow_mixed_radiometry to accept it."
        )
        self.summary = summary


def ensure_consistent(
    scene_manifests: dict[str, dict[str, Any]],
    *,
    allow_mixed: bool = False,
) -> dict[str, Any]:
    """Zwroc podsumowanie radiometrii albo odmow zbudowania datasetu.

    Blokujemy WYLACZNIE sprzecznosc kalibracji (skalibrowane obok nieskalibrowanych) i tylko
    wtedy, gdy uzytkownik nie zgodzil sie na to wprost. Bramka P1.5 zabrania mieszania
    NIEJAWNEGO — swiadoma decyzja pozostaje mozliwa i jest zapisywana w manifescie runu.
    """
    summary = summarize(scene_manifests)
    summary["allowed_explicitly"] = bool(allow_mixed)
    if summary["conflicting_calibration"] and not allow_mixed:
        raise MixedRadiometryError(summary)
    return summary


def conflicting_calibration(scenes: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Sceny o SPRZECZNYM stanie kalibracji — czyli skalibrowane obok nieskalibrowanych.

    `unknown` nie tworzy sprzecznosci: nie wiedziec, czym cos jest, to nie to samo co wiedziec,
    ze jest inne. Sceny `unknown` sa raportowane w podsumowaniu, ale nie blokuja buildu, bo
    blokada oparta na niewiedzy zatrzymywalaby takze poprawne zestawy.
    """
    states: dict[str, list[str]] = {}
    for scene_id, radiometry in scenes.items():
        state = str((radiometry or {}).get("calibration_state") or CALIBRATION_UNKNOWN)
        if state == CALIBRATION_UNKNOWN:
            continue
        states.setdefault(state, []).append(str(scene_id))
    if len(states) <= 1:
        return {}
    return {key: sorted(value) for key, value in sorted(states.items())}


def incompatible_groups(scenes: dict[str, dict[str, Any]]) -> dict[tuple[str, str, str], list[str]]:
    """Pogrupuj sceny po radiometrii i zwroc grupy, gdy jest ich wiecej niz jedna.

    Pusty wynik znaczy „wszystkie sceny maja te sama semantyke wartosci". Kazdy inny wynik
    opisuje mieszanie, ktore musi byc widoczne, zanim powstanie dataset.
    """
    groups: dict[tuple[str, str, str], list[str]] = {}
    for scene_id, radiometry in scenes.items():
        groups.setdefault(calibration_key(radiometry), []).append(str(scene_id))
    if len(groups) <= 1:
        return {}
    return {key: sorted(value) for key, value in sorted(groups.items())}
