"""Kontrakt wyswietlania sceny — R0.1/R0.2.

Najwazniejszy test w tym pliku to `test_source_max_zoom_is_independent_of_availability`:
uklad wspolrzednych musi byc niezmienny, bo to wzgledem niego liczone sa okna odczytu
i geometria adnotacji. Gdyby dostepnosc derywatu potrafila go ruszyc, adnotacje
przesunelyby sie przy publikacji COG.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.scene_display_contract import (
    DISPLAY_KIND_COG,
    DISPLAY_KIND_GEOTIFF,
    DISPLAY_KIND_JP2_BACKED_VRT,
    DISPLAY_KIND_SOURCE,
    SOURCE_KIND_GEOTIFF,
    SOURCE_KIND_JP2,
    classify_display_assets,
    compute_source_max_zoom,
    compute_zoom_contract,
)

BIG_JP2 = {"width": 60476, "height": 43476, "source_overviews": {}}
SMALL_JP2 = {
    "width": 4000,
    "height": 3000,
    # `usable` + natywny typ + maly rozmiar => poziomy zrodla wystarcza do wyswietlania.
    "source_overviews": {"usable": True, "type": "native_multiresolution",
                         "width": 4000, "height": 3000},
}


def _assets(**kwargs):
    base = dict(
        source_path=Path("scene.jp2"),
        display_path=Path("overview.vrt"),
        raster_kind="direct",
        scene_info=BIG_JP2,
        overview_factors=[2, 4, 8, 16, 32, 64],
    )
    base.update(kwargs)
    return classify_display_assets(**base)


def test_source_max_zoom_matches_dimensions():
    assert compute_source_max_zoom(60476, 43476) == 8
    assert compute_source_max_zoom(256, 256) == 0
    assert compute_source_max_zoom(0, 0) == 0


def test_source_max_zoom_is_independent_of_availability():
    """Uklad wspolrzednych nie moze zalezec od tego, czy derywat istnieje.

    To jest bramka blokujaca R0: gdyby `source_max_zoom` zmienil sie po publikacji COG,
    okna odczytu i `map.unproject()` odnosilyby sie do innego poziomu, a adnotacje
    przesunelyby sie na scenie.
    """
    slow = compute_zoom_contract(scene_info=BIG_JP2, assets=_assets())
    fast = compute_zoom_contract(
        scene_info=BIG_JP2,
        assets=_assets(display_path=Path("fullres.tif")),
    )
    assert slow.source_max_zoom == fast.source_max_zoom == 8
    assert slow.available_native_zoom == 7  # piramida zaczyna sie od 2x
    assert fast.available_native_zoom == 8  # COG obsluguje 1x


def test_jp2_backed_vrt_still_requires_jp2_decode():
    assets = _assets()
    assert assets.source_asset_kind == SOURCE_KIND_JP2
    assert assets.display_asset_kind == DISPLAY_KIND_JP2_BACKED_VRT
    assert assets.display_requires_jp2_decode is True


def test_standalone_ovr_uses_tiff_lane_but_keeps_two_x_zoom_limit():
    assets = classify_display_assets(
        source_path=Path("scene.jp2"),
        display_path=Path("overview.vrt.ovr"),
        raster_kind="direct",
        scene_info={"width": 60476, "height": 43476, "source_overviews": {}},
        overview_factors=[2, 4, 8],
    )
    contract = compute_zoom_contract(
        scene_info={"width": 60476, "height": 43476},
        assets=assets,
    )
    assert assets.display_asset_kind == "preview_geotiff"
    assert assets.display_requires_jp2_decode is False
    assert assets.finest_display_factor == 2
    assert contract.available_native_zoom == contract.source_max_zoom - 1


def test_published_cog_stops_requiring_jp2_decode():
    """Po publikacji COG zrodlem dalej jest JP2, ale renderer go nie czyta."""
    assets = _assets(display_path=Path("fullres_zstd.tif"))
    assert assets.source_asset_kind == SOURCE_KIND_JP2
    assert assets.display_asset_kind == DISPLAY_KIND_COG
    assert assets.display_requires_jp2_decode is False
    assert assets.finest_display_factor == 1


def test_raw_jp2_source_requires_decode():
    assets = _assets(display_path=Path("scene.jp2"))
    assert assets.display_asset_kind == DISPLAY_KIND_SOURCE
    assert assets.display_requires_jp2_decode is True


def test_geotiff_scene_is_never_restricted():
    assets = classify_display_assets(
        source_path=Path("scene.tif"), display_path=Path("overview.vrt"),
        raster_kind="direct", scene_info={"width": 47408, "height": 34006},
        overview_factors=[2, 4, 8],
    )
    assert assets.source_asset_kind == SOURCE_KIND_GEOTIFF
    assert assets.display_asset_kind == DISPLAY_KIND_GEOTIFF
    assert assets.display_requires_jp2_decode is False
    contract = compute_zoom_contract(scene_info={"width": 47408, "height": 34006}, assets=assets)
    assert contract.available_native_zoom == contract.source_max_zoom


def test_small_jp2_with_usable_native_levels_is_not_restricted():
    """Ograniczenie dotyczy tylko JP2, ktore faktycznie sa wolne w 1x."""
    assets = _assets(scene_info=SMALL_JP2, display_path=Path("scene.jp2"))
    assert assets.display_requires_jp2_decode is True
    assert assets.finest_display_factor == 1
    contract = compute_zoom_contract(scene_info=SMALL_JP2, assets=assets)
    assert contract.available_native_zoom == contract.source_max_zoom


def test_unknown_pyramid_falls_back_to_one_level_down():
    assets = _assets(overview_factors=[])
    contract = compute_zoom_contract(scene_info=BIG_JP2, assets=assets)
    assert contract.available_native_zoom == contract.source_max_zoom - 1


@pytest.mark.parametrize("factor,steps", [(2, 1), (4, 2), (8, 3)])
def test_available_zoom_drops_by_log2_of_finest_factor(factor, steps):
    assets = _assets(overview_factors=[factor, factor * 2])
    contract = compute_zoom_contract(scene_info=BIG_JP2, assets=assets)
    assert contract.available_native_zoom == contract.source_max_zoom - steps


def test_allows_rejects_above_available_but_not_below():
    contract = compute_zoom_contract(scene_info=BIG_JP2, assets=_assets())
    assert contract.allows(contract.available_native_zoom)
    assert not contract.allows(contract.available_native_zoom + 1)
    assert not contract.allows(-1)
    assert contract.allows(0)


def test_xyz_zoom_drops_by_the_same_number_of_steps():
    contract = compute_zoom_contract(scene_info=BIG_JP2, assets=_assets(), native_xyz_zoom=19)
    assert contract.available_native_xyz_zoom == 18


def test_api_fields_expose_both_zooms_separately():
    contract = compute_zoom_contract(
        scene_info=BIG_JP2, assets=_assets(), display_asset_revision="abc123",
    )
    fields = contract.as_api_fields()
    assert fields["source_max_zoom"] == fields["reference_zoom"] == 8
    assert fields["available_native_zoom"] == 7
    assert fields["display_asset_revision"] == "abc123"
    assert fields["display_requires_jp2_decode"] is True


# --- zoom natywny XYZ dla trybu geo -------------------------------------------------

ARSENYEV_GEO = {
    "has_geo": True,
    "width": 60476,
    "height": 43476,
    # Piksel kwadratowy w stopniach (2,7e-6) daje na tej szerokosci rozne rozstawy w metrach.
    "pixel_spacing_x_m": 0.2155,
    "pixel_spacing_y_m": 0.2999,
    "gsd_m": 0.2577,
    "bounds": [133.2063, 44.0661, 133.3696, 44.1834],
}


def test_native_xyz_zoom_matches_frozen_e0_manifest():
    """Manifest E0 potwierdzil, ze z19 odpowiada 1x dla sceny krytycznej."""
    from services.scene_display_contract import compute_native_xyz_zoom

    assert compute_native_xyz_zoom(ARSENYEV_GEO) == 19


def test_native_xyz_zoom_uses_finest_spacing_not_average():
    """Srednie `gsd_m` zanizyloby wynik — liczy sie najdrobniejsze probkowanie."""
    from services.scene_display_contract import compute_native_xyz_zoom

    finest = compute_native_xyz_zoom(ARSENYEV_GEO)
    coarse_only = compute_native_xyz_zoom(
        {**ARSENYEV_GEO, "pixel_spacing_x_m": None, "pixel_spacing_y_m": None}
    )
    assert finest >= coarse_only


def test_native_xyz_zoom_is_none_without_georeference():
    from services.scene_display_contract import compute_native_xyz_zoom

    assert compute_native_xyz_zoom({"has_geo": False, "gsd_m": 0.3}) is None
    assert compute_native_xyz_zoom({"has_geo": True}) is None


# --- R0.4: rozdzielone statusy --------------------------------------------------------


def test_fullres_status_is_missing_when_pyramid_stops_at_2x():
    """Sedno R0.4: gotowa piramida 2x NIE znaczy dostepnej pelnej rozdzielczosci.

    Wczesniej `overview_status` raportowal wtedy `ready`, a frontend mapowal to na
    "High-resolution view ready" — obietnice, ktora lamala sie przy dojechaniu zoomem.
    """
    from services.scene_display_contract import FULLRES_MISSING, derive_display_statuses

    contract = compute_zoom_contract(scene_info=BIG_JP2, assets=_assets())
    statuses = derive_display_statuses(preview_status="ready", contract=contract)
    assert statuses["preview_status"] == "ready"
    assert statuses["fullres_derivative_status"] == FULLRES_MISSING
    assert statuses["available_native_zoom"] == 7


def test_fullres_status_is_ready_when_one_x_is_servable():
    from services.scene_display_contract import FULLRES_READY, derive_display_statuses

    contract = compute_zoom_contract(
        scene_info=BIG_JP2, assets=_assets(display_path=Path("fullres.tif"))
    )
    statuses = derive_display_statuses(preview_status="ready", contract=contract)
    assert statuses["fullres_derivative_status"] == FULLRES_READY


def test_job_status_wins_over_inference():
    """Gdy zadanie wlasnie trwa, jego stan jest wazniejszy niz to, co da sie obsluzyc."""
    from services.scene_display_contract import FULLRES_BUILDING, derive_display_statuses

    contract = compute_zoom_contract(scene_info=BIG_JP2, assets=_assets())
    statuses = derive_display_statuses(
        preview_status="ready", contract=contract, job_status=FULLRES_BUILDING
    )
    assert statuses["fullres_derivative_status"] == FULLRES_BUILDING


def test_unknown_job_status_falls_back_to_inference():
    from services.scene_display_contract import FULLRES_MISSING, derive_display_statuses

    contract = compute_zoom_contract(scene_info=BIG_JP2, assets=_assets())
    statuses = derive_display_statuses(
        preview_status="ready", contract=contract, job_status="nonsense"
    )
    assert statuses["fullres_derivative_status"] == FULLRES_MISSING


def test_geotiff_reports_fullres_ready_without_any_derivative():
    from services.scene_display_contract import FULLRES_READY, derive_display_statuses

    assets = classify_display_assets(
        source_path=Path("scene.tif"), display_path=Path("scene.tif"),
        raster_kind="direct", scene_info={"width": 12000, "height": 9000},
        overview_factors=[],
    )
    contract = compute_zoom_contract(scene_info={"width": 12000, "height": 9000}, assets=assets)
    statuses = derive_display_statuses(preview_status="native", contract=contract)
    assert statuses["fullres_derivative_status"] == FULLRES_READY
