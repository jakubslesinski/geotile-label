"""Trwaly raport importu scen (DESIGN_DECISIONS.md, scene-import P1.6).

Bramka M2 wymaga, zeby raport pozwalal odtworzyc przyczyne KAZDEJ sceny `blocked` albo
`failed`. Dotad po imporcie zostawaly cztery liczniki i lista ostatnich 20 pozycji w pliku
joba — przy dostawie liczonej w setkach scen przyczyny wiekszosci niepowodzen znikaly.

Testy pilnuja czterech wlasnosci:

- kazda scena ma wpis, takze pominieta i archiwalna → `test_every_package_gets_an_entry`,
- lista bledow NIE jest obcinana → `test_error_list_is_not_truncated`,
- liczniki pozostaja rozlaczne i zgodne z wpisami → `test_counters_match_the_entries`,
- wariant zanonimizowany usuwa sciezki, zachowujac tresc → `test_anonymized_*`.

Uruchomienie: pytest backend/tests/test_import_report.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
from datetime import datetime, timezone

import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db.storage import project_dir  # noqa: E402
from routers import scene_import as si  # noqa: E402
from services.scene_packages import import_report  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_data_dir(monkeypatch, tmp_path):
    """Kazdy test tego pliku pisze do wlasnego katalogu tymczasowego.

    Bez tego `_catalogue` odkladal raporty w `data/` w repozytorium, a katalog narastal
    przez wszystkie dotychczasowe uruchomienia - 168 raportow w `p16-list` i ~23 MB
    lacznie. Dla wiekszosci testow bylo to tylko smiecenie, bo czytaja swoj raport po
    `scan_id`, ale `test_reports_are_listed_newest_first` orzeka o kolejnosci CALEGO
    katalogu i przez to zalezal od stanu zostawionego przez poprzednie przebiegi.
    """
    from db import storage

    monkeypatch.setattr(storage, "DATA_DIR", tmp_path / "appdata")


def _package(index: int, *, status: str = "ready", provider: str = "iceye") -> dict:
    return {
        "source_id": "src1",
        "package_id": f"pkg{index}",
        "provider": provider,
        "package_root_relative": f"DOSTAWA/scene_{index}",
        "assets": [
            {
                "asset_id": f"a{index}",
                "relative_path": f"DOSTAWA/scene_{index}.tif",
                "role": "raster_candidate",
                "asset_role": "measurement",
            },
            {
                "asset_id": f"m{index}",
                "relative_path": f"DOSTAWA/scene_{index}.xml",
                "role": "metadata",
                "asset_role": "product_metadata",
            },
        ],
        "selection": {
            "asset_ids": [f"a{index}"],
            "identity_asset_ids": [f"a{index}"],
            "status": status,
            "product_type": "GRD",
            "raster_kind": "direct",
            "completeness": "complete",
            "diagnostics": {
                "warnings": [{"code": "metadata_not_bound", "message": "1 sidecar excluded"}],
                "errors": [],
                "metadata_conflicts": [],
            },
        },
    }


def _archive_package(index: int) -> dict:
    return {
        "source_id": "src1",
        "package_id": f"zip{index}",
        "provider": "worldview",
        "package_kind": "archive",
        "package_root_relative": f"DOSTAWA/archiwum_{index}.zip",
        "assets": [{"asset_id": f"z{index}", "relative_path": f"DOSTAWA/archiwum_{index}.zip",
                    "role": "archive", "asset_role": "archive"}],
        "selection": {
            "asset_ids": [],
            "status": "archive_duplicate",
            "product_type": "ARCHIVE",
            "raster_kind": "archive",
        },
    }


def _preview(*packages: dict) -> dict:
    return {
        "sources": [{"source_id": "src1", "provider": "iceye", "root_path": r"\\share\DANE"}],
        "packages": list(packages),
    }


def _catalogue(project: str, preview: dict, monkeypatch, failing: set[str] | None = None):
    failing = failing or set()

    def save(_pid, _sid, package, _selection):
        if package["package_id"] in failing:
            raise RuntimeError(f"unreadable raster in {package['package_id']}")

    monkeypatch.setattr(si, "_save_package_scene", save)
    project_dir(project).mkdir(parents=True, exist_ok=True)
    return si._catalogue_scenes(project, preview, [])


def _saved_report(project: str, scan_id: str) -> dict:
    path = project_dir(project) / "import_reports" / f"{scan_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


# --- tresc raportu --------------------------------------------------------------------


def test_every_package_gets_an_entry_including_archives(monkeypatch):
    project = "p16-entries"
    preview = _preview(_package(1), _package(2), _archive_package(1))
    report, _identity, _overviews = _catalogue(project, preview, monkeypatch)
    saved = _saved_report(project, report["scan_id"])

    assert saved["schema_name"] == import_report.REPORT_SCHEMA_NAME
    statuses = sorted(entry["status"] for entry in saved["scenes"])
    assert statuses == ["archived", "ok", "ok"]


def test_entry_carries_the_reason_not_only_the_status(monkeypatch):
    project = "p16-reason"
    preview = _preview(_package(1), _package(2))
    report, _identity, _overviews = _catalogue(project, preview, monkeypatch, failing={"pkg2"})
    saved = _saved_report(project, report["scan_id"])

    failed = next(entry for entry in saved["scenes"] if entry["status"] == "error")
    assert "unreadable raster in pkg2" in failed["error"]
    assert failed["provider"] == "iceye"
    assert failed["product_type"] == "GRD"
    assert failed["assets_by_role"] == {"metadata": 1, "raster_candidate": 1}
    assert failed["assets_by_contract_role"] == {"measurement": 1, "product_metadata": 1}


def test_resolver_diagnostics_survive_into_the_report(monkeypatch):
    project = "p16-diagnostics"
    report, _identity, _overviews = _catalogue(project, _preview(_package(1)), monkeypatch)
    saved = _saved_report(project, report["scan_id"])
    codes = [item["code"] for item in saved["scenes"][0]["diagnostics"]["warnings"]]
    assert codes == ["metadata_not_bound"]


def test_error_list_is_not_truncated_to_the_last_twenty(monkeypatch):
    """Lista bledow jest pelna — obcinanie do 20 nalezy do UI postepu, nie do raportu."""
    project = "p16-errors"
    packages = [_package(index) for index in range(1, 26)]
    failing = {f"pkg{index}" for index in range(1, 26)}
    report, _identity, _overviews = _catalogue(project, _preview(*packages), monkeypatch, failing=failing)
    saved = _saved_report(project, report["scan_id"])

    assert report["failed"] == 25
    assert len(saved["errors"]) == 25
    assert all(item["message"] for item in saved["errors"])


def test_counters_match_the_entries_and_stay_disjoint(monkeypatch):
    project = "p16-counters"
    preview = _preview(_package(1), _package(2), _package(3), _archive_package(1))
    report, _identity, _overviews = _catalogue(project, preview, monkeypatch, failing={"pkg3"})
    saved = _saved_report(project, report["scan_id"])

    assert saved["added"] == 2 and saved["failed"] == 1 and saved["archived"] == 1
    assert saved["considered"] == saved["added"] + saved["updated"] + saved["blocked"] + saved["failed"]
    by_status: dict[str, int] = {}
    for entry in saved["scenes"]:
        by_status[entry["status"]] = by_status.get(entry["status"], 0) + 1
    assert by_status == {"ok": 2, "error": 1, "archived": 1}


def test_prepare_required_scene_is_catalogued_not_failed(monkeypatch, tmp_path):
    """Produkt pochodny czekajacy na przygotowanie nie jest bledem importu.

    Ujawnil to raport P1.6 na rzeczywistej dostawie WV2: MUL+PAN wchodzil jako `failed=1`
    z `preparation_status=invalid`, bo charakterystyka probowala otworzyc raster, ktory
    z definicji jeszcze nie istnieje. `decision_required` mial juz wyjatek w tym samym
    miejscu — `prepare_required` ma dokladnie te sama wlasnosc.
    """
    from db import storage

    monkeypatch.setattr(storage, "DATA_DIR", tmp_path / "appdata")
    project = "p16-prepare"
    storage.project_dir(project).mkdir(parents=True, exist_ok=True)
    package = _package(1, status="prepare_required")
    package["selection"]["raster_kind"] = "derived"
    package["selection"]["product_type"] = "MUL+PAN"

    refreshed: list[str] = []
    monkeypatch.setattr(
        si, "_refresh_scene_from_manifest", lambda _pid, scene_id: refreshed.append(scene_id)
    )

    report, _identity, _overviews = si._catalogue_scenes(project, _preview(package), [])

    assert refreshed == [], "prepare_required nie moze byc charakteryzowany przy imporcie"
    assert report["failed"] == 0 and report["added"] == 1
    saved = json.loads(
        (storage.project_dir(project) / "import_reports" / f"{report['scan_id']}.json")
        .read_text(encoding="utf-8")
    )
    entry = saved["scenes"][0]
    assert entry["status"] == "ok"
    assert entry["selection_status"] == "prepare_required"


def test_report_rolls_up_per_source(monkeypatch):
    project = "p16-rollup"
    preview = _preview(_package(1), _package(2), _archive_package(1))
    report, _identity, _overviews = _catalogue(project, preview, monkeypatch)
    saved = _saved_report(project, report["scan_id"])

    rollup = saved["by_source"][0]
    assert rollup["source_id"] == "src1"
    assert rollup["scenes"] == 3
    assert rollup["by_status"] == {"archived": 1, "ok": 2}
    assert rollup["by_product_type"] == {"ARCHIVE": 1, "GRD": 2}


def test_catalogue_response_stays_small(monkeypatch):
    """Odpowiedz HTTP nie moze rosnac z liczba scen — pelna tresc lezy w pliku."""
    project = "p16-response"
    packages = [_package(index) for index in range(1, 11)]
    report, _identity, _overviews = _catalogue(project, _preview(*packages), monkeypatch)
    assert "scenes" not in report and "errors" not in report
    assert report["scan_id"] and report["added"] == 10


# --- wariant do przekazania dalej --------------------------------------------------------


def test_anonymized_report_drops_paths_but_keeps_the_substance(monkeypatch):
    project = "p16-anon"
    report, _identity, _overviews = _catalogue(project, _preview(_package(1)), monkeypatch)
    saved = _saved_report(project, report["scan_id"])
    anonymous = import_report.anonymize(saved)

    assert anonymous["sources"][0]["root_path"].startswith("path_")
    entry = anonymous["scenes"][0]
    assert entry["package_root_relative"].startswith("path_")
    assert entry["filename"].startswith("path_")
    # Tresc raportu zostaje nietknieta.
    assert entry["status"] == "ok"
    assert entry["provider"] == "iceye"
    assert entry["assets_by_role"] == {"metadata": 1, "raster_candidate": 1}
    assert anonymous["added"] == saved["added"]


def test_anonymized_tokens_are_stable_for_the_same_path():
    payload = {"scenes": [{"filename": "A/B.tif"}, {"filename": "A/B.tif"}, {"filename": "A/C.tif"}]}
    result = import_report.anonymize(payload)
    first, second, third = (item["filename"] for item in result["scenes"])
    assert first == second and first != third


def test_anonymization_does_not_mutate_the_original():
    payload = {"filename": "A/B.tif", "added": 3}
    import_report.anonymize(payload)
    assert payload["filename"] == "A/B.tif"


# --- odczyt ---------------------------------------------------------------------------


def _wait_for_a_later_timestamp(after: str) -> None:
    """Poczekaj, az zegar przejdzie za `after`.

    `list_reports` porzadkuje raporty po `created_at`. Dwa katalogowania wykonane w tej
    samej mikrosekundzie daja rowny klucz az do `scan_id`, a ten jest `uuid4` - wiec
    o kolejnosci decydowalby rzut moneta. To zdarza sie naprawde: w katalogu 166 raportow
    zebranych z dotychczasowych przebiegow byly dwie takie pary. Test ma sprawdzac
    porzadkowanie, a nie wygrywac losowanie, wiec wymusza rozroznialne znaczniki.
    """
    deadline = time.monotonic() + 5.0
    while datetime.now(timezone.utc).isoformat() <= after:
        if time.monotonic() > deadline:
            raise AssertionError(
                "zegar nie przeszedl do przodu w 5 s - bez rozroznialnych znacznikow "
                "ten test nie ma czego sprawdzac"
            )
        time.sleep(0)


def test_reports_are_listed_newest_first(monkeypatch):
    project = "p16-list"
    first, _i, _o = _catalogue(project, _preview(_package(1)), monkeypatch)
    _wait_for_a_later_timestamp(_saved_report(project, first["scan_id"])["created_at"])
    second, _i, _o = _catalogue(project, _preview(_package(2)), monkeypatch)

    listed = import_report.list_reports(project_dir(project))
    scan_ids = [item["scan_id"] for item in listed]
    assert scan_ids == [second["scan_id"], first["scan_id"]]
    assert listed[0]["scene_count"] == 1


def test_reports_with_equal_file_mtime_use_payload_timestamp(tmp_path):
    directory = tmp_path / "import_reports"
    directory.mkdir(parents=True)
    older = directory / "older.json"
    newer = directory / "newer.json"
    older.write_text(json.dumps({
        "scan_id": "older",
        "created_at": "2026-09-01T10:00:00.000001+00:00",
        "scenes": [],
    }), encoding="utf-8")
    newer.write_text(json.dumps({
        "scan_id": "newer",
        "created_at": "2026-09-01T10:00:00.000002+00:00",
        "scenes": [],
    }), encoding="utf-8")
    stamp = 1_800_000_000_000_000_000
    os.utime(older, ns=(stamp, stamp))
    os.utime(newer, ns=(stamp, stamp))

    listed = import_report.list_reports(tmp_path)

    assert [item["scan_id"] for item in listed] == ["newer", "older"]


def test_missing_report_reads_as_none():
    assert import_report.load_report(project_dir("p16-none"), "deadbeef") is None


def test_endpoint_rejects_a_malformed_scan_id(monkeypatch):
    project = "p16-endpoint"
    project_dir(project).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(si, "_require_project", lambda _pid: None)
    with pytest.raises(Exception) as raised:
        si.get_scene_import_report(project, "../../etc/passwd")
    assert "Invalid scan_id" in str(raised.value)
