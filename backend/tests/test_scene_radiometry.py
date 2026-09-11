"""Radiometria i provenance wariantow roboczych (DESIGN_DECISIONS.md, scene-import P1.5).

Cztery warunki bramki P1.5 i ich testy:

- dataset wskazuje, czy uzywa zrodla czy wariantu derived → `test_summary_reports_source_kind`,
- display stretch nie zmienia danych treningowych → `test_display_stretch_does_not_*`,
- nie da sie NIEJAWNIE zmieszac nieskalibrowanego SAR ze skalibrowanym →
  `test_conflicting_calibration_*`,
- reprodukcja wariantu z tych samych wejsc daje ten sam odcisk i geometrie →
  `test_processing_manifest_fingerprint_*` (odcisk) oraz `test_output_is_repeatable_*`
  w `test_pansharpen_pipeline.py` (geometria i piksele).

Wartosci pol radiometrycznych NIE sa zgadywane: kazdy przypadek testowy odwzorowuje pole,
ktore realnie wystepuje w metadanych korpusu — `calibration_factor` w ICEYE, `radiometry`
i `calibration` u Capelli, `radiometricLevel`/`radiometricEnhancement` w IMD WorldView oraz
`RADIOMETRIC_PROCESSING`/`NBITS` w DIMAP Airbusa.

Uruchomienie: pytest backend/tests/test_scene_radiometry.py
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from models.preprocessing import PreprocessingProfile  # noqa: E402
from services.preprocessing_profiles import apply_preprocessing_profile  # noqa: E402
from services.scene_packages import radiometry  # noqa: E402

ICEYE_METADATA = {
    "sar": {
        "satellite": "ICEYE-X7",
        "product_level": "GRD",
        "polarization": "VV",
        "incidence_angle_deg": 32.5,
        # Pole zmierzone w rzeczywistych metadanych ICEYE.
        "calibration_factor": 5.040053395687071e-07,
    }
}
CAPELLA_METADATA = {
    "sar": {
        "satellite": "capella-8",
        "product_type": "GEO",
        "polarization": "HH/HH",
        # Pola zmierzone w `*_extended.json` Capelli.
        "radiometry": "sigma_nought",
        "calibration": "full",
        "scale_factor": 0.00034308869733029704,
        "calibration_id": "calibration_bundle/06fd3645",
    }
}
WORLDVIEW_METADATA = {
    "eo": {
        "satellite": "WV02",
        "product_type": "Standard",
        # Pola zmierzone w rzeczywistym `*.IMD` dostawy WV2.
        "radiometric_level": "Corrected",
        "radiometric_enhancement": "ACOMP",
        "bits_per_pixel": 16,
        "abs_cal_factor": 0.009094740,
    }
}
AIRBUS_METADATA = {
    "eo": {
        "satellite": "PHR1B",
        "product_type": "ORTHO",
        # Pola zmierzone w rzeczywistym `DIM_*.XML`.
        "radiometric_processing": "DISPLAY",
        "processing_level": "ORTHO",
        "bits_per_pixel": 8,
    }
}
SCENE_INFO_16 = {"dtype": "uint16", "channels": 1, "nodata": 0.0, "color_interpretation": ["gray"]}


# --- SAR ------------------------------------------------------------------------------


def test_iceye_calibration_factor_means_uncalibrated_amplitude():
    """Wspolczynnik podany OBOK obrazu znaczy, ze obraz go jeszcze nie zawiera."""
    result = radiometry.describe("SAR", ICEYE_METADATA, SCENE_INFO_16)
    assert result["quantity"] == radiometry.QUANTITY_AMPLITUDE
    assert result["calibration_state"] == radiometry.CALIBRATION_UNCALIBRATED
    assert result["units"] == radiometry.UNITS_LINEAR
    assert result["calibration_available"] is True
    assert result["source"] == "provider_metadata"


def test_capella_declares_sigma_nought_and_is_calibrated():
    result = radiometry.describe("SAR", CAPELLA_METADATA, SCENE_INFO_16)
    assert result["quantity"] == radiometry.QUANTITY_SIGMA0
    assert result["calibration_state"] == radiometry.CALIBRATION_CALIBRATED
    assert result["scale_factor"] == pytest.approx(0.00034308869733029704)


def test_sar_without_radiometry_metadata_is_unknown_not_assumed_dn():
    """„Nie wiemy" jest inna informacja niz „to sa surowe DN" i musi tak zostac zapisane."""
    result = radiometry.describe("SAR", {"sar": {"satellite": "UMBRA"}}, SCENE_INFO_16)
    assert result["quantity"] == radiometry.QUANTITY_UNKNOWN
    assert result["calibration_state"] == radiometry.CALIBRATION_UNKNOWN
    assert result["source"] == "unavailable"


def test_incidence_angle_is_carried_into_the_radiometry_block():
    result = radiometry.describe("SAR", ICEYE_METADATA, SCENE_INFO_16)
    assert result["incidence_angle_deg"] == 32.5


# --- EO -------------------------------------------------------------------------------


def test_worldview_acomp_is_recognised_as_surface_reflectance():
    """`radiometricEnhancement=ACOMP` to korekcja atmosferyczna, a nie surowe DN."""
    result = radiometry.describe("EO", WORLDVIEW_METADATA, {"dtype": "uint16", "channels": 8})
    assert result["quantity"] == radiometry.QUANTITY_SURFACE_REFLECTANCE
    assert result["calibration_state"] == radiometry.CALIBRATION_CALIBRATED
    assert result["bit_depth"] == 16


def test_airbus_display_product_is_not_called_calibrated():
    result = radiometry.describe("EO", AIRBUS_METADATA, {"dtype": "uint8", "channels": 4})
    assert result["quantity"] == radiometry.QUANTITY_DISPLAY
    assert result["calibration_state"] == radiometry.CALIBRATION_UNCALIBRATED
    assert result["bit_depth"] == 8


def test_bit_depth_falls_back_to_the_raster_dtype():
    result = radiometry.describe("EO", {"eo": {"radiometric_processing": "DISPLAY"}},
                                 {"dtype": "uint16", "channels": 3})
    assert result["bit_depth"] == 16


def test_app_never_marks_radiometry_as_applied_by_itself():
    """Aplikacja nie przelicza radiometrii zrodla — rozciagniecie nalezy do PROFILU kafli."""
    for metadata in (ICEYE_METADATA, CAPELLA_METADATA, WORLDVIEW_METADATA, AIRBUS_METADATA):
        assert radiometry.describe(None, metadata, SCENE_INFO_16)["applied_by_app"] is False


# --- jednostka, nodata i interpretacja pasm ----------------------------------------------


def test_display_ready_product_has_no_physical_unit():
    """`linear` znaczy „liniowy wobec wielkosci fizycznej" — dla 8-bitowego obrazu po
    rozciagnieciu jest to stwierdzenie nieprawdziwe."""
    result = radiometry.describe("EO", AIRBUS_METADATA, {"dtype": "uint8", "channels": 4})
    assert result["quantity"] == radiometry.QUANTITY_DISPLAY
    assert result["units"] == radiometry.UNITS_NONE


def test_raw_dn_also_has_no_physical_unit():
    result = radiometry.describe(
        "EO", {"eo": {"radiometric_level": "Basic"}}, {"dtype": "uint16", "channels": 4}
    )
    assert result["quantity"] == radiometry.QUANTITY_DN
    assert result["units"] == radiometry.UNITS_NONE


def test_physical_quantities_keep_a_unit():
    assert radiometry.describe("EO", WORLDVIEW_METADATA, SCENE_INFO_16)["units"] == radiometry.UNITS_LINEAR
    assert radiometry.describe("SAR", CAPELLA_METADATA, SCENE_INFO_16)["units"] == radiometry.UNITS_LINEAR


def test_provider_nodata_is_used_when_the_raster_declares_none():
    """Airbus deklaruje `NODATA` w DIMAP-ie, a sam GeoTIFF go nie niesie.

    Bez tego ramka geokodowania — 37% powierzchni w PNEO, 54% w PHR — liczy sie jako
    prawidlowe zera i przesuwa percentyle rozciagniecia.
    """
    metadata = {"eo": {**AIRBUS_METADATA["eo"], "nodata": 0.0, "saturated": 255.0}}
    result = radiometry.describe("EO", metadata, {"dtype": "uint8", "channels": 3, "nodata": None})
    assert result["nodata"] == 0.0
    assert result["nodata_source"] == "provider_metadata"
    assert result["saturated"] == 255.0


def test_raster_nodata_wins_over_the_declaration():
    """Plik jest zrodlem nadrzednym: jesli sam deklaruje `nodata`, to ono obowiazuje."""
    metadata = {"eo": {**AIRBUS_METADATA["eo"], "nodata": 0.0}}
    result = radiometry.describe("EO", metadata, {"dtype": "uint16", "channels": 3, "nodata": -9999.0})
    assert result["nodata"] == -9999.0
    assert result["nodata_source"] == "raster"


def test_missing_nodata_is_reported_as_absent_not_as_zero():
    result = radiometry.describe("EO", AIRBUS_METADATA, {"dtype": "uint8", "channels": 3})
    assert result["nodata"] is None and result["nodata_source"] is None


def test_colour_interpretation_and_rgb_mapping_are_separate_fields():
    """Dla PHR GDAL raportuje `red, green, blue`, a dostawca deklaruje czerwien jako pasmo 3.

    Gdyby oba siedzialy w jednym polu, manifest przeczylby sam sobie.
    """
    metadata = {"eo": {**AIRBUS_METADATA["eo"], "band_display_order": ["B2", "B1", "B0"]}}
    result = radiometry.describe(
        "EO",
        metadata,
        {"dtype": "uint8", "channels": 4, "color_interpretation": ["red", "green", "blue", "undefined"]},
        selection={"rgb_bands": [3, 2, 1]},
    )
    assert result["color_interpretation"] == ["red", "green", "blue", "undefined"]
    assert result["rgb_bands"] == [3, 2, 1]
    assert result["declared_band_order"] == ["B2", "B1", "B0"]
    assert "band_order" not in result


def test_scene_without_an_rgb_mapping_reports_none():
    result = radiometry.describe("SAR", ICEYE_METADATA, SCENE_INFO_16)
    assert result["rgb_bands"] is None


# --- mieszanie kalibracji ---------------------------------------------------------------


def _manifest(scene_metadata, modality, raster_kind="direct"):
    return {
        "radiometry": radiometry.describe(modality, scene_metadata, SCENE_INFO_16),
        "working_view": {"raster_kind": raster_kind, "variant_id": "variant_x"},
    }


def test_conflicting_calibration_is_detected_between_iceye_and_capella():
    """Realna sytuacja z korpusu: oba zrodla SAR sa skonfigurowane w tym samym srodowisku."""
    summary = radiometry.summarize({
        "iceye-scene": _manifest(ICEYE_METADATA, "SAR"),
        "capella-scene": _manifest(CAPELLA_METADATA, "SAR"),
    })
    assert summary["conflicting_calibration"] is True
    assert summary["mixed"] is True
    assert len(summary["groups"]) == 2


def test_scenes_from_one_provider_do_not_conflict():
    summary = radiometry.summarize({
        "a": _manifest(ICEYE_METADATA, "SAR"),
        "b": _manifest(ICEYE_METADATA, "SAR"),
    })
    assert summary["conflicting_calibration"] is False
    assert summary["mixed"] is False


def test_unknown_radiometry_does_not_create_a_conflict():
    """Blokada oparta na niewiedzy zatrzymywalaby takze poprawne zestawy."""
    summary = radiometry.summarize({
        "known": _manifest(CAPELLA_METADATA, "SAR"),
        "unknown": _manifest({"sar": {"satellite": "UMBRA"}}, "SAR"),
    })
    assert summary["conflicting_calibration"] is False
    # Samo zmieszanie jest nadal widoczne — po prostu nie blokuje.
    assert summary["mixed"] is True


def test_summary_reports_source_kind_per_scene():
    summary = radiometry.summarize({
        "direct-scene": _manifest(ICEYE_METADATA, "SAR", raster_kind="direct"),
        "derived-scene": _manifest(ICEYE_METADATA, "SAR", raster_kind="derived"),
    })
    kinds = {entry["scene_id"]: entry["source_kind"] for entry in summary["scenes"]}
    assert kinds == {"direct-scene": "source", "derived-scene": "derived"}
    assert summary["source_kinds"] == ["derived", "source"]


def test_build_refuses_to_mix_calibrated_and_uncalibrated_sar():
    with pytest.raises(radiometry.MixedRadiometryError) as raised:
        radiometry.ensure_consistent({
            "iceye-scene": _manifest(ICEYE_METADATA, "SAR"),
            "capella-scene": _manifest(CAPELLA_METADATA, "SAR"),
        })
    message = str(raised.value)
    assert "iceye-scene" in message and "capella-scene" in message
    assert "allow_mixed_radiometry" in message


def test_explicit_consent_lets_the_mixed_build_through_and_is_recorded():
    """Bramka zabrania mieszania NIEJAWNEGO — swiadoma decyzja ma zostac udokumentowana."""
    summary = radiometry.ensure_consistent(
        {
            "iceye-scene": _manifest(ICEYE_METADATA, "SAR"),
            "capella-scene": _manifest(CAPELLA_METADATA, "SAR"),
        },
        allow_mixed=True,
    )
    assert summary["allowed_explicitly"] is True
    assert summary["conflicting_calibration"] is True


def test_consistent_project_passes_without_consent():
    summary = radiometry.ensure_consistent({
        "a": _manifest(CAPELLA_METADATA, "SAR"),
        "b": _manifest(CAPELLA_METADATA, "SAR"),
    })
    assert summary["allowed_explicitly"] is False
    assert summary["conflicting_calibration"] is False


def test_mixed_radiometry_is_opt_in_and_off_by_default():
    from models.dataset_config import DatasetConfig

    assert DatasetConfig().allow_mixed_radiometry is False


def test_summary_of_an_empty_project_is_not_a_conflict():
    summary = radiometry.summarize({})
    assert summary["mixed"] is False and summary["scenes"] == []


# --- display stretch a dane treningowe --------------------------------------------------


def _profile(**overrides) -> PreprocessingProfile:
    base = {
        "profile_id": "test",
        "name": "test",
        "modality": "SAR",
        "input_quantity": "amplitude",
        "radiometric_transform": "linear",
        "percentile_stretch": True,
        "stretch_low": 2.0,
        "stretch_high": 98.0,
    }
    base.update(overrides)
    return PreprocessingProfile(**base)


def test_display_stretch_does_not_reach_the_tile_pipeline():
    """Kafle datasetu zaleza WYLACZNIE od profilu, nie od `display_min`/`display_max` sceny.

    Gdyby rozciagniecie wyswietlania zasilalo build, zmiana suwaka w podgladzie zmienialaby
    dane treningowe — a jest to ta sama scena i ten sam profil.
    """
    tile = np.linspace(0, 4000, 64 * 64, dtype=np.float32).reshape(64, 64)
    profile = _profile()

    first = apply_preprocessing_profile(tile, profile)
    # Wartosci wyswietlania sceny sa czescia `scene_info`, a nie profilu — nie ma ich jak
    # przekazac do `apply_preprocessing_profile()`. Ten test utrwala te granice.
    second = apply_preprocessing_profile(tile, profile)
    assert np.array_equal(first, second)
    assert "display_min" not in profile.model_dump()
    assert "display_max" not in profile.model_dump()


def test_tile_values_change_only_when_the_profile_changes():
    tile = np.linspace(0, 4000, 64 * 64, dtype=np.float32).reshape(64, 64)
    default = apply_preprocessing_profile(tile, _profile())
    narrower = apply_preprocessing_profile(tile, _profile(stretch_low=10.0, stretch_high=90.0))
    assert not np.array_equal(default, narrower)


def test_percentile_scope_is_the_tile_not_the_scene():
    """Zakres rozciagniecia liczy sie z KAFLA — profil dopuszcza tylko `tile`."""
    assert _profile().percentile_scope == "tile"
