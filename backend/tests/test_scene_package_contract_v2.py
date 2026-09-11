"""Kontrakt grafu v2 i role assetow (DESIGN_DECISIONS.md, scene-import P0.1 + P0.2).

Podzial testow odpowiada podzialowi wdrozenia:

1. **Identyfikatory** — czyste funkcje UID, testowane bez systemu plikow. Wlasnosci, ktorych
   pilnujemy, to stabilnosc, niezaleznosc od kolejnosci pasm i obecnosc `source_id`
   w `decision_uid`.
2. **Role** — klasyfikacja assetu na podstawie sciezki. Kazdy przypadek pochodzi z sekcji 4
   roadmapy, wiec regresja tutaj oznacza powrot konkretnego bledu importu.
3. **Integracja za flaga** — te same fixture, co w korpusie B0, przepuszczone przez resolver
   przy `GEOTILE_SCENE_PACKAGE_GRAPH_V2=1`. Fixture deklaruja juz `measurement`
   i `non_measurement`, wiec oczekiwania nie sa tu powtarzane recznie.

Test 3 ma swiadomie DWIE strony: sprawdzamy nie tylko, ze pod flaga podglady znikaja, ale tez
ze przy fladze WYLACZONEJ nadal przeciekaja. Bez tej drugiej strony nie wiedzielibysmy, czy
test w ogole cokolwiek mierzy — a flaga domyslnie jest wylaczona, wiec to wlasnie stare
zachowanie obowiazuje w produkcji do czasu przepiecia.

Uruchomienie: pytest backend/tests/test_scene_package_contract_v2.py
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
    build_capella_geo,
    build_capella_two_acquisitions,
    build_iceye_legacy_grd,
    build_worldview_mul_pan,
    build_worldview_pan_only,
)
from services.scene_packages import contracts  # noqa: E402
from services.scene_packages.contracts import (  # noqa: E402
    ASSET_ROLES,
    CONTRACT_VERSION,
    FLAG_GRAPH_V2,
    AssetRef,
    PackageDiagnostic,
    ProductSelection,
    acquisition_uid,
    decision_uid,
    delivery_uid,
    graph_v2_enabled,
    product_uid,
)
from services.scene_packages.roles import (  # noqa: E402
    ROLE_BROWSE,
    ROLE_DELIVERY_METADATA,
    ROLE_LAYOUT,
    ROLE_MEASUREMENT,
    ROLE_PRODUCT_METADATA,
    ROLE_RPC,
    ROLE_TILE_MANIFEST,
    classify_asset_role,
)
from services.scene_packages.resolvers import scan_source  # noqa: E402


# --- 1. Identyfikatory ----------------------------------------------------------------


def test_uids_are_stable_and_prefixed():
    """Ten sam wejsciowy opis daje ten sam UID, a prefiks mowi, czego dotyczy."""
    first = delivery_uid("worldview", "014670314010")
    assert first == delivery_uid("worldview", "014670314010")
    assert first.startswith("dlv_")
    assert acquisition_uid(first, "A1").startswith("acq_")
    assert product_uid("acq_x", "MUL").startswith("prd_")
    assert decision_uid("src", "prd_x").startswith("dec_")


def test_uids_ignore_case_and_surrounding_whitespace():
    """Windows nie rozroznia wielkosci liter w sciezkach; UID nie moze sie od niej zmieniac."""
    assert delivery_uid("WorldView", " 014670314010 ") == delivery_uid("worldview", "014670314010")


def test_product_uid_is_independent_of_band_order():
    """Kolejnosc pasm w dostawie bywa przypadkowa i nie moze rozszczepiac tozsamosci produktu."""
    assert product_uid("acq", "MUL", ["R", "G", "B", "N"]) == product_uid("acq", "MUL", ["N", "B", "G", "R"])


def test_product_uid_separates_components_of_one_acquisition():
    """MUL i PAN tej samej akwizycji to dwa rozne produkty (sekcja 4.6)."""
    acquisition = acquisition_uid(delivery_uid("worldview", "D1"), "A1")
    assert product_uid(acquisition, "MUL", ["R", "G", "B", "N"]) != product_uid(acquisition, "PAN", ["P"])


def test_acquisition_uid_separates_two_acquisitions_in_one_folder():
    """Sekcja 4.4: jeden folder Capelli miesci dwie akwizycje — musza miec rozne UID."""
    delivery = delivery_uid("capella", "CAPELLA_04_06_2024")
    first = acquisition_uid(delivery, "C08", "2024-06-04T10:15:00Z")
    second = acquisition_uid(delivery, "C09", "2024-06-04T13:45:00Z")
    assert first != second


def test_decision_uid_depends_on_the_source():
    """Dwa zrodla o identycznej strukturze nie moga dzielic decyzji uzytkownika."""
    product = product_uid("acq", "MUL")
    assert decision_uid("E:/dostawa_a", product) != decision_uid("E:/dostawa_b", product)


def test_role_vocabulary_is_closed_and_measurement_is_the_only_selectable():
    assert len(set(ASSET_ROLES)) == len(ASSET_ROLES)
    assert contracts.SELECTABLE_ROLES == {ROLE_MEASUREMENT}
    assert AssetRef("a", "x.tif", ROLE_MEASUREMENT).is_selectable
    assert not AssetRef("b", "BROWSE.JPG", ROLE_BROWSE).is_selectable


@pytest.mark.parametrize("value,expected", [("1", True), ("true", True), ("ON", True), ("0", False), ("", False)])
def test_flag_parsing(monkeypatch, value, expected):
    monkeypatch.setenv(FLAG_GRAPH_V2, value)
    assert graph_v2_enabled() is expected


def test_flag_is_off_when_unset(monkeypatch):
    """Domyslnie kontrakt v2 jest WYLACZONY — wdrozenie jest odwracalne."""
    monkeypatch.delenv(FLAG_GRAPH_V2, raising=False)
    assert graph_v2_enabled() is False


def test_selection_serializes_with_its_contract_version():
    """Odbiorca (API/UI) musi widziec wersje kontraktu, zeby nie zgadywac ksztaltu danych."""
    selection = ProductSelection(
        decision_uid="dec_1",
        product_uid="prd_1",
        product_type="MUL",
        status="ready",
        measurement_asset_ids=("a1",),
        source_components={"multispectral": ("a1",)},
        diagnostics=(PackageDiagnostic("partial_delivery", "brak czesci", blocking=True),),
    )
    payload = selection.as_dict()
    assert payload["contract_version"] == CONTRACT_VERSION
    assert payload["source_components"] == {"multispectral": ["a1"]}
    assert payload["diagnostics"][0]["blocking"] is True


def test_blocking_diagnostic_marks_the_product_as_blocked():
    from services.scene_packages.contracts import ProductCandidate

    warned = ProductCandidate("prd", "MUL", diagnostics=(PackageDiagnostic("x", "y"),))
    blocked = ProductCandidate("prd", "MUL", diagnostics=(PackageDiagnostic("x", "y", blocking=True),))
    assert not warned.is_blocked
    assert blocked.is_blocked


# --- 2. Role assetow ------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative_path,expected",
    [
        # Podglady, ktore dzis trafiaja do puli kandydatow (sekcje 4.4 i 4.6).
        ("014670314010_01/BROWSE.JPG", ROLE_BROWSE),
        ("014670314010_01/LAYOUT.JPG", ROLE_LAYOUT),
        ("CAPELLA_C08_SP_GEO_HH_20240604101500_preview.tif", ROLE_BROWSE),
        ("ICEYE_GRD_SLH_1234567_20240604T101500_QUICKLOOK.png", ROLE_BROWSE),
        # Sidecary o jednoznacznej roli.
        ("18APR08_MUL.TIL", ROLE_TILE_MANIFEST),
        ("18APR08_MUL.IMD", ROLE_PRODUCT_METADATA),
        ("18APR08_MUL.RPB", ROLE_RPC),
        ("DeliveryMetadata.xml", ROLE_DELIVERY_METADATA),
        ("README.TXT", ROLE_DELIVERY_METADATA),
        # Dane pomiarowe — w tym PAN, ktorego stara heurystyka nazw bledna wykluczala.
        ("014679500010_01/_PAN/18APR08_PAN_R1C1.TIF", ROLE_MEASUREMENT),
        ("014670314010_01/_MUL/18APR08_MUL_R1C1.TIF", ROLE_MEASUREMENT),
        ("IMG_PHR1A_202401151030_PMS_R1C1.JP2", ROLE_MEASUREMENT),
    ],
)
def test_asset_roles_cover_the_cases_from_the_roadmap(relative_path, expected):
    assert classify_asset_role(relative_path) == expected


def test_role_classification_ignores_path_separator_and_case():
    assert classify_asset_role("A\\B\\browse.jpg") == classify_asset_role("A/B/BROWSE.JPG")


def test_panchromatic_measurement_is_not_treated_as_a_preview():
    """Regresja wprost z ustalenia 17.7.2: `pan` w nazwie nie moze dyskwalifikowac assetu."""
    assert classify_asset_role("_PAN/18APR08_PAN_R1C1.TIF") == ROLE_MEASUREMENT


# --- 3. Integracja za flaga -----------------------------------------------------------


def _measurement_paths(tmp_path: pathlib.Path, builder, provider: str, name: str) -> tuple:
    workspace = tmp_path / name
    workspace.mkdir(parents=True)
    package = builder(workspace)
    packages, _diagnostics = scan_source(package.root, provider)
    # Klucz to `relative_path` (wzgledem SKANOWANEGO zrodla), nie `package_relative_path`.
    # Ten drugi jest liczony wzgledem korzenia PACZKI, a ten przesuwa sie pod flaga: dostawa
    # WorldView przestaje byc paczka w korzeniu i schodzi do podkatalogu produktu.
    paths = {
        asset["relative_path"]
        for entry in packages
        for asset in entry.get("assets", [])
        if asset.get("asset_role") == ROLE_MEASUREMENT
    }
    return package, packages, paths


_INTEGRATION_CASES = [
    ("iceye_legacy", build_iceye_legacy_grd, "iceye"),
    ("capella_geo", build_capella_geo, "capella"),
    ("capella_two", build_capella_two_acquisitions, "capella"),
    ("worldview_mul_pan", build_worldview_mul_pan, "worldview"),
    ("worldview_pan_only", build_worldview_pan_only, "worldview"),
]


@pytest.mark.parametrize("name,builder,provider", _INTEGRATION_CASES)
def test_inventory_carries_asset_roles_for_every_provider(tmp_path, name, builder, provider):
    """Role sa DODATKOWE: pojawiaja sie w inwentarzu niezaleznie od flagi."""
    package, packages, measurements = _measurement_paths(tmp_path, builder, provider, name)
    assert packages, f"resolver nie zwrocil pakietu dla {name}"
    for expected in package.measurement:
        assert expected in measurements, f"{expected} powinien byc measurement w {name}"
    for excluded in package.non_measurement:
        assert excluded not in measurements, f"{excluded} nie moze byc measurement w {name}"


@pytest.mark.parametrize("name,builder,provider", _INTEGRATION_CASES)
def test_graph_v2_keeps_previews_out_of_the_labeling_candidates(tmp_path, monkeypatch, name, builder, provider):
    """Pod flaga zaden asset nie-pomiarowy nie moze trafic do wyboru obrazu."""
    monkeypatch.setenv(FLAG_GRAPH_V2, "1")
    package, packages, _ = _measurement_paths(tmp_path, builder, provider, name)

    for entry in packages:
        by_id = {asset["asset_id"]: asset for asset in entry.get("assets", [])}
        selection = entry.get("selection") or {}
        chosen = [by_id[aid] for aid in selection.get("asset_ids", []) if aid in by_id]
        alternatives = [
            by_id[aid]
            for alternative in (selection.get("alternatives") or [])
            for aid in (alternative.get("asset_ids") or [])
            if aid in by_id
        ]
        for asset in chosen + alternatives:
            assert asset.get("asset_role") == ROLE_MEASUREMENT, (
                f"{name}: {asset.get('package_relative_path')} nie jest measurement, "
                f"a trafil do wyboru (rola: {asset.get('asset_role')})"
            )
        for asset in chosen:
            assert asset["relative_path"] not in package.non_measurement


def test_without_the_flag_the_preview_still_leaks_into_the_candidates(tmp_path, monkeypatch):
    """Druga strona testu: bez flagi obowiazuje STARE zachowanie.

    Gdyby ten test zaczal padac, znaczyloby to, ze zmiana wyciekla poza flage — czyli
    dokladnie to, przed czym flaga ma chronic. Uzywamy Capelli, bo jej `_preview.tif`
    jest rastrem i w modelu opartym na rozszerzeniu jest nieodrozninalny od produktu.
    """
    monkeypatch.delenv(FLAG_GRAPH_V2, raising=False)
    workspace = tmp_path / "capella_off"
    workspace.mkdir()
    package = build_capella_geo(workspace)
    packages, _ = scan_source(package.root, "capella")

    labels = {
        asset["relative_path"]
        for entry in packages
        for asset in entry.get("assets", [])
        if asset.get("role") == "raster_candidate"
    }
    assert package.non_measurement[0] in labels, (
        "bez flagi podglad powinien nadal byc kandydatem — inaczej zmiana ominela flage"
    )
