"""Kwalifikacja scen i kontrakt zadania derywatu 1x — R1.0 i R1.2."""

from __future__ import annotations

from pathlib import Path

from models.job import JobType
from services.scene_packages.fullres_derivative import (
    ACTIVATION_OK,
    ACTIVATION_PROFILE_CHANGED,
    ACTIVATION_REVISION_CHANGED,
    ACTIVATION_SOURCE_CHANGED,
    ACTIVATION_VARIANT_CHANGED,
    COG_PROFILE_VERSION,
    DISQUALIFIED_ALREADY_FULL_RESOLUTION,
    DISQUALIFIED_NOT_DIRECT,
    DISQUALIFIED_NO_JP2_DECODE,
    build_job_payload,
    dedupe_key,
    may_activate,
    qualifies_for_fullres_derivative,
)

BIG_JP2 = {"width": 60476, "height": 43476, "source_overviews": {}}
SMALL_JP2 = {
    "width": 4000,
    "height": 3000,
    "source_overviews": {
        "usable": True,
        "type": "native_multiresolution",
        "width": 4000,
        "height": 3000,
    },
}


def _q(**kwargs):
    base = dict(
        source_path=Path("scene.jp2"),
        display_path=Path("overview.vrt"),
        raster_kind="direct",
        scene_info=BIG_JP2,
        overview_factors=[2, 4, 8, 16],
    )
    base.update(kwargs)
    return qualifies_for_fullres_derivative(**base)


def test_big_slow_jp2_qualifies():
    result = _q()
    assert result.qualifies is True
    assert result.finest_display_factor == 2


def test_standalone_two_x_preview_still_qualifies_for_fullres():
    result = _q(display_path=Path("overview.vrt.ovr"))
    assert result.qualifies is True
    assert result.finest_display_factor == 2


def test_mosaic_does_not_qualify():
    """Mozaiki maja wlasna sciezke przygotowania — derywat bylby praca podwojona."""
    assert _q(raster_kind="virtual_mosaic").reason == DISQUALIFIED_NOT_DIRECT


def test_geotiff_does_not_qualify():
    result = _q(source_path=Path("scene.tif"), display_path=Path("scene.tif"))
    assert result.qualifies is False
    assert result.reason == DISQUALIFIED_NO_JP2_DECODE


def test_small_jp2_with_usable_native_levels_does_not_qualify():
    """~20 min i kilka GB za nic, skoro 1x i tak jest obslugiwane."""
    result = _q(scene_info=SMALL_JP2, display_path=Path("scene.jp2"))
    assert result.qualifies is False
    assert result.reason == DISQUALIFIED_ALREADY_FULL_RESOLUTION


def test_scene_with_published_cog_stops_qualifying():
    """Po publikacji derywatu scena nie moze kwalifikowac sie ponownie."""
    result = _q(display_path=Path("fullres_zstd.tif"))
    assert result.qualifies is False
    assert result.reason == DISQUALIFIED_NO_JP2_DECODE


def test_qualification_agrees_with_the_zoom_contract():
    """Jedno zrodlo prawdy: nie moze byc ograniczonego zoomu bez kwalifikacji.

    Gdyby te dwie reguly rozjechaly sie, powstalby stan, w ktorym uzytkownik ma
    ograniczona rozdzielczosc, a nic tego nie naprawia.
    """
    from services.scene_display_contract import (
        classify_display_assets,
        compute_zoom_contract,
    )

    for scene_info, display in ((BIG_JP2, "overview.vrt"), (SMALL_JP2, "scene.jp2"),
                                (BIG_JP2, "fullres.tif")):
        assets = classify_display_assets(
            source_path=Path("scene.jp2"), display_path=Path(display),
            raster_kind="direct", scene_info=scene_info, overview_factors=[2, 4],
        )
        contract = compute_zoom_contract(scene_info=scene_info, assets=assets)
        restricted = contract.available_native_zoom < contract.source_max_zoom
        qualifies = _q(scene_info=scene_info, display_path=Path(display)).qualifies
        assert restricted == qualifies


# --- kontrakt zadania ----------------------------------------------------------------


def test_job_type_exists_and_is_separate_from_preparation():
    assert JobType.SCENE_FULLRES_DERIVATIVE.value == "scene_fullres_derivative"
    assert JobType.SCENE_FULLRES_DERIVATIVE is not JobType.SCENE_PREPARATION


def test_payload_carries_everything_needed_to_revalidate():
    payload = build_job_payload(
        scene_id="s1", source_fingerprint="fp", variant_id="v1", source_revision="rev",
    )
    assert payload == {
        "scene_id": "s1",
        "source_fingerprint": "fp",
        "variant_id": "v1",
        "cog_profile_version": COG_PROFILE_VERSION,
        "expected_source_revision": "rev",
    }


def test_dedupe_key_is_per_scene():
    assert dedupe_key("p", "s") != dedupe_key("p", "s2")
    assert dedupe_key("p", "s") == dedupe_key("p", "s")


def _payload():
    return build_job_payload(
        scene_id="s1", source_fingerprint="fp", variant_id="v1", source_revision="rev",
    )


def test_activation_allowed_when_nothing_changed():
    check = may_activate(
        _payload(), source_fingerprint="fp", variant_id="v1", source_revision="rev",
    )
    assert check.may_activate is True
    assert check.reason == ACTIVATION_OK


def test_relink_during_build_blocks_activation():
    """Dwadziescia minut budowy to dosc czasu, zeby zrodlo sie zmienilo."""
    check = may_activate(
        _payload(), source_fingerprint="inne", variant_id="v1", source_revision="rev",
    )
    assert check.may_activate is False
    assert check.reason == ACTIVATION_SOURCE_CHANGED
    assert check.details["expected"]["source_fingerprint"] == "fp"
    assert check.details["actual"]["source_fingerprint"] == "inne"


def test_variant_change_blocks_activation():
    check = may_activate(
        _payload(), source_fingerprint="fp", variant_id="v2", source_revision="rev",
    )
    assert check.reason == ACTIVATION_VARIANT_CHANGED


def test_revision_change_blocks_activation():
    check = may_activate(
        _payload(), source_fingerprint="fp", variant_id="v1", source_revision="rev2",
    )
    assert check.reason == ACTIVATION_REVISION_CHANGED


def test_profile_bump_blocks_activation_of_an_old_build():
    stale = dict(_payload(), cog_profile_version=COG_PROFILE_VERSION - 1)
    check = may_activate(
        stale, source_fingerprint="fp", variant_id="v1", source_revision="rev",
    )
    assert check.reason == ACTIVATION_PROFILE_CHANGED
