"""Resolver Capella v2 (DESIGN_DECISIONS.md, scene-import P0.4).

Bramka P0.4 wymienia piec warunkow. Cztery da sie sprawdzic deterministycznie:

- GEO jest automatycznie rozpoznane jako poprawny produkt → `test_geo_*`,
- folder z dwiema akwizycjami daje dwie sceny → `test_two_acquisitions_*`,
- metadata rastera i `extended.json` maja ten sam identyfikator akwizycji
  → `test_*_metadata_*`,
- reczny wybor GEO/GEC nie pozostawia `UNRESOLVED` → `test_manual_selection_*`.

Piaty — "rzeczywiste zrodlo daje 20 glownych measurement TIFF, bez 20 preview" — jest metryka
udzialu sieciowego. Mechanizm, ktory ja realizuje (role assetow), zostal zamkniety w S1
i jest tam pokryty testami; sam licznik 20/20 pozostaje do zmierzenia audytem B0b.

Uruchomienie: pytest backend/tests/test_capella_package_resolver.py
"""

from __future__ import annotations

import pathlib
import sys

import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(BACKEND_ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT / "tests"))

from fixtures.scene_packages import (  # noqa: E402
    build_capella_gec,
    build_capella_geo,
    build_capella_geo_and_gec,
    build_capella_two_acquisitions,
)
from services.scene_packages.contracts import FLAG_GRAPH_V2  # noqa: E402
from services.scene_packages.providers.capella import (  # noqa: E402
    PRODUCT_PREFERENCE_ENV,
    bind_metadata_to_products,
    group_by_acquisition,
    parse_product_name,
    preferred_products,
    product_preference,
)
from services.scene_packages.resolvers import get_resolver, scan_source  # noqa: E402


@pytest.fixture
def graph_v2(monkeypatch):
    monkeypatch.setenv(FLAG_GRAPH_V2, "1")


def _scan(tmp_path: pathlib.Path, builder, name: str):
    workspace = tmp_path / name
    workspace.mkdir(parents=True)
    package = builder(workspace)
    packages, _diagnostics = scan_source(package.root, "capella")
    return package, packages


def _paths(package: dict, asset_ids) -> list[str]:
    by_id = {asset["asset_id"]: asset["package_relative_path"] for asset in package.get("assets", [])}
    return [by_id[aid] for aid in (asset_ids or []) if aid in by_id]


# --- Gramatyka nazw -------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,satellite,timestamp,product",
    [
        ("CAPELLA_C08_SP_GEO_HH_20240604101500.tif", "C08", "20240604101500", "GEO"),
        ("CAPELLA_C08_SP_GEC_HH_20240604101500.tif", "C08", "20240604101500", "GEC"),
        ("CAPELLA_C08_SP_GEO_HH_20240604101500_extended.json", "C08", "20240604101500", "GEO"),
        # Wariant z zakresem czasu: identyfikuje akwizycje POCZATEK, nie koniec zbierania.
        ("CAPELLA_C02_SP_GEC_HH_20230101_120000_120030.tif", "C02", "20230101120000", "GEC"),
        ("CAPELLA_C08_SP_SLC_HH_20240604101500.tif", "C08", "20240604101500", "SLC"),
    ],
)
def test_product_name_grammar(name, satellite, timestamp, product):
    key = parse_product_name(name)
    assert key.satellite == satellite
    assert key.timestamp == timestamp
    assert key.product_type == product


def test_geo_and_gec_of_one_pass_share_an_acquisition():
    """GEO i GEC tego samego przelotu to dwa PRODUKTY jednej akwizycji, nie dwie sceny."""
    geo = parse_product_name("CAPELLA_C08_SP_GEO_HH_20240604101500.tif")
    gec = parse_product_name("CAPELLA_C08_SP_GEC_HH_20240604101500.tif")
    assert geo.acquisition_key == gec.acquisition_key
    assert geo.product_key != gec.product_key


def test_two_satellites_at_the_same_second_are_two_acquisitions():
    """Sam znacznik czasu nie identyfikuje akwizycji — potrzebny jest takze satelita."""
    first = parse_product_name("CAPELLA_C08_SP_GEO_HH_20240604101500.tif")
    second = parse_product_name("CAPELLA_C09_SP_GEO_HH_20240604101500.tif")
    assert first.acquisition_key != second.acquisition_key


