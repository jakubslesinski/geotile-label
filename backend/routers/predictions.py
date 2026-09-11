"""YOLO prediction — model management, execution (SSE), predictions CRUD."""

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from db.storage import (
    load_json, save_json, load_scene_json, mutate_scene_json, save_scene_json,
    project_exists, SCENES_ROOT,
)
from models.assistance import AssistanceProposal
from models.job import JobCreateRequest, JobType, PriorityClass, ResourceClass
from models.prediction import PredictionConfig
from services.assistance_sessions import (
    clear_sessions,
    create_session,
    get_active_session,
    model_sha256,
    preprocessing_hash,
    write_session,
)
from services.attribute_engine import enrich_source_annotation
from services.predictor import (
    MODELS_ROOT,
    PredictionCancelled,
    execute_prediction,
    inspect_yolo_model,
    resolve_device,
    scan_models_dir,
)
from services.scene_raster_resolver import resolve_scene_raster
from services.async_bridge import (
    WorkerCancelled,
    next_generator_in_threadpool,
    run_blocking,
)
from services.jobs.events import stream_job_events
from services.jobs.scheduler import cancel_job, submit_job
from services.jobs.store import JobStoreError, find_active_job
from utils.browse_roots import build_breadcrumbs, get_model_browse_roots, resolve_path_in_roots

router = APIRouter()

MODEL_EXTENSIONS = {".pt"}
YOLO_ENABLED = os.environ.get("GEOTILE_ENABLE_YOLO", "1") == "1"
ACTIVE_PREDICTIONS: dict[str, threading.Event] = {}
ACTIVE_PREDICTIONS_LOCK = threading.Lock()


# --- Models ---


class ModelInspectRequest(BaseModel):
    model_path: str


def _resolve_model_path(model_path: str) -> Path | None:
    resolved_model_path = Path(model_path) if model_path and Path(model_path).exists() else None
    if not resolved_model_path and model_path:
        resolved_model_path, _root = resolve_path_in_roots(model_path, get_model_browse_roots())
    if resolved_model_path and resolved_model_path.exists():
        return resolved_model_path
    return None

