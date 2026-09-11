"""P0.8 contracts: qualified decisions, counters and transactional relink."""

from __future__ import annotations

import copy
import pathlib
import sys
import pytest
from fastapi import BackgroundTasks, HTTPException


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db.storage import load_scene_json, project_dir, save_scene_json  # noqa: E402
from models.scene_source import SceneSelectionDecision  # noqa: E402
from routers import scene_import as si  # noqa: E402
from services.scene_packages import working_view  # noqa: E402
from services.scene_packages.contracts import decision_uid  # noqa: E402


def _package(source_id: str, *, status: str = "decision_required") -> dict:
    uid = decision_uid(source_id, "provider-product-1")
    return {
        "source_id": source_id,
        "package_id": "same-package",
        "decision_uid": uid,
        "provider": "generic",
        "package_root_relative": "delivery/scene.tif",
        "assets": [{
            "asset_id": "asset-1",
            "relative_path": "delivery/scene.tif",
            "role": "raster_candidate",
            "size": 10,
            "mtime_ns": 1,
        }],
        "selection": {
            "decision_uid": uid,
            "asset_ids": ["asset-1"],
            "identity_asset_ids": ["asset-1"],
            "status": status,
            "product_type": "PAN",
            "raster_kind": "direct",
            "alternatives": [{"label": "PAN", "asset_ids": ["asset-1"]}],
        },
    }


def test_decision_uid_does_not_leak_between_equal_package_ids(monkeypatch):
    first = _package("source-a")
    second = _package("source-b")
    preview = {
        "sources": [{"source_id": "source-a"}, {"source_id": "source-b"}],
        "packages": [first, second],
    }
    decision = SceneSelectionDecision(
        package_id="same-package",
        decision_uid=first["decision_uid"],
        asset_ids=["asset-1"],
    )
    saved: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        si,
        "_save_package_scene",
        lambda _pid, _sid, package, selection: saved.append((package["source_id"], dict(selection))),
    )
    pid = "p08-decision-uid"
    project_dir(pid).mkdir(parents=True, exist_ok=True)

    report, _identity, _overviews = si._catalogue_scenes(pid, preview, [decision])

    assert saved[0][1]["selected_by"] == "user"
    assert saved[1][1].get("selected_by") != "user"
    assert report["added"] == 1
    assert report["blocked"] == 1
    assert report["considered"] == sum(report[key] for key in ("added", "updated", "blocked", "failed"))
    with pytest.raises(HTTPException, match="decision_uid is required"):
        si._validate_preview_decisions(
            preview,
            [SceneSelectionDecision(package_id="same-package", asset_ids=["asset-1"])],
        )


def _archive_package(source_id: str) -> dict:
    uid = decision_uid(source_id, "archive-product-1")
    return {
        "source_id": source_id,
        "package_id": "archive-package",
        "decision_uid": uid,
        "provider": "worldview",
        "package_kind": "archive",
        "package_root_relative": "EPWApan_014679500010_0.zip",
        "assets": [{
            "asset_id": "asset-zip",
            "relative_path": "EPWApan_014679500010_0.zip",
            "role": "archive",
            "asset_role": "archive",
            "size": 1366286351,
            "mtime_ns": 1,
        }],
        "selection": {
            "decision_uid": uid,
            "asset_ids": [],
            "identity_asset_ids": [],
            "status": "archive_duplicate",
            "product_type": "ARCHIVE",
            "raster_kind": "archive",
        },
    }


def test_archive_package_is_reported_but_never_catalogued(monkeypatch):
    """P1.3a: archiwum jest widoczne w preview, ale nie zostaje scena.

    Licznik `archived` jest rozlaczny z `considered`, bo archiwum nie jest ani dodana, ani
    zablokowana, ani nieudana scena — nie jest scena w ogole.
    """
    preview = {
        "sources": [{"source_id": "source-a"}],
        "packages": [_archive_package("source-a"), _package("source-a", status="ready")],
    }
    saved: list[str] = []
    monkeypatch.setattr(
        si,
        "_save_package_scene",
        lambda _pid, _sid, package, _selection: saved.append(package["package_id"]),
    )
    pid = "p13-archive-skip"
    project_dir(pid).mkdir(parents=True, exist_ok=True)

    report, _identity, _overviews = si._catalogue_scenes(pid, preview, [])

    assert saved == ["same-package"]
    assert report["archived"] == 1
    assert report["added"] == 1
    assert report["considered"] == sum(report[key] for key in ("added", "updated", "blocked", "failed"))


def test_failed_raster_is_not_counted_as_added(monkeypatch):
    preview = {"sources": [{"source_id": "source-a"}], "packages": [_package("source-a", status="ready")]}
    monkeypatch.setattr(si, "_save_package_scene", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("unreadable")))
    pid = "p08-counter-failure"
    project_dir(pid).mkdir(parents=True, exist_ok=True)
    job = si._new_import_job(pid, "p08-counter-job", 1)

    report, pending_identity, pending_overviews = si._catalogue_scenes(pid, preview, [], job)

    assert report["failed"] == 1
    assert report["added"] == report["updated"] == report["blocked"] == 0
    assert report["considered"] == 1
    assert job["done"] == job["failed"] == 1
    assert pending_identity == pending_overviews == []


def test_unreadable_source_is_not_classified_as_native_overviews(monkeypatch, tmp_path):
    monkeypatch.setattr(
        working_view,
        "inspect_source_overviews",
        lambda _path: {"usable": False, "read_error": "broken raster", "factors": []},
    )
    assert working_view.source_has_overviews(tmp_path / "broken.tif") is False


