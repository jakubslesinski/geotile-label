"""Generic durable-job worker launched by :mod:`services.jobs.scheduler`."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from models.job import TERMINAL_JOB_STATUSES, JobStatus, JobType  # noqa: E402
from services.jobs.events import append_event  # noqa: E402
from services.jobs.processes import capture_process_identity  # noqa: E402
from services.jobs.resources import release_resource, update_resource_holder  # noqa: E402
from services.jobs.store import (  # noqa: E402
    job_dir,
    mutate_state,
    read_spec,
    read_state,
    utc_now,
    write_artifacts,
    write_json_atomic,
    write_performance,
)


HEARTBEAT_INTERVAL_S = 10.0


class JobCancelled(RuntimeError):
    pass


class HandlerReportedError(RuntimeError):
    pass


class JobContext:
    def __init__(self, project_id: str, job_id: str):
        self.project_id = project_id
        self.job_id = job_id
        self._stop = threading.Event()
        self._heartbeat: threading.Thread | None = None

    def update(self, **changes: Any) -> dict[str, Any]:
        def apply(state: dict[str, Any]) -> dict[str, Any]:
            if state.get("status") in TERMINAL_JOB_STATUSES or state.get("cancel_requested"):
                return state
            state.update(changes)
            state["heartbeat"] = utc_now()
            return state

        return mutate_state(self.project_id, self.job_id, apply)

    def emit(self, event: str, data: dict[str, Any] | None = None) -> None:
        payload = data or {}
        progress: dict[str, Any] = {}
        if "done" in payload:
            progress["current"] = payload.get("done")
        if "total" in payload:
            progress["total"] = payload.get("total")
        phase = payload.get("stage") or payload.get("phase") or payload.get("current_tile")
        if phase:
            progress["phase"] = str(phase)
        if progress:
            self.update(**progress)
        append_event(self.project_id, self.job_id, event, payload)

    def cancel_requested(self) -> bool:
        try:
            return bool(read_state(self.project_id, self.job_id).get("cancel_requested"))
        except Exception:
            return False

    def start_heartbeat(self) -> None:
        def beat() -> None:
            while not self._stop.wait(HEARTBEAT_INTERVAL_S):
                try:
                    self.update()
                except Exception:
                    pass

        self._heartbeat = threading.Thread(target=beat, name="job-heartbeat", daemon=True)
        self._heartbeat.start()

    def stop_heartbeat(self) -> None:
        self._stop.set()
        if self._heartbeat and self._heartbeat.is_alive():
            self._heartbeat.join(timeout=2.0)


def _decode_stream_item(item: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(item, dict):
        event = str(item.get("event") or "message")
        data = item.get("data") or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = {"message": data}
        return event, data if isinstance(data, dict) else {"value": data}
    if isinstance(item, (bytes, bytearray)):
        text = bytes(item).decode("utf-8", errors="replace")
        event = "message"
        data: dict[str, Any] = {}
        for line in text.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                raw = line[5:].strip()
                try:
                    value = json.loads(raw)
                    data = value if isinstance(value, dict) else {"value": value}
                except json.JSONDecodeError:
                    data = {"message": raw}
        return event, data
    return "message", {"value": str(item)}


async def _consume_event_response(response: Any, context: JobContext) -> dict[str, Any]:
    completed: dict[str, Any] = {}
    async for item in response.body_iterator:
        if context.cancel_requested():
            raise JobCancelled("Cancellation requested")
        event, data = _decode_stream_item(item)
        context.emit(event, data)
        if event == "complete":
            completed = data
        elif event == "cancelled":
            raise JobCancelled(str(data.get("error") or "Job cancelled"))
        elif event == "error":
            raise HandlerReportedError(str(data.get("error") or data.get("detail") or "Job failed"))
    return completed


def _run_training(spec: dict[str, Any], context: JobContext) -> dict[str, Any]:
    import training_worker

    run_dir = Path((spec.get("payload") or {})["run_dir"])
    os.environ["GEOTILE_COMMON_JOB_PROJECT_ID"] = context.project_id
    os.environ["GEOTILE_COMMON_JOB_ID"] = context.job_id
    code = training_worker.main([str(Path(training_worker.__file__)), str(run_dir)])
    if code != 0:
        state_path = run_dir / "job_state.json"
        try:
            legacy = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            legacy = {}
        raise RuntimeError(legacy.get("error") or f"Training worker exited with code {code}")
    return {
        "training_run_id": context.job_id,
        "run_dir": str(run_dir),
        "manifest": str(run_dir / "training_manifest.json"),
    }


def _run_dataset(spec: dict[str, Any], context: JobContext) -> dict[str, Any]:
    from routers.dataset import prepare_dataset_build

    cancel = threading.Event()
    response = prepare_dataset_build(spec["project_id"], cancel, spec.get("payload") or {})
    result = asyncio.run(_consume_event_response(response, context))
    performance = result.get("performance") if isinstance(result, dict) else None
    if isinstance(performance, dict):
        result["_job_performance"] = performance
    return result


def _run_inference(spec: dict[str, Any], context: JobContext) -> dict[str, Any]:
    from routers.predictions import prepare_prediction_run

    payload = spec.get("payload") or {}
    response = prepare_prediction_run(
        spec["project_id"],
        str(payload["scene_id"]),
        payload,
    )
    result = asyncio.run(_consume_event_response(response, context))
    performance = result.get("performance") if isinstance(result, dict) else None
    if isinstance(performance, dict):
        result["_job_performance"] = performance
    return result


def _run_scene_import(spec: dict[str, Any], context: JobContext) -> dict[str, Any]:
    from routers.scene_import import SceneImportCancelled, run_scene_import_common_job

    try:
        return run_scene_import_common_job(spec, context)
    except SceneImportCancelled as exc:
        raise JobCancelled(str(exc)) from exc


def _run_scene_scan(spec: dict[str, Any], context: JobContext) -> dict[str, Any]:
    from routers.scene_import import run_scene_scan_common_job
    from services.scene_packages.scan_cache import SceneScanCancelled

    try:
        return run_scene_scan_common_job(spec, context)
    except SceneScanCancelled as exc:
        raise JobCancelled(str(exc)) from exc


def _run_scene_preparation(spec: dict[str, Any], context: JobContext) -> dict[str, Any]:
    from routers.scene_import import SceneImportCancelled, run_scene_preparation_common_job

    try:
        return run_scene_preparation_common_job(spec, context)
    except SceneImportCancelled as exc:
        raise JobCancelled(str(exc)) from exc


def _run_scene_fullres_derivative(spec: dict[str, Any], context: JobContext) -> dict[str, Any]:
    from routers.scene_import import SceneImportCancelled, run_scene_fullres_derivative_job

    try:
        return run_scene_fullres_derivative_job(spec, context)
    except SceneImportCancelled as exc:
        raise JobCancelled(str(exc)) from exc


def _run_scene_overview(spec: dict[str, Any], context: JobContext) -> dict[str, Any]:
    from routers.scene_import import SceneImportCancelled, run_scene_overview_common_job

    try:
        return run_scene_overview_common_job(spec, context)
    except SceneImportCancelled as exc:
        raise JobCancelled(str(exc)) from exc


def _run_embedding_analysis(spec: dict[str, Any], context: JobContext) -> dict[str, Any]:
    import embedding_analysis_worker

    payload = spec.get("payload") or {}
    run_dir = Path(payload["run_dir"])
    analysis_request = payload.get("analysis_request")
    if analysis_request:
        # Retries restore the immutable common-job snapshot instead of consuming
        # whichever settings happen to be visible in the legacy directory.
        write_json_atomic(run_dir / "job.json", analysis_request)
    code = embedding_analysis_worker.main(run_dir)
    if code not in {None, 0}:
        raise RuntimeError(f"Embedding analysis exited with code {code}")
    return {"run_dir": str(run_dir), "result": str(run_dir / "result.json")}


def _artifact_progress(context: JobContext):
    def report(phase: str, current: int, total: int) -> None:
        context.update(phase=phase, current=current, total=total)
        context.emit(
            "artifact_progress",
            {"phase": phase, "done": current, "total": total},
        )

    return report


def _run_dataset_export(spec: dict[str, Any], context: JobContext) -> dict[str, Any]:
    from services.artifact_exports import ArtifactExportCancelled, create_dataset_export_artifact

    payload = spec.get("payload") or {}
    try:
        return create_dataset_export_artifact(
            spec["project_id"],
            payload["dataset_dir"],
            dataset_run_id=str(payload["dataset_run_id"]),
            formats=payload.get("formats"),
            output_path=payload.get("output_path"),
            should_cancel=context.cancel_requested,
            progress=_artifact_progress(context),
        )
    except ArtifactExportCancelled as exc:
        raise JobCancelled(str(exc)) from exc


def _run_project_backup(spec: dict[str, Any], context: JobContext) -> dict[str, Any]:
    from services.artifact_exports import ArtifactExportCancelled
    from services.project_backup import ProjectBackupCancelled, create_project_backup_artifact

    try:
        return create_project_backup_artifact(
            spec["project_id"],
            artifact_id=context.job_id,
            should_cancel=context.cancel_requested,
            progress=_artifact_progress(context),
        )
    except (ProjectBackupCancelled, ArtifactExportCancelled) as exc:
        raise JobCancelled(str(exc)) from exc


def _run_artifact_cleanup(spec: dict[str, Any], context: JobContext) -> dict[str, Any]:
    from services.artifact_exports import ArtifactExportCancelled, cleanup_export_artifacts

    payload = spec.get("payload") or {}
    try:
        return cleanup_export_artifacts(
            spec["project_id"],
            cache_retention_days=int(payload.get("cache_retention_days") or 30),
            partial_retention_hours=int(payload.get("partial_retention_hours") or 24),
            should_cancel=context.cancel_requested,
        )
    except ArtifactExportCancelled as exc:
        raise JobCancelled(str(exc)) from exc


def _run_annotation_package_prepare(spec: dict[str, Any], context: JobContext) -> dict[str, Any]:
    from services.annotation_package import (
        AnnotationPackagePrepareCancelled,
        prepare_annotation_package,
    )

    # Postep jest zdarzeniem, nie tylko stanem: przy jednym rastrze 3 GB licznik scen
    # stoi w miejscu przez kilkanascie sekund, a bajty ida dalej.
    def report(update: dict[str, Any]) -> None:
        context.update(**update)
        context.emit("annotation_package_progress", update)

    try:
        return prepare_annotation_package(
            spec["project_id"],
            progress=report,
            should_cancel=context.cancel_requested,
        )
    except AnnotationPackagePrepareCancelled as exc:
        raise JobCancelled(str(exc)) from exc


HANDLERS = {
    JobType.TRAINING.value: _run_training,
    JobType.DATASET_BUILD.value: _run_dataset,
    JobType.SCENE_SCAN.value: _run_scene_scan,
    JobType.SCENE_IMPORT.value: _run_scene_import,
    JobType.SCENE_PREPARATION.value: _run_scene_preparation,
    JobType.SCENE_FULLRES_DERIVATIVE.value: _run_scene_fullres_derivative,
    JobType.SCENE_OVERVIEW.value: _run_scene_overview,
    JobType.SCENE_INFERENCE.value: _run_inference,
    JobType.EMBEDDING_ANALYSIS.value: _run_embedding_analysis,
    JobType.DATASET_EXPORT.value: _run_dataset_export,
    JobType.PROJECT_BACKUP.value: _run_project_backup,
    JobType.ARTIFACT_CLEANUP.value: _run_artifact_cleanup,
    JobType.ANNOTATION_PACKAGE_PREPARE.value: _run_annotation_package_prepare,
}


def run(project_id: str, job_id: str) -> int:
    spec = read_spec(project_id, job_id)
    context = JobContext(project_id, job_id)
    identity = capture_process_identity()
    if identity is None:
        raise RuntimeError("Could not establish exact job-worker identity")
    update_resource_holder(job_id, identity)

    state = read_state(project_id, job_id)
    if state.get("cancel_requested") or state.get("status") == JobStatus.CANCELLED.value:
        mutate_state(
            project_id,
            job_id,
            lambda current: {
                **current,
                "status": JobStatus.CANCELLED.value,
                "phase": "cancelled",
                "ended_at": utc_now(),
            },
        )
        release_resource(job_id)
        return 0

    started = time.perf_counter()
    mutate_state(
        project_id,
        job_id,
        lambda current: {
            **current,
            "status": JobStatus.RUNNING.value,
            "phase": "running",
            "process": identity,
            "heartbeat": utc_now(),
            "started_at": current.get("started_at") or utc_now(),
        },
    )
    append_event(project_id, job_id, "running", {"pid": identity["pid"]})
    context.start_heartbeat()
    handler_performance: dict[str, Any] = {}
    try:
        handler = HANDLERS.get(str(spec.get("job_type")))
        if handler is None:
            raise RuntimeError(f"No handler for job type {spec.get('job_type')}")
        artifacts = handler(spec, context) or {}
        if isinstance(artifacts, dict):
            handler_performance = artifacts.pop("_job_performance", {}) or {}
        write_artifacts(project_id, job_id, artifacts)
        final = mutate_state(
            project_id,
            job_id,
            lambda current: current
            if current.get("status") in TERMINAL_JOB_STATUSES
            else {
                **current,
                "status": JobStatus.COMPLETED.value,
                "phase": "completed",
                "artifacts": artifacts,
                "ended_at": utc_now(),
                "heartbeat": utc_now(),
            },
        )
        append_event(project_id, job_id, "job_completed", {"artifacts": artifacts})
        return 0 if final.get("status") == JobStatus.COMPLETED.value else 2
    except JobCancelled as exc:
        mutate_state(
            project_id,
            job_id,
            lambda current: {
                **current,
                "status": JobStatus.CANCELLED.value,
                "phase": "cancelled",
                "ended_at": utc_now(),
                "error": None,
            },
        )
        append_event(project_id, job_id, "cancelled", {"status": "cancelled", "detail": str(exc)})
        return 0
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        mutate_state(
            project_id,
            job_id,
            lambda current: current
            if current.get("status") in TERMINAL_JOB_STATUSES
            else {
                **current,
                "status": JobStatus.FAILED.value,
                "phase": "failed",
                "ended_at": utc_now(),
                "error": error,
            },
        )
        if not isinstance(exc, HandlerReportedError):
            append_event(project_id, job_id, "error", {"error": error})
        return 1
    finally:
        context.stop_heartbeat()
        write_performance(
            project_id,
            job_id,
            {
                "schema_name": "geotile_job_performance",
                "schema_version": 1,
                "wall_seconds": round(time.perf_counter() - started, 6),
                "finished_at": utc_now(),
                "operation": handler_performance,
            },
        )
        release_resource(job_id)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: job_worker.py <project_id> <job_id>", file=sys.stderr)
        return 2
    return run(argv[1], argv[2])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