@router.get("/models")
def list_models(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    if not YOLO_ENABLED:
        return {
            "models_dir": [],
            "models_dir_path": str(MODELS_ROOT),
            "enabled": False,
            "detail": "YOLO prediction is not included in this desktop build",
        }
    models = scan_models_dir()
    return {
        "models_dir": models,
        "models_dir_path": str(MODELS_ROOT),
        "enabled": True,
    }


@router.post("/models/browse")
def browse_models(project_id: str, body: dict):
    """Browse filesystem for .pt files under configured model roots."""
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    if not YOLO_ENABLED:
        raise HTTPException(503, "YOLO prediction is not included in this desktop build")

    roots = get_model_browse_roots()
    if not roots:
        raise HTTPException(500, "No model browse roots configured")

    browse_path = body.get("path", "")
    if not browse_path:
        return {
            "path": "",
            "parent_path": "",
            "breadcrumbs": [],
            "dirs": [{"name": root.label, "path": str(root.path), "is_root": True} for root in roots],
            "files": [],
        }

    target, root = resolve_path_in_roots(browse_path, roots)
    if not target or not root:
        raise HTTPException(400, "Path outside configured model roots")
    if not target.exists() or not target.is_dir():
        raise HTTPException(404, "Path not found")

    dirs = []
    files = []
    try:
        entries = sorted(target.iterdir(), key=lambda e: e.name.lower())
    except (PermissionError, OSError):
        raise HTTPException(403, "Permission denied")

    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_dir():
                dirs.append({
                    "name": entry.name,
                    "path": str(entry.resolve(strict=False)),
                    "is_root": False,
                })
            elif entry.suffix.lower() in MODEL_EXTENSIONS:
                files.append({
                    "name": entry.name,
                    "path": str(entry.resolve(strict=False)),
                    "size": entry.stat().st_size,
                })
        except (PermissionError, OSError):
            continue

    breadcrumbs = build_breadcrumbs(target, root)
    parent_path = breadcrumbs[-2]["path"] if len(breadcrumbs) > 1 else ""

    return {
        "path": str(target),
        "parent_path": parent_path,
        "breadcrumbs": breadcrumbs,
        "dirs": dirs,
        "files": files,
    }


@router.post("/models/inspect")
def inspect_model(project_id: str, body: ModelInspectRequest):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    if not YOLO_ENABLED:
        raise HTTPException(503, "YOLO prediction is not included in this desktop build")

    resolved_model_path = _resolve_model_path(body.model_path)
    if not resolved_model_path:
        raise HTTPException(400, "Model file not found")

    try:
        return inspect_yolo_model(resolved_model_path)
    except Exception as err:
        raise HTTPException(400, f"Failed to inspect YOLO model: {err}") from err


@router.get("/sam/models")
def list_sam_models(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    from services.sam_assist import bundled_sam_checkpoint, bundled_sam_dir, scan_sam_models

    config = PredictionConfig(**load_json(
        project_id,
        "prediction_config",
        default=PredictionConfig().model_dump(),
    ))
    models_dir = config.sam_models_dir
    models = scan_sam_models(models_dir)
    default_checkpoint = config.sam_checkpoint or bundled_sam_checkpoint(models_dir)

    return {
        "models": models,
        "models_dir_path": str(bundled_sam_dir(models_dir)),
        "models_dir_available": bundled_sam_dir(models_dir).is_dir(),
        "custom_models_dir": bool(models_dir),
        "default_checkpoint": default_checkpoint,
    }


@router.get("/dino/models")
def list_dino_models(project_id: str):
    """Rozpoznane wagi DINO (silnik egzemplarza few-shot) — do wyboru w panelu Predykcja."""
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    from pathlib import Path as _Path

    from services.embedding_backbone import (
        DINO_DIR, dino_runtime_available, resolve_dino_checkpoint, scan_dino_models,
    )

    config = PredictionConfig(**load_json(
        project_id, "prediction_config", default=PredictionConfig().model_dump(),
    ))
    models_dir = config.dino_models_dir
    dir_path = _Path(models_dir) if models_dir else DINO_DIR
    default = resolve_dino_checkpoint(config.dino_checkpoint or None, models_dir)
    return {
        "models": scan_dino_models(models_dir),
        "models_dir_path": str(dir_path),
        "models_dir_available": dir_path.is_dir(),
        "custom_models_dir": bool(models_dir),
        "default_checkpoint": str(default) if default else None,
        "selected_checkpoint": config.dino_checkpoint or None,
        "runtime_available": dino_runtime_available(config.dino_checkpoint or None, models_dir),
    }


@router.post("/sam/models/inspect")
def inspect_sam_model(project_id: str, body: ModelInspectRequest):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    resolved_model_path = _resolve_model_path(body.model_path)
    if not resolved_model_path:
        raise HTTPException(400, "SAM checkpoint not found")
    from services.sam_assist import sam_model_info

    info = sam_model_info(resolved_model_path)
    if not info["supported"]:
        raise HTTPException(400, info["reason"])
    return info


# --- Config ---

@router.get("/config")
def get_prediction_config(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    default = PredictionConfig().model_dump()
    data = load_json(project_id, "prediction_config", default=default)
    return PredictionConfig(**data).model_dump()


@router.put("/config")
def update_prediction_config(project_id: str, body: PredictionConfig):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    # Waliduj TYLKO pola ścieżek, które faktycznie się zmieniły względem zapisanego configu.
    # Frontend odsyła cały config, więc bez tego nieaktualna (np. z poprzedniej maszyny)
    # ścieżka SAM/DINO, której użytkownik nie tyka, blokowałaby zapis każdej innej ścieżki
    # (all-or-nothing). Nietknięte pole nie jest re-walidowane; walidujemy tylko realny edit.
    stored = load_json(project_id, "prediction_config", default={}) or {}

    def is_changed(field: str, value) -> bool:
        return bool(value) and value != stored.get(field)

    if is_changed("sam_models_dir", body.sam_models_dir):
        models_dir = Path(body.sam_models_dir).expanduser().resolve(strict=False)
        if not models_dir.is_dir():
            raise HTTPException(400, "SAM models directory not found")
        body.sam_models_dir = str(models_dir)
    if is_changed("sam_checkpoint", body.sam_checkpoint):
        resolved_checkpoint = _resolve_model_path(body.sam_checkpoint)
        if not resolved_checkpoint:
            raise HTTPException(400, "SAM checkpoint not found")
        from services.sam_assist import sam_model_info

        info = sam_model_info(resolved_checkpoint)
        if not info["supported"]:
            raise HTTPException(400, info["reason"])
        body.sam_checkpoint = str(resolved_checkpoint)
    if is_changed("dino_models_dir", body.dino_models_dir):
        dino_dir = Path(body.dino_models_dir).expanduser().resolve(strict=False)
        if not dino_dir.is_dir():
            raise HTTPException(400, "DINO models directory not found")
        body.dino_models_dir = str(dino_dir)
    if is_changed("dino_checkpoint", body.dino_checkpoint):
        from services.embedding_backbone import resolve_dino_checkpoint

        resolved_dino = resolve_dino_checkpoint(body.dino_checkpoint, body.dino_models_dir or None)
        if resolved_dino is None:
            raise HTTPException(400, "DINO checkpoint not found")
        body.dino_checkpoint = str(resolved_dino)
    save_json(project_id, "prediction_config", body.model_dump())
    return body.model_dump()


# --- Execution (SSE) ---

def _get_scene_path(project_id: str, scene_id: str) -> Path | None:
    try:
        return resolve_scene_raster(project_id, scene_id)
    except FileNotFoundError:
        return None


@router.post("/{scene_id}/run")
async def run_prediction(project_id: str, scene_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    if not YOLO_ENABLED:
        raise HTTPException(503, "YOLO prediction is not included in this desktop build")
    if not _get_scene_path(project_id, scene_id):
        raise HTTPException(404, "Scene not found")
    cfg_data = load_json(
        project_id,
        "prediction_config",
        default=PredictionConfig().model_dump(),
    )
    config = PredictionConfig(**cfg_data)
    resolved_model_path = _resolve_model_path(config.model_path)
    if not resolved_model_path or not resolved_model_path.exists():
        raise HTTPException(400, "Model not configured or file not found")
    device = resolve_device()
    config.model_path = str(resolved_model_path)
    config.device = device
    scene_data = load_scene_json(project_id, scene_id, "scene", default={})
    try:
        submitted = submit_job(
            project_id,
            JobCreateRequest(
                job_type=JobType.SCENE_INFERENCE,
                resource_class=(
                    ResourceClass.GPU_EXCLUSIVE if device == "cuda" else ResourceClass.CPU_HEAVY
                ),
                priority_class=PriorityClass.INTERACTIVE,
                payload={
                    "scene_id": scene_id,
                    "prediction_config": config.model_dump(),
                    "model_sha256": model_sha256(resolved_model_path),
                    "working_grid_uid": scene_data.get("working_grid_uid"),
                },
                dedupe_key=f"scene:{scene_id}",
            ),
        )
    except JobStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    job_id = submitted["job"]["job_id"]
    return EventSourceResponse(stream_job_events(project_id, job_id, legacy_only=True))


def prepare_prediction_run(
    project_id: str,
    scene_id: str,
    job_payload: dict | None = None,
):
    """Resolve model, scene and lineage outside the FastAPI event-loop thread."""

    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    if not YOLO_ENABLED:
        raise HTTPException(503, "YOLO prediction is not included in this desktop build")

    scene_path = _get_scene_path(project_id, scene_id)
    if not scene_path:
        raise HTTPException(404, "Scene not found")

    job_payload = job_payload or {}
    cfg_data = (
        job_payload["prediction_config"]
        if "prediction_config" in job_payload
        else load_json(
            project_id,
            "prediction_config",
            default=PredictionConfig().model_dump(),
        )
    )
    config = PredictionConfig(**cfg_data)

    resolved_model_path = _resolve_model_path(config.model_path)
    if not resolved_model_path or not resolved_model_path.exists():
        raise HTTPException(400, "Model not configured or file not found")

    config.model_path = str(resolved_model_path)
    config.device = config.device if "prediction_config" in job_payload else resolve_device()

    expected_model_sha = job_payload.get("model_sha256")
    current_model_sha = model_sha256(resolved_model_path)
    if expected_model_sha and current_model_sha != expected_model_sha:
        raise HTTPException(409, "Prediction model changed while the job was queued")

    scene_data = load_scene_json(project_id, scene_id, "scene", default={})
    working_grid_uid = scene_data.get("working_grid_uid")
    if "working_grid_uid" in job_payload and job_payload.get("working_grid_uid") != working_grid_uid:
        raise HTTPException(409, "Scene working grid changed while the job was queued")
    model_sha = current_model_sha
    preproc_hash = preprocessing_hash({
        "preprocess_mode": config.preprocess_mode,
        "stretch_low": config.stretch_low,
        "stretch_high": config.stretch_high,
        "gamma": config.gamma,
        "brightness": config.brightness,
        "contrast": config.contrast,
    })

    active_key = f"{project_id}:{scene_id}"
    with ACTIVE_PREDICTIONS_LOCK:
        if active_key in ACTIVE_PREDICTIONS:
            raise HTTPException(409, "Prediction is already running for this scene")
        cancel_event = threading.Event()
        ACTIVE_PREDICTIONS[active_key] = cancel_event

    async def generate():
        gen = None
        inference_performance: dict = {}
        try:
            try:
                gen = execute_prediction(
                    scene_path,
                    config,
                    should_cancel=cancel_event.is_set,
                    performance_callback=lambda value: inference_performance.update(value),
                )
                predictions = []
                while True:
                    step = await next_generator_in_threadpool(
                        gen,
                        max_progress_hz=4.0,
                        should_cancel=cancel_event.is_set,
                    )
                    if not step.has_value:
                        predictions = step.value if step.value else []
                        break
                    progress = step.value
                    yield {"event": "progress", "data": json.dumps(progress)}
            except (PredictionCancelled, WorkerCancelled):
                yield {"event": "cancelled", "data": json.dumps({"status": "cancelled"})}
                return
            except Exception as err:
                yield {"event": "error", "data": json.dumps({"detail": str(err)})}
                return

            def persist_prediction_results():
                # Persist as a versioned assist session (never overwrites).
                model_name = Path(config.model_path).name
                proposals = [
                    AssistanceProposal(
                        session_id="",  # set in create_session
                        source_tool="yolo_scene",
                        geometry_type=pred.get("geometry_type", "bbox"),
                        bbox=pred["bbox"],
                        rotated_bbox=pred.get("rotated_bbox"),
                        class_id=pred.get("class_id"),
                        class_name=pred.get("class_name"),
                        confidence=pred.get("confidence"),
                        model_name=model_name,
                        model_sha256=model_sha,
                        working_grid_uid=working_grid_uid,
                        source_window=None,  # whole-scene prediction
                        preprocessing_hash=preproc_hash,
                        created_at=(
                            pred.get("created_at")
                            or datetime.now(timezone.utc).isoformat()
                        ),
                    )
                    for pred in predictions
                ]
                session = create_session(
                    project_id,
                    scene_id,
                    "yolo_scene",
                    proposals,
                    device=config.device,
                    model_name=model_name,
                    model_sha=model_sha,
                    working_grid_uid=working_grid_uid,
                    params={
                        **config.model_dump(),
                        "runtime_performance": inference_performance,
                    },
                )
                return model_name, proposals, session

            model_name, proposals, session = await run_blocking(persist_prediction_results)

            yield {"event": "complete", "data": json.dumps({
                "total_predictions": len(proposals),
                "model": model_name,
                "session_id": session.session_id,
                "device": config.device,
                "performance": inference_performance,
            })}
        except Exception as err:
            yield {"event": "error", "data": json.dumps({"detail": str(err)})}
        finally:
            cancel_event.set()
            if gen is not None:
                try:
                    await run_blocking(gen.close)
                except (RuntimeError, ValueError):
                    pass
            with ACTIVE_PREDICTIONS_LOCK:
                ACTIVE_PREDICTIONS.pop(active_key, None)

    return EventSourceResponse(generate())


@router.post("/{scene_id}/cancel")
async def cancel_prediction(project_id: str, scene_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    active = find_active_job(
        project_id,
        job_type=JobType.SCENE_INFERENCE.value,
        dedupe_key=f"scene:{scene_id}",
    )
    if active is not None:
        state = cancel_job(project_id, active["job"]["job_id"])
        return {"status": state.get("status"), "job_id": active["job"]["job_id"]}
    # Compatibility with an in-process prediction started by a pre-P1.1 backend.
    active_key = f"{project_id}:{scene_id}"
    with ACTIVE_PREDICTIONS_LOCK:
        cancel_event = ACTIVE_PREDICTIONS.get(active_key)
    if not cancel_event:
        return {"status": "not_running"}
    cancel_event.set()
    return {"status": "cancelling"}


# --- Predictions CRUD ---

@router.get("/{scene_id}/predictions")
def list_predictions(project_id: str, scene_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    session = get_active_session(project_id, scene_id)
    if not session:
        return []
    return [proposal.to_legacy_prediction() for proposal in session.proposals]


class AcceptRejectRequest(BaseModel):
    prediction_ids: list[str] = []
    all: bool = False


@router.patch("/{scene_id}/predictions/accept")
def accept_predictions(project_id: str, scene_id: str, body: AcceptRejectRequest):
    """Accept proposals from the active session — creates annotations."""
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")

    session = get_active_session(project_id, scene_id)
    if not session or not session.proposals:
        raise HTTPException(404, "No predictions found")

    classes = load_json(project_id, "classes", default=[])
    class_name_to_id = {c["name"].lower(): c["id"] for c in classes}

    project_data = load_json(project_id, "project", default={})
    profile = project_data.get("profile") or {}
    author_email = profile.get("labeling_author_email") or None

    accepted_count = 0
    accepted_annotations: list[dict] = []
    missing_classes: set[str] = set()
    for proposal in session.proposals:
        if proposal.status != "pending":
            continue
        if not body.all and proposal.proposal_id not in body.prediction_ids:
            continue

        # Prefer the class already carried by the proposal; fall back to the
        # model class-name → project-class mapping (whole-scene YOLO case).
        mapped_class_id = proposal.class_id
        if mapped_class_id is None and proposal.class_name:
            mapped_class_id = class_name_to_id.get(proposal.class_name.lower())
        if mapped_class_id is None or mapped_class_id not in {c["id"] for c in classes}:
            missing_classes.add(proposal.class_name or str(proposal.class_id))
            continue

        proposal.status = "accepted"
        accepted_count += 1
        ann_id = uuid.uuid4().hex[:10]
        created = proposal.created_at
        created_iso = created.isoformat() if hasattr(created, "isoformat") else str(created)

        ann = {
            "id": ann_id,
            "source_annotation_id": ann_id,
            "scene_id": scene_id,
            "class_id": mapped_class_id,
            "geometry_type": proposal.geometry_type or "bbox",
            "bbox": proposal.bbox,
            "rotated_bbox": proposal.rotated_bbox,
            # Poligon wierny mapie liczony przy tworzeniu propozycji (routers/assist.py).
            # Zapis `None` zostawialby adnotacje bez ksztaltu i renderer odbudowywalby ja
            # z `rotated_bbox` — czyli jako prostokat PIKSELOWY, widoczny jako skos.
            "polygon_scene_px": proposal.polygon_scene_px,
            "front_edge_scene_px": None,
            "front_vector_scene_px": proposal.front_vector_scene_px,
            "orientation_angle_deg": None,
            "is_negative": False,
            "annotation_source": _assist_annotation_source(proposal.source_tool),
            "annotator_email": author_email,
            "created_by": author_email,
            "updated_by": author_email,
            "source_model": proposal.model_name,
            "created_at": created_iso,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        accepted_annotations.append(enrich_source_annotation(project_id, scene_id, ann))

    if session.is_resolved():
        session.status = "resolved"
    write_session(project_id, scene_id, session)
    if accepted_annotations:
        annotations, _revision = mutate_scene_json(
            project_id,
            scene_id,
            "annotations",
            lambda current: [*current, *accepted_annotations],
            default=[],
        )
    else:
        annotations = load_scene_json(project_id, scene_id, "annotations", default=[])

    scene_data = load_scene_json(project_id, scene_id, "scene")
    if scene_data:
        scene_data["annotation_count"] = len(annotations)
        save_scene_json(project_id, scene_id, "scene", scene_data)

    return {
        "accepted": accepted_count,
        "total_annotations": len(annotations),
        "missing_classes": sorted(missing_classes),
    }


@router.post("/{scene_id}/predictions/delete")
def delete_predictions(project_id: str, scene_id: str, body: AcceptRejectRequest):
    """Permanently remove selected proposals from the active assist session."""
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")

    session = get_active_session(project_id, scene_id)
    if not session:
        return {"deleted": 0}

    selected_ids = set(body.prediction_ids)
    before = len(session.proposals)
    session.proposals = [
        proposal
        for proposal in session.proposals
        if not (body.all or proposal.proposal_id in selected_ids)
    ]
    deleted_count = before - len(session.proposals)

    session.status = "resolved" if session.is_resolved() else "active"
    write_session(project_id, scene_id, session)
    return {"deleted": deleted_count}


@router.delete("/{scene_id}/predictions")
def clear_predictions(project_id: str, scene_id: str):
    """Clear all assist sessions for a scene."""
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    clear_sessions(project_id, scene_id)
    return {"status": "cleared"}


def _assist_annotation_source(source_tool: str) -> str:
    """Provenance source recorded on accepted annotations (filterable in datasets)."""
    return {
        "yolo_scene": "model_assisted",
        "sam_click": "assisted_sam",
        "exemplar": "assisted_exemplar",
        "exemplar_sar": "assisted_exemplar_sar",
        "exemplar_dino": "assisted_exemplar_dino",
        "sam_text": "assisted_sam_text",
    }.get(source_tool, "model_assisted")
