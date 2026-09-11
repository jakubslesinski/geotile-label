"""P1.2 contracts for durable provider scans and rebuildable JSON cache."""

from __future__ import annotations

import json
import pathlib
import sys

import pytest
from fastapi import HTTPException


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db import storage  # noqa: E402
from models.job import JobSpec, JobType, PriorityClass, ResourceClass  # noqa: E402
from models.scene_source import SceneImportPreviewRequest, SceneSourceCreate  # noqa: E402
from routers import scene_import as si  # noqa: E402
from services.jobs.store import (  # noqa: E402
    create_job,
    job_dir,
    list_all_jobs,
    utc_now,
)
from services.scene_packages import scan_cache  # noqa: E402
from services.scene_packages.scan_cache import (  # noqa: E402
    SceneScanCancelled,
    SourceChangedDuringScan,
    scan_source_cached,
)


def _configure_data_dir(tmp_path: pathlib.Path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(si, "DATA_DIR", tmp_path)


def _generic_source(tmp_path: pathlib.Path, names=("a.tif", "b.tif")) -> pathlib.Path:
    root = tmp_path / "source"
    root.mkdir(parents=True, exist_ok=True)
    for index, name in enumerate(names):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((f"raster-{index}" * 100).encode())
    return root


def test_warm_scan_reuses_inventory_without_resolving_source_assets(tmp_path, monkeypatch):
    _configure_data_dir(tmp_path, monkeypatch)
    root = _generic_source(tmp_path)
    calls: list[str] = []
    original = scan_cache.resolve_package_candidate

    def record_resolution(resolver, source_root, candidate):
        calls.append(candidate.relative_root)
        return original(resolver, source_root, candidate)

    monkeypatch.setattr(scan_cache, "resolve_package_candidate", record_resolution)
    cold = scan_source_cached(root, "generic")
    assert cold["cache"]["status"] == "miss"
    assert len(calls) == 2

    warm = scan_source_cached(root, "generic")
    assert warm["cache"]["status"] == "hit"
    assert warm["cache"]["misses"] == 0
    assert len(calls) == 2, "warm scan must not reopen/re-resolve source assets"
    assert warm["packages"] == cold["packages"]


def test_changed_candidate_reuses_unchanged_directory_cache(tmp_path, monkeypatch):
    _configure_data_dir(tmp_path, monkeypatch)
    root = _generic_source(tmp_path, ("one/a.tif", "two/b.tif"))
    calls: list[str] = []
    original = scan_cache.resolve_package_candidate

    def record_resolution(resolver, source_root, candidate):
        calls.append(candidate.relative_root)
        return original(resolver, source_root, candidate)

    monkeypatch.setattr(scan_cache, "resolve_package_candidate", record_resolution)
    scan_source_cached(root, "generic")
    calls.clear()
    (root / "two" / "b.tif").write_bytes(b"changed" * 300)

    changed = scan_source_cached(root, "generic")
    assert changed["cache"]["status"] == "partial"
    assert changed["cache"]["hits"] == 1
    assert changed["cache"]["misses"] == 1
    assert calls == ["two/b.tif"]


def test_deleted_cache_is_rebuilt_correctly(tmp_path, monkeypatch):
    _configure_data_dir(tmp_path, monkeypatch)
    root = _generic_source(tmp_path)
    first = scan_source_cached(root, "generic")
    cache_path = pathlib.Path(first["cache"]["path"])
    before = first["packages"]
    cache_path.unlink()

    rebuilt = scan_source_cached(root, "generic")
    assert rebuilt["cache"]["status"] == "miss"
    assert pathlib.Path(rebuilt["cache"]["path"]).is_file()
    assert rebuilt["packages"] == before
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["schema_name"] == scan_cache.SCAN_CACHE_SCHEMA_NAME
    assert payload["schema_version"] == scan_cache.SCAN_CACHE_SCHEMA_VERSION


def test_cache_descriptor_tracks_contract_logic_and_resolver_versions(tmp_path, monkeypatch):
    _configure_data_dir(tmp_path, monkeypatch)
    root = _generic_source(tmp_path, ("scene.tif",))
    result = scan_source_cached(root, "generic")
    payload = json.loads(pathlib.Path(result["cache"]["path"]).read_text(encoding="utf-8"))
    descriptor = payload["descriptor"]
    assert payload["schema_version"] == 2
    assert descriptor["scan_logic_version"] == 2
    assert descriptor["contract_version"] >= 2
    assert descriptor["resolver_version"] >= 2


def test_one_time_cache_invalidation_removes_only_legacy_payloads(tmp_path, monkeypatch):
    _configure_data_dir(tmp_path, monkeypatch)
    cache_root = tmp_path / "cache" / "scene-package-inventories"
    cache_root.mkdir(parents=True)
    legacy = cache_root / "legacy.json"
    legacy.write_text(json.dumps({
        "schema_name": scan_cache.SCAN_CACHE_SCHEMA_NAME,
        "schema_version": 1,
        "descriptor": {"resolver_version": 1},
    }), encoding="utf-8")
    current = cache_root / "current.json"
    current.write_text(json.dumps({
        "schema_name": scan_cache.SCAN_CACHE_SCHEMA_NAME,
        "schema_version": scan_cache.SCAN_CACHE_SCHEMA_VERSION,
        "descriptor": {
            "scan_logic_version": scan_cache.SCAN_CACHE_LOGIC_VERSION,
            "contract_version": scan_cache.CONTRACT_VERSION,
        },
    }), encoding="utf-8")
    unrelated = cache_root / "notes.txt"
    unrelated.write_text("not a cache payload", encoding="utf-8")

    first = scan_cache.invalidate_legacy_scan_caches()
    second = scan_cache.invalidate_legacy_scan_caches()

    assert first["status"] == "complete"
    assert first["removed"] == 1
    assert not legacy.exists()
    assert current.exists()
    assert unrelated.exists()
    assert second["status"] == "already_complete"
    assert second["removed"] == 0


def test_source_change_between_snapshots_is_detected(tmp_path, monkeypatch):
    _configure_data_dir(tmp_path, monkeypatch)
    root = _generic_source(tmp_path, ("scene.tif",))
    original = scan_cache.snapshot_source_tree
    calls = 0

    def change_before_final_snapshot(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            (root / "scene.tif").write_bytes(b"changed during scan" * 100)
        return original(*args, **kwargs)

    monkeypatch.setattr(scan_cache, "snapshot_source_tree", change_before_final_snapshot)
    with pytest.raises(SourceChangedDuringScan, match="source_changed_during_scan"):
        scan_source_cached(root, "generic")
    assert not scan_cache.scan_cache_path(root, "generic").exists()


def test_preview_http_only_submits_job_and_does_not_scan(tmp_path, monkeypatch):
    _configure_data_dir(tmp_path, monkeypatch)
    root = _generic_source(tmp_path, ("scene.tif",))
    monkeypatch.setattr(
        si,
        "_scan_source_records",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("HTTP performed scan")),
    )
    monkeypatch.setattr(
        si,
        "_submit_scene_scan_job",
        lambda *_args, **_kwargs: {"job": {"job_id": "scan-job"}},
    )

    result = si.preview_scene_sources(SceneImportPreviewRequest(
        sources=[SceneSourceCreate(provider="generic", root_path=str(root))],
        modality="EO",
    ))
    assert result == {"status": "queued", "scan_job_id": "scan-job"}


def test_project_creation_scan_uses_independent_metadata_resource(monkeypatch):
    captured: dict = {}

    def capture_submission(scope, request):
        captured.update({"scope": scope, "request": request})
        return {"job": {"job_id": "scan-job"}}

    monkeypatch.setattr(si, "submit_job", capture_submission)
    si._submit_scene_scan_job(
        [{"provider": "generic", "root_path": r"C:\data", "enabled": True}],
        "EO",
        purpose="create_project",
    )

    request = captured["request"]
    assert captured["scope"] == si.SCENE_SCAN_JOB_SCOPE
    assert request.resource_class == ResourceClass.IO_METADATA
    assert request.priority_class == PriorityClass.INTERACTIVE


class _Context:
    def __init__(self):
        self.state: dict = {}
        self.events: list[tuple[str, dict]] = []

    def update(self, **changes):
        self.state.update(changes)
        return self.state

    def emit(self, event, data):
        self.events.append((event, data))

    def cancel_requested(self):
        return False


class _CancelOnSecondSourceContext(_Context):
    def __init__(self):
        super().__init__()
        self.cancelled = False

    def emit(self, event, data):
        super().emit(event, data)
        if event == "scene_scan_progress" and data.get("stage") == "source" and data.get("source_index") == 2:
            self.cancelled = True

    def cancel_requested(self):
        return self.cancelled


def test_cancelled_scan_keeps_partial_checkpoint_private(tmp_path, monkeypatch):
    _configure_data_dir(tmp_path, monkeypatch)
    root = _generic_source(tmp_path, ("scene.tif",))
    job_id = "cancelled-scan"
    job_dir(si.SCENE_SCAN_JOB_SCOPE, job_id).mkdir(parents=True, exist_ok=True)

    def cancel_after_source(_sources, _modality, **kwargs):
        kwargs["source_complete_callback"]({
            "source_index": 1,
            "source_total": 2,
            "source_id": "src",
            "packages": [{"package_id": "partial"}],
            "diagnostics": [],
            "detected_modalities": ["EO"],
            "cache": [],
        })
        raise SceneScanCancelled("cancelled")

    monkeypatch.setattr(si, "_scan_source_records", cancel_after_source)
    before = set(si._preview_dir().glob("*.json"))
    with pytest.raises(SceneScanCancelled):
        si.run_scene_scan_common_job({
            "job_id": job_id,
            "payload": {
                "sources": [
                    {"source_id": "src", "provider": "generic", "root_path": str(root)},
                    {"source_id": "src2", "provider": "generic", "root_path": str(root)},
                ],
                "expected_modality": "EO",
            },
        }, _Context())

    partial = json.loads(
        (job_dir(si.SCENE_SCAN_JOB_SCOPE, job_id) / "partial_preview.json").read_text(encoding="utf-8")
    )
    assert partial["status"] == "partial"
    assert partial["packages"] == [{"package_id": "partial"}]
    assert set(si._preview_dir().glob("*.json")) == before


def test_stable_job_publishes_preview_only_after_all_sources(tmp_path, monkeypatch):
    _configure_data_dir(tmp_path, monkeypatch)
    root = _generic_source(tmp_path, ("scene.tif",))
    job_id = "stable-scan"
    job_dir(si.SCENE_SCAN_JOB_SCOPE, job_id).mkdir(parents=True, exist_ok=True)
    context = _Context()

    result = si.run_scene_scan_common_job({
        "job_id": job_id,
        "payload": {
            "sources": [{
                "source_id": "src",
                "provider": "generic",
                "root_path": str(root),
                "enabled": True,
            }],
            "expected_modality": "EO",
        },
    }, context)

    preview = si._load_preview(result["preview_id"])
    assert preview["scan_status"] == "complete"
    assert preview["scan_job_id"] == job_id
    assert len(preview["packages"]) == 1
    assert pathlib.Path(result["partial_preview_path"]).is_file()
    assert any(event == "scene_scan_progress" for event, _data in context.events)


def test_resumed_scan_reuses_sources_completed_before_cancellation(tmp_path, monkeypatch):
    _configure_data_dir(tmp_path, monkeypatch)
    first = _generic_source(tmp_path / "first", ("one.tif",))
    second = _generic_source(tmp_path / "second", ("two.tif",))
    sources = [
        {"source_id": "first", "provider": "generic", "root_path": str(first)},
        {"source_id": "second", "provider": "generic", "root_path": str(second)},
    ]
    job_dir(si.SCENE_SCAN_JOB_SCOPE, "cancel-before-second").mkdir(parents=True, exist_ok=True)
    with pytest.raises(SceneScanCancelled):
        si.run_scene_scan_common_job({
            "job_id": "cancel-before-second",
            "payload": {"sources": sources, "expected_modality": "EO"},
        }, _CancelOnSecondSourceContext())

    assert scan_cache.scan_cache_path(first, "generic").is_file()
    assert not scan_cache.scan_cache_path(second, "generic").is_file()

    job_dir(si.SCENE_SCAN_JOB_SCOPE, "resumed-scan").mkdir(parents=True, exist_ok=True)
    result = si.run_scene_scan_common_job({
        "job_id": "resumed-scan",
        "payload": {"sources": sources, "expected_modality": "EO"},
    }, _Context())
    statuses = [item["status"] for item in result["scan_cache"]]
    assert statuses == ["hit", "miss"]


def test_runtime_scan_jobs_are_recovered_by_common_job_index(tmp_path, monkeypatch):
    _configure_data_dir(tmp_path, monkeypatch)
    create_job(JobSpec(
        job_id="runtime-scan",
        job_type=JobType.SCENE_SCAN,
        project_id=si.SCENE_SCAN_JOB_SCOPE,
        resource_class=ResourceClass.IO_METADATA,
        priority_class=PriorityClass.INTERACTIVE,
        priority=300,
        payload={"sources": []},
        created_at=utc_now(),
    ))
    assert any(
        item["job"]["job_id"] == "runtime-scan"
        for item in list_all_jobs()
    )


def test_apply_validates_selected_assets_but_ignores_changed_browse(tmp_path, monkeypatch):
    _configure_data_dir(tmp_path, monkeypatch)
    root = _generic_source(tmp_path, ("selected.tif", "browse.jpg"))
    selected = root / "selected.tif"
    browse = root / "browse.jpg"
    selected_stat = selected.stat()
    browse_stat = browse.stat()
    package = {
        "source_id": "src",
        "package_id": "pkg",
        "provider": "generic",
        "decision_uid": "decision",
        "assets": [
            {
                "asset_id": "selected",
                "relative_path": "selected.tif",
                "role": "raster_candidate",
                "size": selected_stat.st_size,
                "mtime_ns": selected_stat.st_mtime_ns,
            },
            {
                "asset_id": "browse",
                "relative_path": "browse.jpg",
                "role": "raster_candidate",
                "asset_role": "browse",
                "size": browse_stat.st_size,
                "mtime_ns": browse_stat.st_mtime_ns,
            },
        ],
        "selection": {
            "status": "ready",
            "asset_ids": ["selected"],
            "identity_asset_ids": ["selected"],
        },
        "source_snapshot": {
            "assets": [
                {
                    "asset_id": "selected",
                    "relative_path": "selected.tif",
                    "size": selected_stat.st_size,
                    "mtime_ns": selected_stat.st_mtime_ns,
                },
                {
                    "asset_id": "browse",
                    "relative_path": "browse.jpg",
                    "size": browse_stat.st_size,
                    "mtime_ns": browse_stat.st_mtime_ns,
                },
            ],
        },
    }
    preview = {
        "sources": [{"source_id": "src", "root_path": str(root)}],
        "packages": [package],
    }

    browse.write_bytes(b"new browse" * 100)
    si._validate_preview_inputs(preview)

    selected.write_bytes(b"changed selected" * 100)
    with pytest.raises(HTTPException) as error:
        si._validate_preview_inputs(preview)
    assert error.value.status_code == 409