def test_grouping_splits_a_flat_delivery_folder():
    grouped = group_by_acquisition([
        "CAPELLA_C08_SP_GEO_HH_20240604101500.tif",
        "CAPELLA_C09_SP_GEO_HH_20240604134500.tif",
    ])
    assert sorted(grouped) == ["C08_20240604101500", "C09_20240604134500"]


def test_unrecognised_names_are_not_merged_into_an_acquisition():
    """Plik bez klucza trafia do grupy pustej, a nie do dowolnej akwizycji."""
    grouped = group_by_acquisition(["zrzut_ekranu.tif"])
    assert list(grouped) == [""]


# --- Preferencja produktu -------------------------------------------------------------


def test_default_preference_puts_geo_first(monkeypatch):
    """Rzeczywista dostawa to glownie GEO; zalozenie o istnieniu GEC bylo zrodlem usterki."""
    monkeypatch.delenv(PRODUCT_PREFERENCE_ENV, raising=False)
    assert product_preference()[0] == "GEO"


def test_preference_is_configurable(monkeypatch):
    monkeypatch.setenv(PRODUCT_PREFERENCE_ENV, "GEC,GEO")
    assert product_preference() == ("GEC", "GEO")
    assert preferred_products([
        "CAPELLA_C08_SP_GEO_HH_20240604101500.tif",
        "CAPELLA_C08_SP_GEC_HH_20240604101500.tif",
    ])[0] == "GEC"


def test_bad_preference_narrows_instead_of_breaking(monkeypatch):
    """Nierozpoznany token jest ignorowany, a brakujace produkty dopisane w kolejnosci domyslnej."""
    monkeypatch.setenv(PRODUCT_PREFERENCE_ENV, "NONSENS,GEC")
    assert product_preference() == ("GEC", "GEO")


def test_preference_falls_back_when_the_favourite_is_absent(monkeypatch):
    monkeypatch.setenv(PRODUCT_PREFERENCE_ENV, "GEC,GEO")
    product, chosen = preferred_products(["CAPELLA_C08_SP_GEO_HH_20240604101500.tif"])
    assert product == "GEO" and len(chosen) == 1


def test_non_labeling_products_are_never_preferred():
    """SLC nie jest obrazem do etykietowania, wiec sam nie moze dac gotowej sceny."""
    assert preferred_products(["CAPELLA_C08_SP_SLC_HH_20240604101500.tif"]) == (None, [])


# --- Wiazanie metadanych --------------------------------------------------------------


def test_metadata_binds_within_one_acquisition_only():
    """Sekcja 4.4: reczny wybor "moze powiazac metadata z innej akwizycji"."""
    bound = bind_metadata_to_products(
        ["CAPELLA_C08_SP_GEO_HH_20240604101500.tif"],
        [
            "CAPELLA_C08_SP_GEO_HH_20240604101500_extended.json",
            "CAPELLA_C09_SP_GEO_HH_20240604134500_extended.json",
        ],
    )
    assert bound["CAPELLA_C08_SP_GEO_HH_20240604101500.tif"] == [
        "CAPELLA_C08_SP_GEO_HH_20240604101500_extended.json"
    ]


def test_metadata_of_the_other_product_stays_with_its_own_product():
    """`extended.json` produktu GEC nie moze opisac produktu GEO tej samej akwizycji."""
    bound = bind_metadata_to_products(
        ["CAPELLA_C08_SP_GEO_HH_20240604101500.tif"],
        ["CAPELLA_C08_SP_GEC_HH_20240604101500_extended.json"],
    )
    assert bound["CAPELLA_C08_SP_GEO_HH_20240604101500.tif"] == []


# --- Resolver -------------------------------------------------------------------------


def test_geo_is_not_recognised_without_the_flag(tmp_path, monkeypatch):
    """Stan faktyczny z sekcji 4.4: resolver rozpoznaje wylacznie GEC."""
    monkeypatch.delenv(FLAG_GRAPH_V2, raising=False)
    _, packages = _scan(tmp_path, build_capella_geo, "geo_off")
    assert packages[0]["selection"]["product_type"] == "UNRESOLVED"


