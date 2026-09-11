"""Kontrakty rozpoznawania paczek dostawców (DESIGN_DECISIONS.md, scene-import B0).

Ten moduł pełni dwie role naraz i to rozróżnienie jest celowe:

1. **Baseline** — testy bez markera opisują zachowanie, które jest DZIŚ poprawne i nie może
   ulec regresji podczas przebudowy w P0.
2. **Zamiar** — testy oznaczone `xfail(strict=True)` opisują zachowanie, które roadmapa
   obiecuje w bramkach P0.3–P0.6, a którego dziś nie ma. `strict=True` sprawia, że w chwili
   naprawy test zacznie zgłaszać XPASS i **zmusi do zdjęcia markera**, zamiast po cichu
   przepuścić zmianę. Dzięki temu korpus nie zestarzeje się niezauważenie.

Każdy `xfail` wskazuje etap roadmapy, który go zdejmie.

Fixture są generowane (patrz `fixtures/scene_packages`), więc żadne dane dostawcy nie trafiają
do repozytorium ani do CI.

Uruchomienie: pytest backend/tests/test_provider_package_contracts.py
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
    build_capella_two_acquisitions,
    build_iceye_cog_geojson,
    build_iceye_legacy_grd,
    build_iceye_source_root_with_spreadsheet,
    build_pleiades_neo_dimap,
    build_pleiades_phr_dimap,
    build_readme_only_directory,
    build_worldview_incomplete_extract,
    build_worldview_mul_pan,
    build_worldview_pan_only,
)
from services.scene_packages.resolvers import scan_source  # noqa: E402
from services.scene_packages.contracts import graph_v2_enabled  # noqa: E402

#: Flaga jest odczytywana RAZ, na poziomie modulu: dotyczy calego uruchomienia, a nie
#: pojedynczego testu. Testy, ktore same nia manipuluja, siedza w
#: `test_scene_package_contract_v2.py` i nie uzywaja ponizszych markerow.
GRAPH_V2 = graph_v2_enabled()

#: Test opisujacy USTERKE, ktora kontrakt v2 juz naprawil. Pod flaga nie ma czego mierzyc,
#: wiec go pomijamy — inaczej naprawa wygladalaby jak regresja. Marker znika razem
#: z flaga, gdy v2 stanie sie domyslne.
defect_baseline = pytest.mark.skipif(
    GRAPH_V2, reason="baseline usterki naprawionej przez GEOTILE_SCENE_PACKAGE_GRAPH_V2"
)


def xfail_until_graph_v2(reason: str):
    """Zamiar spelniony przez kontrakt v2: pod flaga ma PRZECHODZIC, bez niej nadal `xfail`.

    `strict=True` zostaje w obu trybach, wiec kazda zmiana w ktorakolwiek strone jest
    zglaszana, zamiast po cichu przejsc.
    """
    return pytest.mark.xfail(condition=not GRAPH_V2, strict=True, reason=reason)


def _scan(tmp_path: pathlib.Path, builder, provider: str, name: str):
    """Zbuduj fixture i przeskanuj GO SAMEGO jako zrodlo wskazane przez uzytkownika.

    Skanujemy `package.root`, a nie katalog nadrzedny. Roznica jest istotna: sygnatury
    dostawcow (`_looks_like_package_root`) dzialaja na plikach LEZACYCH BEZPOSREDNIO
    w skanowanym katalogu. Skan rodzica omijalby ten mechanizm i testowalby zwykle
    schodzenie do podkatalogow, czyli zupelnie inna sciezke kodu.
    """
    workspace = tmp_path / name
    workspace.mkdir(parents=True)
    package = builder(workspace)
    packages, diagnostics = scan_source(package.root, provider)
    return package, packages, diagnostics


def _selection(packages: list[dict]) -> dict:
    assert packages, "resolver nie zwrocil zadnego pakietu"
    return packages[0].get("selection") or {}


def _alternative_labels(selection: dict) -> list[str]:
    return [str(item.get("label") or "") for item in (selection.get("alternatives") or [])]


# --- ICEYE ----------------------------------------------------------------------------


def test_iceye_legacy_grd_resolves_to_ready_grd(tmp_path):
    """BASELINE: pojedyncza dostawa GRD (GeoTIFF + XML) rozwiazuje sie bez decyzji."""
    _, packages, _ = _scan(tmp_path, build_iceye_legacy_grd, "iceye", "legacy")
    selection = _selection(packages)
    assert len(packages) == 1
    assert selection.get("product_type") == "GRD"
    assert selection.get("status") == "ready"


def test_iceye_cog_geojson_resolves_to_ready_grd(tmp_path):
    """BASELINE: nowsza dostawa GRD COG + GeoJSON rowniez rozwiazuje sie bez decyzji."""
    _, packages, _ = _scan(tmp_path, build_iceye_cog_geojson, "iceye", "cog")
    selection = _selection(packages)
    assert selection.get("product_type") == "GRD"
    assert selection.get("status") == "ready"


@defect_baseline
def test_iceye_source_root_is_currently_merged_into_one_package(tmp_path):
    """BASELINE USTERKI (sekcja 4.3): arkusz z `ICEYE` w nazwie scala caly root w jeden pakiet.

    Test utrwala stan faktyczny, zeby zmiana byla widoczna. Oczekiwany docelowy wynik
    opisuje `test_iceye_source_root_should_yield_two_acquisitions`.
    """
    _, packages, _ = _scan(
        tmp_path, build_iceye_source_root_with_spreadsheet, "iceye", "root"
    )
    assert len(packages) == 1, "stan faktyczny: caly root jako jedna paczka"
    # Dowodem scalenia jest inwentarz: jedna paczka wciaga rastry OBU akwizycji.
    rasters = [
        asset["package_relative_path"]
        for asset in packages[0]["assets"]
        if asset.get("role") == "raster_candidate"
    ]
    assert len(rasters) == 2, rasters
    assert any("1111111" in item for item in rasters) and any("2222222" in item for item in rasters)


@xfail_until_graph_v2("P0.1/P0.3: root z arkuszem musi dac dwie akwizycje, nie jedna paczke")
def test_iceye_source_root_should_yield_two_acquisitions(tmp_path):
    _, packages, _ = _scan(
        tmp_path, build_iceye_source_root_with_spreadsheet, "iceye", "root2"
    )
    assert len(packages) == 2


# --- Capella --------------------------------------------------------------------------


@xfail_until_graph_v2("P0.4: produkt GEO musi byc rozpoznawany automatycznie")
def test_capella_geo_should_resolve_automatically(tmp_path):
    _, packages, _ = _scan(tmp_path, build_capella_geo, "capella", "geo")
    selection = _selection(packages)
    assert selection.get("product_type") == "GEO"
    assert selection.get("status") == "ready"


@defect_baseline
def test_capella_preview_blocks_even_the_supported_gec_product(tmp_path):
    """USTALENIE POZA AUDYTEM: `_preview.tif` psuje takze sciezke GEC, nie tylko GEO.

    Sekcja 4.4 stwierdza, ze resolver rozpoznaje wylacznie `GEC`. To prawda o regexie, ale
    w realnej dostawie obok produktu lezy `<stem>_preview.tif`, ktory rowniez zawiera token
    `GEC` i wpada do tej samej puli kandydatow. `_one_or_decision()` widzi wtedy dwa assety
    o tej samej polaryzacji i konczy `decision_required`. Automatyczny import nie dziala
    wiec nawet dla produktu formalnie wspieranego.
    """
    _, packages, _ = _scan(tmp_path, build_capella_gec, "capella", "gec")
    selection = _selection(packages)
    assert selection.get("status") == "decision_required"
    assert any("preview" in label for label in _alternative_labels(selection)), _alternative_labels(selection)


@xfail_until_graph_v2("P0.4: `_preview.tif` ma byc rola browse, nie kandydatem")
def test_capella_gec_should_resolve_with_preview_present(tmp_path):
    _, packages, _ = _scan(tmp_path, build_capella_gec, "capella", "gec2")
    selection = _selection(packages)
    assert selection.get("product_type") == "GEC"
    assert selection.get("status") == "ready"


@xfail_until_graph_v2("P0.4: folder z dwiema akwizycjami ma dac dwie sceny")
def test_capella_folder_with_two_acquisitions_should_yield_two_packages(tmp_path):
    _, packages, _ = _scan(tmp_path, build_capella_two_acquisitions, "capella", "two")
    assert len(packages) == 2


# --- WorldView ------------------------------------------------------------------------


def test_worldview_mul_pan_is_recognised_with_confirmed_rgb(tmp_path):
    """BASELINE: MUL+PAN dziala i potwierdza mapowanie RGB [5,3,2] z IMD."""
    _, packages, _ = _scan(tmp_path, build_worldview_mul_pan, "worldview", "mulpan")
    selection = _selection(packages)
    assert selection.get("product_type") == "MUL+PAN"
    assert selection.get("status") == "prepare_required"
    assert selection.get("rgb_bands") == [5, 3, 2]
    assert selection.get("multispectral_asset_ids")
    assert selection.get("panchromatic_asset_ids")


@defect_baseline
def test_worldview_pan_only_is_currently_unresolved(tmp_path):
    """BASELINE USTERKI (sekcja 17.7.1): brak galezi PAN-only konczy sie decyzja."""
    _, packages, _ = _scan(tmp_path, build_worldview_pan_only, "worldview", "panonly")
    selection = _selection(packages)
    assert selection.get("product_type") == "UNRESOLVED"
    assert selection.get("status") == "decision_required"


@defect_baseline
def test_worldview_pan_only_alternatives_currently_include_browse_and_layout(tmp_path):
    """BASELINE USTERKI (sekcja 17.7.2): BROWSE.JPG i LAYOUT.JPG trafiaja do wyboru."""
    _, packages, _ = _scan(tmp_path, build_worldview_pan_only, "worldview", "panonly2")
    labels = _alternative_labels(_selection(packages))
    assert any(label.endswith("BROWSE.JPG") for label in labels), labels
    assert any(label.endswith("LAYOUT.JPG") for label in labels), labels


@xfail_until_graph_v2("P0.6: PAN-only ma byc gotowa scena panchromatyczna z 6/6 czesci wg TIL")
def test_worldview_pan_only_should_be_ready_panchromatic_mosaic(tmp_path):
    _, packages, _ = _scan(tmp_path, build_worldview_pan_only, "worldview", "panonly3")
    selection = _selection(packages)
    assert selection.get("status") == "ready"
    assert len(selection.get("asset_ids") or []) == 6


@xfail_until_graph_v2("P0.2/P0.6: browse i layout nie moga byc kandydatami measurement")
def test_worldview_alternatives_should_exclude_browse_and_layout(tmp_path):
    _, packages, _ = _scan(tmp_path, build_worldview_pan_only, "worldview", "panonly4")
    labels = _alternative_labels(_selection(packages))
    assert not any(label.endswith((".JPG", ".jpg")) for label in labels), labels


@defect_baseline
def test_incomplete_extract_is_currently_indistinguishable_from_pan_only(tmp_path):
    """BASELINE USTERKI: niepelna ekstrakcja i poprawny PAN-only daja ten sam status.

    Rozroznia je dzis wylacznie liczba alternatyw, czego UI nie interpretuje. Bramka P0.6
    wymaga jawnego odroznienia, bo to dwie zupelnie rozne sytuacje dla uzytkownika.
    """
    _, full, _ = _scan(tmp_path, build_worldview_pan_only, "worldview", "cmp_full")
    _, partial, _ = _scan(tmp_path, build_worldview_incomplete_extract, "worldview", "cmp_part")
    assert _selection(full).get("status") == _selection(partial).get("status") == "decision_required"


@xfail_until_graph_v2("P0.6: niepelna ekstrakcja musi byc odrozniona od kompletnego PAN-only")
def test_incomplete_extract_should_report_missing_parts(tmp_path):
    _, packages, _ = _scan(tmp_path, build_worldview_incomplete_extract, "worldview", "part2")
    selection = _selection(packages)
    assert selection.get("completeness") == "partial"


@defect_baseline
def test_readme_only_directory_is_currently_treated_as_worldview_package(tmp_path):
    """BASELINE USTERKI (sekcja 17.2.2): sam plik README czyni z katalogu root paczki WV."""
    _, packages, _ = _scan(tmp_path, build_readme_only_directory, "worldview", "readme")
    # Sygnatura zadziala tylko wtedy, gdy README lezy BEZPOSREDNIO w skanowanym katalogu.
    assert len(packages) == 1
    assert packages[0].get("package_root_relative") in {".", ""}, packages[0]


@xfail_until_graph_v2("P0.1: sygnatura dostawcy nie moze opierac sie na nazwie generycznej (README)")
def test_readme_only_directory_should_not_look_like_a_worldview_package(tmp_path):
    _, packages, _ = _scan(tmp_path, build_readme_only_directory, "worldview", "readme2")
    assert not packages or packages[0].get("package_root_relative") not in {".", ""}


# --- Airbus ---------------------------------------------------------------------------


@defect_baseline
def test_pleiades_resolver_requires_rgb_token_in_filename(tmp_path):
    """BASELINE: stara sciezka wymaga tokenu `_RGB`, ktorego dostawa PHR nie ma.

    Wczesniejsza wersja tego testu opatrzona byla zastrzezeniem, ze nazewnictwo w fixture
    jest ZALOZENIEM, bo nie dysponowalismy potwierdzona natywna paczka. Zastrzezenie zostalo
    zdjete: fixture odwzorowuja teraz zweryfikowane nazwy z rzeczywistych dostaw PNEO i PHR,
    a rozstrzygniecie — ze byla to usterka produktu, a nie niereprezentatywny fixture —
    nalezy do P0.5 i jest zamkniete w `test_airbus_package_resolver.py`.
    """
    _, packages, _ = _scan(tmp_path, build_pleiades_phr_dimap, "pleiades_neo", "phr")
    selection = _selection(packages)
    assert selection.get("status") == "decision_required"
    assert selection.get("product_type") == "UNRESOLVED"


@xfail_until_graph_v2("P0.5: dostawa PHR musi zostac rozpoznana jako produkt PMS")
def test_phr_delivery_should_be_recognised(tmp_path):
    _, packages, _ = _scan(tmp_path, build_pleiades_phr_dimap, "pleiades_neo", "phr2")
    selection = _selection(packages)
    assert selection.get("status") == "ready"
    assert selection.get("product_type") == "PMS"


# --- Wlasciwosci wspolne --------------------------------------------------------------


def test_scan_is_read_only_for_every_fixture(tmp_path):
    """Skan nie moze modyfikowac zrodla — inwariant 3.1 roadmapy.

    Porownujemy pelny snapshot (sciezka, rozmiar, mtime_ns) przed i po skanie kazdego
    fixture. To ta sama wlasnosc, ktorej pilnuje audyt rzeczywistych danych w B0b.
    """
    builders = [
        (build_iceye_legacy_grd, "iceye"),
        (build_capella_geo, "capella"),
        (build_worldview_mul_pan, "worldview"),
        (build_worldview_pan_only, "worldview"),
        (build_pleiades_neo_dimap, "pleiades_neo"),
    ]
    for index, (builder, provider) in enumerate(builders):
        root = tmp_path / f"ro_{index}"
        root.mkdir(parents=True)
        builder(root)

        def snapshot() -> dict[str, tuple[int, int]]:
            return {
                str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
                for path in sorted(root.rglob("*"))
                if path.is_file()
            }

        before = snapshot()
        scan_source(root, provider)
        assert snapshot() == before, f"skan zmodyfikowal zrodlo dla {provider}"
