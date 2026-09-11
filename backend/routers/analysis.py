"""Analiza embeddingów datasetu (Dataset Intelligence, DI2) — endpointy.

Uruchamia worker w tle (wzorzec treningu), serwuje status + wynik i zapytania
„20 najbardziej podobnych" na żądanie. Bramkowane: bez backbone'u DINO zwraca czytelny
powód (zamiast cichej awarii).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from db.storage import project_dir, project_exists
from models.job import JobCreateRequest, JobType, PriorityClass, ResourceClass
from services.jobs.scheduler import submit_job
from services.jobs.store import (
    JobStoreError,
    find_active_job,
    interprocess_lock,
    write_json_atomic,
)

router = APIRouter()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _analysis_dir(project_id: str) -> Path:
    return project_dir(project_id) / "analysis" / "embedding"


def _read_json(path: Path, default=None):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


class EmbeddingAnalysisRequest(BaseModel):
    backbone: str | None = None  # nazwa checkpointu DINO; None → domyślny (najlżejszy)
    mislabel_margin: float = 0.02
    dup_threshold: float = 0.97  # cosine ≥ próg → near-duplikat
    dataset_run_id: str | None = None  # opublikowany run → split do wykrywania przecieku train/val


@router.post("/embedding")
def start_embedding_analysis(project_id: str, body: EmbeddingAnalysisRequest | None = None):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    from services.embedding_backbone import dino_runtime_available, resolve_dino_checkpoint

    if not dino_runtime_available():
        raise HTTPException(
            400,
            "Backbone DINO niedostępny - brak wag w MODELS_ROOT/dino "
            "(np. dinov2_vits14_reg4_pretrain.pth).",
        )
    body = body or EmbeddingAnalysisRequest()
    if body.dataset_run_id:
        # Wskazanie runu jest jawne — jeśli nie istnieje, mów od razu (bez cichego pominięcia).
        from services.dataset_runs import get_dataset_run_manifest

        if get_dataset_run_manifest(project_id, body.dataset_run_id) is None:
            raise HTTPException(404, f"Dataset run not found: {body.dataset_run_id}")
    run_dir = _analysis_dir(project_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    job = {
        "project_id": project_id,
        "backbone": body.backbone or (resolve_dino_checkpoint().name if resolve_dino_checkpoint() else None),
        "chip": 64,
        "min_size_px": 6,
        "mislabel_margin": body.mislabel_margin,
        "dup_threshold": body.dup_threshold,
        "dataset_run_id": body.dataset_run_id,
        "embedding_batch_size": 256,
        "ann_batch_size": 8192,
        "ann_candidate_k": 64,
        "exact_limit": 10_000,
        "mislabel_top_k": 2000,
        "created_at": _utc_now(),
    }
    # This legacy view uses one fixed directory. Serialize the active-job check,
    # snapshot writes and submission so concurrent clicks cannot replace the
    # inputs of a worker that is already queued.
    with interprocess_lock(run_dir / ".submit.lock") as acquired:
        if not acquired:
            raise HTTPException(409, "Embedding analysis submission is busy")
        if find_active_job(
            project_id,
            job_type=JobType.EMBEDDING_ANALYSIS.value,
            dedupe_key="embedding_analysis",
        ):
            raise HTTPException(409, "Embedding analysis is already running for this project")
        write_json_atomic(run_dir / "job.json", job)
        write_json_atomic(
            run_dir / "state.json",
            {"status": "queued", "created_at": job["created_at"]},
        )
        try:
            from services.predictor import resolve_device

            device = resolve_device()
            submitted = submit_job(
                project_id,
                JobCreateRequest(
                    job_type=JobType.EMBEDDING_ANALYSIS,
                    resource_class=(
                        ResourceClass.GPU_EXCLUSIVE
                        if device == "cuda"
                        else ResourceClass.CPU_HEAVY
                    ),
                    priority_class=PriorityClass.USER_BACKGROUND,
                    payload={"run_dir": str(run_dir), "analysis_request": job},
                    dedupe_key="embedding_analysis",
                ),
            )
        except JobStoreError as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            write_json_atomic(
                run_dir / "state.json",
                {"status": "failed", "error": f"Nie udało się uruchomić analizy: {exc}"},
            )
            raise HTTPException(500, f"Nie udało się uruchomić analizy: {exc}") from exc
    return {
        "status": "started",
        "job_id": submitted["job"]["job_id"],
        "backbone": job["backbone"],
    }


# Po ilu sekundach bez pulsu uznajemy workera za martwego. Worker bije co
# HEARTBEAT_INTERVAL_S (10 s), więc 60 s to 6× zapasu — przeżyje pauzę GC, zajęty dysk czy
# chwilowe obciążenie maszyny, a nie każe użytkownikowi czekać w nieskończoność.
_ANALYSIS_STALE_AFTER_S = 60.0


def _annotate_analysis_state(state: dict | None) -> dict | None:
    """Oznacz `stale`, gdy stan twierdzi że biegnie, a worker przestał bić pulsem.

    Worker analizy żyje w OSOBNYM procesie, więc — inaczej niż przy imporcie — nie da się
    sprawdzić żywego wątku. Twardy crash (abort w bibliotece natywnej) nie przechodzi przez
    `except`, więc bez tego `state.json` zostawałby na "running" na zawsze, a UI wisiałoby
    na „w toku". Zamiast mutować stan, tylko go anotujemy — decyzję (ponowny start) zostawiamy
    użytkownikowi, tak samo jak robi to `_annotate_import_job`.
    """
    if not state:
        return state
    if state.get("status") not in {"queued", "running"}:
        state["stale"] = False
        return state
    stamp = state.get("heartbeat") or state.get("created_at")
    age: float | None = None
    if stamp:
        try:
            started = datetime.fromisoformat(str(stamp))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - started).total_seconds()
        except ValueError:
            age = None  # nieczytelny znacznik nie może wywrócić odczytu statusu
    state["heartbeat_age_s"] = round(age, 1) if age is not None else None
    state["stale"] = age is not None and age > _ANALYSIS_STALE_AFTER_S
    return state


@router.get("/embedding")
def get_embedding_analysis(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    run_dir = _analysis_dir(project_id)
    return {
        "state": _annotate_analysis_state(_read_json(run_dir / "state.json", default=None)),
        "result": _read_json(run_dir / "result.json", default=None),
    }


@router.get("/embedding/chip/{filename}")
def get_embedding_chip(project_id: str, filename: str):
    """Serwuj miniaturę przykładu klasy (galeria). Nazwa pliku pochodzi z `class_examples`."""
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.jpg", filename):
        raise HTTPException(400, "Invalid chip filename")
    path = _analysis_dir(project_id) / "chips" / filename
    if not path.is_file():
        raise HTTPException(404, "Chip not found")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/embedding/nearest")
def get_nearest_objects(
    project_id: str,
    annotation_id: str = Query(...),
    k: int = Query(20, ge=1, le=100),
):
    """„20 najbardziej podobnych" do wskazanej adnotacji — z zapisanych embeddingów."""
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    run_dir = _analysis_dir(project_id)
    manifest_path = run_dir / "embedding_index.json"
    if manifest_path.is_file():
        from services.embedding_index import (
            annotation_row,
            nearest_from_artifacts,
        )

        query_index = annotation_row(run_dir, annotation_id)
        if query_index is None:
            raise HTTPException(404, "Adnotacji nie ma w indeksie analizy (odśwież analizę).")
        classes = {c["id"]: c["name"] for c in _load_project_classes(project_id)}
        return {
            "query_annotation_id": annotation_id,
            "neighbors": nearest_from_artifacts(
                run_dir, query_index, k=k, class_names=classes
            ),
        }

    # Backward-compatible read of schema v1 artifacts produced before P1.4.
    objects = _read_json(run_dir / "objects.json", default=None)
    emb_path = run_dir / "embeddings.npy"
    if objects is None or not emb_path.is_file():
        raise HTTPException(404, "Brak wyniku analizy - uruchom analizę embeddingów najpierw.")
    query_index = next((i for i, obj in enumerate(objects) if obj.get("annotation_id") == annotation_id), None)
    if query_index is None:
        raise HTTPException(404, "Adnotacji nie ma w indeksie analizy (odśwież analizę).")

    import numpy as np

    from services.embedding_analysis import nearest_objects

    embeddings = np.load(emb_path)
    classes = {c["id"]: c["name"] for c in _load_project_classes(project_id)}
    index = {"embeddings": embeddings, "objects": objects, "class_names": classes}
    return {"query_annotation_id": annotation_id, "neighbors": nearest_objects(index, query_index, k=k)}


def _load_project_classes(project_id: str):
    from db.storage import load_json

    return load_json(project_id, "classes", default=[])
