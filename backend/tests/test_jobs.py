"""P1.1 contract tests for the durable JSON Job Manager."""

from __future__ import annotations

import json
import asyncio
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db import storage  # noqa: E402
from models.job import (  # noqa: E402
    PRIORITY_VALUES,
    JobSpec,
    JobStatus,
    JobType,
    PriorityClass,
    ResourceClass,
)
from services.jobs import scheduler, store as job_store  # noqa: E402
from services.jobs.events import append_event, read_events, stream_job_events  # noqa: E402
from services.jobs.processes import (  # noqa: E402
    capture_process_identity,
    process_status,
)
from services.jobs.resources import (  # noqa: E402
    acquire_resource,
    release_resource,
    update_resource_holder,
)
from services.jobs.store import (  # noqa: E402
    create_job,
    job_dir,
    list_jobs,
    mutate_state,
    read_spec,
    read_state,
    utc_now,
)


def _project(temp_root: pathlib.Path, project_id: str = "project") -> str:
    storage.DATA_DIR = temp_root
    root = temp_root / "projects" / project_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.json").write_text(
        json.dumps({"id": project_id, "name": project_id}), encoding="utf-8"
    )
    return project_id


def _create(
    project_id: str,
    job_id: str,
    resource: ResourceClass = ResourceClass.CPU_HEAVY,
    priority: PriorityClass = PriorityClass.USER_BACKGROUND,
) -> None:
    now = utc_now()
    create_job(
        JobSpec(
            job_id=job_id,
            job_type=JobType.SCENE_INFERENCE,
            project_id=project_id,
            resource_class=resource,
            priority_class=priority,
            priority=PRIORITY_VALUES[priority],
            payload={"scene_id": "scene"},
            created_at=now,
        )
    )


def test_state_mutations_are_atomic_under_threads():
    with tempfile.TemporaryDirectory(prefix="geotile-jobs-atomic-") as name:
        project_id = _project(pathlib.Path(name))
        _create(project_id, "atomic")

        def increment() -> None:
            mutate_state(
                project_id,
                "atomic",
                lambda state: {**state, "counter": int(state.get("counter") or 0) + 1},
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _index: increment(), range(40)))

        state = read_state(project_id, "atomic")
        assert state["counter"] == 40
        assert state["revision"] == 41
        json.loads((job_dir(project_id, "atomic") / "state.json").read_text(encoding="utf-8"))
        assert not list(job_dir(project_id, "atomic").glob("*.tmp"))


def test_job_spec_is_separate_and_immutable_during_state_updates():
    with tempfile.TemporaryDirectory(prefix="geotile-jobs-spec-") as name:
        project_id = _project(pathlib.Path(name))
        _create(project_id, "immutable")
        before = (job_dir(project_id, "immutable") / "job.json").read_bytes()
        mutate_state(project_id, "immutable", lambda state: {**state, "phase": "work"})
        after = (job_dir(project_id, "immutable") / "job.json").read_bytes()
        assert before == after
        assert read_spec(project_id, "immutable")["job_id"] == "immutable"


def test_read_state_retries_a_transient_windows_visibility_gap(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="geotile-jobs-read-retry-") as name:
        project_id = _project(pathlib.Path(name))
        _create(project_id, "read-retry")
        original = job_store.read_json
        attempts = 0

        def transient_read(path, default=None):
            nonlocal attempts
            if path.name == "state.json" and attempts < 2:
                attempts += 1
                return default
            return original(path, default)

        monkeypatch.setattr(job_store, "read_json", transient_read)
        monkeypatch.setattr(job_store.time, "sleep", lambda _seconds: None)

        state = job_store.read_state(project_id, "read-retry")

        assert attempts == 2
        assert state["job_id"] == "read-retry"


def test_event_sequences_are_ordered_and_persistent_under_concurrency():
    with tempfile.TemporaryDirectory(prefix="geotile-jobs-events-") as name:
        project_id = _project(pathlib.Path(name))
        _create(project_id, "events")
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda index: append_event(project_id, "events", "progress", {"i": index}), range(30)))
        events = read_events(project_id, "events")
        assert [item["seq"] for item in events] == list(range(1, 31))
        assert len({item["data"]["i"] for item in events}) == 30


def test_disconnecting_event_observer_does_not_cancel_job_and_reconnect_resumes():
    with tempfile.TemporaryDirectory(prefix="geotile-jobs-reconnect-") as name:
        project_id = _project(pathlib.Path(name))
        _create(project_id, "reconnect")
        mutate_state(
            project_id,
            "reconnect",
            lambda state: {**state, "status": JobStatus.RUNNING.value},
        )
        first = append_event(project_id, "reconnect", "progress", {"done": 1, "total": 2})

        async def disconnect() -> None:
            observer = stream_job_events(project_id, "reconnect", after=0)
            item = await anext(observer)
            assert item["event"] == "progress"
            await observer.aclose()

        asyncio.run(disconnect())
        state = read_state(project_id, "reconnect")
        assert state["status"] == JobStatus.RUNNING.value
        assert state["cancel_requested"] is False

        append_event(project_id, "reconnect", "complete", {"result": "ok"})
        mutate_state(
            project_id,
            "reconnect",
            lambda current: {**current, "status": JobStatus.COMPLETED.value},
        )

        async def reconnect() -> None:
            observer = stream_job_events(project_id, "reconnect", after=first["seq"])
            item = await anext(observer)
            assert item["event"] == "complete"
            await observer.aclose()

        asyncio.run(reconnect())


