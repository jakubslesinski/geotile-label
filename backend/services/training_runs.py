"""Training run identity, job state and lifecycle.

Three different things are deliberately kept apart:

* ``config_fingerprint`` — deterministic hash of *what* is being trained. Identical
  configuration always yields the same value.
* ``training_run_id``    — identity of a single execution.
* ``attempt``            — which repetition of that configuration this is.

Repeating a configuration is **allowed on purpose**. Beyond resuming after a crash,
training is stochastic even with a fixed seed (non-deterministic CUDA kernels), so two
runs of the same configuration give a variance estimate — without it there is no way
to tell whether a 0.5 mAP gap between architectures is signal or noise.

The mutable job state (``job_state.json``) is separate from the immutable
``training_manifest.json``, which is written **only after** a run completes. A
half-finished run therefore never leaves a manifest behind.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from db.storage import _atomic_write_bytes, project_dir

TRAINING_RUN_SCHEMA_VERSION = 1

JobStatus = Literal["queued", "running", "completed", "cancelled", "failed", "interrupted"]

# A worker refreshes its heartbeat far more often than this. Anything older means the
# process is gone without having recorded an ending — a hard kill, a power loss or an
# application shutdown.
HEARTBEAT_STALE_SECONDS = 120
ACTIVE_TRAINING_LOCK = ".active_training.json"


class TrainingRunError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def training_runs_dir(project_id: str) -> Path:
    path = project_dir(project_id) / "training_runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def training_run_dir(project_id: str, run_id: str) -> Path:
    if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
        raise TrainingRunError(f"Invalid training run id: {run_id}")
    return training_runs_dir(project_id) / run_id


def _active_lock_path(project_id: str) -> Path:
    return training_runs_dir(project_id) / ACTIVE_TRAINING_LOCK


def acquire_training_lock(project_id: str, run_id: str) -> None:
    """Atomically reserve the project's single training worker slot."""
    lock_path = _active_lock_path(project_id)
    payload = json.dumps({"training_run_id": run_id, "created_at": utc_now()}).encode("utf-8")
    for _ in range(2):
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            return
        except FileExistsError:
            current = read_json(lock_path, default={}) or {}
            owner = current.get("training_run_id")
            if owner:
                state = resolve_job_state(project_id, str(owner))
                if state.get("status") in ("queued", "running"):
                    raise TrainingRunError(
                        f"Training run '{owner}' is already active in this project"
                    )
                if not state:
                    try:
                        created_at = datetime.fromisoformat(str(current.get("created_at")))
                        if created_at.tzinfo is None:
                            created_at = created_at.replace(tzinfo=timezone.utc)
                        age = (datetime.now(timezone.utc) - created_at).total_seconds()
                    except (TypeError, ValueError):
                        age = HEARTBEAT_STALE_SECONDS + 1
                    if age <= HEARTBEAT_STALE_SECONDS:
                        raise TrainingRunError(
                            f"Training run '{owner}' is currently starting in this project"
                        )
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
    raise TrainingRunError("Could not reserve the training worker slot")


def release_training_lock(project_id: str, run_id: str) -> None:
    """Release the slot only when it still belongs to ``run_id``."""
    lock_path = _active_lock_path(project_id)
    current = read_json(lock_path, default={}) or {}
    if current.get("training_run_id") != run_id:
        return
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