def test_geo_resolves_automatically(tmp_path, graph_v2):
    """Bramka P0.4: GEO jest automatycznie rozpoznane jako poprawny produkt."""
    package, packages = _scan(tmp_path, build_capella_geo, "geo_on")
    selection = packages[0]["selection"]
    assert selection["status"] == "ready"
    assert selection["product_type"] == "GEO"
    assert _paths(packages[0], selection["asset_ids"]) == list(package.measurement)


def test_gec_still_resolves(tmp_path, graph_v2):
    """Regresja: jedyny wspierany dotad produkt musi dzialac po zmianie."""
    _, packages = _scan(tmp_path, build_capella_gec, "gec_on")
    selection = packages[0]["selection"]
    assert selection["status"] == "ready"
    assert selection["product_type"] == "GEC"


def test_preview_never_reaches_the_selection(tmp_path, graph_v2):
    """20 glownych TIFF bez 20 preview — mechanizm domkniety w S1, tu regresja."""
    package, packages = _scan(tmp_path, build_capella_geo, "prev")
    chosen = _paths(packages[0], packages[0]["selection"]["asset_ids"])
    assert not any(item in package.non_measurement for item in chosen), chosen


def test_two_acquisitions_yield_two_scenes(tmp_path, graph_v2):
    """Bramka P0.4: folder z dwiema akwizycjami daje dwie sceny."""
    _, packages = _scan(tmp_path, build_capella_two_acquisitions, "two")
    assert len(packages) == 2
    for entry in packages:
        selection = entry["selection"]
        assert selection["status"] == "ready"
        assert selection["product_type"] == "GEO"
        assert len(selection["asset_ids"]) == 1


def test_each_acquisition_carries_its_own_metadata(tmp_path, graph_v2):
    """Bramka P0.4: metadata rastera i `extended.json` maja ten sam identyfikator akwizycji."""
    _, packages = _scan(tmp_path, build_capella_two_acquisitions, "two_meta")
    for entry in packages:
        selection = entry["selection"]
        raster = _paths(entry, selection["asset_ids"])[0]
        metadata = _paths(entry, selection.get("metadata_asset_ids"))
        acquisition = parse_product_name(raster).acquisition_key
        assert metadata, f"{raster}: brak zwiazanych metadanych"
        for item in metadata:
            assert parse_product_name(item).acquisition_key == acquisition, (raster, item)


def test_geo_and_gec_of_one_acquisition_stay_one_scene(tmp_path, graph_v2):
    """Dwa produkty jednej akwizycji to jedna scena — rozdzielamy przeloty, nie produkty."""
    _, packages = _scan(tmp_path, build_capella_geo_and_gec, "geogec")
    assert len(packages) == 1
    selection = packages[0]["selection"]
    assert selection["product_type"] == "GEO"
    assert len(selection["asset_ids"]) == 1


def test_product_preference_changes_what_the_resolver_picks(tmp_path, graph_v2, monkeypatch):
    monkeypatch.setenv(PRODUCT_PREFERENCE_ENV, "GEC,GEO")
    _, packages = _scan(tmp_path, build_capella_geo_and_gec, "geogec_pref")
    assert packages[0]["selection"]["product_type"] == "GEC"


# --- Reczny wybor ---------------------------------------------------------------------


def _manual_select(package: dict, asset_ids: list[str]) -> dict:
    """Odtworz to, co robi endpoint `select-asset`, bez warstwy HTTP."""
    selection = dict(package["selection"])
    selection["asset_ids"] = asset_ids
    selection["identity_asset_ids"] = asset_ids
    selection["status"] = "ready"
    selection["selected_by"] = "user"
    selection.setdefault("diagnostics", {"warnings": [], "errors": [], "metadata_conflicts": []})
    get_resolver(package["provider"]).refine_manual_selection(package, selection)
    return selection


