"""Contract tests for durable scene import jobs and the legacy UI adapter."""

from __future__ import annotations

import pathlib
import sys
from typing import Any


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from routers import scene_import as si  # noqa: E402
from services.jobs.processes import capture_process_identity  # noqa: E402


def _detail(*, status: str, catalogue_ready: bool = False) -> dict[str, Any]:
    return {
        "job": {
            "job_id": "20260829T120000000000Z_deadbeef",
            "job_type": "scene_import",
            "project_id": "project01",
            "created_at": "2026-08-29T12:00:00+00:00",
            "payload": {"import_total": 4},
        },
        "state": {
            "status": status,
            "process": capture_process_identity() if status == "running" else None,
            "catalogue_ready": catalogue_ready,
            "import_phase": "overviews" if catalogue_ready else "catalogue",
            "import_total": 4,
            "import_done": 3,
            "overviews_total": 2,
            "overviews_done": 1,
            "created_at": "2026-08-29T12:00:00+00:00",
            "updated_at": "2026-08-29T12:01:00+00:00",
        },
    }


def test_common_import_adapter_opens_project_while_overviews_continue():
    view = si._common_import_view(_detail(status="running", catalogue_ready=True))
    assert view["state"] == "done"
    assert view["phase"] == "overviews"
    assert view["overviews_incomplete"] is True
    assert view["live"] is True


def test_common_import_adapter_marks_pre_catalogue_interruption_as_error():
    view = si._common_import_view(_detail(status="interrupted"))
    assert view["state"] == "error"
    assert view["stale"] is False
    assert "resume" in view["error"].lower()


class _Context:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {}
        self.updates: list[dict[str, Any]] = []
        self.events: list[tuple[str, dict[str, Any]]] = []

    def update(self, **changes: Any) -> dict[str, Any]:
        self.state.update(changes)
        self.updates.append(dict(changes))
        return dict(self.state)

    def emit(self, event: str, data: dict[str, Any] | None = None) -> None:
        self.events.append((event, data or {}))

    def cancel_requested(self) -> bool:
        return False


def test_common_import_marks_catalogue_ready_before_overviews(monkeypatch):
    context = _Context()
    preview = {"sources": [{"source_id": "source01"}], "packages": [{"package_id": "pkg01"}]}
    report = {"total": 1, "added": 1, "updated": 0, "missing": 0, "blocked": 0, "cancelled": False}

    def fake_catalogue(project_id, snapshot, decisions, job, **kwargs):
        job.update(done=1, added=1)
        kwargs["progress_callback"](job)
        return report, [("scene01", "scene.tif")], [("scene01", "scene.tif")]

    overview_states: list[tuple[bool, str]] = []
    monkeypatch.setattr(si, "_catalogue_scenes", fake_catalogue)
    monkeypatch.setattr(si, "compute_scene_package_identity", lambda *args, **kwargs: None)
    monkeypatch.setattr(si, "rebuild_scenes_index", lambda *args, **kwargs: None)
    monkeypatch.setattr(si, "load_json", lambda *args, **kwargs: {})
    monkeypatch.setattr(si, "save_json", lambda *args, **kwargs: None)
    def fake_overview(*args):
        overview_states.append(
            (bool(context.state.get("catalogue_ready")), str(context.state.get("import_phase")))
        )
        return {"scene_id": "scene01", "status": "ready", "wall_seconds": 0.01}

    monkeypatch.setattr(si, "_build_scene_display_overviews", fake_overview)

    result = si.run_scene_import_common_job(
        {
            "project_id": "project01",
            "job_id": "job01",
            "payload": {"preview": preview, "decisions": [], "resume": False},
        },
        context,
    )

    assert overview_states == [(True, "overviews")]
    assert context.state["catalogue_ready"] is True
    assert context.state["import_phase"] == "complete"
    assert context.state["identity_done"] == 1
    assert context.state["overviews_done"] == 1
    assert result["catalogue_ready"] is True