def test_refresh_persists_invalid_state_and_propagates_read_error(monkeypatch):
    project_id = "p08-refresh-error"
    project_dir(project_id).mkdir(parents=True, exist_ok=True)
    save_scene_json(project_id, "scene-1", "scene", {"id": "scene-1", "preparation_status": "ready"})
    save_scene_json(project_id, "scene-1", "scene_manifest", {"working_view": {"preparation_status": "ready"}})
    monkeypatch.setattr(
        si.SceneRasterResolver,
        "resolve",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cannot open raster")),
    )

    with pytest.raises(RuntimeError, match="could not be characterized"):
        si._refresh_scene_from_manifest(project_id, "scene-1")

    scene = load_scene_json(project_id, "scene-1", "scene", default={})
    manifest = load_scene_json(project_id, "scene-1", "scene_manifest", default={})
    assert scene["preparation_status"] == "invalid"
    assert "cannot open raster" in scene["scene_info_error"]
    assert manifest["working_view"]["preparation_status"] == "invalid"


def test_duplicate_canonical_source_root_is_rejected(tmp_path):
    root = tmp_path / "delivery"
    root.mkdir()
    with pytest.raises(HTTPException, match="canonical"):
        si._validate_unique_source_roots([
            {"source_id": "source-a", "root_path": str(root)},
            {"source_id": "source-b", "root_path": str(root / ".")},
        ])


def test_relink_dry_run_reports_missing_and_changed_assets(monkeypatch):
    old_package = {
        "source_id": "source-a",
        "package_id": "pkg-1",
        "product_type": "PAN",
        "assets": [
            {"asset_id": "a", "relative_path": "a.tif", "size": 10},
            {"asset_id": "b", "relative_path": "b.tif", "size": 20},
        ],
    }
    new_package = {
        "source_id": "source-a",
        "package_id": "pkg-1",
        "assets": [{"asset_id": "a", "relative_path": "a.tif", "size": 11}],
        "selection": {"product_type": "PAN"},
    }
    monkeypatch.setattr(si, "list_scene_ids", lambda _pid: ["scene-1"])
    monkeypatch.setattr(si, "load_scene_json", lambda *_args, **_kwargs: {"source_package": old_package})
    monkeypatch.setattr(si, "_scan_source_records", lambda *_args, **_kwargs: {"packages": [new_package]})

    report = si._build_relink_report(
        "project",
        {"source_id": "source-a", "root_path": "old"},
        {"source_id": "source-a", "root_path": "new"},
    )

    assert report["compatible"] is False
    assert [item["asset_id"] for item in report["missing_assets"]] == ["b"]
    assert [item["asset_id"] for item in report["changed_assets"]] == ["a"]


def test_failed_relink_restores_json_state_and_keeps_working_artifacts(monkeypatch, tmp_path):
    project_id = "p08-relink-rollback"
    source_id = "source-a"
    project_dir(project_id).mkdir(parents=True, exist_ok=True)
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    old_root.mkdir()
    new_root.mkdir()
    scene_before = {"id": "scene-1", "preparation_status": "ready", "overview_status": "ready"}
    manifest_before = {
        "source_package": {"source_id": source_id, "package_id": "pkg-1"},
        "working_view": {"preparation_status": "ready", "raster_ref": {"relative_path": "working.vrt"}},
        "source_identity": {"status": "complete", "source_scene_uid": "scene-sha256:old"},
    }
    save_scene_json(project_id, "scene-1", "scene", scene_before)
    save_scene_json(project_id, "scene-1", "scene_manifest", manifest_before)
    working_vrt = project_dir(project_id) / "working.vrt"
    working_vrt.write_text("old working VRT", encoding="utf-8")
    source = {"source_id": source_id, "provider": "generic", "root_path": str(old_root), "enabled": True}
    sources = {"sources": [source]}
    persisted: dict = copy.deepcopy(sources)

    def save_sources(_project_id, value):
        nonlocal persisted
        persisted = copy.deepcopy(value)

    monkeypatch.setattr(si, "save_scene_sources", save_sources)
    monkeypatch.setattr(si, "_build_relink_report", lambda *_args: {"compatible": True})
    monkeypatch.setattr(si, "_common_scene_import_jobs_enabled", lambda: True)
    monkeypatch.setattr(si, "_ensure_scene_import_slot_available", lambda _pid: None)
    monkeypatch.setattr(si, "_submit_scene_import_job", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("queue failed")))
    cleared: list[str] = []
    monkeypatch.setattr(si, "clear_all_scene_display_overviews", lambda *_args: cleared.append("cleared"))

    with pytest.raises(HTTPException, match="not committed"):
        si._relink_source_transaction(
            project_id,
            source_id,
            new_root,
            source,
            sources,
            False,
            BackgroundTasks(),
        )

    assert persisted["sources"][0]["root_path"] == str(old_root)
    assert load_scene_json(project_id, "scene-1", "scene", default={}) == scene_before
    assert load_scene_json(project_id, "scene-1", "scene_manifest", default={}) == manifest_before
    assert working_vrt.read_text(encoding="utf-8") == "old working VRT"
    assert cleared == []


def test_completed_job_without_catalogue_readiness_is_an_error():
    detail = {
        "job": {"job_id": "job", "job_type": "scene_import", "project_id": "project", "payload": {}},
        "state": {"status": "completed", "catalogue_ready": False, "import_phase": "complete"},
    }
    view = si._common_import_view(detail)
    assert view["state"] == "error"
    assert "unresolved" in view["error"].lower()