def test_manual_selection_sets_the_product_type(tmp_path, graph_v2):
    """Bramka P0.4: reczny wybor GEO/GEC nie pozostawia `UNRESOLVED`."""
    _, packages = _scan(tmp_path, build_capella_geo_and_gec, "manual")
    entry = packages[0]
    gec = next(
        asset["asset_id"]
        for asset in entry["assets"]
        if asset["package_relative_path"].endswith("GEC_HH_20240604101500.tif")
    )
    selection = _manual_select(entry, [gec])
    assert selection["product_type"] == "GEC"


def test_manual_selection_rebinds_metadata_to_the_chosen_product(tmp_path, graph_v2):
    """Po recznej zmianie produktu metadane nie moga zostac po poprzednim wyborze."""
    _, packages = _scan(tmp_path, build_capella_geo_and_gec, "manual_meta")
    entry = packages[0]
    assert entry["selection"]["product_type"] == "GEO"

    gec = next(
        asset["asset_id"]
        for asset in entry["assets"]
        if asset["package_relative_path"].endswith("GEC_HH_20240604101500.tif")
    )
    selection = _manual_select(entry, [gec])
    metadata = _paths(entry, selection.get("metadata_asset_ids"))
    assert metadata == ["CAPELLA_C08_SP_GEC_HH_20240604101500_extended.json"], metadata


def test_manual_selection_across_acquisitions_does_not_mix_metadata(tmp_path, graph_v2):
    """Wprost przypadek z konca sekcji 4.4."""
    _, packages = _scan(tmp_path, build_capella_two_acquisitions, "manual_acq")
    entry = packages[1]
    other = next(
        asset["asset_id"]
        for asset in entry["assets"]
        if asset["package_relative_path"] == "CAPELLA_C08_SP_GEO_HH_20240604101500.tif"
    )
    selection = _manual_select(entry, [other])
    metadata = _paths(entry, selection.get("metadata_asset_ids"))
    assert metadata == ["CAPELLA_C08_SP_GEO_HH_20240604101500_extended.json"], metadata


def test_manual_selection_of_mixed_products_is_reported(tmp_path, graph_v2):
    """Wybor mieszajacy typy produktow nie jest scena — status ma to powiedziec."""
    _, packages = _scan(tmp_path, build_capella_geo_and_gec, "manual_mixed")
    entry = packages[0]
    both = [
        asset["asset_id"]
        for asset in entry["assets"]
        if asset["package_relative_path"].endswith("_HH_20240604101500.tif")
    ]
    assert len(both) == 2
    selection = _manual_select(entry, both)
    codes = {item["code"] for item in selection["diagnostics"]["warnings"]}
    assert "mixed_product_types" in codes, selection["diagnostics"]


def test_manual_selection_is_untouched_without_the_flag(tmp_path, monkeypatch):
    """Bez flagi reczny wybor zachowuje sie jak dotad — zmiana nie wycieka poza flage."""
    monkeypatch.delenv(FLAG_GRAPH_V2, raising=False)
    _, packages = _scan(tmp_path, build_capella_gec, "manual_off")
    entry = packages[0]
    asset_id = entry["assets"][0]["asset_id"]
    selection = _manual_select(entry, [asset_id])
    assert "metadata_asset_ids" not in selection


def test_sibling_providers_do_not_inherit_the_iceye_grammar(graph_v2):
    """Umbra i BlackSky dziedzicza po `IceyeResolver`, ale nie po jego gramatyce nazw.

    Bez tego rozroznienia reczny wybor u tych dostawcow bylby doprecyzowywany regulami ICEYE,
    czyli zmienialby zachowanie dostawcy, ktorego ten etap w ogole nie dotyczy.
    """
    assert get_resolver("iceye").manual_grammar == "iceye"
    assert get_resolver("capella").manual_grammar == "capella"
    assert get_resolver("umbra").manual_grammar is None
    assert get_resolver("blacksky").manual_grammar is None

    package = {
        "provider": "umbra",
        "assets": [{"asset_id": "a1", "package_relative_path": "UMBRA_GEC_1.tif", "role": "raster_candidate"}],
        "selection": {"product_type": "UNRESOLVED"},
    }
    selection = _manual_select(package, ["a1"])
    assert selection["product_type"] == "UNRESOLVED"
    assert "metadata_asset_ids" not in selection
