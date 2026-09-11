"""Hierarchiczny podglad importu (DESIGN_DECISIONS.md, scene-import P2.1).

Zakres zostal SWIADOMIE przyciety: sekcja 2.1 wymienia dziesiec grup informacji, a wiersz
podgladu pokazuje z nich cztery. Testy pilnuja jednego i drugiego — i tego, co jest w wierszu,
i tego, czego w nim celowo NIE MA:

- hierarchia grupuje produkty tej samej akwizycji → `test_products_of_one_acquisition_*`,
- archiwum nalezy do dostawy, nie tworzy wlasnej → `test_archive_belongs_to_*`,
- `6/6` nie trafia do wiersza, `4/6` juz tak → `test_complete_delivery_is_not_flagged`,
- szacunek przygotowania tylko dla produktow pochodnych → `test_preparation_estimate_*`,
- powod automatycznego wyboru jest zawsze podany → `test_selection_reason_*`.

Uruchomienie: pytest backend/tests/test_preview_tree.py
"""

from __future__ import annotations

import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.scene_packages import preview_tree  # noqa: E402


def _asset(asset_id: str, path: str, role: str = "measurement", size: int = 1024) -> dict:
    return {
        "asset_id": asset_id,
        "relative_path": path,
        "package_relative_path": path.split("/")[-1],
        "role": "raster_candidate" if role == "measurement" else "metadata",
        "asset_role": role,
        "size": size,
    }


def _iceye(polarization: str, task: str = "1234567") -> dict:
    stem = f"ICEYE_X7_GRD_SM_{task}_20240604T101500_{polarization}"
    return {
        "source_id": "src",
        "provider": "iceye",
        "package_id": f"pkg-{polarization}",
        "package_root_relative": f"DOSTAWA_{task}",
        "assets": [
            _asset(f"a-{polarization}", f"DOSTAWA_{task}/{stem}.tif"),
            _asset(f"m-{polarization}", f"DOSTAWA_{task}/{stem}.xml", role="product_metadata"),
        ],
        "selection": {
            "asset_ids": [f"a-{polarization}"],
            "identity_asset_ids": [f"a-{polarization}"],
            "status": "ready",
            "product_type": "GRD",
            "raster_kind": "direct",
            "diagnostics": {"warnings": [], "errors": [], "metadata_conflicts": []},
        },
    }


def _worldview_mul_pan(*, declared_parts: int = 4, present: int = 4) -> dict:
    parts = [f"a{index}" for index in range(1, present + 1)]
    assets = [
        _asset(part, f"014670314010_01_003/_PAN/R1C{index}.TIF", size=400 * 1024 * 1024)
        for index, part in enumerate(parts, start=1)
    ]
    return {
        "source_id": "src",
        "provider": "worldview",
        "package_id": "pkg-mulpan",
        "package_root_relative": "014670314010_01_003",
        "assets": assets,
        "selection": {
            "asset_ids": parts,
            "identity_asset_ids": parts,
            "status": "prepare_required",
            "product_type": "MUL+PAN",
            "raster_kind": "derived",
            "declared_parts": declared_parts,
            "completeness": "complete" if present == declared_parts else "partial",
            "missing_parts": [] if present == declared_parts else ["R2C2.TIF"],
            "multispectral_asset_ids": parts[:2],
            "panchromatic_asset_ids": parts[2:],
            "source_components": {"multispectral": parts[:2], "panchromatic": parts[2:]},
            "rgb_bands": [5, 3, 2],
            "diagnostics": {"warnings": [], "errors": [], "metadata_conflicts": []},
        },
    }


def _archive(name: str, root_relative: str, *, delivery_root: str, status: str = "archive_duplicate") -> dict:
    return {
        "source_id": "src",
        "provider": "worldview",
        "package_id": f"zip-{name}",
        "package_kind": "archive",
        "package_root_relative": root_relative,
        "assets": [_asset(f"z-{name}", root_relative, role="archive")],
        "selection": {
            "asset_ids": [],
            "status": status,
            "product_type": "ARCHIVE",
            "raster_kind": "archive",
            "archive": {
                "delivery_root": delivery_root,
                "extracted_present": 42,
                "extracted_total": 42,
                "compressed_bytes": 1024,
            },
            "diagnostics": {"warnings": [], "errors": [], "metadata_conflicts": []},
        },
    }


SOURCES = [{"source_id": "src", "provider": "iceye", "root_path": "//share/DANE"}]


def _first_delivery(tree: dict) -> dict:
    return tree["sources"][0]["deliveries"][0]


# --- hierarchia -------------------------------------------------------------------------


