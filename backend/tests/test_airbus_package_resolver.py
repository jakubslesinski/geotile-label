"""Resolver Airbus v2: PNEO i PHR (DESIGN_DECISIONS.md, scene-import P0.5).

Bramka P0.5 wymienia piec warunkow. Cztery da sie sprawdzic deterministycznie na fixture,
ktore odwzorowuja **zweryfikowane** nazewnictwo i strukture DIMAP-u z rzeczywistych dostaw:

- PHR nie moze zostac po cichu zapisany jako Pleiades Neo → `test_phr_*`,
- natywna paczka PNEO przechodzi rozpoznanie → `test_pneo_*`,
- ZIP-only jest widoczny → zamkniete w P1.3a (`test_archive_discovery.py`),
- sensor, data, GSD i uklad spektralny → `test_*_metadata_*` i `test_*_rgb_*`.

Piaty — produkt kafelkowany jako jedna scena z kompletem czesci — jest sprawdzany
WYLACZNIE na fixture (`test_tiled_product_*`), bo w korpusie nie ma dostawy Airbusa
z wiecej niz jednym kaflem: wszystkie maja `R1C1` i `NTILES = 1`.

Najwazniejsza wlasnosc pilnowana tutaj to **kolejnosc kanalow z metadanych, nie z konwencji**:
PHR deklaruje `B2/B1/B0` przy pasmach `B0..B3` (czyli RGB = 3,2,1), a PNEO `R/G/B` przy
wlasnym `Raster_Index` (czyli 1,2,3). Zamiana tych dwoch daje obraz o przeklamanych barwach,
ktorego nie widac po samym podgladzie.

Uruchomienie: pytest backend/tests/test_airbus_package_resolver.py
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
    build_pleiades_ms_and_pan,
    build_pleiades_neo_dimap,
    build_pleiades_neo_multi_tile,
    build_pleiades_phr_dimap,
)
from services.metadata_parser import parse_scene_metadata  # noqa: E402
from services.scene_packages.contracts import FLAG_GRAPH_V2  # noqa: E402
from services.scene_packages.dimap import parse_dimap_bands, read_dimap_bands  # noqa: E402
from services.scene_packages.providers import airbus as grammar  # noqa: E402
from services.scene_packages.resolvers import scan_source  # noqa: E402
from services.scene_manifest import _provider_mismatch  # noqa: E402


@pytest.fixture
def graph_v2(monkeypatch):
    monkeypatch.setenv(FLAG_GRAPH_V2, "1")


def _scan(tmp_path: pathlib.Path, builder, name: str):
    workspace = tmp_path / name
    workspace.mkdir(parents=True)
    package = builder(workspace)
    packages, _diagnostics = scan_source(package.root, "pleiades_neo")
    return package, packages


def _selection(packages: list[dict]) -> dict:
    assert packages, "skan nie zwrocil zadnego pakietu"
    return packages[0]["selection"]


# --- gramatyka nazw ---------------------------------------------------------------------


def test_grammar_reads_both_token_orders():
    """PNEO ma `satelita_czas_spektrum`, PHR `satelita_spektrum_czas` — parsujemy po tokenach."""
    pneo = grammar.parse_path("IMG_PNEO4_202305150851506_PMS-FS_ORT_698037a1-10fa-4211-c245-2bfb3dc1ee9f_RGB_R1C1.TIF")
    phr = grammar.parse_path("IMG_PHR1B_PMS_202111010946043_ORT_2a562ee2-09b6-41fc-c1ab-5d6e9fd4c711_R1C1.TIF")
    assert (pneo.satellite, pneo.mission, pneo.spectral, pneo.variant) == ("PNEO4", "PNEO", "PMS-FS", "RGB")
    assert (phr.satellite, phr.mission, phr.spectral, phr.variant) == ("PHR1B", "PHR", "PMS", None)
    assert pneo.timestamp == "202305150851506" and phr.timestamp == "202111010946043"


def test_product_key_is_the_uuid_not_a_name_prefix():
    """Dostawca nadaje ten sam uuid plikowi `DIM_*` i jego rastrom — to jest klucz produktu."""
    uuid = "698037a1-10fa-4211-c245-2bfb3dc1ee9f"
    raster = grammar.parse_path(f"IMG_PNEO4_202305150851506_PMS-FS_ORT_{uuid}_RGB_R1C1.TIF")
    sidecar = grammar.parse_path(f"DIM_PNEO4_202305150851506_PMS-FS_ORT_{uuid}.XML")
    assert raster.product_uuid == sidecar.product_uuid == uuid
    assert raster.product_key == f"{uuid}_RGB"
    assert sidecar.product_key == uuid


def test_variants_share_an_acquisition_but_are_separate_products():
    uuid = "698037a1-10fa-4211-c245-2bfb3dc1ee9f"
    rgb = grammar.parse_path(f"IMG_PNEO4_202305150851506_PMS-FS_ORT_{uuid}_RGB_R1C1.TIF")
    ned = grammar.parse_path(f"IMG_PNEO4_202305150851506_PMS-FS_ORT_{uuid}_NED_R1C1.TIF")
    assert rgb.acquisition_key == ned.acquisition_key
    assert rgb.product_key != ned.product_key


def test_false_colour_variant_is_not_a_labelling_product():
    uuid = "698037a1-10fa-4211-c245-2bfb3dc1ee9f"
    base = f"IMG_PNEO4_202305150851506_PMS-FS_ORT_{uuid}"
    assert grammar.is_labeling_product(f"{base}_RGB_R1C1.TIF") is True
    assert grammar.is_labeling_product(f"{base}_NED_R1C1.TIF") is False


def test_sidecar_binds_to_every_product_of_its_acquisition():
    """Jeden DIMAP PNEO opisuje ZARAZEM plik `_RGB` i `_NED`."""
    uuid = "698037a1-10fa-4211-c245-2bfb3dc1ee9f"
    base = f"IMG_PNEO4_202305150851506_PMS-FS_ORT_{uuid}"
    sidecar = f"DIM_PNEO4_202305150851506_PMS-FS_ORT_{uuid}.XML"
    bound = grammar.bind_metadata_to_products([f"{base}_RGB_R1C1.TIF", f"{base}_NED_R1C1.TIF"], [sidecar])
    assert all(value == [sidecar] for value in bound.values())
    assert len(bound) == 2


def test_sidecar_of_another_acquisition_is_not_bound():
    first = "IMG_PHR1A_PMS_202109170855303_ORT_11111111-1111-4111-8111-111111111111_R1C1.TIF"
    other = "DIM_PHR1A_PMS_202111010946043_ORT_22222222-2222-4222-8222-222222222222.XML"
    assert grammar.bind_metadata_to_products([first], [other]) == {first: []}
    assert grammar.orphan_metadata([first], [other]) == [other]


# --- kolejnosc kanalow z DIMAP-u ---------------------------------------------------------


def test_phr_band_order_comes_from_the_metadata(tmp_path, graph_v2):
    """PHR: `B2/B1/B0` przy pasmach `B0..B3` daje pasma 3, 2, 1 — nie 1, 2, 3."""
    fixture, packages = _scan(tmp_path, build_pleiades_phr_dimap, "phr-bands")
    assert _selection(packages)["rgb_bands"] == [3, 2, 1] == fixture.notes["rgb_bands"]


def test_pneo_band_order_comes_from_the_metadata(tmp_path, graph_v2):
    fixture, packages = _scan(tmp_path, build_pleiades_neo_dimap, "pneo-bands")
    assert _selection(packages)["rgb_bands"] == [1, 2, 3] == fixture.notes["rgb_bands"]


def test_each_file_of_a_product_gets_its_own_band_mapping():
    """PNEO opisuje kazdy plik osobnym blokiem; globalna lista pasm ma szesc pozycji."""
    document = (
        "<Dimap_Document><Raster_Data><Data_Access>"
        "<Data_Files><Data_File><DATA_FILE_PATH href='A_RGB.TIF'/></Data_File>"
        "<Raster_Display><Raster_Index_List>"
        "<Raster_Index><BAND_ID>R</BAND_ID><BAND_INDEX>1</BAND_INDEX></Raster_Index>"
        "<Raster_Index><BAND_ID>G</BAND_ID><BAND_INDEX>2</BAND_INDEX></Raster_Index>"
        "<Raster_Index><BAND_ID>B</BAND_ID><BAND_INDEX>3</BAND_INDEX></Raster_Index>"
        "</Raster_Index_List><Band_Display_Order>"
        "<RED_CHANNEL>R</RED_CHANNEL><GREEN_CHANNEL>G</GREEN_CHANNEL><BLUE_CHANNEL>B</BLUE_CHANNEL>"
        "</Band_Display_Order></Raster_Display></Data_Files>"
        "<Data_Files><Data_File><DATA_FILE_PATH href='A_NED.TIF'/></Data_File>"
        "<Raster_Display><Raster_Index_List>"
        "<Raster_Index><BAND_ID>NIR</BAND_ID><BAND_INDEX>1</BAND_INDEX></Raster_Index>"
        "<Raster_Index><BAND_ID>RE</BAND_ID><BAND_INDEX>2</BAND_INDEX></Raster_Index>"
        "<Raster_Index><BAND_ID>DB</BAND_ID><BAND_INDEX>3</BAND_INDEX></Raster_Index>"
        "</Raster_Index_List><Band_Display_Order>"
        "<RED_CHANNEL>NIR</RED_CHANNEL><GREEN_CHANNEL>RE</GREEN_CHANNEL><BLUE_CHANNEL>DB</BLUE_CHANNEL>"
        "</Band_Display_Order></Raster_Display></Data_Files>"
        "</Data_Access><Raster_Spectral_Record>"
        "<Band_ID>R</Band_ID><Band_ID>G</Band_ID><Band_ID>B</Band_ID>"
        "<Band_ID>NIR</Band_ID><Band_ID>RE</Band_ID><Band_ID>DB</Band_ID>"
        "</Raster_Spectral_Record></Raster_Data></Dimap_Document>"
    )
    bands = parse_dimap_bands(document)
    assert bands.files["A_RGB.TIF"].display_order == ("R", "G", "B")
    assert bands.files["A_NED.TIF"].display_order == ("NIR", "RE", "DB")
    # Bez podzialu na pliki pozycja z szescioelementowej listy dalaby dla NED pasma 4, 5, 6.
    assert bands.rgb_bands("A_NED.TIF") == [1, 2, 3]


def test_unreadable_dimap_yields_no_band_order(tmp_path):
    path = tmp_path / "DIM_broken.XML"
    path.write_text("<Dimap_Document", encoding="utf-8")
    bands = read_dimap_bands(path)
    assert bands.is_readable is False and bands.rgb_bands() is None


def test_missing_display_order_is_reported_not_guessed():
    bands = parse_dimap_bands("<Dimap_Document><Band_ID>B0</Band_ID></Dimap_Document>")
    assert bands.rgb_bands() is None


def test_special_values_are_read_from_the_metadata(tmp_path):
    """DIMAP deklaruje `NODATA` i `SATURATED`, a raster Airbusa ich nie niesie.

    Nazwa elementu (`SPECIAL_VALUE_COUNT`) sugeruje liczbe wystapien, ale jest to WARTOSC
    piksela — ustalone pomiarem: dostawy deklaruja `NODATA = 0`, a w rastrach PNEO i PHR zer
    we wszystkich pasmach jest odpowiednio 37% i 54% powierzchni.
    """
    fixture = build_pleiades_phr_dimap(tmp_path / "phr-special")
    bands = read_dimap_bands(next(fixture.root.rglob("DIM_PHR*.XML")))
    assert bands.nodata() == 0.0
    assert bands.saturated() == 255.0


def test_special_values_are_reported_per_file(tmp_path):
    fixture = build_pleiades_neo_dimap(tmp_path / "pneo-special")
    bands = read_dimap_bands(next(fixture.root.rglob("DIM_PNEO*.XML")))
    assert set(bands.files) == {
        name.split("/")[-1] for name in (*fixture.measurement, *fixture.non_measurement)
    }
    assert all(entry.special_values["NODATA"] == 0.0 for entry in bands.files.values())


def test_inconsistent_special_values_do_not_become_a_scene_value():
    """Gdy pliki dostawy deklaruja rozne `NODATA`, scena nie ma jednej takiej wartosci."""
    document = (
        "<Dimap_Document><Raster_Data><Data_Access>"
        "<Data_Files><Data_File><DATA_FILE_PATH href='A.TIF'/></Data_File><Raster_Display>"
        "<Special_Value><SPECIAL_VALUE_TEXT>NODATA</SPECIAL_VALUE_TEXT>"
        "<SPECIAL_VALUE_COUNT>0</SPECIAL_VALUE_COUNT></Special_Value>"
        "</Raster_Display></Data_Files>"
        "<Data_Files><Data_File><DATA_FILE_PATH href='B.TIF'/></Data_File><Raster_Display>"
        "<Special_Value><SPECIAL_VALUE_TEXT>NODATA</SPECIAL_VALUE_TEXT>"
        "<SPECIAL_VALUE_COUNT>65535</SPECIAL_VALUE_COUNT></Special_Value>"
        "</Raster_Display></Data_Files>"
        "</Data_Access></Raster_Data></Dimap_Document>"
    )
    bands = parse_dimap_bands(document)
    assert bands.nodata() is None
    assert bands.nodata("A.TIF") == 0.0 and bands.nodata("B.TIF") == 65535.0


def test_metadata_parser_exposes_the_special_values(tmp_path):
    fixture = build_pleiades_phr_dimap(tmp_path / "phr-parser-special")
    dimap = next(fixture.root.rglob("DIM_PHR*.XML"))
    eo = parse_scene_metadata(None, [str(dimap)], dimap.name)["eo"] or {}
    assert eo["nodata"] == 0.0 and eo["saturated"] == 255.0


# --- rozpoznanie produktu ----------------------------------------------------------------


def test_phr_delivery_becomes_a_ready_scene(tmp_path, graph_v2):
    """Dziesiec takich dostaw w korpusie konczylo jako `wymaga decyzji`."""
    _fixture, packages = _scan(tmp_path, build_pleiades_phr_dimap, "phr-ready")
    selection = _selection(packages)
    assert selection["status"] == "ready"
    assert selection["product_type"] == "PMS"
    assert len(selection["asset_ids"]) == 1


def test_pneo_delivery_selects_the_natural_colour_product(tmp_path, graph_v2):
    fixture, packages = _scan(tmp_path, build_pleiades_neo_dimap, "pneo-ready")
    selection = _selection(packages)
    assert selection["status"] == "ready"
    assert selection["product_type"] == "PMS-FS"
    chosen = {
        asset["asset_id"]: asset["package_relative_path"]
        for asset in packages[0]["assets"]
        if asset["asset_id"] in selection["asset_ids"]
    }
    assert all("_RGB_" in path for path in chosen.values()), chosen
    assert fixture.non_measurement  # wariant NED istnieje w dostawie


def test_false_colour_variant_is_offered_as_an_alternative(tmp_path, graph_v2):
    """Decyzja uzytkownika ma wybierac SEMANTYCZNY wariant produktu, nie liste plikow."""
    _fixture, packages = _scan(tmp_path, build_pleiades_neo_dimap, "pneo-alt")
    labels = [item["label"] for item in _selection(packages)["alternatives"]]
    assert labels == ["PMS-FS NED"]


def test_tiled_product_is_one_scene_with_every_part(tmp_path, graph_v2):
    """Fixture SYNTETYCZNY: w korpusie nie ma dostawy Airbusa z wiecej niz jednym kaflem."""
    fixture, packages = _scan(tmp_path, build_pleiades_neo_multi_tile, "pneo-tiles")
    selection = _selection(packages)
    assert len(packages) == 1, "czesci jednego produktu nie moga dac dwoch scen"
    assert len(selection["asset_ids"]) == fixture.notes["parts"] == 2
    assert selection["raster_kind"] == "virtual_mosaic"


def test_delivery_without_a_pansharpened_product_requires_preparation(tmp_path, graph_v2):
    _fixture, packages = _scan(tmp_path, build_pleiades_ms_and_pan, "pneo-ms-pan")
    selection = _selection(packages)
    assert selection["status"] == "prepare_required"
    assert selection["product_type"] == "MS-FS_RGB+PAN"
    assert selection["multispectral_asset_ids"] and selection["panchromatic_asset_ids"]


def test_metadata_is_bound_to_the_selected_product(tmp_path, graph_v2):
    _fixture, packages = _scan(tmp_path, build_pleiades_phr_dimap, "phr-meta")
    selection = _selection(packages)
    bound = {
        asset["package_relative_path"]
        for asset in packages[0]["assets"]
        if asset["asset_id"] in (selection.get("metadata_asset_ids") or [])
    }
    assert any(name.upper().endswith(".XML") and "DIM_" in name.upper() for name in bound), bound


# --- sensor i deklaracja dostawcy --------------------------------------------------------


def test_phr_metadata_reports_the_real_sensor(tmp_path):
    fixture = build_pleiades_phr_dimap(tmp_path / "phr-sensor")
    dimap = next(fixture.root.rglob("DIM_PHR*.XML"))
    metadata = parse_scene_metadata(None, [str(dimap)], dimap.name)
    assert metadata["sensor"] == "PHR1B" == fixture.notes["sensor"]
    assert (metadata["eo"] or {})["mission"] == "PHR"
    assert metadata["parser_name"] == "airbus_dimap_v2"


def test_pneo_metadata_reports_the_satellite_index(tmp_path):
    """`PNEO` bez indeksu to misja, nie satelita — sensor ma byc `PNEO4`."""
    fixture = build_pleiades_neo_dimap(tmp_path / "pneo-sensor")
    dimap = next(fixture.root.rglob("DIM_PNEO*.XML"))
    metadata = parse_scene_metadata(None, [str(dimap)], dimap.name)
    assert metadata["sensor"] == "PNEO4" == fixture.notes["sensor"]
    assert (metadata["eo"] or {})["spectral_processing"] == "PMS-FS"


def test_phr_declared_as_pneo_is_reported_not_recorded_silently():
    """Warunek bramki P0.5: PHR nie moze bez ostrzezenia trafic do projektu jako PNEO."""
    mismatch = _provider_mismatch(
        {"provider": "pleiades_neo"},
        {"sensor": "PHR1B", "eo": {"mission": "PHR"}},
    )
    assert mismatch is not None
    assert mismatch["code"] == "phr_declared_as_pneo"
    assert mismatch["detected_sensor"] == "PHR1B"


def test_matching_declaration_produces_no_warning():
    assert _provider_mismatch(
        {"provider": "pleiades_neo"},
        {"sensor": "PNEO4", "eo": {"mission": "PNEO"}},
    ) is None


# --- odwracalnosc -------------------------------------------------------------------------


def test_phr_is_unresolved_without_the_flag(tmp_path, monkeypatch):
    """Baseline usterki: stara sciezka wymaga tokenu `_RGB`, ktorego PHR nie ma."""
    monkeypatch.delenv(FLAG_GRAPH_V2, raising=False)
    _fixture, packages = _scan(tmp_path, build_pleiades_phr_dimap, "phr-legacy")
    selection = _selection(packages)
    assert selection["status"] == "decision_required"
    assert selection["product_type"] == "UNRESOLVED"