def test_gpu_exclusive_lease_allows_only_one_job():
    with tempfile.TemporaryDirectory(prefix="geotile-jobs-gpu-") as name:
        project_id = _project(pathlib.Path(name))
        first = acquire_resource(project_id, "gpu-a", ResourceClass.GPU_EXCLUSIVE.value)
        second = acquire_resource(project_id, "gpu-b", ResourceClass.GPU_EXCLUSIVE.value)
        assert first is not None
        assert second is None
        assert release_resource("gpu-a")
        assert acquire_resource(project_id, "gpu-b", ResourceClass.GPU_EXCLUSIVE.value)
        release_resource("gpu-b")


def test_metadata_scan_does_not_wait_for_heavy_io_lease():
    with tempfile.TemporaryDirectory(prefix="geotile-jobs-io-metadata-") as name:
        project_id = _project(pathlib.Path(name))
        assert acquire_resource(project_id, "overview", ResourceClass.IO_HEAVY.value)
        assert acquire_resource(project_id, "scan", ResourceClass.IO_METADATA.value)
        assert acquire_resource(project_id, "second-scan", ResourceClass.IO_METADATA.value) is None
        release_resource("overview")
        release_resource("scan")


def test_scheduler_dispatches_only_one_gpu_job(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="geotile-jobs-dispatch-") as name:
        project_id = _project(pathlib.Path(name))
        _create(project_id, "gpu-first", ResourceClass.GPU_EXCLUSIVE)
        _create(project_id, "gpu-second", ResourceClass.GPU_EXCLUSIVE)
        identity = capture_process_identity()
        assert identity is not None
        monkeypatch.setattr(scheduler, "_spawn_worker", lambda _project, _job: identity)
        instance = scheduler.JobScheduler()
        assert instance.dispatch_once() == 1
        statuses = {item["job"]["job_id"]: item["state"]["status"] for item in list_jobs(project_id)}
        assert sorted(statuses.values()) == ["queued", "starting"]
        release_resource("gpu-first")
        release_resource("gpu-second")


def test_scheduler_prefers_interactive_job_over_older_maintenance_job(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="geotile-jobs-priority-") as name:
        project_id = _project(pathlib.Path(name))
        _create(
            project_id,
            "maintenance-first",
            ResourceClass.GPU_EXCLUSIVE,
            PriorityClass.MAINTENANCE,
        )
        _create(
            project_id,
            "interactive-second",
            ResourceClass.GPU_EXCLUSIVE,
            PriorityClass.INTERACTIVE,
        )
        identity = capture_process_identity()
        assert identity is not None
        started: list[str] = []

        def record_spawn(_project: str, job_id: str):
            started.append(job_id)
            return identity

        monkeypatch.setattr(scheduler, "_spawn_worker", record_spawn)
        instance = scheduler.JobScheduler()
        assert instance.dispatch_once() == 1
        assert started == ["interactive-second"]
        assert read_state(project_id, "interactive-second")["status"] == "starting"
        assert read_state(project_id, "maintenance-first")["status"] == "queued"
        release_resource("interactive-second")


def test_training_dataset_and_inference_share_worker_contract():
    from job_worker import HANDLERS

    assert {
        JobType.TRAINING.value,
        JobType.DATASET_BUILD.value,
        JobType.SCENE_SCAN.value,
        JobType.SCENE_IMPORT.value,
        JobType.SCENE_PREPARATION.value,
        JobType.SCENE_OVERVIEW.value,
        JobType.SCENE_INFERENCE.value,
        JobType.DATASET_EXPORT.value,
        JobType.PROJECT_BACKUP.value,
        JobType.ARTIFACT_CLEANUP.value,
    }.issubset(HANDLERS)


def test_restart_reclassifies_dead_running_job_as_interrupted():
    with tempfile.TemporaryDirectory(prefix="geotile-jobs-recovery-") as name:
        project_id = _project(pathlib.Path(name))
        _create(project_id, "dead")
        mutate_state(
            project_id,
            "dead",
            lambda state: {
                **state,
                "status": JobStatus.RUNNING.value,
                "process": {
                    "pid": 2_000_000_000,
                    "marker_kind": "psutil_create_time_ns",
                    "start_marker": 1,
                },
                "heartbeat": "2000-01-01T00:00:00+00:00",
            },
        )
        report = scheduler.recover_jobs()
        assert report["interrupted"] == 1
        assert read_state(project_id, "dead")["status"] == JobStatus.INTERRUPTED.value


def test_cancel_terminates_exact_process_tree_and_releases_resource():
    with tempfile.TemporaryDirectory(prefix="geotile-jobs-cancel-") as name:
        project_id = _project(pathlib.Path(name))
        _create(project_id, "cancel-tree", ResourceClass.GPU_EXCLUSIVE)
        code = (
            "import subprocess,sys,time; "
            "c=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "print(c.pid, flush=True); time.sleep(60)"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        child_pid = int(process.stdout.readline().strip())
        identity = None
        for _ in range(50):
            identity = capture_process_identity(process.pid)
            if identity:
                break
            time.sleep(0.02)
        assert identity is not None
        assert acquire_resource(project_id, "cancel-tree", ResourceClass.GPU_EXCLUSIVE.value)
        assert update_resource_holder("cancel-tree", identity)
        mutate_state(
            project_id,
            "cancel-tree",
            lambda state: {
                **state,
                "status": JobStatus.RUNNING.value,
                "process": identity,
                "heartbeat": utc_now(),
            },
        )
        try:
            state = scheduler.cancel_job(project_id, "cancel-tree")
            assert state["status"] == JobStatus.CANCELLED.value
            assert process_status(identity) in {"absent", "recycled"}
            child_identity = capture_process_identity(child_pid)
            assert child_identity is None or process_status(child_identity) != "live"
            assert acquire_resource(project_id, "next-gpu", ResourceClass.GPU_EXCLUSIVE.value)
            release_resource("next-gpu")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