def test_on_demand_import_defers_all_overviews(monkeypatch):
    context = _Context()
    preview = {"sources": [{"source_id": "source01"}], "packages": [{"package_id": "pkg01"}]}
    report = {"total": 1, "added": 1, "updated": 0, "missing": 0, "blocked": 0, "cancelled": False}

    monkeypatch.setattr(
        si,
        "_catalogue_scenes",
        lambda *args, **kwargs: (report, [], [("scene01", "scene.tif")]),
    )
    monkeypatch.setattr(si, "rebuild_scenes_index", lambda *args, **kwargs: None)
    monkeypatch.setattr(si, "load_json", lambda *args, **kwargs: {})
    monkeypatch.setattr(si, "save_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        si,
        "_build_scene_display_overviews",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("overview must be deferred")),
    )

    result = si.run_scene_import_common_job(
        {
            "project_id": "project01",
            "job_id": "job01",
            "payload": {
                "preview": preview,
                "decisions": [],
                "resume": False,
                "import_mode": "on_demand",
            },
        },
        context,
    )

    assert result["overviews_total"] == 1
    assert result["overviews_done"] == 0
    assert result["overviews_deferred"] is True
    assert context.state["catalogue_ready"] is True
    assert context.state["import_phase"] == "complete"


def test_prepare_all_keeps_creation_pending_until_overviews_finish(monkeypatch):
    context = _Context()
    preview = {"sources": [{"source_id": "source01"}], "packages": [{"package_id": "pkg01"}]}
    report = {"total": 1, "added": 1, "updated": 0, "missing": 0, "blocked": 0, "cancelled": False}
    readiness_during_build: list[bool] = []

    monkeypatch.setattr(
        si,
        "_catalogue_scenes",
        lambda *args, **kwargs: (report, [], [("scene01", "scene.tif")]),
    )
    monkeypatch.setattr(si, "rebuild_scenes_index", lambda *args, **kwargs: None)
    monkeypatch.setattr(si, "load_json", lambda *args, **kwargs: {})
    monkeypatch.setattr(si, "save_json", lambda *args, **kwargs: None)

    def build(*args, **kwargs):
        readiness_during_build.append(bool(context.state.get("catalogue_ready")))
        return {"scene_id": "scene01", "status": "ready", "overview_bytes": 1}

    monkeypatch.setattr(si, "_build_scene_display_overviews", build)
    result = si.run_scene_import_common_job(
        {
            "project_id": "project01",
            "job_id": "job01",
            "payload": {
                "preview": preview,
                "decisions": [],
                "resume": False,
                "import_mode": "prepare_all",
            },
        },
        context,
    )

    assert readiness_during_build == [False]
    assert result["overviews_done"] == 1
    assert context.state["catalogue_ready"] is True


def test_adaptive_overviews_prioritize_opened_scene(monkeypatch):
    built: list[str] = []
    monkeypatch.setattr(
        si,
        "_build_scene_display_overviews",
        lambda _project_id, scene_id, _profile: built.append(scene_id) or {
            "scene_id": scene_id,
            "status": "ready",
        },
    )

    si._run_adaptive_overviews(
        "project01",
        [("scene01", "one.tif"), ("scene02", "two.tif"), ("scene03", "three.tif")],
        profile={"workers": 1},
        cancel_check=lambda: False,
        on_started=lambda *args: None,
        on_finished=lambda *args: None,
        priority_scene_ids=lambda: ["scene03"],
    )

    assert built == ["scene03", "scene01", "scene02"]


def test_opened_scene_is_persistently_prioritized_in_active_import(monkeypatch):
    active = {
        "job": {
            "job_id": "import01",
            "payload": {"import_mode": "background"},
        },
        "state": {"status": "running", "import_phase": "overviews"},
    }
    state = {"status": "running", "overview_priority_scene_ids": ["scene02"]}
    monkeypatch.setattr(si, "_require_project", lambda *_args: None)
    monkeypatch.setattr(
        si,
        "load_scene_json",
        lambda *args, **kwargs: {"id": "scene01", "overview_status": "pending"},
    )
    monkeypatch.setattr(si, "find_active_job", lambda *args, **kwargs: active)

    def mutate(_project_id, _job_id, callback):
        callback(state)
        return dict(state)

    monkeypatch.setattr(si, "mutate_state", mutate)
    result = si.request_scene_display_overview("project01", "scene01")

    assert result["action"] == "prioritized"
    assert result["job_id"] == "import01"
    assert state["overview_priority_scene_ids"] == ["scene01", "scene02"]


