"""Resolver ICEYE v2 (DESIGN_DECISIONS.md, scene-import P0.3).

Bramka P0.3 wymienia piec warunkow. Cztery da sie sprawdzic deterministycznie na fixture:

- rzeczywisty root daje produkt per akwizycja, nie jeden pakiet → `test_source_root_*`,
- wybrane GRD maja `product_level=GRD` → `test_*_product_level_*`,
- nowe COG/GeoJSON i legacy TIFF/XML maja osobne fixture → korpus B0 plus tutaj,
- inwentarz nie otwiera SLC HDF5 jako obrazu → `test_slc_hdf5_*`.

Piaty — spadek liczby konfliktow z 914 do 0 — jest metryka RZECZYWISTYCH danych z udzialu
sieciowego i nie moze byc odtworzony w CI. Zastepuje go tutaj test mechanizmu, ktory te
konflikty wytwarzal: wiazanie sidecara do produktu (`test_metadata_binding_*`). Sam licznik
914→0 zostaje do zmierzenia harnessem z B0b, gdy udzial bedzie dostepny.

Testy sa uruchamiane w OBU stanach flagi tam, gdzie to ma sens, bo wartoscia jest nie tylko
nowe zachowanie, ale i pewnosc, ze stare pozostalo nietkniete.

Uruchomienie: pytest backend/tests/test_iceye_package_resolver.py
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
    build_iceye_cog_geojson,
    build_iceye_dual_polarization,
    build_iceye_grd_suffix_only,
    build_iceye_legacy_grd,
    build_iceye_multi_product_acquisition,
    build_iceye_source_root_with_spreadsheet,
)
from services.scene_packages.contracts import FLAG_GRAPH_V2  # noqa: E402
from services.scene_packages.providers.iceye import (  # noqa: E402
    bind_metadata_to_products,
    is_complex_product,
    parse_product_name,
)
from services.scene_packages.resolvers import scan_source  # noqa: E402


@pytest.fixture
def graph_v2(monkeypatch):
    monkeypatch.setenv(FLAG_GRAPH_V2, "1")


def _scan(tmp_path: pathlib.Path, builder, name: str):
    workspace = tmp_path / name
    workspace.mkdir(parents=True)
    package = builder(workspace)
    packages, _diagnostics = scan_source(package.root, "iceye")
    return package, packages


def _paths(package: dict, asset_ids) -> list[str]:
    by_id = {asset["asset_id"]: asset["package_relative_path"] for asset in package.get("assets", [])}
    return [by_id[aid] for aid in (asset_ids or []) if aid in by_id]


# --- Gramatyka nazw -------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,level,task,polarization",
    [
        ("ICEYE_X12_GRD_SM_7654321_20240605T091200.tif", "GRD", "7654321", None),
        ("ICEYE_GRD_SLH_1234567_20240604T101500.tif", "GRD", "1234567", None),
        # Token poziomu WYLACZNIE jako sufiks — luka wymieniona w zakresie P0.3.
        ("ICEYE_X7_SLH_9876543_20240610T142200_GRD.tif", "GRD", "9876543", None),
        ("ICEYE_X3_VID_SM_5555555_20240612T081500.tif", "VID", "5555555", None),
        ("ICEYE_X3_SLC_SM_5555555_20240612T081500.h5", "SLC", "5555555", None),
        ("ICEYE_X4_GRD_SM_6666666_20240615T033000_HH.tif", "GRD", "6666666", "HH"),
    ],
)
def test_product_name_grammar(name, level, task, polarization):
    key = parse_product_name(name)
    assert key.product_level == level
    assert key.task_id == task
    assert key.polarization == polarization


def test_datetime_is_not_mistaken_for_a_task_id():
    """`20240604T101500` zawiera osmiocyfrowa liczbe; nie moze udawac identyfikatora zadania."""
    key = parse_product_name("ICEYE_GRD_SLH_1234567_20240604T101500.tif")
    assert key.datetime_utc == "20240604T101500"
    assert key.task_id == "1234567"


def test_acquisition_key_ignores_processing_level():
    """GRD, VID i SLC z jednego przelotu to JEDNA akwizycja — rozdziela je dopiero produkt."""
    grd = parse_product_name("ICEYE_X3_GRD_SM_5555555_20240612T081500.tif")
    vid = parse_product_name("ICEYE_X3_VID_SM_5555555_20240612T081500.tif")
    assert grd.acquisition_key == vid.acquisition_key
    assert grd.product_key != vid.product_key


def test_unparseable_name_yields_no_product_key():
    """Bez klucza nie wiazemy niczego — zgadywanie jest gorsze od braku metadanych."""
    assert parse_product_name("notatka.txt").product_key is None


@pytest.mark.parametrize("name,expected", [
    ("ICEYE_X3_SLC_SM_5555555.h5", True),
    ("ICEYE_X3_SLC_SM_5555555.hdf5", True),
    ("ICEYE_X3_GRD_SM_5555555.tif", False),
])
def test_complex_product_detection(name, expected):
    assert is_complex_product(name) is expected


# --- Wiazanie sidecarow ---------------------------------------------------------------


def test_metadata_binding_keeps_vid_sidecar_away_from_grd():
    """Rdzen usterki z sekcji 4.3: sidecar VID nadawal produktowi GRD `product_level=VID`."""
    bound = bind_metadata_to_products(
        ["ICEYE_X3_GRD_SM_5555555_20240612T081500.tif"],
        [
            "ICEYE_X3_GRD_SM_5555555_20240612T081500.xml",
            "ICEYE_X3_VID_SM_5555555_20240612T081500.xml",
        ],
    )
    attached = bound["ICEYE_X3_GRD_SM_5555555_20240612T081500.tif"]
    assert attached == ["ICEYE_X3_GRD_SM_5555555_20240612T081500.xml"]


def test_metadata_binding_accepts_a_level_free_acquisition_sidecar():
    """Sidecar bez wlasnego poziomu opisuje przelot i moze zwiazac sie z produktem."""
    bound = bind_metadata_to_products(
        ["ICEYE_X3_GRD_SM_5555555_20240612T081500.tif"],
        ["ICEYE_5555555_20240612T081500.geojson"],
    )
    assert bound["ICEYE_X3_GRD_SM_5555555_20240612T081500.tif"] == [
        "ICEYE_5555555_20240612T081500.geojson"
    ]


def test_metadata_binding_separates_polarizations():
    """Dwie polaryzacje jednej akwizycji nie moga wymienic sie sidecarami."""
    bound = bind_metadata_to_products(
        [
            "ICEYE_X4_GRD_SM_6666666_20240615T033000_HH.tif",
            "ICEYE_X4_GRD_SM_6666666_20240615T033000_HV.tif",
        ],
        [
            "ICEYE_X4_GRD_SM_6666666_20240615T033000_HH.xml",
            "ICEYE_X4_GRD_SM_6666666_20240615T033000_HV.xml",
        ],
    )
    for polarization in ("HH", "HV"):
        raster = f"ICEYE_X4_GRD_SM_6666666_20240615T033000_{polarization}.tif"
        assert bound[raster] == [f"ICEYE_X4_GRD_SM_6666666_20240615T033000_{polarization}.xml"]


def test_metadata_binding_drops_what_it_cannot_place():
    """Sidecar bez rozpoznawalnego klucza nie jest przypisywany "na wszelki wypadek"."""
    bound = bind_metadata_to_products(
        ["ICEYE_X3_GRD_SM_5555555_20240612T081500.tif"],
        ["Zestawienie_ICEYE_2024.xml"],
    )
    assert bound["ICEYE_X3_GRD_SM_5555555_20240612T081500.tif"] == []


# --- Resolver: topologia --------------------------------------------------------------


def test_source_root_with_spreadsheet_yields_one_package_without_the_flag(tmp_path, monkeypatch):
    """Bez flagi obowiazuje stan z sekcji 4.3 — arkusz scala caly root."""
    monkeypatch.delenv(FLAG_GRAPH_V2, raising=False)
    _, packages = _scan(tmp_path, build_iceye_source_root_with_spreadsheet, "root_off")
    assert len(packages) == 1


def test_source_root_with_spreadsheet_yields_one_package_per_acquisition(tmp_path, graph_v2):
    """Bramka P0.3: root daje produkt per akwizycja, nie jeden scalony pakiet."""
    _, packages = _scan(tmp_path, build_iceye_source_root_with_spreadsheet, "root_on")
    assert len(packages) == 2
    roots = sorted(str(entry.get("package_root_relative")) for entry in packages)
    assert any("1111111" in item for item in roots)
    assert any("2222222" in item for item in roots)
    # Kazda paczka widzi WYLACZNIE wlasny raster — to jest miara rozdzielenia.
    for entry in packages:
        rasters = [
            asset["package_relative_path"]
            for asset in entry["assets"]
            if asset.get("asset_role") == "measurement"
        ]
        assert len(rasters) == 1, rasters


# --- Resolver: rozpoznanie produktu ---------------------------------------------------


@pytest.mark.parametrize("builder,name", [
    (build_iceye_legacy_grd, "legacy"),
    (build_iceye_cog_geojson, "cog"),
])
def test_known_deliveries_stay_ready_under_the_flag(tmp_path, graph_v2, builder, name):
    """Regresja: dostawy dzialajace przed zmiana musza dzialac po niej."""
    _, packages = _scan(tmp_path, builder, name)
    selection = packages[0]["selection"]
    assert selection["product_type"] == "GRD"
    assert selection["status"] == "ready"


def test_grd_suffix_only_is_not_recognised_without_the_flag(tmp_path, monkeypatch):
    """Stan faktyczny: `..._GRD.tif` wypada poza wzorzec i konczy jako `decision_required`."""
    monkeypatch.delenv(FLAG_GRAPH_V2, raising=False)
    _, packages = _scan(tmp_path, build_iceye_grd_suffix_only, "suffix_off")
    assert packages[0]["selection"]["status"] != "ready"


def test_grd_suffix_only_resolves_under_the_flag(tmp_path, graph_v2):
    """Zakres P0.3: nazwa konczaca sie `_GRD.tif` jest produktem GRD."""
    package, packages = _scan(tmp_path, build_iceye_grd_suffix_only, "suffix_on")
    selection = packages[0]["selection"]
    assert selection["status"] == "ready"
    assert selection["product_type"] == "GRD"
    assert _paths(packages[0], selection["asset_ids"]) == list(package.measurement)


def test_vid_is_not_selected_as_a_labeling_product(tmp_path, graph_v2):
    """Z akwizycji GRD+VID+SLC wybierany jest wylacznie GRD."""
    package, packages = _scan(tmp_path, build_iceye_multi_product_acquisition, "multi")
    selection = packages[0]["selection"]
    assert selection["status"] == "ready"
    assert _paths(packages[0], selection["asset_ids"]) == list(package.measurement)
    assert package.non_measurement[0] not in _paths(packages[0], selection["asset_ids"])


def test_slc_hdf5_never_enters_the_inventory(tmp_path, graph_v2):
    """Bramka P0.3: kontener HDF5 nie jest kandydatem na obraz do etykietowania."""
    package, packages = _scan(tmp_path, build_iceye_multi_product_acquisition, "slc")
    slc = str(package.notes["slc_container"])
    paths = [asset["package_relative_path"] for asset in packages[0]["assets"]]
    assert slc not in paths, paths


def test_selected_grd_carries_only_its_own_sidecar(tmp_path, graph_v2):
    """Bramka P0.3: sidecar VID nie moze trafic do metadanych produktu GRD."""
    _, packages = _scan(tmp_path, build_iceye_multi_product_acquisition, "bind")
    selection = packages[0]["selection"]
    bound = _paths(packages[0], selection.get("metadata_asset_ids"))
    assert bound == ["ICEYE_X3_GRD_SM_5555555_20240612T081500.xml"], bound


def test_sidecar_of_another_product_is_excluded_without_a_warning(tmp_path, graph_v2):
    """Sidecar VID nalezy do produktu VID — jego pominiecie przy GRD nie jest anomalia.

    Ostrzeganie o nim zamienialoby diagnostyke w szum przy kazdej dostawie wieloproduktowej,
    a takie sa u ICEYE regula. Ostrzezenie jest zarezerwowane dla sidecarow, ktore nie naleza
    do NICZEGO — patrz test ponizej.
    """
    _, packages = _scan(tmp_path, build_iceye_multi_product_acquisition, "other_product")
    selection = packages[0]["selection"]
    assert _paths(packages[0], selection["metadata_asset_ids"]) == [
        "ICEYE_X3_GRD_SM_5555555_20240612T081500.xml"
    ]
    warnings = selection["diagnostics"]["warnings"]
    assert not any(item.get("code") == "metadata_not_bound" for item in warnings), warnings


def test_orphan_sidecar_is_reported_not_silently_dropped(tmp_path, graph_v2):
    """Sidecar bez zadnego produktu musi zostawic slad — cisza byla czescia usterki z 4.3."""
    workspace = tmp_path / "orphan"
    workspace.mkdir()
    package = build_iceye_multi_product_acquisition(workspace)
    (package.root / "Zestawienie_ICEYE_2024.xml").write_text("<x/>", encoding="utf-8")

    packages, _ = scan_source(package.root, "iceye")

    warnings = packages[0]["selection"]["diagnostics"]["warnings"]
    assert any(item.get("code") == "metadata_not_bound" for item in warnings), warnings


def test_dual_polarization_yields_one_logical_scene_per_polarization(tmp_path, graph_v2):
    """Zakres P0.3: polaryzacje sa oddzielnymi wariantami logicznymi."""
    _, packages = _scan(tmp_path, build_iceye_dual_polarization, "dualpol")
    assert len(packages) == 2
    scene_ids = sorted(str(entry["selection"]["provider_scene_id"]) for entry in packages)
    assert scene_ids[0].endswith("_HH") and scene_ids[1].endswith("_HV")
    for entry in packages:
        polarization = str(entry["selection"]["provider_scene_id"])[-2:]
        for path in _paths(entry, entry["selection"]["asset_ids"]):
            assert path.endswith(f"_{polarization}.tif"), (polarization, path)


def test_each_polarization_carries_only_its_own_sidecar(tmp_path, graph_v2):
    """Wiazanie musi zejsc do wariantow logicznych, nie zatrzymac sie na decyzji nadrzednej.

    Selekcja nadrzedna przy dwoch polaryzacjach ma status `decision_required` i pusta liste
    assetow, wiec wiazanie na jej poziomie nic nie robi. Gdyby na tym poprzestac, sceny HH
    i HV dostalyby wspolna, plaska pule sidecarow — ten sam blad co w sekcji 4.3, tylko
    o poziom nizej.
    """
    _, packages = _scan(tmp_path, build_iceye_dual_polarization, "dualpol_meta")
    for entry in packages:
        selection = entry["selection"]
        polarization = str(selection["provider_scene_id"])[-2:]
        bound = _paths(entry, selection.get("metadata_asset_ids"))
        assert bound, f"{polarization}: brak zwiazanych metadanych"
        for path in bound:
            assert path.endswith(f"_{polarization}.xml"), (polarization, bound)


# --- Status metadanych ----------------------------------------------------------------


def _write_iceye_xml(path: pathlib.Path, level: str, spacing: str) -> str:
    """Minimalny sidecar ICEYE. `satellite_name` jest wymagany, zeby parser go rozpoznal."""
    path.write_text(
        "<product>"
        "<satellite_name>ICEYE-X3</satellite_name>"
        f"<product_name>ICEYE_X3_{level}_SM_5555555_20240612T081500</product_name>"
        f"<product_level>{level}</product_level>"
        f"<range_spacing>{spacing}</range_spacing>"
        "</product>",
        encoding="utf-8",
    )
    return str(path)


def test_conflicting_sidecars_are_reported_in_the_status(tmp_path, graph_v2):
    """Sekcja 4.3: `metadata_status` pozostawal `ok` mimo 914 konfliktow.

    Sprzeczne sidecary sa tu podane parserowi wprost, bo po naprawie wiazania resolver
    normalnie by ich razem nie przekazal. Test pilnuje SYGNALU, nie wiazania: gdyby kiedys
    inna sciezka znowu podala sprzeczne pliki, status ma o tym powiedziec.
    """
    from services.metadata_parser import STATUS_METADATA_CONFLICTS, parse_scene_metadata

    files = [
        _write_iceye_xml(tmp_path / "a.xml", "GRD", "0.5"),
        _write_iceye_xml(tmp_path / "b.xml", "VID", "2.5"),
    ]
    parsed = parse_scene_metadata(None, files, "ICEYE_X3_GRD_SM_5555555_20240612T081500.tif")

    assert parsed["parser_diagnostics"]["metadata_conflicts"], "konflikt musi byc wykryty"
    assert parsed["metadata_status"] == STATUS_METADATA_CONFLICTS


def test_status_without_the_flag_keeps_the_old_value(tmp_path, monkeypatch):
    """Bez flagi status pozostaje `ok` — zmiana nie wycieka poza flage."""
    monkeypatch.delenv(FLAG_GRAPH_V2, raising=False)
    from services.metadata_parser import parse_scene_metadata

    files = [
        _write_iceye_xml(tmp_path / "a.xml", "GRD", "0.5"),
        _write_iceye_xml(tmp_path / "b.xml", "VID", "2.5"),
    ]
    parsed = parse_scene_metadata(None, files, "ICEYE_X3_GRD_SM_5555555_20240612T081500.tif")
    assert parsed["parser_diagnostics"]["metadata_conflicts"]
    assert parsed["metadata_status"] == "ok"


def test_consistent_sidecars_stay_ok(tmp_path, graph_v2):
    """Kontrola negatywna: bez konfliktu status nie moze sie pogorszyc."""
    from services.metadata_parser import parse_scene_metadata

    files = [_write_iceye_xml(tmp_path / "only.xml", "GRD", "0.5")]
    parsed = parse_scene_metadata(None, files, "ICEYE_X3_GRD_SM_5555555_20240612T081500.tif")
    assert not parsed["parser_diagnostics"]["metadata_conflicts"]
    assert parsed["metadata_status"] == "ok"
