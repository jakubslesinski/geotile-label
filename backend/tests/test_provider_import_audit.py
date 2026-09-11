"""Wlasnosci harnessu audytu importu dostawcow (DESIGN_DECISIONS.md, scene-import B0b).

Testujemy sam harness, nie rzeczywiste dostawy: audyt na danych produkcyjnych jest z
zalozenia opcjonalny i zalezny od dostepnosci udzialu sieciowego. Trzy wlasnosci z bramki B0
musza jednak dzialac deterministycznie w CI:

1. brak osiagalnych zrodel NIE moze udawac sukcesu,
2. skan nie modyfikuje zrodla,
3. zmiana zrodla w trakcie skanu jest wykrywana jako `changed_during_scan`.

Uruchomienie: pytest backend/tests/test_provider_import_audit.py
"""

from __future__ import annotations

import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(BACKEND_ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT / "tests"))

from benchmarks import benchmark_provider_import as audit  # noqa: E402
from fixtures.scene_packages import build_worldview_pan_only  # noqa: E402


def test_unreachable_source_is_reported_as_skipped(tmp_path):
    """Brak zrodla daje `SKIPPED_REAL_DATA`, nigdy cichego sukcesu."""
    record = audit.audit_source("nieistniejace", tmp_path / "nie_ma", "worldview")
    assert record["status"] == audit.STATUS_SKIPPED
    assert "reason" in record


def test_report_of_only_unreachable_sources_is_not_a_success(tmp_path):
    """Raport zlozony z samych pominietych zrodel ma status zbiorczy `SKIPPED_REAL_DATA`."""
    records = [
        audit.audit_source("a", tmp_path / "brak_a", "iceye"),
        audit.audit_source("b", tmp_path / "brak_b", "capella"),
    ]
    report = audit.build_report(records, anonymized=False)
    assert report["summary"]["sources_reachable"] == 0
    assert report["summary"]["overall_status"] == audit.STATUS_SKIPPED


def test_stable_source_is_audited_without_modification(tmp_path):
    """Skan realnej struktury nie zmienia ani jednego bajtu zrodla."""
    workspace = tmp_path / "wv"
    workspace.mkdir()
    package = build_worldview_pan_only(workspace)

    record = audit.audit_source("wv", package.root, "worldview")

    assert record["status"] == audit.STATUS_STABLE
    snapshot = record["snapshot"]
    assert snapshot["difference"]["identical"], snapshot["difference"]
    assert snapshot["fingerprint_before"] == snapshot["fingerprint_after"]
    assert record["packages"] >= 1
    # Audyt musi widziec role assetow — to podstawa baseline'u dla P0.2.
    assert record["assets_by_role"].get("raster_candidate")


def test_source_changed_during_scan_is_detected(tmp_path, monkeypatch):
    """Bramka B0: przyrost czesci w trakcie skanu daje `changed_during_scan`.

    Odtwarzamy sytuacje z audytu rzeczywistych danych, w ktorej katalog PAN-only urosl
    z trzech do szesciu plikow podczas odczytu. Zamiast liczyc na wyscig, wstrzykujemy
    zmiane deterministycznie: podmieniony `scan_source` dopisuje brakujace czesci dokladnie
    wtedy, gdy audyt jest miedzy snapshotem "przed" i "po".
    """
    workspace = tmp_path / "wv_changing"
    workspace.mkdir()
    package = build_worldview_pan_only(workspace)
    pan_dir = package.root / "014679500010_01" / "_PAN"

    from services.scene_packages import resolvers as resolvers_module

    original = resolvers_module.scan_source

    def scanning_that_grows(root, provider):
        result = original(root, provider)
        # Dostawa "dokoncza sie" w trakcie skanu — dokladnie jak przy kopiowaniu z sieci.
        (pan_dir / "18APR08_PAN_R4C1.TIF").write_bytes(b"x")
        return result

    monkeypatch.setattr(resolvers_module, "scan_source", scanning_that_grows)

    record = audit.audit_source("wv_changing", package.root, "worldview")

    assert record["status"] == audit.STATUS_CHANGED, record["status"]
    difference = record["snapshot"]["difference"]
    assert difference["added_count"] == 1, difference
    assert not difference["identical"]
    assert record["snapshot"]["fingerprint_before"] != record["snapshot"]["fingerprint_after"]


