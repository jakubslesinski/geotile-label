"""Training workbench: base model registry, preflight and run lifecycle.

Mounted conditionally in `main.py`, like `predictions` and `assist`: a missing
torch/ultralytics must never take the application down.
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from db.storage import load_json, project_exists
from models.job import JobCreateRequest, JobType, PriorityClass, ResourceClass
from services.dataset_runs import (
    DatasetPublicationError,
    dataset_identity_summary,
    get_dataset_run_manifest,
    read_publication,
    require_dataset_exact_identities,
    resolve_dataset_path,
)
from services.model_registry import (
    ModelRegistryError,
    list_models,
    model_lineage,
    promote_model,
    read_registry,
    register_model,
    remove_model,
)
from services.training_models import (
    TrainingModelError,
    list_base_models,
    required_task_for,
    resolve_base_model,
)
from services.training_preflight import run_preflight
from services.training_runs import (
    TrainingRunError,
    cancel_training_run,
    config_fingerprint,
    create_training_run_id,
    delete_training_run,
    list_training_runs,
    next_attempt,
    read_json,
    resolve_job_state,
    runs_for_fingerprint,
    training_run_dir,
    training_run_summary,
    utc_now,
    write_job_state,
    write_json_atomic,
)
from services.jobs.scheduler import cancel_job as cancel_common_job
from services.jobs.scheduler import submit_job
from services.jobs.store import JobStoreError

router = APIRouter()

LOCKED_TRAINING_OPTIONS = {
    "data", "project", "name", "model", "mode", "task", "device", "epochs",
    "imgsz", "batch", "seed", "patience", "resume", "pretrained", "exist_ok",
}
ADVANCED_TRAINING_OPTIONS = {
    "optimizer", "lr0", "lrf", "momentum", "weight_decay", "warmup_epochs",
    "warmup_momentum", "warmup_bias_lr", "box", "cls", "dfl", "cos_lr",
    "close_mosaic", "amp", "workers", "cache", "deterministic", "save_period",
    "plots", "rect", "multi_scale", "single_cls", "fraction", "freeze", "dropout",
    "hsv_h", "hsv_s", "hsv_v", "degrees", "translate", "scale", "shear",
    "perspective", "flipud", "fliplr", "bgr", "mosaic", "mixup", "cutmix",
    "copy_paste", "copy_paste_mode", "auto_augment", "erasing", "crop_fraction",
}


class TrainingStartRequest(BaseModel):
    dataset_run_id: str
    base_model: str
    epochs: int = Field(default=100, ge=1, le=1000)
    imgsz: int = Field(default=640, ge=64, le=2048)
    # -1 lets ultralytics size the batch to available VRAM.
    batch: int = Field(default=-1, ge=-1, le=512)
    seed: int = Field(default=0, ge=0)
    patience: int = Field(default=25, ge=0, le=1000)
    device: Literal["auto", "cpu", "cuda"] = "auto"
    advanced_options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("batch")
    @classmethod
    def validate_batch(cls, value: int) -> int:
        if value == 0:
            raise ValueError("batch must be -1 (automatic) or a positive integer")
        return value

    @field_validator("advanced_options")
    @classmethod
    def validate_advanced_options(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > len(ADVANCED_TRAINING_OPTIONS):
            raise ValueError("Too many advanced training options")
        unknown = sorted(set(value) - ADVANCED_TRAINING_OPTIONS)
        if unknown:
            raise ValueError(f"Unsupported advanced training options: {', '.join(unknown)}")
        locked = sorted(set(value) & LOCKED_TRAINING_OPTIONS)
        if locked:
            raise ValueError(f"Application-managed training options: {', '.join(locked)}")
        for key, item in value.items():
            values = item if isinstance(item, list) else [item]
            if isinstance(item, dict) or any(
                isinstance(entry, (dict, list)) or entry is None
                or not isinstance(entry, (str, int, float, bool))
                for entry in values
            ):
                raise ValueError(f"'{key}' must be a scalar or a flat list of scalars")
        workers = value.get("workers")
        if workers is not None and (
            isinstance(workers, bool)
            or not isinstance(workers, int)
            or workers < 0
            or workers > 64
        ):
            raise ValueError("'workers' must be an integer from 0 to 64")
        cache = value.get("cache")
        if cache is not None and cache not in (True, False, "ram", "disk"):
            raise ValueError("'cache' must be true, false, 'ram' or 'disk'")
        return value


def _profile(project_id: str) -> dict[str, Any]:
    return (load_json(project_id, "project", default={}) or {}).get("profile") or {}


def _class_names(project_id: str) -> list[str]:
    classes = load_json(project_id, "classes", default=[]) or []
    return [
        str(item.get("name") or item.get("id"))
        for item in sorted(classes, key=lambda entry: int(entry.get("id", 0)))
        if isinstance(item, dict)
    ]


def _resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _require_project(project_id: str) -> None:
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")


def _prepare(project_id: str, body: TrainingStartRequest) -> dict[str, Any]:
    """Shared resolution for preflight and start, so both judge the same thing."""
    annotation_mode = _profile(project_id).get("annotation_mode")
    try:
        task = required_task_for(annotation_mode)
        base = resolve_base_model(body.base_model, annotation_mode)
    except TrainingModelError as exc:
        raise HTTPException(400, str(exc)) from exc

    manifest = get_dataset_run_manifest(project_id, body.dataset_run_id)
    if not manifest:
        raise HTTPException(404, "Dataset run not found")
    if manifest.get("status") != "complete":
        raise HTTPException(400, "Dataset run is not complete")
    publication = read_publication(project_id, body.dataset_run_id)
    if publication.get("status") != "published":
        raise HTTPException(400, "Dataset run must be published before training")
    try:
        require_dataset_exact_identities(manifest)
    except DatasetPublicationError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        dataset_dir = resolve_dataset_path(project_id, body.dataset_run_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc

    return {
        "task": task,
        "base": base,
        "dataset_dir": Path(dataset_dir),
        "dataset_manifest": manifest,
        "device": _resolve_device(body.device),
        "class_names": _class_names(project_id),
    }


@router.get("/base-models")
def get_base_models(project_id: str):
    _require_project(project_id)
    return list_base_models(_profile(project_id).get("annotation_mode"))


@router.get("/base-models/{file_name}")
def get_base_model(project_id: str, file_name: str, check_geometry: bool = Query(True)):
    _require_project(project_id)
    try:
        return resolve_base_model(
            file_name,
            _profile(project_id).get("annotation_mode") if check_geometry else None,
        )
    except TrainingModelError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/preflight")
def post_training_preflight(project_id: str, body: TrainingStartRequest):
    _require_project(project_id)
    context = _prepare(project_id, body)
    fingerprint = _fingerprint(project_id, body, context)
    result = run_preflight(
        dataset_dir=context["dataset_dir"],
        task=context["task"],
        requested_device=context["device"],
        project_classes=context["class_names"],
        audit_readiness=(context["dataset_manifest"].get("audit_summary") or {}).get("readiness"),
        imgsz=body.imgsz,
        batch=body.batch,
        advanced_options=body.advanced_options,
        estimated_vram_gb=context["base"].get("estimated_vram_gb"),
    )
    previous = runs_for_fingerprint(project_id, fingerprint)
    return {
        **result,
        "config_fingerprint": fingerprint,
        "device": context["device"],
        "task": context["task"],
        # Repeating a configuration is allowed; the UI says so rather than blocking,
        # because repeats are how run-to-run variance becomes visible.
        "previous_runs": [training_run_summary(project_id, run_id) for run_id in previous],
        "next_attempt": len(previous) + 1,
    }


def _fingerprint(project_id: str, body: TrainingStartRequest, context: dict[str, Any]) -> str:
    return config_fingerprint({
        "dataset_run_id": body.dataset_run_id,
        "dataset_input_hash": context["dataset_manifest"].get("input_hash"),
        "base_model": context["base"]["file"],
        "base_model_sha256": context["base"]["sha256"],
        "task": context["task"],
        "epochs": body.epochs,
        "imgsz": body.imgsz,
        "batch": body.batch,
        "seed": body.seed,
        "patience": body.patience,
        "advanced_options": body.advanced_options,
    })


@router.post("/runs")
def post_training_run(project_id: str, body: TrainingStartRequest):
    _require_project(project_id)
    context = _prepare(project_id, body)
    preflight = run_preflight(
        dataset_dir=context["dataset_dir"],
        task=context["task"],
        requested_device=context["device"],
        project_classes=context["class_names"],
        audit_readiness=(context["dataset_manifest"].get("audit_summary") or {}).get("readiness"),
        imgsz=body.imgsz,
        batch=body.batch,
        advanced_options=body.advanced_options,
        estimated_vram_gb=context["base"].get("estimated_vram_gb"),
    )
    if not preflight["can_start"]:
        blocking = "; ".join(item["detail"] for item in preflight["checks"] if item["status"] == "error")
        raise HTTPException(400, f"Preflight failed: {blocking}")

    fingerprint = _fingerprint(project_id, body, context)
    run_id = create_training_run_id(fingerprint)
    attempt = next_attempt(project_id, fingerprint)
    run_dir = training_run_dir(project_id, run_id)
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        raise

    job = {
        "training_run_id": run_id,
        "config_fingerprint": fingerprint,
        "attempt": attempt,
        "project_id": project_id,
        "dataset_run_id": body.dataset_run_id,
        "dataset_input_hash": context["dataset_manifest"].get("input_hash"),
        "dataset_split_mode": (context["dataset_manifest"].get("dataset_config") or {}).get("split_mode"),
        "dataset_split_seed": (context["dataset_manifest"].get("dataset_config") or {}).get("split_seed"),
        "dataset_source_identity": dataset_identity_summary(context["dataset_manifest"]),
        "class_names": context["class_names"],
        "preprocessing_profile_id": (context["dataset_manifest"].get("preprocessing_profile") or {}).get("profile_id"),
        "preprocessing_profile_hash": (
            context["dataset_manifest"].get("preprocessing_profile") or {}
        ).get("profile_hash"),
        "task": context["task"],
        "base_model": context["base"]["file"],
        "base_model_sha256": context["base"]["sha256"],
        "base_model_path": context["base"]["path"],
        "base_model_license": context["base"]["license"],
        "dataset_dir": str(context["dataset_dir"]),
        "data_yaml": str(context["dataset_dir"] / ("data_obb.yaml" if context["task"] == "obb" else "data.yaml")),
        "epochs": body.epochs,
        "epochs_total": body.epochs,
        "imgsz": body.imgsz,
        "batch": body.batch,
        "seed": body.seed,
        "patience": body.patience,
        "device": context["device"],
        "advanced_options": body.advanced_options,
        # Exact measurements and the effective/manual-vs-recommended decision are
        # retained for auditability.  The recommendation itself is never applied by
        # the backend without an explicit configuration change from the user.
        "resource_preflight": preflight.get("resource_recommendation"),
        "app_version": load_json(project_id, "project", default={}).get("app_version"),
        "created_at": utc_now(),
        **_library_versions(),
    }
    try:
        write_json_atomic(run_dir / "job.json", job)
        write_job_state(project_id, run_id, {
            **{key: job[key] for key in (
                "config_fingerprint", "attempt", "dataset_run_id", "base_model",
                "task", "device", "epochs_total",
            )},
            "status": "queued",
            "created_at": job["created_at"],
            "epochs_done": 0,
        })

        common_job = submit_job(
            project_id,
            JobCreateRequest(
                job_type=JobType.TRAINING,
                resource_class=(
                    ResourceClass.GPU_EXCLUSIVE
                    if context["device"] == "cuda"
                    else ResourceClass.CPU_HEAVY
                ),
                priority_class=PriorityClass.USER_BACKGROUND,
                payload={"run_dir": str(run_dir)},
            ),
            desired_job_id=run_id,
        )
    except Exception as exc:
        write_job_state(project_id, run_id, {
            "status": "failed",
            "error": f"Could not start the training process: {exc}",
            "ended_at": utc_now(),
        })
        raise HTTPException(500, f"Could not start the training process: {exc}") from exc

    return {
        "training_run_id": run_id,
        "job_id": common_job["job"]["job_id"],
        "config_fingerprint": fingerprint,
        "attempt": attempt,
    }


def _library_versions() -> dict[str, Any]:
    versions: dict[str, Any] = {"torch": None, "ultralytics": None, "cuda": None}
    try:
        import torch

        versions["torch"] = torch.__version__
        versions["cuda"] = torch.version.cuda
    except Exception:
        pass
    try:
        import ultralytics

        versions["ultralytics"] = ultralytics.__version__
    except Exception:
        pass
    return versions


@router.get("/runs")
def get_training_runs(project_id: str, dataset_run_id: str | None = Query(None)):
    _require_project(project_id)
    return list_training_runs(project_id, dataset_run_id)


@router.get("/runs/{run_id}")
def get_training_run(project_id: str, run_id: str):
    _require_project(project_id)
    try:
        run_dir = training_run_dir(project_id, run_id)
    except TrainingRunError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not run_dir.is_dir():
        raise HTTPException(404, "Training run not found")
    return {
        **training_run_summary(project_id, run_id),
        "run_dir": str(run_dir.resolve()),
        "job": read_json(run_dir / "job.json", default={}),
        "manifest": read_json(run_dir / "training_manifest.json", default=None),
        "state": resolve_job_state(project_id, run_id),
        "metrics_detail": read_json(run_dir / "metrics.json", default={}),
    }


def _live_artifact_path(project_id: str, run_id: str, name: str) -> Path | None:
    """Ścieżka artefaktu ultralytics — finalna (skopiowana po treningu) albo przyrostowa
    z podkatalogu ultralytics w trakcie treningu. Dzięki temu Results widzi krzywe na żywo."""
    run_dir = training_run_dir(project_id, run_id)
    final = run_dir / name
    if final.is_file():
        return final
    live = run_dir / "ultralytics" / name
    if live.is_file():
        return live
    # save_dir bywa inny (exist_ok/name) — weź pierwszy pasujący plik w podkatalogach runu.
    for candidate in sorted(run_dir.glob(f"*/{name}")):
        if candidate.is_file():
            return candidate
    return None


@router.get("/runs/{run_id}/history")
def get_training_history(project_id: str, run_id: str):
    _require_project(project_id)
    path = _live_artifact_path(project_id, run_id, "results.csv")
    if path is None:
        return {"columns": [], "rows": []}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = [str(column).strip() for column in (reader.fieldnames or [])]
        rows: list[dict[str, Any]] = []
        for raw in reader:
            row: dict[str, Any] = {}
            for key, value in raw.items():
                clean_key = str(key).strip()
                clean_value = str(value or "").strip()
                try:
                    row[clean_key] = float(clean_value)
                except ValueError:
                    row[clean_key] = clean_value
            rows.append(row)
    return {"columns": columns, "rows": rows}


@router.get("/runs/{run_id}/artifacts/confusion-matrix")
def get_confusion_matrix(project_id: str, run_id: str):
    _require_project(project_id)
    path = _live_artifact_path(project_id, run_id, "confusion_matrix.png")
    if path is None:
        raise HTTPException(404, "Confusion matrix is not available for this run")
    return FileResponse(path, media_type="image/png", filename="confusion_matrix.png")


@router.get("/runs/{run_id}/artifacts/confusion-analysis")
def get_confusion_analysis(project_id: str, run_id: str):
    """Dane macierzy pomylek: symetryczne podobienstwo klas + leaf-order + pary.

    Warstwa danych (DI1). Zwraca 404 z czytelnym powodem, gdy przebieg nie ma
    zapisanej macierzy (np. starszy run albo trening bez walidacji) — brak cichej awarii.
    """
    _require_project(project_id)
    data = read_json(training_run_dir(project_id, run_id) / "confusion_matrix.json", default=None)
    if not data or not data.get("matrix"):
        raise HTTPException(404, "Confusion matrix data is not available for this run")

    from services.dataset_intelligence import (
        confused_pairs,
        confusion_leaf_order,
        confusion_to_similarity,
    )

    class_names = data.get("class_names") or []
    similarity = confusion_to_similarity(data["matrix"], class_names)
    order = confusion_leaf_order(similarity)
    return {
        "class_names": class_names,
        "matrix": data["matrix"],
        "orientation": data.get("orientation"),
        "similarity": similarity.tolist(),
        "leaf_order": order,
        "confused_pairs": confused_pairs(similarity, class_names, top_k=30),
    }


@router.post("/runs/{run_id}/cancel")
def post_cancel_training_run(project_id: str, run_id: str):
    _require_project(project_id)
    try:
        run_dir = training_run_dir(project_id, run_id)
    except TrainingRunError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not run_dir.is_dir():
        raise HTTPException(404, "Training run not found")
    try:
        cancel_common_job(project_id, run_id)
        return resolve_job_state(project_id, run_id)
    except JobStoreError:
        # Backward compatibility for runs created before P1.1.
        return cancel_training_run(project_id, run_id)


def _resolve_training_run_dir(project_id: str, run_id: str):
    try:
        run_dir = training_run_dir(project_id, run_id)
    except TrainingRunError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not run_dir.is_dir():
        raise HTTPException(404, "Training run not found")
    return run_dir


def training_run_delete_info(project_id: str, run_id: str) -> dict:
    """What deleting this training run would affect: models registered from it,
    whether one is the project model, and whether the run is still active."""
    summary = training_run_summary(project_id, run_id)
    registry = read_registry(project_id)
    models = [
        {
            "model_id": model.get("model_id"),
            "is_current": model.get("model_id") == registry.get("current_model_id"),
        }
        for model in registry.get("models", [])
        if model.get("training_run_id") == run_id
    ]
    job = resolve_job_state(project_id, run_id)
    active = str(job.get("status") or "") in ("running", "queued", "starting")
    return {
        "training_run_id": run_id,
        "status": summary.get("status"),
        "models": models,
        "is_current_model": any(model["is_current"] for model in models),
        "active": active,
        "blocked": bool(models),
    }


@router.get("/runs/{run_id}/delete-info")
def get_training_run_delete_info(project_id: str, run_id: str):
    _require_project(project_id)
    _resolve_training_run_dir(project_id, run_id)
    return training_run_delete_info(project_id, run_id)


@router.delete("/runs/{run_id}")
def delete_training_run_endpoint(project_id: str, run_id: str, force: bool = Query(False)):
    _require_project(project_id)
    _resolve_training_run_dir(project_id, run_id)
    info = training_run_delete_info(project_id, run_id)
    # An active run's files are held by a live worker — never delete, even with force.
    if info["active"]:
        raise HTTPException(status_code=409, detail={**info, "reason": "active"})
    if info["blocked"] and not force:
        raise HTTPException(status_code=409, detail=info)
    removed_models = remove_model(project_id, run_id)
    existed = delete_training_run(project_id, run_id)
    return {
        "deleted": existed,
        "training_run_id": run_id,
        "removed_models": removed_models,
        "dependents": info,
    }


@router.get("/runs/{run_id}/test-evaluation")
def get_test_evaluation(project_id: str, run_id: str):
    _require_project(project_id)
    run_dir = training_run_dir(project_id, run_id)
    evaluation = read_json(run_dir / "test_evaluation.json", default=None)
    error = read_json(run_dir / "test_evaluation_error.json", default=None)
    return {
        "evaluated": evaluation is not None,
        "evaluation": evaluation,
        "error": (error or {}).get("error"),
    }


@router.post("/runs/{run_id}/test-evaluation")
def post_test_evaluation(project_id: str, run_id: str):
    """Run the final test-set evaluation. Allowed once per training run.

    Kept deliberately one-shot: repeating it after seeing the number would turn the
    test split into a second validation set and inflate the estimate.
    """
    _require_project(project_id)
    run_dir = training_run_dir(project_id, run_id)
    if not run_dir.is_dir():
        raise HTTPException(404, "Training run not found")
    if (run_dir / "test_evaluation.json").exists():
        raise HTTPException(
            409,
            "This run has already been evaluated on the test split. The result is final "
            "and is not recomputed.",
        )
    if not (run_dir / "training_manifest.json").is_file():
        raise HTTPException(400, "This run did not finish, so it cannot be evaluated.")

    worker = Path(__file__).resolve().parent.parent / "training_worker.py"
    try:
        subprocess.Popen(
            [sys.executable, str(worker), str(run_dir), "eval"],
            cwd=str(worker.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        raise HTTPException(500, f"Could not start the evaluation process: {exc}") from exc
    return {"status": "started", "training_run_id": run_id}


@router.get("/models")
def get_models(project_id: str):
    _require_project(project_id)
    return list_models(project_id)


@router.post("/models/{training_run_id}/register")
def post_register_model(project_id: str, training_run_id: str):
    _require_project(project_id)
    try:
        return register_model(project_id, training_run_id)
    except ModelRegistryError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/models/{model_id}/promote")
def post_promote_model(project_id: str, model_id: str):
    _require_project(project_id)
    try:
        return promote_model(project_id, model_id)
    except ModelRegistryError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/models/{model_id}/lineage")
def get_model_lineage(project_id: str, model_id: str):
    _require_project(project_id)
    try:
        return model_lineage(project_id, model_id)
    except ModelRegistryError as exc:
        raise HTTPException(404, str(exc)) from exc
