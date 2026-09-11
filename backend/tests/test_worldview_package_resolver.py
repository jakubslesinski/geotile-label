"""Resolver WorldView v2 i manifesty kafli (DESIGN_DECISIONS.md, scene-import P0.6).

Bramka P0.6 wymienia szesc warunkow; piec da sie sprawdzic deterministycznie na fixture:

- obie natywne dostawy WV2 rozpoznane wg regul → `test_mul_pan_*`, `test_pan_only_*`,
- lista i kolejnosc czesci z TIL, nie z sortowania nazw → `test_part_order_*`,
- brak szesciu pozornych konfliktow PAN/MUL → `test_scene_metadata_*`,
- niepelna ekstrakcja odrozniona od PAN-only → `test_incomplete_*`,
- splaszczone rastry dostawy pozostaja osobnymi scenami `generic` → `test_flattened_*`.

Szosty — "PAN-only otwiera sie jako grayscale z liniowym robust stretch, bez profilu SAR/log" —
lezy w warstwie wyswietlania, nie w resolverze: profil wynika z MODALNOSCI PROJEKTU
(`resolve_preprocessing_profile`), a nie z wyboru produktu. To, co resolver moze zagwarantowac
i co jest tu testowane, to ze produkt jednopasmowy NIE dostaje odwzorowania RGB
(`test_pan_only_has_no_rgb_mapping`). Sam profil wyswietlania pozostaje do sprawdzenia
end-to-end na rzeczywistej dostawie.

Uruchomienie: pytest backend/tests/test_worldview_package_resolver.py
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
    build_flattened_generic_rasters,
    build_worldview_incomplete_extract,
    build_worldview_mul_only,
    build_worldview_mul_pan,
    build_worldview_pan_only,
    build_worldview_til_custom_order,
    build_worldview_two_products,
)
from services.scene_packages.contracts import FLAG_GRAPH_V2  # noqa: E402
from services.scene_packages.providers import worldview as grammar  # noqa: E402
from services.scene_packages.resolvers import get_resolver, scan_source  # noqa: E402
from services.scene_packages.tile_manifests import parse_tile_manifest  # noqa: E402


@pytest.fixture
def graph_v2(monkeypatch):
    monkeypatch.setenv(FLAG_GRAPH_V2, "1")


def _scan(tmp_path: pathlib.Path, builder, name: str, provider: str = "worldview"):
    workspace = tmp_path / name
    workspace.mkdir(parents=True)
    package = builder(workspace)
    packages, _diagnostics = scan_source(package.root, provider)
    return package, packages


def _paths(package: dict, asset_ids) -> list[str]:
    by_id = {asset["asset_id"]: asset["package_relative_path"] for asset in package.get("assets", [])}
    return [by_id[aid] for aid in (asset_ids or []) if aid in by_id]


# --- Manifest kafli (TIL) -------------------------------------------------------------


_TIL = (
    "BEGIN_GROUP = TILESET\n"
    "\tnumTiles = 3;\n"
    '\tBEGIN_GROUP = TILE_1\n\t\tfilename = "B.TIF";\n\tEND_GROUP = TILE_1\n'
    '\tBEGIN_GROUP = TILE_2\n\t\tfilename = "A.TIF";\n\tEND_GROUP = TILE_2\n'
    '\tBEGIN_GROUP = TILE_3\n\t\tfilename = "C.TIF";\n\tEND_GROUP = TILE_3\n'
    "END_GROUP = TILESET\nEND;\n"
)


def test_manifest_preserves_the_declared_order():
    manifest = parse_tile_manifest(_TIL)
    assert manifest.declared_parts == ("B.TIF", "A.TIF", "C.TIF")
    assert manifest.declared_count == 3
    assert manifest.order_key("A.TIF") == 1 and manifest.order_key("B.TIF") == 0


def test_manifest_order_key_puts_unknown_parts_last():
    assert parse_tile_manifest(_TIL).order_key("NIEZNANY.TIF") == 3


def test_manifest_reports_missing_parts():
    assert parse_tile_manifest(_TIL).missing(["A.TIF"]) == ("B.TIF", "C.TIF")


def test_declared_count_wins_over_the_listed_names():
    """Manifest deklarujacy szesc kafli, a wymieniajacy trzy, jest sam niepelny.

    Uznanie go za trzyczesciowy ukryloby brak — a wykrycie braku jest calym sensem TIL.
    """
    text = 'BEGIN_GROUP = TILESET\n\tnumTiles = 6;\n\tfilename = "A.TIF";\nEND_GROUP = TILESET\n'
    manifest = parse_tile_manifest(text)
    assert manifest.expected_count == 6
    assert manifest.unnamed_declared_count == 5


def test_unreadable_manifest_yields_an_empty_result_not_an_error():
    """Brak deklaracji ma prowadzic do decyzji zachowawczej, nie do przerwania importu."""
    manifest = parse_tile_manifest("to nie jest TIL")
    assert manifest.is_empty and manifest.expected_count == 0


# --- Gramatyka ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,component,part",
    [
        ("014670314010_01/_MUL/14JUN11_MUL_R1C1.TIF", grammar.COMPONENT_MULTISPECTRAL, "R1C1"),
        ("014670314010_01/_PAN/14JUN11_PAN_R1C2.TIF", grammar.COMPONENT_PANCHROMATIC, "R1C2"),
        ("014670314010_01/_PSH/14JUN11_PANSHARP_R1C1.TIF", grammar.COMPONENT_PANSHARPENED, "R1C1"),
        ("014670314010_01/_MUL/14JUN11_MUL.IMD", grammar.COMPONENT_MULTISPECTRAL, None),
    ],
)
def test_component_and_part_are_read_from_the_whole_path(path, component, part):
    key = grammar.parse_path(path)
    assert key.component == component
    assert key.part_id == part


def test_pansharpened_is_not_mistaken_for_panchromatic():
    """`PANSHARP` zawiera `PAN`; dopasowanie tokenowe musi rozstrzygnac to poprawnie."""
    assert grammar.component_of("_PSH/14JUN11_PANSHARP_R1C1.TIF") == grammar.COMPONENT_PANSHARPENED


def test_components_of_one_delivery_share_an_acquisition():
    """MUL i PAN to dwa komponenty JEDNEJ akwizycji — inaczej pansharpening nie ma czego laczyc."""
    mul = grammar.parse_path("014670314010_01/_MUL/14JUN11_MUL_R1C1.TIF")
    pan = grammar.parse_path("014670314010_01/_PAN/14JUN11_PAN_R1C1.TIF")
    assert mul.acquisition_key == pan.acquisition_key
    assert mul.product_key != pan.product_key


def test_real_worldview_name_exposes_timestamp_order_and_product():
    key = grammar.parse_path(
        "014670314010_01_003/014670314010_01_P001_PAN/"
        "14JUN11102144-P2AS_R1C1-014670314010_01_P001.TIF"
    )
    assert key.order_id == "014670314010_01"
    assert key.capture_date == "14JUN11"
    assert key.capture_time == "102144"
    assert key.product_id == "P001"
    assert key.acquisition_key == "014670314010_01_14JUN11102144_P001"


def test_pan_metadata_does_not_bind_to_a_mul_raster():
    """Sedno szesciu pozornych konfliktow z sekcji 4.6."""
    bound = grammar.bind_metadata_to_products(
        ["014670314010_01/_MUL/14JUN11_MUL_R1C1.TIF"],
        ["014670314010_01/_MUL/14JUN11_MUL.IMD", "014670314010_01/_PAN/14JUN11_PAN.IMD"],
    )
    assert bound["014670314010_01/_MUL/14JUN11_MUL_R1C1.TIF"] == [
        "014670314010_01/_MUL/14JUN11_MUL.IMD"
    ]


# --- Resolver: dostawa MUL+PAN --------------------------------------------------------


def test_mul_pan_keeps_its_recognised_shape(tmp_path, graph_v2):
    """Regula z P0.6 dla `014670...`: MUL 2 czesci, PAN 2 czesci, prepare_required, RGB [5,3,2]."""
    _, packages = _scan(tmp_path, build_worldview_mul_pan, "mulpan")
    selection = packages[0]["selection"]
    assert selection["status"] == "prepare_required"
    assert selection["product_type"] == "MUL+PAN"
    assert selection["rgb_bands"] == [5, 3, 2]
    assert len(selection["multispectral_asset_ids"]) == 2
    assert len(selection["panchromatic_asset_ids"]) == 2
    assert selection["completeness"] == "complete"


def test_pansharpening_component_lists_keep_til_order(tmp_path, graph_v2):
    workspace = tmp_path / "mulpan_order"
    workspace.mkdir()
    fixture = build_worldview_mul_pan(workspace)
    for til_path in fixture.root.rglob("*.TIL"):
        text = til_path.read_text(encoding="utf-8")
        names = list(reversed([line.split('"')[1] for line in text.splitlines() if "filename" in line]))
        til_path.write_text(
            text.replace(names[1], "__FIRST__").replace(names[0], names[1]).replace("__FIRST__", names[0]),
            encoding="utf-8",
        )
    packages, _ = scan_source(fixture.root, "worldview")
    entry = packages[0]
    selection = entry["selection"]
    assert _paths(entry, selection["multispectral_asset_ids"]) == _paths(
        entry,
        selection["source_components"][grammar.COMPONENT_MULTISPECTRAL],
    )
    assert _paths(entry, selection["panchromatic_asset_ids"]) == _paths(
        entry,
        selection["source_components"][grammar.COMPONENT_PANCHROMATIC],
    )
    assert all("R1C2" in paths[0] for paths in (
        _paths(entry, selection["multispectral_asset_ids"]),
        _paths(entry, selection["panchromatic_asset_ids"]),
    ))


def test_scene_metadata_of_mul_pan_describes_the_multispectral_component(tmp_path, graph_v2):
    """Bramka P0.6: nie ma szesciu pozornych konfliktow PAN/MUL.

    Working view ma byc pansharpened RGB, wiec charakterystyke spektralna wnosi MUL. Sekcja 4.6
    odnotowala odwrotnosc — wynikowa metadata opisywala glownie PAN. Kluczowe jest to, ze do
    parsera NIE trafiaja oba `.IMD` naraz; to ich scalanie produkowalo konflikty.
    """
    _, packages = _scan(tmp_path, build_worldview_mul_pan, "mulpan_meta")
    entry = packages[0]
    selection = entry["selection"]
    assert selection["primary_component"] == grammar.COMPONENT_MULTISPECTRAL
    scene_metadata = _paths(entry, selection["metadata_asset_ids"])
    assert scene_metadata, "scena musi miec metadane"
    assert all("_MUL/" in item for item in scene_metadata), scene_metadata
    assert not any(item.endswith("_PAN.IMD") for item in scene_metadata), scene_metadata


def test_panchromatic_metadata_is_kept_available_not_discarded(tmp_path, graph_v2):
    """Metadane komponentu pobocznego nie znikaja — sa dostepne osobno, tylko nie scalane."""
    _, packages = _scan(tmp_path, build_worldview_mul_pan, "mulpan_keep")
    entry = packages[0]
    per_component = entry["selection"]["component_metadata_asset_ids"]
    panchromatic = _paths(entry, per_component[grammar.COMPONENT_PANCHROMATIC])
    assert any(item.endswith("_PAN.IMD") for item in panchromatic), panchromatic


# --- Resolver: dostawa PAN-only -------------------------------------------------------


def test_pan_only_is_not_a_scene_without_the_flag(tmp_path, monkeypatch):
    """Stan faktyczny z sekcji 4.6: resolver nie ma galezi PAN-only."""
    monkeypatch.delenv(FLAG_GRAPH_V2, raising=False)
    _, packages = _scan(tmp_path, build_worldview_pan_only, "pan_off")
    assert packages[0]["selection"]["status"] == "decision_required"


def test_pan_only_is_a_ready_panchromatic_mosaic(tmp_path, graph_v2):
    """Regula z P0.6 dla `014679...`: jedna scena PAN-only, 6 czesci wg TIL, `ready`."""
    package, packages = _scan(tmp_path, build_worldview_pan_only, "pan_on")
    selection = packages[0]["selection"]
    assert selection["status"] == "ready"
    assert selection["product_type"] == "PAN"
    assert selection["raster_kind"] == "virtual_mosaic"
    assert selection["declared_parts"] == package.notes["declared_parts"] == 6
    assert selection["completeness"] == "complete"
    assert len(selection["asset_ids"]) == 6


def test_pan_only_has_no_rgb_mapping(tmp_path, graph_v2):
    """Produkt jednopasmowy nie ma odwzorowania RGB; udawanie, ze ma, mylилoby wyswietlanie."""
    _, packages = _scan(tmp_path, build_worldview_pan_only, "pan_gray")
    assert packages[0]["selection"]["rgb_bands"] is None


def test_pan_only_does_not_trigger_pansharpening(tmp_path, graph_v2):
    """"Brak automatycznego pansharpeningu bez MUL" — wprost z regul P0.6."""
    _, packages = _scan(tmp_path, build_worldview_pan_only, "pan_nosharp")
    selection = packages[0]["selection"]
    assert selection["raster_kind"] != "derived"
    assert "multispectral_asset_ids" not in selection


def test_mul_only_is_a_multispectral_view(tmp_path, graph_v2):
    """Zakres P0.6 wymienia MUL-only jako osobny, poprawny wariant."""
    _, packages = _scan(tmp_path, build_worldview_mul_only, "mulonly")
    selection = packages[0]["selection"]
    assert selection["status"] == "ready"
    assert selection["product_type"] == "MUL"


def test_two_products_in_one_delivery_become_two_scenes(tmp_path, graph_v2):
    fixture, packages = _scan(tmp_path, build_worldview_two_products, "two_products")
    assert len(packages) == fixture.notes["expected_scenes"] == 2
    assert {entry["selection"]["product_type"] for entry in packages} == {"PAN"}
    assert all(len(entry["selection"]["asset_ids"]) == fixture.notes["parts_per_scene"] for entry in packages)
    assert len({entry["selection"]["provider_scene_id"] for entry in packages}) == 2


# --- Kolejnosc czesci -----------------------------------------------------------------


def test_part_order_comes_from_the_til_not_from_sorting(tmp_path, graph_v2):
    """Bramka P0.6: lista i kolejnosc czesci pochodza z TIL, nie z sortowania nazw.

    Fixture ma TIL w kolejnosci ODWROTNEJ do nazw — przy zgodnych porzadkach ten test
    niczego by nie mierzyl.
    """
    package, packages = _scan(tmp_path, build_worldview_til_custom_order, "order")
    entry = packages[0]
    chosen = _paths(entry, entry["selection"]["asset_ids"])
    declared = [name for name in package.notes["til_order"]]
    assert [pathlib.PurePosixPath(item).name for item in chosen] == declared, chosen


def test_part_order_is_reflected_in_the_mosaic_order(tmp_path, graph_v2):
    _, packages = _scan(tmp_path, build_worldview_til_custom_order, "order2")
    assert packages[0]["selection"]["mosaic_parts_order"] == ["R3C1", "R2C1", "R1C1"]


# --- Niepelna ekstrakcja --------------------------------------------------------------


def test_incomplete_extract_is_distinguishable_from_pan_only(tmp_path, graph_v2):
    """Bramka P0.6: niepelna ekstrakcja jest odrozniona od poprawnego PAN-only."""
    _, full = _scan(tmp_path, build_worldview_pan_only, "cmp_full")
    _, partial = _scan(tmp_path, build_worldview_incomplete_extract, "cmp_part")

    assert full[0]["selection"]["completeness"] == "complete"
    assert partial[0]["selection"]["completeness"] == "partial"
    assert full[0]["selection"]["status"] != partial[0]["selection"]["status"]


def test_missing_parts_block_ready_and_are_named(tmp_path, graph_v2):
    """Brakujaca czesc BLOKUJE `ready`; import niepelnego pokrycia ma byc decyzja uzytkownika."""
    _, packages = _scan(tmp_path, build_worldview_incomplete_extract, "partial")
    selection = packages[0]["selection"]
    assert selection["status"] == "decision_required"
    assert selection["declared_parts"] == 6
    assert len(selection["missing_parts"]) == 3
    codes = {item["code"] for item in selection["diagnostics"]["warnings"]}
    assert "partial_delivery" in codes
    assert selection["alternatives"][0]["asset_ids"] == selection["asset_ids"]
    assert "partial" in selection["alternatives"][0]["label"].lower()


def test_inconsistent_til_count_cannot_look_complete(tmp_path, graph_v2):
    workspace = tmp_path / "bad_til"
    workspace.mkdir()
    fixture = build_worldview_til_custom_order(workspace)
    til_path = next(fixture.root.rglob("*.TIL"))
    til_path.write_text(
        til_path.read_text(encoding="utf-8").replace("numTiles = 3", "numTiles = 6"),
        encoding="utf-8",
    )
    packages, _ = scan_source(fixture.root, "worldview")
    selection = packages[0]["selection"]
    assert selection["status"] == "decision_required"
    assert selection["completeness"] == "partial"
    assert selection["missing_parts_unknown_count"] == 3
    assert "tile_manifest_count_mismatch" in {
        item["code"] for item in selection["diagnostics"]["warnings"]
    }


def test_incomplete_extract_was_indistinguishable_without_the_flag(tmp_path, monkeypatch):
    """Stan faktyczny: oba przypadki dawaly ten sam status i zadnej informacji o brakach."""
    monkeypatch.delenv(FLAG_GRAPH_V2, raising=False)
    _, full = _scan(tmp_path, build_worldview_pan_only, "cmp_full_off")
    _, partial = _scan(tmp_path, build_worldview_incomplete_extract, "cmp_part_off")
    assert full[0]["selection"]["status"] == partial[0]["selection"]["status"] == "decision_required"
    assert "completeness" not in full[0]["selection"]


# --- Rastry generyczne ----------------------------------------------------------------


def test_flattened_rasters_stay_separate_generic_scenes(tmp_path, graph_v2):
    """Bramka P0.6: splaszczone rastry dostawy pozostaja oddzielnymi scenami `generic`.

    Nazwy niosa tokeny `MUL` i `PAN`, wiec gdyby gramatyka komponentow wyciekla poza resolver
    WorldView, te trzy sceny zostalyby scalone w jedna dostawe dwukomponentowa.
    """
    package, packages = _scan(tmp_path, build_flattened_generic_rasters, "generic", provider="generic")
    assert len(packages) == package.notes["expected_scenes"] == 3
    for entry in packages:
        assert entry["selection"]["status"] == "ready"


# --- Reczny wybor ---------------------------------------------------------------------


def _manual_select(package: dict, asset_ids: list[str]) -> dict:
    selection = dict(package["selection"])
    selection["asset_ids"] = asset_ids
    selection["identity_asset_ids"] = asset_ids
    selection["status"] = "ready"
    selection["selected_by"] = "user"
    selection.setdefault("diagnostics", {"warnings": [], "errors": [], "metadata_conflicts": []})
    get_resolver(package["provider"]).refine_manual_selection(package, selection)
    return selection


def test_manual_selection_of_pan_parts_sets_the_product_type(tmp_path, graph_v2):
    _, packages = _scan(tmp_path, build_worldview_mul_pan, "manual")
    entry = packages[0]
    pan = [
        asset["asset_id"]
        for asset in entry["assets"]
        if asset["package_relative_path"].startswith("_PAN/")
        and asset["package_relative_path"].endswith(".TIF")
    ]
    selection = _manual_select(entry, pan)
    assert selection["product_type"] == "PAN"
    metadata = _paths(entry, selection["metadata_asset_ids"])
    assert all("_PAN/" in item for item in metadata), metadata


def test_manual_selection_of_mul_and_pan_preserves_the_derived_product(tmp_path, graph_v2):
    _, packages = _scan(tmp_path, build_worldview_mul_pan, "manual_mixed")
    entry = packages[0]
    both = [
        asset["asset_id"]
        for asset in entry["assets"]
        if asset["package_relative_path"].endswith(".TIF")
    ]
    selection = _manual_select(entry, both)
    assert selection["product_type"] == "MUL+PAN"
    assert selection["primary_component"] == grammar.COMPONENT_MULTISPECTRAL


def test_explicit_partial_import_keeps_partial_provenance(tmp_path, graph_v2):
    _, packages = _scan(tmp_path, build_worldview_incomplete_extract, "partial_manual")
    entry = packages[0]
    selection = _manual_select(entry, list(entry["selection"]["asset_ids"]))
    assert selection["status"] == "ready"
    assert selection["selected_by"] == "user"
    assert selection["product_type"] == "PAN"
    assert selection["completeness"] == "partial"
    assert len(selection["missing_parts"]) == 3