def test_products_of_one_acquisition_are_grouped_together():
    """Dwie polaryzacje tego samego przelotu to JEDNA akwizycja, nie dwa niezalezne wiersze."""
    tree = preview_tree.build([_iceye("VV"), _iceye("VH")], SOURCES)
    delivery = _first_delivery(tree)
    assert len(delivery["acquisitions"]) == 1
    labels = sorted(product["label"] for product in delivery["acquisitions"][0]["products"])
    assert labels == ["GRD VH", "GRD VV"]


def test_different_acquisitions_are_separate_nodes():
    tree = preview_tree.build([_iceye("VV", task="1234567"), _iceye("VV", task="7654321")], SOURCES)
    acquisitions = [
        acquisition
        for delivery in tree["sources"][0]["deliveries"]
        for acquisition in delivery["acquisitions"]
    ]
    assert len(acquisitions) == 2


def test_provider_without_a_grammar_falls_back_to_the_package_identity():
    generic = {
        "source_id": "src",
        "provider": "generic",
        "package_id": "pkg-generic",
        "package_root_relative": "LOC/scena.tif",
        "assets": [_asset("a1", "LOC/scena.tif")],
        "selection": {"asset_ids": ["a1"], "status": "ready", "product_type": "IMAGE"},
    }
    tree = preview_tree.build([generic], SOURCES)
    acquisition = _first_delivery(tree)["acquisitions"][0]
    assert acquisition["products"][0]["label"] == "IMAGE"
    assert acquisition["acquisition_id"]


# --- archiwa ---------------------------------------------------------------------------


def test_archive_belongs_to_its_delivery_not_to_a_new_one():
    """ZIP w korzeniu zrodla wskazuje dostawe katalogiem z wnetrza archiwum."""
    packages = [
        _worldview_mul_pan(),
        _archive("wv", "EPWAv2_014670314010_0.zip", delivery_root="014670314010_01_003"),
    ]
    tree = preview_tree.build(packages, SOURCES)
    deliveries = tree["sources"][0]["deliveries"]
    assert len(deliveries) == 1, "archiwum nie moze tworzyc wlasnej dostawy"
    assert len(deliveries[0]["archives"]) == 1
    assert deliveries[0]["archives"][0]["name"] == "EPWAv2_014670314010_0.zip"


def test_archive_inside_the_delivery_directory_is_attached_to_it():
    """Konwencja Airbusa: ZIP lezy w katalogu dostawy."""
    product = _worldview_mul_pan()
    packages = [
        product,
        _archive("inside", "014670314010_01_003/delivery.zip", delivery_root="IMG_001"),
    ]
    tree = preview_tree.build(packages, SOURCES)
    deliveries = tree["sources"][0]["deliveries"]
    assert len(deliveries) == 1
    assert len(deliveries[0]["archives"]) == 1


def test_archive_without_a_delivery_stays_visible_as_its_own_node():
    """Dostawa istniejaca WYLACZNIE w archiwum nie moze zniknac z podgladu."""
    packages = [_archive("only", "196_pulk/pneo.zip", delivery_root="STD_A", status="archive_only")]
    tree = preview_tree.build(packages, SOURCES)
    delivery = _first_delivery(tree)
    assert delivery["acquisitions"] == []
    assert delivery["archives"][0]["status"] == "archive_only"


def test_archive_is_never_a_product():
    packages = [_worldview_mul_pan(), _archive("wv", "x.zip", delivery_root="014670314010_01_003")]
    tree = preview_tree.build(packages, SOURCES)
    products = [
        product
        for delivery in tree["sources"][0]["deliveries"]
        for acquisition in delivery["acquisitions"]
        for product in acquisition["products"]
    ]
    assert [product["package_kind"] for product in products] == ["delivery"]


# --- co jest w wierszu, a czego celowo nie ma -------------------------------------------


def test_complete_delivery_is_not_flagged_as_incomplete():
    """`6/6` nie wymaga uwagi i nie ma byc podswietlane."""
    tree = preview_tree.build([_worldview_mul_pan()], SOURCES)
    product = _first_delivery(tree)["acquisitions"][0]["products"][0]
    assert product["incomplete"] is False
    assert product["detail"]["missing_parts_total"] == 0


def test_incomplete_delivery_is_flagged_with_the_missing_parts():
    tree = preview_tree.build([_worldview_mul_pan(declared_parts=6, present=4)], SOURCES)
    product = _first_delivery(tree)["acquisitions"][0]["products"][0]
    assert product["incomplete"] is True
    assert product["detail"]["missing_parts"] == ["R2C2.TIF"]
    assert product["detail"]["parts_source"] == "tile_manifest"


def test_row_carries_counts_not_diagnostic_messages():
    """Wiersz ma licznik; tresc ostrzezen czeka w rozwinieciu."""
    package = _iceye("VV")
    package["selection"]["diagnostics"]["warnings"] = [
        {"code": "metadata_not_bound", "message": "1 sidecar excluded"}
    ]
    tree = preview_tree.build([package], SOURCES)
    product = _first_delivery(tree)["acquisitions"][0]["products"][0]
    assert product["warning_count"] == 1
    assert "message" not in product
    assert product["detail"]["diagnostics"]["warnings"][0]["code"] == "metadata_not_bound"