def config_fingerprint(payload: dict[str, Any]) -> str:
    """Deterministic hash of the training configuration."""
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def create_training_run_id(fingerprint: str) -> str:
    """Run id mirrors the dataset-run scheme: timestamp plus a short config hash."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}_{fingerprint[:8]}"


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json_atomic(path: Path, value: Any) -> None:
    payload = json.dumps(value, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    _atomic_write_bytes(path, payload)


def job_state_path(project_id: str, run_id: str) -> Path:
    return training_run_dir(project_id, run_id) / "job_state.json"


def manifest_path(project_id: str, run_id: str) -> Path:
    return training_run_dir(project_id, run_id) / "training_manifest.json"


def write_job_state(project_id: str, run_id: str, state: dict[str, Any]) -> None:
    write_json_atomic(job_state_path(project_id, run_id), state)


def _process_alive(pid: int | None) -> bool | None:
    """True/False when it can be determined, None when it cannot.

    ``psutil`` arrives with ultralytics, so it is used when present but never
    required — the heartbeat alone is enough to detect an abandoned run.
    """
    if not pid:
        return None
    try:
        import psutil  # type: ignore
    except Exception:
        return None
    try:
        return psutil.pid_exists(int(pid))
    except Exception:
        return None


def _heartbeat_age_seconds(state: dict[str, Any]) -> float | None:
    beat = state.get("heartbeat")
    if not beat:
        return None
    try:
        stamp = datetime.fromisoformat(str(beat))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamp).total_seconds()


def resolve_job_state(project_id: str, run_id: str) -> dict[str, Any]:
    """Job state with abandoned runs reclassified as ``interrupted``.

    A worker killed from outside never gets to write an ending, so a stored state of
    ``running`` cannot be trusted on its own. A run whose process is gone — or whose
    heartbeat went stale — is reported as ``interrupted`` instead of hanging in
    ``running`` forever.
    """
    state = read_json(job_state_path(project_id, run_id), default={}) or {}
    if state.get("status") not in ("queued", "running"):
        return state

    alive = _process_alive(state.get("pid"))
    age = _heartbeat_age_seconds(state)
    # A live process is trusted even across a long epoch that has not beaten in a while —
    # a stale heartbeat alone must not kill a run that is demonstrably still running. Only
    # when the process is gone (or cannot be checked and the heartbeat is stale) is the run
    # treated as abandoned.
    if alive is True:
        abandoned = False
    elif alive is False:
        abandoned = True
    else:  # alive is None — psutil unavailable or pid unknown; fall back to the heartbeat
        abandoned = age is not None and age > HEARTBEAT_STALE_SECONDS
    if not abandoned:
        return state

    reclassified = {
        **state,
        "status": "interrupted",
        "ended_at": state.get("ended_at") or utc_now(),
        "error": state.get("error")
        or "The training process ended without recording a result "
        "(application closed, machine restarted, or the process was killed).",
    }
    write_job_state(project_id, run_id, reclassified)
    return reclassified


def next_attempt(project_id: str, fingerprint: str) -> int:
    """How many times this configuration has been run before, plus one."""
    return len(runs_for_fingerprint(project_id, fingerprint)) + 1


def runs_for_fingerprint(project_id: str, fingerprint: str) -> list[str]:
    result = []
    for candidate in training_runs_dir(project_id).iterdir():
        if not candidate.is_dir():
            continue
        state = read_json(candidate / "job_state.json", default={}) or {}
        if state.get("config_fingerprint") == fingerprint:
            result.append(candidate.name)
    return sorted(result)


def training_run_summary(project_id: str, run_id: str) -> dict[str, Any]:
    state = resolve_job_state(project_id, run_id)
    manifest = read_json(manifest_path(project_id, run_id), default={}) or {}
    metrics = read_json(training_run_dir(project_id, run_id) / "metrics.json", default={}) or {}
    return {
        "training_run_id": run_id,
        "config_fingerprint": state.get("config_fingerprint"),
        "attempt": state.get("attempt"),
        "status": state.get("status", "unknown"),
        "dataset_run_id": state.get("dataset_run_id"),
        "base_model": state.get("base_model"),
        "task": state.get("task"),
        "device": state.get("device"),
        "epochs_total": state.get("epochs_total"),
        "epochs_done": state.get("epochs_done", 0),
        "started_at": state.get("started_at"),
        "ended_at": state.get("ended_at"),
        "error": state.get("error"),
        "has_manifest": bool(manifest),
        "metrics": metrics.get("summary") if isinstance(metrics, dict) else None,
        # Per-class {className: {precision, recall, mAP50, mAP50-95}} for the per-class radar.
        "metrics_per_class": metrics.get("per_class") if isinstance(metrics, dict) else None,
    }


def list_training_runs(project_id: str, dataset_run_id: str | None = None) -> dict[str, Any]:
    runs = []
    for candidate in sorted(training_runs_dir(project_id).iterdir(), reverse=True):
        if not candidate.is_dir():
            continue
        summary = training_run_summary(project_id, candidate.name)
        if dataset_run_id and summary.get("dataset_run_id") != dataset_run_id:
            continue
        runs.append(summary)
    return {
        "schema_name": "geotile_training_runs_index",
        "schema_version": TRAINING_RUN_SCHEMA_VERSION,
        "project_id": project_id,
        "dataset_run_id": dataset_run_id,
        "run_count": len(runs),
        "runs": runs,
    }


def delete_training_run(project_id: str, run_id: str) -> bool:
    """Remove a training run directory (weights, logs, manifest). Pure filesystem.

    Model-registry cleanup and dependency guards are the caller's responsibility.
    Returns True if the directory existed.
    """
    run_dir = training_run_dir(project_id, run_id)
    existed = run_dir.exists()
    if existed:
        shutil.rmtree(run_dir)
    return existed


def cancel_training_run(project_id: str, run_id: str) -> dict[str, Any]:
    """Stop a running worker.

    Killing the process is what actually frees GPU memory: an in-process cancel flag
    would neither interrupt ultralytics mid-epoch nor release the CUDA context.
    """
    state = resolve_job_state(project_id, run_id)
    if state.get("status") not in ("queued", "running"):
        return state

    pid = state.get("pid")
    killed = False
    if pid:
        try:
            import psutil  # type: ignore

            process = psutil.Process(int(pid))
            process.terminate()
            try:
                process.wait(timeout=15)
            except Exception:
                process.kill()
            killed = True
        except Exception:
            try:
                os.kill(int(pid), 9)
                killed = True
            except Exception:
                killed = False

    cancelled = {
        **state,
        "status": "cancelled",
        "ended_at": utc_now(),
        "error": None if killed else "Could not confirm the training process was stopped",
    }
    write_job_state(project_id, run_id, cancelled)
    release_training_lock(project_id, run_id)
    return cancelled
