"""Kontrakty wiazania metadanych z produktem (DESIGN_DECISIONS.md, scene-import B0).

Uzupelnia `test_provider_package_contracts.py`: tamten modul pilnuje ROZPOZNANIA dostawy,
ten — poprawnosci przypisania rol assetom i metadanych do konkretnego produktu.

Ta sama konwencja: testy bez markera utrwalaja stan faktyczny (baseline), testy z
`xfail(strict=True)` opisuja zamiar z bramek P0.2, P0.5 i P0.6. `strict=True` wymusza zdjecie
markera w chwili naprawy.

Uruchomienie: pytest backend/tests/test_provider_metadata_binding.py
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
    build_pleiades_phr_dimap,
    build_worldview_mul_pan,
    build_worldview_pan_only,
)
from services.scene_manifest import infer_sensor  # noqa: E402
from services.scene_packages.contracts import graph_v2_enabled  # noqa: E402
from services.scene_packages.resolvers import scan_source  # noqa: E402


GRAPH_V2 = graph_v2_enabled()
defect_baseline = pytest.mark.skipif(
    GRAPH_V2, reason="baseline usterki naprawionej przez GEOTILE_SCENE_PACKAGE_GRAPH_V2"
)


def xfail_until_graph_v2(reason: str):
    return pytest.mark.xfail(condition=not GRAPH_V2, strict=True, reason=reason)


def _scan(tmp_path: pathlib.Path, builder, provider: str, name: str):
    workspace = tmp_path / name
    workspace.mkdir(parents=True)
    package = builder(workspace)
    packages, _ = scan_source(package.root, provider)
    assert packages, "resolver nie zwrocil zadnego pakietu"
    return package, packages[0]


def _assets_by_role(package: dict) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for asset in package.get("assets") or []:
        grouped.setdefault(str(asset.get("role")), []).append(
            str(asset.get("package_relative_path"))
        )
    return grouped


def _assets_by_asset_role(package: dict) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for asset in package.get("assets") or []:
        grouped.setdefault(str(asset.get("asset_role")), []).append(
            str(asset.get("package_relative_path"))
        )
    return grouped


# --- Role assetow (P0.2) --------------------------------------------------------------


@defect_baseline
def test_browse_and_layout_are_currently_measurement_candidates(tmp_path):
    """BASELINE USTERKI (sekcja 17.7.2, krok 1 lancucha przyczynowego).

    `RASTER_EXTENSIONS` zawiera `.jpg`, wiec `build_inventory()` nadaje `BROWSE.JPG`
    i `LAYOUT.JPG` role `raster_candidate` — te sama, co prawdziwym czesciom rastra.
    To jest zrodlo, z ktorego biora sie one pozniej w alternatywach wyboru.
    """
    _, package = _scan(tmp_path, build_worldview_pan_only, "worldview", "roles")
    candidates = _assets_by_role(package).get("raster_candidate", [])
    assert any(item.endswith("BROWSE.JPG") for item in candidates), candidates
    assert any(item.endswith("LAYOUT.JPG") for item in candidates), candidates


def test_browse_and_layout_should_have_dedicated_roles(tmp_path):
    _, package = _scan(tmp_path, build_worldview_pan_only, "worldview", "roles2")
    grouped = _assets_by_asset_role(package)
    assert "browse" in grouped or "layout" in grouped, grouped


@defect_baseline
def test_capella_preview_is_currently_a_measurement_candidate(tmp_path):
    """BASELINE USTERKI: `_preview.tif` ma te sama role co wlasciwy produkt."""
    _, package = _scan(tmp_path, build_capella_geo, "capella", "prev")
    candidates = _assets_by_role(package).get("raster_candidate", [])
    assert any(item.endswith("_preview.tif") for item in candidates), candidates


def test_capella_preview_should_not_be_a_measurement_candidate(tmp_path):
    _, package = _scan(tmp_path, build_capella_geo, "capella", "prev2")
    measurements = _assets_by_asset_role(package).get("measurement", [])
    assert not any(item.endswith("_preview.tif") for item in measurements), measurements


# --- Wiazanie metadanych do komponentu produktu (P0.2, P0.6) --------------------------


def test_worldview_components_share_one_flat_metadata_pool(tmp_path):
    """BASELINE USTERKI (sekcja 4.6): metadata MUL i PAN leza w jednej plaskiej puli.

    Obie sciezki `.IMD` maja te sama role `metadata` i nic nie wiaze ich z komponentem.
    To jest przyczyna szesciu pozornych konfliktow (`band_id`, GSD, czas z dokladnoscia
    do mikrosekund) opisanych w sekcji 4.6 — parser scala je jako jeden obiekt.
    """
    _, package = _scan(tmp_path, build_worldview_mul_pan, "worldview", "meta")
    metadata = _assets_by_role(package).get("metadata", [])
    assert any(item.endswith("_MUL/14JUN11_MUL.IMD") for item in metadata), metadata
    assert any(item.endswith("_PAN/14JUN11_PAN.IMD") for item in metadata), metadata


@defect_baseline
def test_worldview_selection_currently_has_no_per_component_metadata(tmp_path):
    """BASELINE USTERKI: selekcja rozdziela RASTRY komponentow, ale nie ich metadata.

    `multispectral_asset_ids` i `panchromatic_asset_ids` istnieja, wiec podzial komponentow
    jest juz czesciowo obecny. Brakuje odpowiednika dla metadanych, przez co
    `_package_metadata_files()` nie ma czego uzyc i scala wszystko.
    """
    _, package = _scan(tmp_path, build_worldview_mul_pan, "worldview", "meta2")
    selection = package.get("selection") or {}
    assert selection.get("multispectral_asset_ids"), "podzial rastrow komponentow istnieje"
    assert selection.get("panchromatic_asset_ids")
    assert "metadata_asset_ids" not in selection, selection.keys()


@xfail_until_graph_v2("P0.2: selekcja ma zwracac metadata per komponent (`source_components`)")
def test_worldview_selection_should_bind_metadata_per_component(tmp_path):
    _, package = _scan(tmp_path, build_worldview_mul_pan, "worldview", "meta3")
    selection = package.get("selection") or {}
    assert selection.get("metadata_asset_ids") or selection.get("source_components")


@defect_baseline
def test_worldview_til_is_present_but_not_used_for_part_ordering(tmp_path):
    """BASELINE: TIL jest w inwentarzu jako metadata, ale kolejnosc czesci z niego nie wynika.

    Bramka P0.6 wymaga, zeby lista i kolejnosc czesci pochodzily z TIL, a nie z sortowania
    nazw. Dzis TIL jest tylko kolejnym plikiem metadata — test utrwala ten stan.
    """
    _, package = _scan(tmp_path, build_worldview_pan_only, "worldview", "til")
    metadata = _assets_by_role(package).get("metadata", [])
    assert any(item.endswith(".TIL") for item in metadata), metadata
    selection = package.get("selection") or {}
    assert "declared_parts" not in selection and "completeness" not in selection, selection.keys()


# --- Dostawca zadeklarowany kontra wykryty (P0.5, sekcja 3.4) -------------------------


def test_sensor_inference_distinguishes_phr_from_pleiades_neo():
    """BASELINE POPRAWNY: detekcja z nazwy odroznia PHR1A od PNEO3.

    Istotne dla P0.5: problem NIE lezy w rozpoznawaniu sensora z nazwy, tylko w tym, ze
    zadeklarowany przez uzytkownika dostawca nie jest konfrontowany z wynikiem detekcji.
    """
    assert infer_sensor("DIM_PHR1A_202401151030_PMS.XML", []) == "Pleiades"
    assert infer_sensor("IMG_PNEO3_202403011030_PMS-FS_R1C1.JP2", []) == "Pleiades Neo"


@pytest.mark.xfail(
    strict=True,
    reason="P0.5/3.4: niezgodnosc declared vs detected musi byc zapisana w pakiecie",
)
def test_phr_delivery_declared_as_pneo_should_record_provider_mismatch(tmp_path):
    """Dostawa PHR zadeklarowana jako Pleiades Neo ma zglosic niezgodnosc, nie milczec."""
    _, package = _scan(tmp_path, build_pleiades_phr_dimap, "pleiades_neo", "phr")
    diagnostics = (package.get("selection") or {}).get("diagnostics") or {}
    warnings = diagnostics.get("warnings") or []
    codes = {str(item.get("code")) for item in warnings}
    assert "provider_mismatch" in codes or "sensor_mismatch" in codes, warnings