def test_scene_overview_common_job_reports_one_completed_unit(monkeypatch):
    context = _Context()
    monkeypatch.setattr(si, "load_scene_json", lambda *args, **kwargs: {"id": "scene01"})
    monkeypatch.setattr(
        si,
        "_build_scene_display_overviews",
        lambda *args, **kwargs: {
            "scene_id": "scene01",
            "status": "ready",
            "wall_seconds": 0.25,
            "overview_bytes": 1024,
        },
    )

    result = si.run_scene_overview_common_job(
        {"project_id": "project01", "payload": {"scene_id": "scene01"}},
        context,
    )

    assert result["status"] == "ready"
    assert context.state["phase"] == "scene_overview:complete"
    assert context.state["current"] == 1


def test_overview_profile_is_conservative_or_explicit(monkeypatch):
    monkeypatch.setenv("GEOTILE_OVERVIEW_WORKERS", "3")
    monkeypatch.setenv("GEOTILE_OVERVIEW_COMPRESSION", "LZW")
    profile = si._select_overview_profile(2)
    assert profile["workers"] == 2
    assert profile["requested_workers"] == 3
    assert profile["worker_selection"] == "environment_override"
    assert profile["compression"] == "LZW"


def test_common_scene_preparation_activates_result(monkeypatch):
    context = _Context()
    activated: list[tuple[str, str, dict[str, Any]]] = []
    monkeypatch.setattr(si, "load_scene_json", lambda *args, **kwargs: {"source_package": {}})
    monkeypatch.setattr(
        si,
        "prepare_pansharpened_cog",
        lambda project_id, scene_id, rgb_bands, cancel_check: {
            "variant_id": "variant01",
            "rgb_bands": rgb_bands,
        },
    )
    monkeypatch.setattr(
        si,
        "_activate_prepared_result",
        lambda project_id, scene_id, result: activated.append((project_id, scene_id, result)),
    )

    result = si.run_scene_preparation_common_job(
        {
            "project_id": "project01",
            "job_id": "job01",
            "payload": {"scene_id": "scene01", "rgb_bands": [3, 2, 1]},
        },
        context,
    )

    assert activated == [("project01", "scene01", {"variant_id": "variant01", "rgb_bands": [3, 2, 1]})]
    assert result["variant_id"] == "variant01"
    assert context.state["phase"] == "scene_preparation:complete"
    assert context.state["current"] == 3


def test_relink_marks_identity_and_overview_pending_before_resume(monkeypatch, tmp_path):
    from fastapi import BackgroundTasks
    from db.storage import load_scene_json, project_dir, save_scene_json
    from models.scene_source import SceneSourceRelinkRequest

    project_id = "p17relink01"
    source_id = "source01"
    project_dir(project_id).mkdir(parents=True, exist_ok=True)
    save_scene_json(project_id, "scene01", "scene", {
        "id": "scene01",
        "source_id": source_id,
        "source_identity_status": "complete",
        "overview_status": "ready",
    })
    save_scene_json(project_id, "scene01", "scene_manifest", {
        "source_package": {"source_id": source_id},
        "source_identity": {
            "status": "complete",
            "source_scene_uid": "scene-sha256:previous",
        },
    })
    source = {
        "source_id": source_id,
        "provider": "generic",
        "root_path": str(tmp_path),
        "enabled": True,
    }
    monkeypatch.setattr(si, "_require_project", lambda _project_id: None)
    monkeypatch.setattr(si, "load_scene_sources", lambda _project_id: {"sources": [source]})
    monkeypatch.setattr(si, "save_scene_sources", lambda *args, **kwargs: None)
    monkeypatch.setattr(si, "_regenerate_project_vrts", lambda *args, **kwargs: None)
    monkeypatch.setattr(si, "clear_all_scene_display_overviews", lambda *args, **kwargs: None)
    monkeypatch.setattr(si, "_common_scene_import_jobs_enabled", lambda: True)
    monkeypatch.setattr(
        si,
        "_submit_scene_import_job",
        lambda *args, **kwargs: {"job": {"job_id": "resume01"}},
    )

    result = si.relink_project_scene_source(
        project_id,
        source_id,
        SceneSourceRelinkRequest(root_path=str(tmp_path)),
        BackgroundTasks(),
    )

    manifest = load_scene_json(project_id, "scene01", "scene_manifest", default={})
    scene = load_scene_json(project_id, "scene01", "scene", default={})
    assert manifest["source_identity"]["status"] == "pending"
    assert manifest["source_identity"]["source_scene_uid"] == "scene-sha256:previous"
    assert scene["source_identity_status"] == "pending"
    assert scene["overview_status"] == "pending"
    assert result["import_job_id"] == "resume01"