def test_component_products_are_labelled_by_component():
    tree = preview_tree.build([_worldview_mul_pan()], SOURCES)
    product = _first_delivery(tree)["acquisitions"][0]["products"][0]
    assert product["label"] == "MUL 2 + PAN 2"


def test_asset_groups_collapse_roles_that_do_not_change_a_decision():
    package = _iceye("VV")
    package["assets"].append(_asset("b1", "DOSTAWA_1234567/quicklook.png", role="browse"))
    package["assets"].append(_asset("f1", "DOSTAWA_1234567/footprint.kml", role="footprint"))
    tree = preview_tree.build([package], SOURCES)
    assets = _first_delivery(tree)["acquisitions"][0]["products"][0]["detail"]["assets"]
    assert assets == {"measurement": 1, "metadata": 1, "browse": 1, "auxiliary": 1}


def test_legacy_inventory_without_contract_roles_still_counts_measurement():
    """Inwentarz z cache sprzed P0.2 nie moze wygladac na dostawe bez ani jednego rastra."""
    package = _iceye("VV")
    for asset in package["assets"]:
        asset.pop("asset_role")
    tree = preview_tree.build([package], SOURCES)
    assets = _first_delivery(tree)["acquisitions"][0]["products"][0]["detail"]["assets"]
    assert assets["measurement"] == 1 and assets["metadata"] == 1


# --- powod wyboru i koszt przygotowania -------------------------------------------------


def test_selection_reason_is_always_present():
    tree = preview_tree.build([_iceye("VV")], SOURCES)
    reason = _first_delivery(tree)["acquisitions"][0]["products"][0]["detail"]["selection_reason"]
    assert reason["code"] == "only_candidate" and reason["message"]


def test_selection_reason_names_the_pending_decision():
    package = _iceye("VV")
    package["selection"]["status"] = "decision_required"
    package["selection"]["alternatives"] = [{"label": "GEO", "asset_ids": ["a-VV"]}]
    tree = preview_tree.build([package], SOURCES)
    reason = _first_delivery(tree)["acquisitions"][0]["products"][0]["detail"]["selection_reason"]
    assert reason["code"] == "needs_decision"


def test_user_choice_is_reported_as_such():
    package = _iceye("VV")
    package["selection"]["selected_by"] = "user"
    tree = preview_tree.build([package], SOURCES)
    reason = _first_delivery(tree)["acquisitions"][0]["products"][0]["detail"]["selection_reason"]
    assert reason["code"] == "user_choice"


def test_manual_file_choice_is_distinguished_from_a_product_variant():
    """`user_override` mowi, ze wybor nie odpowiada zadnemu wariantowi resolvera.

    Bez tego rozroznienia reczna lista plikow i wybor jednej z podanych alternatyw zapisywaly
    sie identycznie, wiec przy pozniejszej diagnozie nie dalo sie ich odroznic.
    """
    package = _iceye("VV")
    package["selection"]["selected_by"] = "user_override"
    tree = preview_tree.build([package], SOURCES)
    reason = _first_delivery(tree)["acquisitions"][0]["products"][0]["detail"]["selection_reason"]
    assert reason["code"] == "user_override"


def test_preparation_estimate_is_given_only_for_derived_products():
    tree = preview_tree.build([_iceye("VV"), _worldview_mul_pan()], SOURCES, free_bytes=10 * 1024 ** 3)
    products = {
        product["product_type"]: product
        for delivery in tree["sources"][0]["deliveries"]
        for acquisition in delivery["acquisitions"]
        for product in acquisition["products"]
    }
    assert products["GRD"]["detail"]["preparation"] is None
    preparation = products["MUL+PAN"]["detail"]["preparation"]
    # Dwa pliki PAN po 400 MiB, trzy pasma na wyjsciu.
    assert preparation["estimated_output_bytes"] == 2 * 400 * 1024 * 1024 * 3
    assert preparation["fits"] is True


def test_preparation_estimate_reports_when_space_is_short():
    tree = preview_tree.build([_worldview_mul_pan()], SOURCES, free_bytes=1024)
    preparation = _first_delivery(tree)["acquisitions"][0]["products"][0]["detail"]["preparation"]
    assert preparation["fits"] is False


def test_unknown_free_space_is_not_reported_as_a_failure():
    tree = preview_tree.build([_worldview_mul_pan()], SOURCES, free_bytes=None)
    preparation = _first_delivery(tree)["acquisitions"][0]["products"][0]["detail"]["preparation"]
    assert preparation["fits"] is None