def test_anonymized_report_does_not_leak_the_path(tmp_path):
    """Wariant zanonimizowany nie moze ujawnic sciezki zrodla (sekcja 13, prywatnosc)."""
    workspace = tmp_path / "wv_anon"
    workspace.mkdir()
    package = build_worldview_pan_only(workspace)

    record = audit.audit_source("wv", package.root, "worldview", anonymize=True)

    assert record["source"].startswith("src_")
    assert str(package.root) not in record["source"]
    assert "014679500010" not in record["source"]


def test_snapshot_respects_the_entry_cap(tmp_path):
    """Limit wpisow chroni przed przejsciem po ogromnym drzewie i jest raportowany."""
    workspace = tmp_path / "wv_cap"
    workspace.mkdir()
    package = build_worldview_pan_only(workspace)

    record = audit.audit_source("wv", package.root, "worldview", max_entries=2)

    assert record["snapshot"]["truncated"] is True
    assert record["snapshot"]["files_before"] <= 2
    assert record["snapshot"]["max_entries"] == 2


def test_audit_reports_metadata_binding_for_the_p03_gate(tmp_path, monkeypatch):
    """Bramka P0.3 mierzy spadek konfliktow metadanych; audyt musi dostarczyc licznik.

    Na rzeczywistym zrodle ICEYE nie da sie odtworzyc liczby 914 w CI, ale mozna zmierzyc
    jej przyczyne: ile sidecarow resolver zwiazal z wybranym produktem, a ile zostalo luzem.
    Fixture ma jedna akwizycje z produktami GRD, VID i SLC — po zwiazaniu do GRD nalezy
    dokladnie jeden z dwoch sidecarow.
    """
    from fixtures.scene_packages import build_iceye_multi_product_acquisition
    from services.scene_packages.contracts import FLAG_GRAPH_V2

    monkeypatch.setenv(FLAG_GRAPH_V2, "1")
    workspace = tmp_path / "iceye_bind"
    workspace.mkdir()
    package = build_iceye_multi_product_acquisition(workspace)

    record = audit.audit_source("iceye", package.root, "iceye")

    binding = record["metadata_binding"]
    assert binding["metadata_assets"] == 2, binding
    assert binding["bound_to_selection"] == 1, binding
    assert binding["excluded_from_selection"] == 1, binding
    # VID jest RASTREM, wiec jego rola to `measurement` — role sa klasyfikacja sciezki,
    # niezalezna od dostawcy. To, ze VID nie jest produktem do etykietowania, wie dopiero
    # gramatyka ICEYE i dlatego odsiewa go SELEKCJA, a nie rola.
    assert record["assets_by_contract_role"].get("measurement") == 2
    assert record["selection_statuses"].get("ready") == 1


def test_audit_binding_counter_is_neutral_without_the_flag(tmp_path, monkeypatch):
    """Bez flagi resolver nie wiaze metadanych, wiec licznik nie moze udawac, ze wiaze."""
    from fixtures.scene_packages import build_iceye_multi_product_acquisition
    from services.scene_packages.contracts import FLAG_GRAPH_V2

    monkeypatch.delenv(FLAG_GRAPH_V2, raising=False)
    workspace = tmp_path / "iceye_bind_off"
    workspace.mkdir()
    package = build_iceye_multi_product_acquisition(workspace)

    record = audit.audit_source("iceye", package.root, "iceye")

    binding = record["metadata_binding"]
    assert binding["excluded_from_selection"] == 0, binding
    assert binding["bound_to_selection"] == binding["metadata_assets"]
