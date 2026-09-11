"""Persistent priority scheduler shared by all project job types."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models.job import (
    ACTIVE_JOB_STATUSES,
    PRIORITY_VALUES,
    TERMINAL_JOB_STATUSES,
    JobCreateRequest,
    JobSpec,
    JobStatus,
    JobType,
)
from services.jobs.events import append_event
from services.jobs.processes import capture_process_identity, process_status, terminate_process_tree
from services.jobs.resources import acquire_resource, release_resource, update_resource_holder
from services.jobs.store import (
    JobStoreError,
    claim_job,
    create_job,
    create_job_id,
    find_active_job,
    interprocess_lock,
    job_dir,
    jobs_dir,
    list_all_jobs,
    mutate_state,
    patch_state,
    read_spec,
    read_state,
    runtime_dir,
    utc_now,
    write_json_atomic,
)
from services.worker_env import build_worker_env


SUPPORTED_JOB_TYPES = {item.value for item in JobType}
HEARTBEAT_STALE_SECONDS = 120.0


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


def _heartbeat_stale(state: dict[str, Any]) -> bool:
    stamp = _parse_time(state.get("heartbeat") or state.get("updated_at") or state.get("created_at"))
    if stamp is None:
        return True
    return (datetime.now(timezone.utc) - stamp).total_seconds() > HEARTBEAT_STALE_SECONDS


def _mirror_training_terminal(spec: dict[str, Any], state: dict[str, Any]) -> None:
    if spec.get("job_type") != JobType.TRAINING.value:
        return
    run_dir_value = (spec.get("payload") or {}).get("run_dir")
    if not run_dir_value:
        return
    path = Path(run_dir_value) / "job_state.json"
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        current = {}
    status = str(state.get("status") or "interrupted")
    value = {
        **current,
        "status": status,
        "ended_at": state.get("ended_at") or utc_now(),
        "error": state.get("error"),
    }
    write_json_atomic(path, value)


def submit_job(
    project_id: str,
    request: JobCreateRequest,
    *,
    desired_job_id: str | None = None,
    retry_of: str | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    if request.job_type.value not in SUPPORTED_JOB_TYPES:
        raise JobStoreError(f"Unsupported job type: {request.job_type.value}")
    submit_lock = jobs_dir(project_id) / ".submit.lock"
    with interprocess_lock(submit_lock) as acquired:
        if not acquired:
            raise TimeoutError("Could not reserve job submission")
        if request.dedupe_key:
            active = find_active_job(
                project_id,
                job_type=request.job_type.value,
                dedupe_key=request.dedupe_key,
            )
            if active:
                raise JobStoreError(
                    f"Job '{active['job']['job_id']}' is already active for {request.dedupe_key}"
                )
        job_id = desired_job_id or create_job_id()
        created_at = utc_now()
        spec = JobSpec(
            job_id=job_id,
            job_type=request.job_type,
            project_id=project_id,
            resource_class=request.resource_class,
            priority_class=request.priority_class,
            priority=PRIORITY_VALUES[request.priority_class],
            payload=request.payload,
            dedupe_key=request.dedupe_key,
            retry_of=retry_of,
            attempt=max(1, int(attempt)),
            created_at=created_at,
        )
        spec_data, state = create_job(spec)
        append_event(project_id, job_id, "queued", {"status": "queued"})
    get_scheduler().wake()
    return {"job": spec_data, "state": state}


def _spawn_worker(project_id: str, job_id: str) -> dict[str, Any]:
    worker = Path(__file__).resolve().parents[2] / "job_worker.py"
    directory = job_dir(project_id, job_id)
    stdout = (directory / "worker.stdout.log").open("ab")
    stderr = (directory / "worker.stderr.log").open("ab")
    try:
        process = subprocess.Popen(
            [sys.executable, str(worker), project_id, job_id],
            cwd=str(worker.parent),
            env=build_worker_env(),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
        )
    finally:
        stdout.close()
        stderr.close()
    identity = None
    for _ in range(20):
        identity = capture_process_identity(process.pid)
        if identity is not None:
            break
        time.sleep(0.01)
    if identity is None:
        try:
            process.kill()
        except Exception:
            pass
        raise RuntimeError(f"Could not capture worker identity for PID {process.pid}")
    return identity


def _try_dispatch(item: dict[str, Any]) -> bool:
    spec = item["job"]
    project_id, job_id = spec["project_id"], spec["job_id"]
    with claim_job(project_id, job_id) as claimed:
        if not claimed:
            return False
        state = read_state(project_id, job_id)
        if state.get("status") != JobStatus.QUEUED.value:
            return False
        lease = acquire_resource(project_id, job_id, str(spec["resource_class"]))
        if lease is None:
            return False
        try:
            starting = mutate_state(
                project_id,
                job_id,
                lambda current: (
                    {
                        **current,
                        "status": JobStatus.STARTING.value,
                        "phase": "starting",
                        "heartbeat": utc_now(),
                    }
                    if current.get("status") == JobStatus.QUEUED.value
                    and not current.get("cancel_requested")
                    else current
                ),
            )
            if starting.get("status") != JobStatus.STARTING.value:
                release_resource(job_id)
                return False
            append_event(project_id, job_id, "starting", {"resource": spec["resource_class"]})
            identity = _spawn_worker(project_id, job_id)

            def attach_process(current: dict[str, Any]) -> dict[str, Any]:
                if current.get("status") in TERMINAL_JOB_STATUSES:
                    return current
                current["process"] = identity
                current["heartbeat"] = utc_now()
                return current

            mutate_state(project_id, job_id, attach_process)
            update_resource_holder(job_id, identity)
            return True
        except Exception as exc:
            release_resource(job_id)
            failed = patch_state(
                project_id,
                job_id,
                status=JobStatus.FAILED.value,
                phase="failed",
                error=f"Could not start job worker: {exc}",
                ended_at=utc_now(),
            )
            append_event(project_id, job_id, "error", {"error": failed["error"]})
            _mirror_training_terminal(spec, failed)
            return False


def recover_jobs() -> dict[str, int]:
    report = {"live": 0, "interrupted": 0, "cancelled": 0, "unknown": 0}
    for item in list_all_jobs():
        spec, state = item["job"], item["state"]
        status = str(state.get("status") or "")
        if status not in {"starting", "running", "cancelling"}:
            continue
        identity_status = process_status(state.get("process"))
        if identity_status == "live":
            report["live"] += 1
            continue
        if identity_status == "unknown" and not _heartbeat_stale(state):
            report["unknown"] += 1
            continue
        cancelled = bool(state.get("cancel_requested")) or status == "cancelling"
        terminal = JobStatus.CANCELLED.value if cancelled else JobStatus.INTERRUPTED.value
        error = None if cancelled else (
            "The worker disappeared or its identity could not be confirmed after a stale heartbeat."
        )
        updated = patch_state(
            spec["project_id"],
            spec["job_id"],
            status=terminal,
            phase=terminal,
            error=error,
            ended_at=utc_now(),
        )
        # An unknown process may still own GPU memory. Preserve its lease until exact
        # identity becomes absent/recycled; this avoids unsafe overcommit.
        if identity_status in {"absent", "recycled"}:
            release_resource(spec["job_id"])
        append_event(
            spec["project_id"],
            spec["job_id"],
            "cancelled" if cancelled else "error",
            {"status": terminal, "error": error},
        )
        _mirror_training_terminal(spec, updated)
        report["cancelled" if cancelled else "interrupted"] += 1
    return report


def cancel_job(project_id: str, job_id: str) -> dict[str, Any]:
    spec = read_spec(project_id, job_id)

    def request_cancel(state: dict[str, Any]) -> dict[str, Any]:
        if state.get("status") in TERMINAL_JOB_STATUSES:
            return state
        state["cancel_requested"] = True
        if state.get("status") == JobStatus.QUEUED.value:
            state["status"] = JobStatus.CANCELLED.value
            state["phase"] = "cancelled"
            state["ended_at"] = utc_now()
        else:
            state["status"] = JobStatus.CANCELLING.value
            state["phase"] = "cancelling"
        return state

    state = mutate_state(project_id, job_id, request_cancel)
    if state.get("status") == JobStatus.CANCELLED.value:
        release_resource(job_id)
        append_event(project_id, job_id, "cancelled", {"status": "cancelled"})
        _mirror_training_terminal(spec, state)
        return state
    if state.get("status") in TERMINAL_JOB_STATUSES:
        return state

    identity = state.get("process")
    result = terminate_process_tree(identity) if identity else {"status": "unknown"}
    after = process_status(identity)
    if after in {"absent", "recycled"} or result.get("status") == "terminated":
        state = patch_state(
            project_id,
            job_id,
            status=JobStatus.CANCELLED.value,
            phase="cancelled",
            ended_at=utc_now(),
            error=None,
        )
        release_resource(job_id)
        append_event(
            project_id,
            job_id,
            "cancelled",
            {"status": "cancelled", "process": result},
        )
        _mirror_training_terminal(spec, state)
    else:
        state = patch_state(
            project_id,
            job_id,
            error="Cancellation requested, but exact process termination is not yet confirmed.",
        )
        append_event(project_id, job_id, "cancelling", {"process": result})
    return state


def _prepare_training_retry(spec: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    from services.training_runs import (
        create_training_run_id,
        next_attempt,
        read_json,
        training_run_dir,
        write_job_state,
        write_json_atomic as write_training_json,
    )

    old_dir = Path((spec.get("payload") or {}).get("run_dir") or "")
    old_job = read_json(old_dir / "job.json", default={}) or {}
    fingerprint = str(old_job.get("config_fingerprint") or "retry")
    run_id = create_training_run_id(fingerprint)
    run_dir = training_run_dir(spec["project_id"], run_id)
    run_dir.mkdir(parents=True, exist_ok=False)
    attempt = next_attempt(spec["project_id"], fingerprint)
    created_at = utc_now()
    job = {
        **old_job,
        "training_run_id": run_id,
        "attempt": attempt,
        "created_at": created_at,
        "retry_of": old_job.get("training_run_id"),
    }
    write_training_json(run_dir / "job.json", job)
    write_job_state(
        spec["project_id"],
        run_id,
        {
            **{
                key: job.get(key)
                for key in (
                    "config_fingerprint",
                    "attempt",
                    "dataset_run_id",
                    "base_model",
                    "task",
                    "device",
                    "epochs_total",
                )
            },
            "status": "queued",
            "created_at": created_at,
            "epochs_done": 0,
        },
    )
    return run_id, {"run_dir": str(run_dir)}


def retry_job(project_id: str, job_id: str) -> dict[str, Any]:
    spec = read_spec(project_id, job_id)
    state = read_state(project_id, job_id)
    if state.get("status") not in TERMINAL_JOB_STATUSES:
        raise JobStoreError("Only a terminal job can be retried")
    desired_id = None
    payload = dict(spec.get("payload") or {})
    if spec.get("job_type") == JobType.TRAINING.value:
        desired_id, payload = _prepare_training_retry(spec)
    elif spec.get("job_type") == JobType.SCENE_IMPORT.value:
        # A retry is a recovery operation, not a replay of an expired preview snapshot.
        # Rescan current enabled sources and skip scene phases already persisted as complete.
        payload = {"resume": True, "decisions": [], "import_total": 0}
    request = JobCreateRequest(
        job_type=spec["job_type"],
        resource_class=spec["resource_class"],
        priority_class=spec["priority_class"],
        payload=payload,
        dedupe_key=spec.get("dedupe_key"),
    )
    return submit_job(
        project_id,
        request,
        desired_job_id=desired_id,
        retry_of=job_id,
        attempt=int(spec.get("attempt") or 1) + 1,
    )


class JobScheduler:
    def __init__(self, poll_interval_s: float = 0.5):
        self.poll_interval_s = poll_interval_s
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="geotile-job-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def wake(self) -> None:
        self.start()
        self._wake.set()

    def dispatch_once(self) -> int:
        queued = [
            item
            for item in list_all_jobs()
            if item["state"].get("status") == JobStatus.QUEUED.value
        ]
        queued.sort(
            key=lambda item: (
                -int(item["job"].get("priority") or 0),
                str(item["job"].get("created_at") or ""),
            )
        )
        started = 0
        for item in queued:
            if _try_dispatch(item):
                started += 1
        return started

    def _run(self) -> None:
        recover_jobs()
        last_recovery = time.monotonic()
        while not self._stop.is_set():
            try:
                self.dispatch_once()
                if time.monotonic() - last_recovery >= 10.0:
                    recover_jobs()
                    last_recovery = time.monotonic()
            except Exception:
                # Scheduler must stay alive; individual errors are persisted by dispatch.
                pass
            self._wake.wait(self.poll_interval_s)
            self._wake.clear()


_SCHEDULER = JobScheduler()


def get_scheduler() -> JobScheduler:
    return _SCHEDULER
