"""Package-based scene source preview, import, rescan and preparation."""

from __future__ import annotations

import hashlib
import json
import copy
import concurrent.futures
import multiprocessing
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from db.storage import (
    DATA_DIR,
    create_project_root,
    delete_scene_data,
    list_scene_ids,
    load_json,
    load_scene_json,
    project_dir,
    project_exists,
    save_json,
    save_scene_json,
    scene_dir,
)
from models.job import ACTIVE_JOB_STATUSES, JobCreateRequest, JobType, PriorityClass, ResourceClass
from models.project import Project, default_project_profile
from models.scene import Scene
from models.scene_source import (
    ProjectFromSourcesCreate,
    SceneAssetSelectionRequest,
    SceneMigrationApplyRequest,
    SceneImportConfigRequest,
    SceneImportMode,
    SceneImportPreviewRequest,
    SceneImportResumeRequest,
    SceneSelectionDecision,
    ScenePrepareRequest,
    SceneSourceCreate,
    SceneSourceRelinkRequest,
    SceneSourcesApplyRequest,
)
from services.jobs.processes import process_status
from services.jobs.scheduler import cancel_job, retry_job, submit_job
from services.jobs.store import (
    JobStoreError,
    find_active_job,
    job_dir as common_job_dir,
    jobs_dir as common_jobs_dir,
    list_jobs,
    mutate_state,
    read_json as read_job_json,
    read_job_detail,
    read_state,
    validate_id as validate_job_id,
    write_json_atomic as write_job_json,
)
from services.preprocessing_profiles import ensure_preprocessing_profiles
from services.scene_loader import SCENE_INFO_VERSION, get_scene_info
from services.scene_identity import compute_working_variant_fingerprint
from services.scene_overviews import (
    apply_source_overview_metadata,
    inspect_source_overviews,
    source_overviews_are_display_ready,
)
from services.scene_manifest import rebuild_scenes_index, write_scene_manifest
from services.scene_packages import PROVIDER_MODALITY, get_resolver, supported_providers
from services.scene_packages.archives import ARCHIVE_STATUSES
from services.scene_packages.import_report import (
    ImportReportBuilder,
    anonymize as anonymize_report,
    list_reports as list_import_reports,
    load_report as load_import_report,
    manifest_annotations as report_manifest_annotations,
    reports_dir as import_reports_dir,
    scene_entry as build_report_scene_entry,
)
from services.scene_packages.mosaics import inspect_parts as inspect_mosaic_parts
from services.scene_packages.migration import (
    apply as apply_migration,
    apply_decisions as apply_migration_decisions,
    plan as migration_plan,
    public_plan as public_migration_plan,
)
from services.scene_packages.preview_tree import (
    build as build_preview_tree,
    free_space_bytes as preview_free_space,
)
from services.scene_packages.contracts import decision_uid
from services.scene_packages.identity import (
    compute_scene_package_identity,
    invalidate_scene_source_derivatives,
    scene_identity_scope,
)
from services.scene_packages.scan_cache import (
    SceneScanCancelled,
    SourceChangedDuringScan,
    scan_source_cached,
)
from services.scene_packages.working_view import (
    DISPLAY_OVERVIEW_RASTER_KINDS,
    build_working_variant_definition,
    clear_all_scene_display_overviews,
    create_vrt,
    prepare_pansharpened_cog,
    working_variant_id,
)
from services.scene_raster_resolver import (
    SceneRasterResolver,
    ensure_scene_display_overviews,
    invalidate_scene_render_caches,
    sync_scene_source_overviews,
)
from services.scene_sources import (
    canonical_source_root,
    extended_path,
    load_scene_sources,
    plain_path,
    new_source_id,
    resolve_source_asset,
    resolve_source_root,
    save_scene_sources,
    source_by_id,
    utc_now,
)


router = APIRouter()
PREVIEW_TTL = timedelta(minutes=30)
SCENE_SCAN_JOB_SCOPE = "runtime.scene-scans"
ACTIVE_PREPARATIONS: dict[str, threading.Event] = {}
ACTIVE_PREPARATIONS_LOCK = threading.Lock()


def _scene_source_overviews_display_ready(scene: dict[str, Any]) -> bool:
    info = scene.get("scene_info") or {}
    return source_overviews_are_display_ready(
        info.get("source_overviews") or {},
        width=info.get("width"),
        height=info.get("height"),
    )
# Anulowanie trwających importów: wątek roboczy sprawdza swój Event między scenami.
ACTIVE_IMPORT_JOBS: dict[str, threading.Event] = {}
ACTIVE_IMPORT_JOBS_LOCK = threading.Lock()
DEFAULT_SCENE_IMPORT_MODE: SceneImportMode = "background"
#: Domyslnie WYLACZONE. Budowa COG pelnej rozdzielczosci kosztuje dziesiatki minut i
#: kilka GB na scene, wiec nie moze startowac sama, zanim uzytkownik o tym zdecyduje.
#: Zmiana dotyczy takze projektow, ktore nigdy nie zapisaly tej opcji jawnie.
DEFAULT_AUTO_FULLRES_COG_ENABLED = False


def _common_scene_import_jobs_enabled() -> bool:
    return os.environ.get("GEOTILE_SCENE_IMPORT_COMMON_JOBS", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _common_scene_preparation_jobs_enabled() -> bool:
    default = "1" if _common_scene_import_jobs_enabled() else "0"
    return os.environ.get("GEOTILE_SCENE_PREPARATION_COMMON_JOBS", default).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _normalize_import_mode(value: Any) -> SceneImportMode:
    mode = str(value or DEFAULT_SCENE_IMPORT_MODE).strip().lower()
    if mode not in {"on_demand", "background", "prepare_all"}:
        return DEFAULT_SCENE_IMPORT_MODE
    return mode  # type: ignore[return-value]


def _project_import_mode(project_id: str, requested: Any = None) -> SceneImportMode:
    if requested is not None:
        return _normalize_import_mode(requested)
    config = load_json(project_id, "scene_import_config", default={}) or {}
    return _normalize_import_mode(config.get("import_mode"))


def _project_auto_fullres_cog_enabled(project_id: str) -> bool:
    config = load_json(project_id, "scene_import_config", default={}) or {}
    value = config.get("auto_fullres_cog_enabled", DEFAULT_AUTO_FULLRES_COG_ENABLED)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _save_project_import_config(
    project_id: str,
    *,
    import_mode: SceneImportMode | None = None,
    auto_fullres_cog_enabled: bool | None = None,
) -> dict[str, Any]:
    current = load_json(project_id, "scene_import_config", default={}) or {}
    selected_mode = _normalize_import_mode(
        import_mode if import_mode is not None else current.get("import_mode")
    )
    selected_auto_fullres = (
        bool(auto_fullres_cog_enabled)
        if auto_fullres_cog_enabled is not None
        else _project_auto_fullres_cog_enabled(project_id)
    )
    payload = {
        "schema_name": "geotile_scene_import_config",
        "schema_version": 2,
        "import_mode": selected_mode,
        "auto_fullres_cog_enabled": selected_auto_fullres,
        "updated_at": utc_now(),
    }
    save_json(
        project_id,
        "scene_import_config",
        payload,
    )
    return payload


def _save_project_import_mode(project_id: str, mode: SceneImportMode) -> None:
    _save_project_import_config(project_id, import_mode=mode)


class SceneSourceAddRequest(BaseModel):
    source: SceneSourceCreate


def _preview_dir() -> Path:
    path = DATA_DIR / "runtime" / "scene-import-previews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _preview_path(preview_id: str) -> Path:
    if not preview_id or any(char not in "0123456789abcdef" for char in preview_id):
        raise HTTPException(400, "Invalid preview_id")
    return _preview_dir() / f"{preview_id}.json"


def _write_preview(payload: dict[str, Any]) -> None:
    path = _preview_path(payload["preview_id"])
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(temp, path)


def _load_preview(preview_id: str) -> dict[str, Any]:
    path = _preview_path(preview_id)
    if not path.is_file():
        raise HTTPException(404, "Scene import preview not found")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        created_at = datetime.fromisoformat(payload["created_at"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "Scene import preview is invalid") from exc
    if datetime.now(timezone.utc) - created_at > PREVIEW_TTL:
        path.unlink(missing_ok=True)
        raise HTTPException(410, "Scene import preview expired; scan the sources again")
    return payload


def _resolve_input_root(value: str) -> Path:
    """Korzen skanowania w formie rozszerzonej (patrz `scene_sources.extended_path`).

    Drugie — obok `resolve_source_root` — wejscie w zrodlo. Bez prefiksu tutaj skan
    gubi po cichu kazdy plik, ktorego pelna sciezka przekracza 260 znakow, a wlasnie
    tedy idzie „Skanuj i sprawdz". Prefiks nakladamy PO `resolve()`, bo normalizacja
    potrafi go zdjac.
    """
    path = Path(value).expanduser().resolve(strict=False)
    if not path.is_dir():
        raise HTTPException(400, f"Source folder not found: {value}")
    return extended_path(path)


def _validate_unique_source_roots(sources: list[dict[str, Any]]) -> None:
    """Reject aliases of a root before a project can persist duplicate sources."""

    seen: dict[str, str] = {}
    for source in sources:
        key = canonical_source_root(str(source.get("root_path") or ""))
        source_id = str(source.get("source_id") or "new source")
        previous = seen.get(key)
        if previous and previous != source_id:
            raise HTTPException(409, "The same canonical scene source root is already registered")
        seen[key] = source_id


def _package_decision_key(package: dict[str, Any]) -> str:
    return str(
        package.get("decision_uid")
        or (package.get("selection") or {}).get("decision_uid")
        or decision_uid(
            str(package.get("source_id") or ""),
            str((package.get("selection") or {}).get("product_uid") or package.get("package_id") or ""),
        )
    )


def _resolve_preview_decisions(preview: dict[str, Any], decisions: list[Any]) -> dict[str, Any]:
    """Bind decisions to source-qualified products, retaining unambiguous v1 input."""

    by_uid = {_package_decision_key(package): package for package in preview.get("packages", [])}
    by_package: dict[str, list[dict[str, Any]]] = {}
    for package in preview.get("packages", []):
        by_package.setdefault(str(package.get("package_id") or ""), []).append(package)
    resolved: dict[str, Any] = {}
    for item in decisions:
        requested_uid = str(getattr(item, "decision_uid", None) or "")
        package_id = str(getattr(item, "package_id", None) or "")
        if requested_uid:
            package = by_uid.get(requested_uid)
            if package is None:
                raise HTTPException(400, f"Unknown decision_uid: {requested_uid}")
            if str(package.get("package_id") or "") != package_id:
                raise HTTPException(400, f"decision_uid does not match package {package_id}")
        else:
            candidates = by_package.get(package_id, [])
            if not candidates:
                raise HTTPException(400, f"Unknown package in asset decision: {package_id}")
            if len(candidates) != 1:
                raise HTTPException(400, f"Ambiguous package decision; decision_uid is required: {package_id}")
            package = candidates[0]
        key = _package_decision_key(package)
        if key in resolved:
            raise HTTPException(400, f"Duplicate asset decision for package {package_id}")
        resolved[key] = item
    return resolved


def _scan_source_records(
    source_records: list[dict[str, Any]],
    expected_modality: str | None,
    *,
    should_cancel=None,
    progress_callback=None,
    source_complete_callback=None,
) -> dict[str, Any]:
    packages: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    detected_modalities: set[str] = set()
    cache_results: list[dict[str, Any]] = []
    for source_index, source in enumerate(source_records, start=1):
        if should_cancel is not None and should_cancel():
            raise SceneScanCancelled("Scene source scan cancelled")
        provider = source["provider"]
        root = _resolve_input_root(source["root_path"])
        provider_modality = PROVIDER_MODALITY.get(provider)
        if provider_modality:
            detected_modalities.add(provider_modality)
            if expected_modality and provider_modality != expected_modality:
                raise HTTPException(
                    400,
                    f"Provider {provider} is {provider_modality}, but project modality is {expected_modality}",
                )
        if progress_callback is not None:
            progress_callback({
                "stage": "source",
                "source_index": source_index,
                "source_total": len(source_records),
                "source_id": source["source_id"],
                "root_path": plain_path(root),
            })

        def source_progress(stage: str, current: int, total: int | None, detail: dict[str, Any]) -> None:
            if progress_callback is not None:
                progress_callback({
                    "stage": stage,
                    "current": current,
                    "total": total,
                    "source_index": source_index,
                    "source_total": len(source_records),
                    "source_id": source["source_id"],
                    **detail,
                })

        scanned = scan_source_cached(
            root,
            provider,
            should_cancel=should_cancel,
            progress=source_progress,
        )
        discovered = scanned["packages"]
        source_diagnostics = scanned["diagnostics"]
        cache_results.append({
            "source_id": source["source_id"],
            **scanned["cache"],
        })
        ignored_package_ids = set(source.get("ignored_package_ids") or [])
        for package in discovered:
            if package.get("package_id") in ignored_package_ids:
                continue
            package["source_id"] = source["source_id"]
            uid = decision_uid(
                str(source["source_id"]),
                str((package.get("selection") or {}).get("product_uid") or package["package_id"]),
            )
            package["decision_uid"] = uid
            package.setdefault("selection", {})["decision_uid"] = uid
            package["source_snapshot"] = {
                "root_path": plain_path(root),
                "root_mtime_ns": root.stat().st_mtime_ns,
                "assets": [
                    {
                        "asset_id": asset.get("asset_id"),
                        "relative_path": asset.get("relative_path"),
                        "size": asset.get("size"),
                        "mtime_ns": asset.get("mtime_ns"),
                    }
                    for asset in package.get("assets") or []
                ],
            }
            packages.append(package)
        diagnostics.extend({"source_id": source["source_id"], **item} for item in source_diagnostics)
        if source_complete_callback is not None:
            source_complete_callback({
                "source_index": source_index,
                "source_total": len(source_records),
                "source_id": source["source_id"],
                "packages": list(packages),
                "diagnostics": list(diagnostics),
                "detected_modalities": sorted(detected_modalities),
                "cache": list(cache_results),
            })
    if len(detected_modalities) > 1:
        raise HTTPException(400, "A project cannot combine SAR and EO scene sources")
    return {
        "packages": packages,
        "diagnostics": diagnostics,
        "detected_modalities": sorted(detected_modalities),
        "scan_cache": cache_results,
    }


@router.get("/scene-import/providers")
def list_scene_import_providers():
    return [
        {"id": provider, "modality": PROVIDER_MODALITY.get(provider)}
        for provider in supported_providers()
    ]


def _new_preview_source_records(body: SceneImportPreviewRequest) -> list[dict[str, Any]]:
    source_records = []
    for item in body.sources:
        root = _resolve_input_root(item.root_path)
        source_records.append({
            # Tozsamosc zrodla i klucz porownania licza sie na sciezce BEZ prefiksu:
            # inaczej `source_id` zmienilby sie dla istniejacych projektow, a wykrywanie
            # duplikatow porownywaloby dwie rozne formy tej samej sciezki.
            "source_id": new_source_id(item.provider, plain_path(root)),
            "provider": item.provider,
            "root_path": plain_path(root),
            "canonical_root": canonical_source_root(plain_path(root)),
            "enabled": item.enabled,
            "added_at": utc_now(),
            "last_scan_at": None,
            "last_scan_status": None,
        })
    _validate_unique_source_roots(source_records)
    return source_records


def _scene_scan_dedupe_key(
    source_records: list[dict[str, Any]],
    expected_modality: str | None,
) -> str:
    value = {
        "sources": sorted(
            (
                str(source.get("provider") or ""),
                canonical_source_root(str(source.get("root_path") or "")),
            )
            for source in source_records
        ),
        "expected_modality": expected_modality,
    }
    digest = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return f"scene-scan:{digest}"


def _submit_scene_scan_job(
    source_records: list[dict[str, Any]],
    expected_modality: str | None,
    *,
    purpose: str,
    project_id: str | None = None,
) -> dict[str, Any]:
    return submit_job(
        SCENE_SCAN_JOB_SCOPE,
        JobCreateRequest(
            job_type=JobType.SCENE_SCAN,
            # Package discovery reads names and compact metadata only.  It must not
            # wait behind multi-minute raster overview/import jobs.
            resource_class=ResourceClass.IO_METADATA,
            priority_class=(
                PriorityClass.INTERACTIVE
                if purpose == "create_project"
                else PriorityClass.USER_BACKGROUND
            ),
            payload={
                "sources": copy.deepcopy(source_records),
                "expected_modality": expected_modality,
                "purpose": purpose,
                "target_project_id": project_id,
            },
            dedupe_key=_scene_scan_dedupe_key(source_records, expected_modality),
        ),
    )


def _scan_job_public_result(job_id: str) -> dict[str, Any]:
    try:
        detail = read_job_detail(SCENE_SCAN_JOB_SCOPE, validate_job_id(job_id))
    except JobStoreError as exc:
        raise HTTPException(404, str(exc)) from exc
    state = detail["state"]
    result: dict[str, Any] = {
        "scan_job_id": job_id,
        "status": state.get("status"),
        "phase": state.get("phase"),
        "current": state.get("current"),
        "total": state.get("total"),
        "source_index": state.get("scan_source_index"),
        "source_total": state.get("scan_source_total"),
        "error": state.get("error"),
    }
    preview_id = (detail.get("artifacts") or {}).get("preview_id")
    if state.get("status") == "completed" and preview_id:
        result["preview"] = _public_preview(_load_preview(str(preview_id)))
    return result


@router.post("/scene-import/previews", status_code=202)
def preview_scene_sources(body: SceneImportPreviewRequest):
    source_records = _new_preview_source_records(body)
    try:
        submitted = _submit_scene_scan_job(
            source_records,
            body.modality,
            purpose="create_project",
        )
    except JobStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "status": "queued",
        "scan_job_id": submitted["job"]["job_id"],
    }


@router.get("/scene-import/scan-jobs/{job_id}")
def get_scene_scan_job(job_id: str):
    return _scan_job_public_result(job_id)


@router.post("/scene-import/scan-jobs/{job_id}/cancel")
def cancel_scene_scan_job(job_id: str):
    try:
        return cancel_job(SCENE_SCAN_JOB_SCOPE, validate_job_id(job_id))
    except JobStoreError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/scene-import/scan-jobs/{job_id}/resume", status_code=202)
def resume_scene_scan_job(job_id: str):
    try:
        submitted = retry_job(SCENE_SCAN_JOB_SCOPE, validate_job_id(job_id))
    except JobStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "status": "queued",
        "scan_job_id": submitted["job"]["job_id"],
        "retry_of": job_id,
    }


def run_scene_scan_common_job(spec: dict[str, Any], context: Any) -> dict[str, Any]:
    """Durable worker entry point; only a stable complete result becomes a preview."""

    payload = spec.get("payload") or {}
    sources = copy.deepcopy(payload.get("sources") or [])
    if not sources:
        raise RuntimeError("Scene scan job has no sources")
    partial_path = common_job_dir(SCENE_SCAN_JOB_SCOPE, str(spec["job_id"])) / "partial_preview.json"

    def progress(item: dict[str, Any]) -> None:
        context.update(
            phase=f"scene_scan:{item.get('stage') or 'scan'}",
            current=item.get("current"),
            total=item.get("total"),
            scan_source_index=item.get("source_index"),
            scan_source_total=item.get("source_total"),
            scan_source_id=item.get("source_id"),
            scan_relative_root=item.get("relative_root"),
        )
        context.emit("scene_scan_progress", item)

    def checkpoint(item: dict[str, Any]) -> None:
        write_job_json(partial_path, {
            "schema_name": "geotile_scene_import_preview_partial",
            "schema_version": 1,
            "status": "partial",
            "scan_job_id": spec["job_id"],
            "updated_at": utc_now(),
            "sources": sources[: int(item["source_index"])],
            "packages": item["packages"],
            "diagnostics": item["diagnostics"],
            "detected_modalities": item["detected_modalities"],
            "scan_cache": item["cache"],
        })
        context.update(partial_source_count=item["source_index"])

    try:
        scan = _scan_source_records(
            sources,
            payload.get("expected_modality"),
            should_cancel=context.cancel_requested,
            progress_callback=progress,
            source_complete_callback=checkpoint,
        )
    except SourceChangedDuringScan:
        # Preserve the machine-readable reason in the worker error and never call
        # `_write_preview` for this unstable result.
        raise
    if context.cancel_requested():
        raise SceneScanCancelled("Scene source scan cancelled before preview publication")

    preview_id = uuid.uuid4().hex
    preview_payload = {
        "schema_name": "geotile_scene_import_preview",
        "schema_version": 2,
        "preview_id": preview_id,
        "created_at": utc_now(),
        "expires_at": (datetime.now(timezone.utc) + PREVIEW_TTL).isoformat(),
        "scan_job_id": spec["job_id"],
        "scan_status": "complete",
        "sources": sources,
        **scan,
        # P2.1: pogrupowana postac tego samego wyniku. Plaska lista `packages` zostaje —
        # jest kontraktem decyzji i apply — a drzewo jest widokiem obok niej.
        "tree": build_preview_tree(
            scan.get("packages") or [],
            sources,
            free_bytes=preview_free_space(DATA_DIR),
        ),
    }
    _write_preview(preview_payload)
    return {
        "preview_id": preview_id,
        "preview_path": str(_preview_path(preview_id)),
        "partial_preview_path": str(partial_path),
        "package_count": len(scan.get("packages") or []),
        "scan_cache": scan.get("scan_cache") or [],
    }


@router.post("/projects/from-sources")
def create_project_from_sources(body: ProjectFromSourcesCreate, background_tasks: BackgroundTasks):
    preview = _load_preview(body.preview_id)
    _validate_requested_sources(preview, body.sources)
    _validate_unique_source_roots(preview.get("sources", []))
    _validate_preview_decisions(preview, body.decisions)
    profile = body.profile or default_project_profile(
        modality=(preview.get("detected_modalities") or ["EO"])[0],
        georeferencing="GEO",
    )
    _validate_source_modalities(preview["sources"], profile.modality)
    _validate_preview_inputs(preview, body.decisions)

    project_id = uuid.uuid4().hex[:12]
    root = create_project_root(project_id, body.name, body.project_location)
    first_source_root = preview["sources"][0]["root_path"] if preview.get("sources") else ""
    project = Project(
        id=project_id,
        name=body.name,
        scene_folder=first_source_root,
        project_root=str(root),
        created_in_appdata=body.project_location is None,
        profile=profile,
        scene_count=0,
    )
    save_json(project_id, "project", project.model_dump())
    save_scene_sources(project_id, {"sources": preview["sources"]})
    save_json(project_id, "classes", _load_classes(body.classes_file))
    _tile_size = int(body.tile_size)
    _tile_buffer = max(0, min(int(body.buffer), _tile_size // 2))
    save_json(project_id, "tiling_config", {"tile_size": _tile_size, "buffer": _tile_buffer})
    save_json(project_id, "dataset_config", _dataset_defaults(profile.model_dump()))
    _save_project_import_mode(project_id, body.import_mode)
    ensure_preprocessing_profiles(project_id)

    # Katalogowanie scen (per-scena get_scene_info) potrafi trwać minuty przy setkach
    # wielkich rastrów, więc NIE blokujemy żądania — uruchamiamy je w wątku roboczym,
    # który raportuje postęp do import_jobs/<job_id>.json (odpytywane przez UI paskiem).
    total = _count_importable(preview, body.decisions)
    if _common_scene_import_jobs_enabled():
        try:
            submitted = _submit_scene_import_job(
                project_id,
                preview=preview,
                decisions=body.decisions,
                skip_complete=False,
                import_mode=body.import_mode,
            )
        except JobStoreError as exc:
            raise HTTPException(409, str(exc)) from exc
        job_id = str(submitted["job"]["job_id"])
    else:
        job_id = uuid.uuid4().hex
        _init_import_job(project_id, job_id, total)
        threading.Thread(
            target=_run_import_job,
            args=(project_id, job_id, preview, body.decisions),
            name=f"scene-import-{project_id[:8]}",
            daemon=True,
        ).start()
    project_data = load_json(project_id, "project", default={})
    return {**project_data, "import_job_id": job_id, "import_state": "running", "total": total}


def _submit_scene_import_job(
    project_id: str,
    *,
    preview: dict[str, Any] | None = None,
    decisions: list[Any] | None = None,
    skip_complete: bool,
    import_mode: SceneImportMode | None = None,
) -> dict[str, Any]:
    """Submit an immutable import snapshot to the persistent common Job Manager."""

    decision_payload = [
        item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
        for item in (decisions or [])
    ]
    selected_mode = _project_import_mode(project_id, import_mode)
    payload: dict[str, Any] = {
        "resume": bool(skip_complete),
        "decisions": decision_payload,
        "import_total": _count_importable(preview or {}, decisions or []),
        "import_mode": selected_mode,
    }
    if preview is not None:
        payload["preview"] = preview
    submitted = submit_job(
        project_id,
        JobCreateRequest(
            job_type=JobType.SCENE_IMPORT,
            resource_class=ResourceClass.IO_HEAVY,
            priority_class=PriorityClass.USER_BACKGROUND,
            payload=payload,
            dedupe_key=f"scene-import:{project_id}",
        ),
    )
    try:
        write_job_json(
            common_jobs_dir(project_id) / "latest_scene_import.json",
            {"job_id": submitted["job"]["job_id"], "created_at": submitted["job"]["created_at"]},
        )
    except OSError:
        pass
    return submitted


def _ensure_scene_import_slot_available(project_id: str) -> None:
    active = find_active_job(
        project_id,
        job_type=JobType.SCENE_IMPORT.value,
        dedupe_key=f"scene-import:{project_id}",
    )
    if active is not None:
        raise HTTPException(
            409,
            f"Scene import job '{active['job']['job_id']}' is already active for this project",
        )


def _submit_scene_preparation_job(
    project_id: str,
    scene_id: str,
    rgb_bands: list[int],
) -> dict[str, Any]:
    return submit_job(
        project_id,
        JobCreateRequest(
            job_type=JobType.SCENE_PREPARATION,
            resource_class=ResourceClass.IO_HEAVY,
            priority_class=PriorityClass.INTERACTIVE,
            payload={"scene_id": scene_id, "rgb_bands": rgb_bands},
            dedupe_key=f"scene-prepare:{project_id}:{scene_id}",
        ),
    )


def _submit_scene_fullres_job(
    project_id: str,
    scene_id: str,
    *,
    retry_of: str | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    """Zglos budowe pelnorozdzielczego derywatu (R1.2).

    `dedupe_key` sprawia, ze wielokrotne otwarcie tej samej sceny nie kolejkuje drugiego
    dwudziestominutowego zadania. `IO_HEAVY` ma domyslny limit 1, wiec „jeden ciezki
    derywat naraz" wynika z istniejacego mechanizmu, a nie z osobnego licznika.
    """
    from services.scene_packages.fullres_derivative import build_job_payload, dedupe_key

    scene = load_scene_json(project_id, scene_id, "scene", default={}) or {}
    manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={}) or {}
    identity = manifest.get("source_identity") or {}
    working = manifest.get("working_view") or {}
    payload = build_job_payload(
        scene_id=scene_id,
        source_fingerprint=identity.get("source_scene_fingerprint")
        or identity.get("source_package_fingerprint"),
        variant_id=working.get("variant_id") or scene.get("working_variant_id"),
        source_revision=scene.get("overview_fingerprint"),
    )
    return submit_job(
        project_id,
        JobCreateRequest(
            job_type=JobType.SCENE_FULLRES_DERIVATIVE,
            resource_class=ResourceClass.IO_HEAVY,
            priority_class=PriorityClass.USER_BACKGROUND,
            payload=payload,
            dedupe_key=dedupe_key(project_id, scene_id),
        ),
        retry_of=retry_of,
        attempt=attempt,
    )


def _submit_scene_overview_job(project_id: str, scene_id: str) -> dict[str, Any]:
    return submit_job(
        project_id,
        JobCreateRequest(
            job_type=JobType.SCENE_OVERVIEW,
            resource_class=ResourceClass.IO_HEAVY,
            priority_class=PriorityClass.INTERACTIVE,
            payload={"scene_id": scene_id},
            dedupe_key=f"scene-overview:{project_id}:{scene_id}",
        ),
    )


def _common_import_view(detail: dict[str, Any]) -> dict[str, Any]:
    """Map common Job state to the legacy import contract consumed by the current UI."""

    spec = detail["job"]
    state = detail["state"]
    if spec.get("job_type") != JobType.SCENE_IMPORT.value:
        raise JobStoreError(f"Job is not a scene import: {spec.get('job_id')}")
    status = str(state.get("status") or "queued")
    catalogue_ready = bool(state.get("catalogue_ready"))
    if catalogue_ready:
        legacy_state = "done"
    elif status == "completed":
        legacy_state = "error"
    elif status == "cancelled":
        legacy_state = "cancelled"
    elif status in {"failed", "interrupted"}:
        legacy_state = "error"
    else:
        legacy_state = "running"
    active = status in {"queued", "starting", "running", "cancelling"}
    process_state = process_status(state.get("process")) if state.get("process") else "unknown"
    live = active and (status in {"queued", "starting"} or process_state == "live")
    phase = str(state.get("import_phase") or "catalogue")
    if phase not in {"catalogue", "identity", "overviews", "complete"}:
        phase = "catalogue"
    total = int(state.get("import_total") or (spec.get("payload") or {}).get("import_total") or 0)
    done = int(state.get("import_done") or 0)
    overviews_total = int(state.get("overviews_total") or 0)
    overviews_done = int(state.get("overviews_done") or 0)
    import_mode = _normalize_import_mode(
        state.get("import_mode") or (spec.get("payload") or {}).get("import_mode")
    )
    overviews_deferred = bool(state.get("overviews_deferred")) or import_mode == "on_demand"
    error = state.get("error")
    if status == "completed" and not catalogue_ready and not error:
        error = "Scene catalogue contains failed or unresolved products"
    if status == "interrupted" and not error:
        error = "Scene import worker was interrupted; resume the import to continue."
    return {
        "schema_name": "geotile_scene_import_job",
        "schema_version": 3,
        "job_id": spec.get("job_id"),
        "project_id": spec.get("project_id"),
        "state": legacy_state,
        "phase": phase,
        "total": total,
        "done": done,
        "added": int(state.get("import_added") or 0),
        "updated": int(state.get("import_updated") or 0),
        "blocked": int(state.get("import_blocked") or 0),
        "failed": int(state.get("import_failed") or 0),
        "identity_total": int(state.get("identity_total") or 0),
        "identity_done": int(state.get("identity_done") or 0),
        "overviews_total": overviews_total,
        "overviews_done": overviews_done,
        "overview_active_scene_ids": state.get("overview_active_scene_ids") or [],
        "overview_profile": state.get("overview_profile"),
        "import_mode": import_mode,
        "overviews_deferred": overviews_deferred,
        "current": state.get("import_current"),
        "recent": state.get("import_recent") or [],
        "errors": state.get("import_errors") or [],
        "report": state.get("import_report"),
        "started_at": state.get("started_at") or state.get("created_at"),
        "updated_at": state.get("updated_at"),
        "error": error,
        "live": live,
        "stale": legacy_state == "running" and not live,
        "overviews_incomplete": bool(
            not overviews_deferred and overviews_total and overviews_done < overviews_total
        ),
    }


def _read_common_import(project_id: str, job_id: str) -> dict[str, Any] | None:
    try:
        detail = read_job_detail(project_id, job_id)
    except JobStoreError:
        return None
    if detail["job"].get("job_type") != JobType.SCENE_IMPORT.value:
        return None
    return _common_import_view(detail)


def _latest_common_import(project_id: str) -> dict[str, Any] | None:
    pointer = read_job_json(common_jobs_dir(project_id) / "latest_scene_import.json", default={}) or {}
    pointer_job_id = pointer.get("job_id")
    if pointer_job_id:
        current = _read_common_import(project_id, str(pointer_job_id))
        if current is not None:
            return current
    candidates = [
        item for item in list_jobs(project_id)
        if item["job"].get("job_type") == JobType.SCENE_IMPORT.value
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda item: str(item["job"].get("created_at") or ""))
    return _common_import_view(latest)


@router.get("/projects/{project_id}/scene-import/config")
def get_scene_import_config(project_id: str):
    _require_project(project_id)
    return {
        "schema_name": "geotile_scene_import_config",
        "schema_version": 2,
        "import_mode": _project_import_mode(project_id),
        "auto_fullres_cog_enabled": _project_auto_fullres_cog_enabled(project_id),
    }


@router.put("/projects/{project_id}/scene-import/config")
def update_scene_import_config(project_id: str, body: SceneImportConfigRequest):
    _require_project(project_id)
    return _save_project_import_config(
        project_id,
        import_mode=body.import_mode,
        auto_fullres_cog_enabled=body.auto_fullres_cog_enabled,
    )


def _annotate_import_job(project_id: str, job: dict[str, Any]) -> dict[str, Any]:
    """Oznacz job: `live` (żywy wątek), `stale` (twierdzi że biegnie bez wątku → restart apki),
    `overviews_incomplete` (piramidy niedokończone). Dzięki temu UI proponuje wznowienie/dokończenie
    — także dla stanu `done`, gdy piramidy budowane „po done" zostały porzucone przez restart."""
    key = f"{project_id}:{job.get('job_id')}"
    with ACTIVE_IMPORT_JOBS_LOCK:
        live = key in ACTIVE_IMPORT_JOBS
    job["live"] = live
    if job.get("state") == "running":
        job["stale"] = not live
    ovr_total = job.get("overviews_total") or 0
    ovr_done = job.get("overviews_done") or 0
    job["overviews_incomplete"] = bool(ovr_total and ovr_done < ovr_total)
    return job


@router.get("/projects/{project_id}/import-jobs/{job_id}")
def get_import_job(project_id: str, job_id: str):
    _require_project(project_id)
    common = _read_common_import(project_id, job_id)
    if common is not None:
        return common
    path = _import_jobs_dir(project_id) / f"{_safe_job_id(job_id)}.json"
    if not path.is_file():
        raise HTTPException(404, "Import job not found")
    return _annotate_import_job(project_id, json.loads(path.read_text(encoding="utf-8")))


@router.get("/projects/{project_id}/import-jobs")
def get_latest_import_job(project_id: str):
    _require_project(project_id)
    common = _latest_common_import(project_id)
    jobs_dir = _import_jobs_dir(project_id)
    files = sorted(jobs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if common is None and not files:
        return {"state": "none"}
    if not files:
        return common
    legacy = _annotate_import_job(project_id, json.loads(files[0].read_text(encoding="utf-8")))
    if common is None:
        return legacy
    try:
        common_stamp = datetime.fromisoformat(str(common.get("updated_at") or common.get("started_at"))).timestamp()
    except (TypeError, ValueError):
        common_stamp = 0.0
    return common if common_stamp >= files[0].stat().st_mtime else legacy


@router.get("/projects/{project_id}/scene-import/migration/dry-run")
def scene_import_migration_dry_run(project_id: str):
    """Co stanie sie z projektem po wlaczeniu kontraktu grafu v2 (P2.2).

    Nic nie zapisuje. Skan idzie przez cache, wiec powtorne wywolanie jest tanie.
    """
    _require_project(project_id)
    return public_migration_plan(migration_plan(
        project_id,
        lambda root, provider: scan_source_cached(root, provider)["packages"],
    ))


@router.post("/projects/{project_id}/scene-import/migration/apply")
def scene_import_migration_apply(project_id: str, body: SceneMigrationApplyRequest | None = None):
    """Zastosuj plan migracji. Sceny niejednoznaczne dostaja `migration_required`.

    Plan jest liczony ponownie tuz przed zapisem: zatwierdzanie planu sprzed godziny
    zapisywaloby stan, ktorego juz nie ma.
    """
    _require_project(project_id)
    plan = migration_plan(
        project_id,
        lambda root, provider: scan_source_cached(root, provider)["packages"],
    )
    try:
        plan = apply_migration_decisions(plan, body.decisions if body is not None else None)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return apply_migration(
        project_id,
        plan,
        backup=bool(body is None or body.backup),
        persist_auto=_persist_migrated_package_scene,
    )


@router.get("/projects/{project_id}/import-reports")
def list_scene_import_reports(project_id: str, limit: int = 50):
    """Naglowki zapisanych raportow importu, od najnowszego (P1.6)."""
    _require_project(project_id)
    return {"reports": list_import_reports(project_dir(project_id), limit=max(1, min(limit, 200)))}


@router.get("/projects/{project_id}/import-reports/{scan_id}")
def get_scene_import_report(project_id: str, scan_id: str, anonymize: bool = False):
    """Pelny raport jednego importu.

    `anonymize=true` zwraca kopie bez sciezek i nazw plikow — do przekazania dalej. Skroty sa
    stabilne w obrebie raportu, wiec o konkretnej scenie nadal da sie rozmawiac.
    """
    _require_project(project_id)
    if not re.fullmatch(r"[0-9a-fA-F]{8,64}", scan_id or ""):
        raise HTTPException(400, "Invalid scan_id")
    report = load_import_report(project_dir(project_id), scan_id)
    if report is None:
        raise HTTPException(404, "Import report not found")
    return anonymize_report(report) if anonymize else report


@router.post("/projects/{project_id}/import-jobs/{job_id}/cancel")
def cancel_import_job(project_id: str, job_id: str):
    _require_project(project_id)
    common = _read_common_import(project_id, job_id)
    if common is not None:
        try:
            state = cancel_job(project_id, job_id)
        except JobStoreError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"status": state.get("status")}
    key = f"{project_id}:{_safe_job_id(job_id)}"
    with ACTIVE_IMPORT_JOBS_LOCK:
        event = ACTIVE_IMPORT_JOBS.get(key)
    if not event:
        raise HTTPException(404, "No active import job")
    event.set()
    return {"status": "cancelling"}


@router.post("/projects/{project_id}/import-jobs/resume")
def resume_import_job(project_id: str, body: SceneImportResumeRequest | None = None):
    """Wznów niedokończony import: ponownie skanuje źródła projektu i dokatalogowuje tylko
    sceny niekompletne (brak scene_info / scene_info_error) oraz dolicza brakującą tożsamość
    i piramidy. Sceny już gotowe są pomijane (tanie przejście)."""
    _require_project(project_id)
    sources = [s for s in load_scene_sources(project_id).get("sources", []) if s.get("enabled", True)]
    if not sources:
        raise HTTPException(400, "No scene sources to resume")
    import_mode = _project_import_mode(project_id, body.import_mode if body else None)
    if body and body.import_mode is not None:
        _save_project_import_mode(project_id, import_mode)
    if _common_scene_import_jobs_enabled():
        try:
            submitted = _submit_scene_import_job(
                project_id,
                preview=None,
                decisions=[],
                skip_complete=True,
                import_mode=import_mode,
            )
        except JobStoreError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {
            "import_job_id": submitted["job"]["job_id"],
            "import_state": "running",
            "total": 0,
        }

    with ACTIVE_IMPORT_JOBS_LOCK:
        already_running = any(key.startswith(f"{project_id}:") for key in ACTIVE_IMPORT_JOBS)
    if already_running:
        raise HTTPException(409, "Import already running for this project")
    project = load_json(project_id, "project", default={})
    scan = _scan_source_records(sources, (project.get("profile") or {}).get("modality"))
    preview = {"sources": sources, "packages": scan["packages"]}
    job_id = uuid.uuid4().hex
    total = _count_importable(preview, [])
    _init_import_job(project_id, job_id, total)
    threading.Thread(
        target=_run_import_job,
        args=(project_id, job_id, preview, [], True),
        name=f"scene-import-resume-{project_id[:8]}",
        daemon=True,
    ).start()
    return {"import_job_id": job_id, "import_state": "running", "total": total}


@router.post("/projects/{project_id}/scenes/{scene_id}/display-overview")
def request_scene_display_overview(project_id: str, scene_id: str):
    """Ensure the opened scene receives the next available overview slot.

    While a bulk import owns the I/O lease, the request is persisted in that job and its
    pending queue is reordered. Otherwise an interactive, deduplicated one-scene job is used.
    """

    _require_project(project_id)
    scene = load_scene_json(project_id, scene_id, "scene", default={}) or {}
    if not scene:
        raise HTTPException(404, "Scene not found")
    scene, _overview_changed = sync_scene_source_overviews(
        project_id,
        scene_id,
        scene,
        force=True,
    )
    status = str(scene.get("overview_status") or "pending")
    if status == "native" and not _scene_source_overviews_display_ready(scene):
        status = "pending"
        _set_scene_overview_status(project_id, scene_id, status)
    if status in {"ready", "native"}:
        return {"status": status, "action": "already_ready", "job_id": None}

    active_import = find_active_job(
        project_id,
        job_type=JobType.SCENE_IMPORT.value,
        dedupe_key=f"scene-import:{project_id}",
    )
    if active_import is not None:
        import_mode = _normalize_import_mode(
            (active_import["job"].get("payload") or {}).get("import_mode")
        )
        phase = str(active_import["state"].get("import_phase") or "catalogue")
        pending_ids = {
            str(value) for value in (active_import["state"].get("overview_pending_scene_ids") or [])
        }
        active_ids = {
            str(value) for value in (active_import["state"].get("overview_active_scene_ids") or [])
        }
        queue_known = "overview_pending_scene_ids" in active_import["state"]
        scene_belongs_to_import = (
            phase in {"catalogue", "identity"}
            or not queue_known
            or scene_id in pending_ids
            or scene_id in active_ids
        )
        if import_mode != "on_demand" and phase != "complete" and scene_belongs_to_import:
            job_id = str(active_import["job"]["job_id"])

            def prioritize(current: dict[str, Any]) -> dict[str, Any]:
                if current.get("status") not in ACTIVE_JOB_STATUSES:
                    return current
                existing = [
                    str(value)
                    for value in (current.get("overview_priority_scene_ids") or [])
                    if str(value) != scene_id
                ]
                current["overview_priority_scene_ids"] = [scene_id, *existing][:16]
                current["overview_priority_requested_at"] = utc_now()
                return current

            state = mutate_state(project_id, job_id, prioritize)
            if state.get("status") in ACTIVE_JOB_STATUSES:
                return {
                    "status": status,
                    "action": "prioritized",
                    "job_id": job_id,
                    "job_type": JobType.SCENE_IMPORT.value,
                }

    dedupe_key = f"scene-overview:{project_id}:{scene_id}"
    active_overview = find_active_job(
        project_id,
        job_type=JobType.SCENE_OVERVIEW.value,
        dedupe_key=dedupe_key,
    )
    if active_overview is not None:
        return {
            "status": status,
            "action": "already_queued",
            "job_id": active_overview["job"]["job_id"],
            "job_type": JobType.SCENE_OVERVIEW.value,
        }
    _set_scene_overview_status(project_id, scene_id, "queued")
    try:
        submitted = _submit_scene_overview_job(project_id, scene_id)
    except JobStoreError as exc:
        _set_scene_overview_status(project_id, scene_id, status)
        raise HTTPException(409, str(exc)) from exc
    return {
        "status": "queued",
        "action": "queued",
        "job_id": submitted["job"]["job_id"],
        "job_type": JobType.SCENE_OVERVIEW.value,
    }


@router.get("/projects/{project_id}/scene-sources")
def get_project_scene_sources(project_id: str):
    _require_project(project_id)
    payload = load_scene_sources(project_id)
    for source in payload.get("sources", []):
        source["available"] = resolve_source_root(source) is not None
    return payload


@router.post("/projects/{project_id}/scene-sources", status_code=202)
def add_project_scene_source(project_id: str, body: SceneSourceAddRequest):
    _require_project(project_id)
    project = load_json(project_id, "project", default={})
    root = _resolve_input_root(body.source.root_path)
    record = {
        "source_id": new_source_id(body.source.provider, plain_path(root)),
        "provider": body.source.provider,
        "root_path": plain_path(root),
        "canonical_root": canonical_source_root(plain_path(root)),
        "enabled": body.source.enabled,
        "added_at": utc_now(),
        "last_scan_at": None,
        "last_scan_status": None,
    }
    _validate_source_modalities([record], (project.get("profile") or {}).get("modality"))
    sources = load_scene_sources(project_id)
    _validate_unique_source_roots([*sources.get("sources", []), record])
    try:
        submitted = _submit_scene_scan_job(
            [record],
            (project.get("profile") or {}).get("modality"),
            purpose="add_project_source",
            project_id=project_id,
        )
    except JobStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "queued", "scan_job_id": submitted["job"]["job_id"]}


@router.post("/projects/{project_id}/scene-sources/{source_id}/rescan", status_code=202)
def rescan_project_scene_source(project_id: str, source_id: str):
    _require_project(project_id)
    source = source_by_id(project_id, source_id)
    if not source:
        raise HTTPException(404, "Scene source not found")
    project = load_json(project_id, "project", default={})
    try:
        submitted = _submit_scene_scan_job(
            [source],
            (project.get("profile") or {}).get("modality"),
            purpose="rescan_project_source",
            project_id=project_id,
        )
    except JobStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "queued", "scan_job_id": submitted["job"]["job_id"]}


@router.post("/projects/{project_id}/scene-sources/apply")
def apply_project_scene_source_preview(
    project_id: str,
    body: SceneSourcesApplyRequest,
    background_tasks: BackgroundTasks,
):
    _require_project(project_id)
    preview = _load_preview(body.preview_id)
    _validate_preview_inputs(preview, body.decisions)
    _validate_preview_decisions(preview, body.decisions)
    if _common_scene_import_jobs_enabled():
        _ensure_scene_import_slot_available(project_id)
    import_mode = _project_import_mode(project_id, body.import_mode)
    if body.import_mode is not None:
        _save_project_import_mode(project_id, import_mode)
    current = load_scene_sources(project_id)
    known = {source["source_id"]: source for source in current.get("sources", [])}
    _validate_unique_source_roots([
        *current.get("sources", []),
        *[
            source
            for source in preview.get("sources", [])
            if source.get("source_id") not in known
        ],
    ])
    for source in preview.get("sources", []):
        source["last_scan_at"] = utc_now()
        source["last_scan_status"] = "ok"
        known[source["source_id"]] = source
    save_scene_sources(project_id, {"sources": list(known.values())})
    if _common_scene_import_jobs_enabled():
        try:
            submitted = _submit_scene_import_job(
                project_id,
                preview=preview,
                decisions=body.decisions,
                skip_complete=False,
                import_mode=import_mode,
            )
        except JobStoreError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {
            "accepted": True,
            "import_job_id": submitted["job"]["job_id"],
            "import_state": "running",
            "total": _count_importable(preview, body.decisions),
        }
    result = _apply_preview(project_id, preview, body.decisions, background_tasks)
    project = load_json(project_id, "project", default={})
    project["scene_count"] = result["total"]
    save_json(project_id, "project", project)
    rebuild_scenes_index(project_id)
    return result


def _build_relink_report(
    project_id: str,
    current_source: dict[str, Any],
    candidate_source: dict[str, Any],
) -> dict[str, Any]:
    """Compare persisted delivery/products/assets with a candidate root."""

    scan = _scan_source_records([candidate_source], None)
    discovered = {str(item.get("package_id") or ""): item for item in scan.get("packages", [])}
    expected: dict[str, dict[str, Any]] = {}
    for scene_id in list_scene_ids(project_id):
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        package = manifest.get("source_package") or {}
        if package.get("source_id") == current_source.get("source_id"):
            package_id = str(package.get("package_id") or "")
            if package_id:
                expected[package_id] = package

    missing_packages = sorted(set(expected) - set(discovered))
    added_packages = sorted(set(discovered) - set(expected))
    changed_products: list[dict[str, Any]] = []
    missing_assets: list[dict[str, Any]] = []
    changed_assets: list[dict[str, Any]] = []
    for package_id in sorted(set(expected) & set(discovered)):
        before = expected[package_id]
        after = discovered[package_id]
        old_product = str(before.get("product_type") or "")
        new_product = str((after.get("selection") or {}).get("product_type") or "")
        if old_product != new_product:
            changed_products.append({"package_id": package_id, "before": old_product, "after": new_product})
        new_assets = {str(item.get("asset_id") or ""): item for item in after.get("assets", [])}
        for old_asset in before.get("assets", []):
            asset_id = str(old_asset.get("asset_id") or "")
            new_asset = new_assets.get(asset_id)
            if new_asset is None:
                missing_assets.append({
                    "package_id": package_id,
                    "asset_id": asset_id,
                    "relative_path": old_asset.get("relative_path"),
                })
                continue
            before_signature = (str(old_asset.get("relative_path") or ""), int(old_asset.get("size") or -1))
            after_signature = (str(new_asset.get("relative_path") or ""), int(new_asset.get("size") or -1))
            if before_signature != after_signature:
                changed_assets.append({
                    "package_id": package_id,
                    "asset_id": asset_id,
                    "before": {"relative_path": before_signature[0], "size": before_signature[1]},
                    "after": {"relative_path": after_signature[0], "size": after_signature[1]},
                })
    compatible = not (missing_packages or changed_products or missing_assets or changed_assets)
    return {
        "schema_version": 1,
        "source_id": current_source.get("source_id"),
        "current_root": current_source.get("root_path"),
        "candidate_root": candidate_source.get("root_path"),
        "expected_packages": len(expected),
        "found_packages": len(discovered),
        "missing_packages": missing_packages,
        "added_packages": added_packages,
        "changed_products": changed_products,
        "missing_assets": missing_assets,
        "changed_assets": changed_assets,
        "compatible": compatible,
    }


@router.post("/projects/{project_id}/scene-sources/{source_id}/relink")
def relink_project_scene_source(
    project_id: str,
    source_id: str,
    body: SceneSourceRelinkRequest,
    background_tasks: BackgroundTasks,
):
    _require_project(project_id)
    root = _resolve_input_root(body.root_path)
    sources = load_scene_sources(project_id)
    target = next((item for item in sources["sources"] if item.get("source_id") == source_id), None)
    if not target:
        raise HTTPException(404, "Scene source not found")
    return _relink_source_transaction(
        project_id,
        source_id,
        root,
        target,
        sources,
        body.dry_run,
        background_tasks,
    )


def _relink_source_transaction(
    project_id: str,
    source_id: str,
    root: Path,
    target: dict[str, Any],
    sources: dict[str, Any],
    dry_run: bool,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    candidate = {**target, "root_path": plain_path(root),
                 "canonical_root": canonical_source_root(plain_path(root))}
    _validate_unique_source_roots([
        candidate if item.get("source_id") == source_id else item
        for item in sources.get("sources", [])
    ])
    report = _build_relink_report(project_id, target, candidate)
    if dry_run:
        return {"status": "preview", "report": report}
    if not report["compatible"]:
        raise HTTPException(409, {"message": "Relink target does not match the registered delivery", "report": report})
    if _common_scene_import_jobs_enabled():
        _ensure_scene_import_slot_available(project_id)

    sources_before = copy.deepcopy(sources)
    scene_snapshots: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    affected_scene_ids: list[str] = []
    for scene_id in list_scene_ids(project_id):
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        if (manifest.get("source_package") or {}).get("source_id") == source_id:
            affected_scene_ids.append(scene_id)
            scene_snapshots[scene_id] = (
                load_scene_json(project_id, scene_id, "scene", default={}),
                copy.deepcopy(manifest),
            )

    target.update(candidate)
    target["last_scan_at"] = utc_now()
    target["last_scan_status"] = "relinked"
    submitted: dict[str, Any] | None = None
    try:
        save_scene_sources(project_id, sources)
        for scene_id in affected_scene_ids:
            scene_before, manifest_before = scene_snapshots[scene_id]
            scene = copy.deepcopy(scene_before)
            manifest = copy.deepcopy(manifest_before)
            identity = manifest.setdefault("source_identity", {})
            identity["status"] = "pending"
            manifest["source_identity_status"] = "pending"
            manifest.setdefault("working_view", {})["preparation_status"] = "relink_pending"
            save_scene_json(project_id, scene_id, "scene_manifest", manifest)
            if scene:
                scene["source_identity_status"] = "pending"
                scene["overview_status"] = "pending"
                scene["preparation_status"] = "relink_pending"
                save_scene_json(project_id, scene_id, "scene", scene)
        if _common_scene_import_jobs_enabled() and affected_scene_ids:
            submitted = _submit_scene_import_job(
                project_id,
                preview=None,
                decisions=[],
                skip_complete=True,
            )
    except Exception as exc:
        save_scene_sources(project_id, sources_before)
        for scene_id, (scene, manifest) in scene_snapshots.items():
            save_scene_json(project_id, scene_id, "scene", scene)
            save_scene_json(project_id, scene_id, "scene_manifest", manifest)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(409, f"Relink was not committed: {exc}") from exc

    for scene_id in affected_scene_ids:
        try:
            clear_all_scene_display_overviews(project_id, scene_id)
            invalidate_scene_render_caches(project_id, scene_id)
        except Exception:
            pass
    if submitted is not None:
        return {
            "status": "relinked",
            "source": target,
            "affected_scenes": len(affected_scene_ids),
            "report": report,
            "import_job_id": submitted["job"]["job_id"],
            "import_state": "running",
        }
    background_tasks.add_task(_regenerate_project_vrts, project_id, source_id)
    for scene_id in affected_scene_ids:
        background_tasks.add_task(_refresh_scene_from_manifest, project_id, scene_id)
        background_tasks.add_task(compute_scene_package_identity, project_id, scene_id)
        background_tasks.add_task(_build_scene_display_overviews, project_id, scene_id)
    return {
        "status": "relinked",
        "source": target,
        "affected_scenes": len(affected_scene_ids),
        "report": report,
    }


@router.delete("/projects/{project_id}/scene-sources/{source_id}")
def remove_project_scene_source(project_id: str, source_id: str):
    """Usun zrodlo scen wraz z jego skatalogowanymi scenami (bez plikow zrodlowych).

    Sceny z tego zrodla maja `raster_ref` wskazujacy jego katalog, wiec bez zrodla i tak
    staja sie niedzialajace — usuwamy je (kasuje ich adnotacje; GUI potwierdza z liczba).
    Pliki na dysku zrodla nie sa ruszane.
    """
    _require_project(project_id)
    sources = load_scene_sources(project_id)
    target = next((s for s in sources.get("sources", []) if s.get("source_id") == source_id), None)
    if target is None:
        raise HTTPException(404, "Scene source not found")

    removed_scenes = 0
    for scene_id in list(list_scene_ids(project_id)):
        scene = load_scene_json(project_id, scene_id, "scene", default={})
        if str(scene.get("source_id") or "") == source_id:
            delete_scene_data(project_id, scene_id)
            shutil.rmtree(project_dir(project_id) / "derived_scenes" / scene_id, ignore_errors=True)
            removed_scenes += 1

    sources["sources"] = [s for s in sources.get("sources", []) if s.get("source_id") != source_id]
    save_scene_sources(project_id, sources)

    project = load_json(project_id, "project", default={})
    project["scene_count"] = len(list_scene_ids(project_id))
    save_json(project_id, "project", project)
    rebuild_scenes_index(project_id)
    return {
        "status": "removed",
        "source_id": source_id,
        "removed_scenes": removed_scenes,
        "source_files_deleted": False,
        "scene_count": project["scene_count"],
    }


@router.delete("/projects/{project_id}/scene-sources/derived/unreferenced")
def cleanup_unreferenced_scene_variants(project_id: str):
    _require_project(project_id)
    referenced: set[Path] = set()
    root = project_dir(project_id).resolve(strict=False)
    for scene_id in list_scene_ids(project_id):
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        for ref in (
            (manifest.get("working_view") or {}).get("raster_ref"),
            (manifest.get("working_view") or {}).get("processing_manifest"),
        ):
            if isinstance(ref, dict) and ref.get("storage") == "project" and ref.get("relative_path"):
                referenced.add((root / str(ref["relative_path"])).resolve(strict=False))
    derived = root / "derived_scenes"
    removed_variants = 0
    removed_bytes = 0
    if derived.is_dir():
        for variant_dir in sorted((path for path in derived.glob("*/*") if path.is_dir()), reverse=True):
            if any(path == variant_dir or variant_dir in path.parents for path in referenced):
                continue
            removed_bytes += sum(path.stat().st_size for path in variant_dir.rglob("*") if path.is_file())
            import shutil

            shutil.rmtree(variant_dir)
            removed_variants += 1
    return {"removed_variants": removed_variants, "removed_bytes": removed_bytes}


@router.post("/projects/{project_id}/scenes/{scene_id}/select-asset")
def select_scene_asset(project_id: str, scene_id: str, body: SceneAssetSelectionRequest):
    _require_project(project_id)
    manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
    if not manifest:
        raise HTTPException(404, "Scene manifest not found")
    working = manifest.get("working_view") or {}
    annotations = load_scene_json(project_id, scene_id, "annotations", default=[])
    if working.get("locked") or annotations:
        raise HTTPException(409, "Working view is locked; create a separate scene for another variant")
    package = manifest.get("source_package") or {}
    known_ids = {asset.get("asset_id") for asset in package.get("assets") or []}
    if any(asset_id not in known_ids for asset_id in body.asset_ids):
        raise HTTPException(400, "Selected asset does not belong to this scene package")
    selection = package.setdefault("selection", {})
    # P2.1: decyzja uzytkownika ma wybierac SEMANTYCZNA alternatywe produktu. Reczna lista
    # plikow jest nadal dopuszczalna, ale zapisuje sie inaczej — `user_override` mowi, ze
    # wybor nie odpowiada zadnemu wariantowi rozpoznanemu przez resolver, wiec przy pozniejszej
    # diagnozie nie da sie go pomylic z wyborem jednej z podanych alternatyw.
    offered = {
        tuple(sorted(str(asset_id) for asset_id in (alternative.get("asset_ids") or [])))
        for alternative in (selection.get("alternatives") or [])
    }
    offered.add(tuple(sorted(str(asset_id) for asset_id in (selection.get("asset_ids") or []))))
    chosen = tuple(sorted(str(asset_id) for asset_id in body.asset_ids))
    selection["asset_ids"] = body.asset_ids
    selection["identity_asset_ids"] = body.asset_ids
    selection["rgb_bands"] = body.rgb_bands
    selection["selected_by"] = "user" if chosen in offered else "user_override"
    # P0.4: bez tego reczny wybor zostawial `product_type=UNRESOLVED` mimo statusu `ready`,
    # a metadane pozostawaly zwiazane z poprzednim, automatycznym wyborem — czyli mogly
    # pochodzic z innej akwizycji tej samej dostawy (sekcja 4.4).
    selection.setdefault("diagnostics", {"warnings": [], "errors": [], "metadata_conflicts": []})
    get_resolver(package.get("provider") or "generic").refine_manual_selection(package, selection)
    derived = selection.get("product_type") in {"MUL+PAN", "MS-FS_RGB+PAN"}
    if derived and len(body.asset_ids) > 1 and (not body.rgb_bands or len(body.rgb_bands) != 3):
        raise HTTPException(400, "Three RGB band indexes are required for this product")
    selection["status"] = "prepare_required" if derived and len(body.asset_ids) > 1 else "ready"
    selection["raster_kind"] = "derived" if derived else "direct" if len(body.asset_ids) == 1 else "virtual_mosaic"
    _configure_working_view(project_id, scene_id, package, manifest, selection)
    save_scene_json(project_id, scene_id, "scene_manifest", manifest)
    _refresh_scene_from_manifest(project_id, scene_id)
    return load_scene_json(project_id, scene_id, "scene_manifest", default={})


@router.post("/projects/{project_id}/scenes/{scene_id}/prepare")
async def prepare_scene(project_id: str, scene_id: str, body: ScenePrepareRequest):
    _require_project(project_id)
    if _common_scene_preparation_jobs_enabled():
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        if not manifest:
            raise HTTPException(404, "Scene manifest not found")
        selection = (manifest.get("source_package") or {}).get("selection") or {}
        rgb_bands = body.rgb_bands or selection.get("rgb_bands")
        if not rgb_bands or len(rgb_bands) != 3:
            raise HTTPException(400, "Three confirmed RGB bands are required")
        try:
            submitted = _submit_scene_preparation_job(project_id, scene_id, list(rgb_bands))
        except JobStoreError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {
            "accepted": True,
            "job_id": submitted["job"]["job_id"],
            "status": submitted["state"]["status"],
        }
    key = f"{project_id}:{scene_id}"
    with ACTIVE_PREPARATIONS_LOCK:
        if key in ACTIVE_PREPARATIONS:
            raise HTTPException(409, "Scene preparation is already running")
        cancel_event = threading.Event()
        ACTIVE_PREPARATIONS[key] = cancel_event

    async def generate():
        import asyncio

        try:
            yield {"event": "progress", "data": json.dumps({"done": 0, "total": 3, "stage": "prepare_inputs"})}
            manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
            selection = (manifest.get("source_package") or {}).get("selection") or {}
            rgb_bands = body.rgb_bands or selection.get("rgb_bands")
            if not rgb_bands or len(rgb_bands) != 3:
                raise RuntimeError("Three confirmed RGB bands are required")
            yield {"event": "progress", "data": json.dumps({"done": 1, "total": 3, "stage": "pansharpen"})}
            result = await asyncio.to_thread(
                prepare_pansharpened_cog,
                project_id,
                scene_id,
                rgb_bands,
                cancel_check=cancel_event.is_set,
            )
            yield {"event": "progress", "data": json.dumps({"done": 2, "total": 3, "stage": "manifest"})}
            _activate_prepared_result(project_id, scene_id, result)
            yield {"event": "complete", "data": json.dumps({"done": 3, "total": 3, "variant_id": result["variant_id"]})}
        except InterruptedError:
            yield {"event": "cancelled", "data": json.dumps({"status": "cancelled"})}
        except Exception as exc:
            yield {"event": "error", "data": json.dumps({"error": str(exc)})}
        finally:
            with ACTIVE_PREPARATIONS_LOCK:
                ACTIVE_PREPARATIONS.pop(key, None)

    return EventSourceResponse(generate())


@router.post("/projects/{project_id}/scenes/{scene_id}/prepare/cancel")
def cancel_scene_preparation(project_id: str, scene_id: str):
    if _common_scene_preparation_jobs_enabled():
        active = find_active_job(
            project_id,
            job_type=JobType.SCENE_PREPARATION.value,
            dedupe_key=f"scene-prepare:{project_id}:{scene_id}",
        )
        if active is not None:
            try:
                state = cancel_job(project_id, str(active["job"]["job_id"]))
            except JobStoreError as exc:
                raise HTTPException(404, str(exc)) from exc
            return {"status": state.get("status")}
    key = f"{project_id}:{scene_id}"
    with ACTIVE_PREPARATIONS_LOCK:
        event = ACTIVE_PREPARATIONS.get(key)
    if not event:
        raise HTTPException(404, "No active scene preparation")
    event.set()
    return {"status": "cancelling"}


def _set_scene_overview_status(project_id: str, scene_id: str, status: str) -> None:
    try:
        scene = load_scene_json(project_id, scene_id, "scene", default={})
        if scene and scene.get("overview_status") != status:
            scene["overview_status"] = status
            save_scene_json(project_id, scene_id, "scene", scene)
    except Exception:
        pass


def _build_scene_display_overviews(
    project_id: str,
    scene_id: str,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Zadanie tła: piramida wyświetlania dla sceny direct. Best-effort — błąd
    (np. brak GDAL, źródło chwilowo niedostępne) nie może przewrócić importu.
    Zapisuje status piramidy na scenie (ready/native/error) dla flagi w tabeli scen."""
    started = time.perf_counter()
    _set_scene_overview_status(project_id, scene_id, "building")
    try:
        result = ensure_scene_display_overviews(project_id, scene_id, profile=profile)
        # None = źródło ma już własne overviews / nie-direct / brak GDAL → wyświetlanie i tak szybkie.
        current_scene = load_scene_json(project_id, scene_id, "scene", default={}) or {}
        status = (
            "ready"
            if result is not None
            else "native"
            if _scene_source_overviews_display_ready(current_scene)
            else "pending"
        )
        if result is not None:
            scene = load_scene_json(project_id, scene_id, "scene", default={}) or {}
            source_state = ((scene.get("scene_info") or {}).get("source_overviews") or {})
            project_state = inspect_source_overviews(result)
            apply_source_overview_metadata(scene, source_state, effective_type="project_vrt_ovr")
            scene["overview_factors"] = list(project_state.get("factors") or [])
            scene["overview_fingerprint"] = project_state.get("fingerprint")
            scene["overview_status"] = status
            # JP2 characterization is intentionally metadata-only during import.
            # Once the fast project OVR exists, sample its coarsest level and persist
            # one scene-wide stretch instead of recomputing percentiles per tile.
            try:
                import rasterio

                from services.scene_characterization import characterize_raster

                project = load_json(project_id, "project", default={})
                manifest_for_stats = load_scene_json(
                    project_id,
                    scene_id,
                    "scene_manifest",
                    default={},
                )
                source_package = manifest_for_stats.get("source_package") or {}
                with rasterio.open(result) as prepared_src:
                    prepared = characterize_raster(
                        prepared_src,
                        filename=scene.get("filename"),
                        source_path=result,
                        modality=(
                            manifest_for_stats.get("modality")
                            or scene.get("modality")
                            or (project.get("profile") or {}).get("modality")
                        ),
                        product_type=source_package.get("product_type"),
                        selected_sensors=(project.get("profile") or {}).get("sensors") or [],
                    )
                info = scene.setdefault("scene_info", {})
                for key in ("display_min", "display_max", "display_mode", "display_stats"):
                    info[key] = prepared.get(key)
                info["display_stats_source"] = "project_vrt_ovr"
                info.pop("display_stats_error", None)
            except Exception as stats_exc:
                scene.setdefault("scene_info", {})["display_stats_error"] = (
                    f"{type(stats_exc).__name__}: {stats_exc}"
                )
            save_scene_json(project_id, scene_id, "scene", scene)
            manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
            if manifest:
                display = manifest.setdefault("display", {})
                display["overview_type"] = "project_vrt_ovr"
                display["overview_factors"] = scene["overview_factors"]
                display["overview_fingerprint"] = scene["overview_fingerprint"]
                save_scene_json(project_id, scene_id, "scene_manifest", manifest)
            # Tiles rendered from the native JP2 while the job was running must
            # never survive the switch to the project VRT/OVR. The new scene
            # fingerprint also changes browser URLs; this removes server-side
            # thumbnails, histograms, and every display-variant tile cache.
            invalidate_scene_render_caches(project_id, scene_id)
        else:
            scene, _overview_changed = sync_scene_source_overviews(
                project_id,
                scene_id,
                force=True,
            )
            _set_scene_overview_status(project_id, scene_id, status)
        overview_bytes = 0
        if result is not None:
            sidecar = result.with_name(result.name + ".ovr")
            if sidecar.is_file():
                overview_bytes = sidecar.stat().st_size
        return {
            "scene_id": scene_id,
            "status": status,
            "wall_seconds": round(time.perf_counter() - started, 6),
            "path": str(result) if result is not None else None,
            "overview_bytes": overview_bytes,
        }
    except Exception as exc:
        _set_scene_overview_status(project_id, scene_id, "error")
        return {
            "scene_id": scene_id,
            "status": "error",
            "wall_seconds": round(time.perf_counter() - started, 6),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _import_jobs_dir(project_id: str) -> Path:
    path = project_dir(project_id) / "import_jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_job_id(job_id: str) -> str:
    try:
        return validate_job_id(job_id)
    except JobStoreError as exc:
        raise HTTPException(400, "Invalid job_id") from exc


def _new_import_job(project_id: str, job_id: str, total: int) -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_name": "geotile_scene_import_job",
        "schema_version": 1,
        "job_id": job_id,
        "project_id": project_id,
        "state": "running",           # running | done | error
        "phase": "catalogue",         # catalogue | identity | overviews | complete
        "total": total,
        "done": 0,
        "added": 0,
        "updated": 0,
        "blocked": 0,
        "failed": 0,
        "identity_total": 0,
        "identity_done": 0,
        "overviews_total": 0,
        "overviews_done": 0,
        "current": None,              # {"filename", "stage", "started_at"}
        "recent": [],                 # ogon ostatnich przetworzonych (do logu w UI)
        "errors": [],
        "started_at": now,
        "updated_at": now,
    }


def _write_import_job(project_id: str, job: dict[str, Any]) -> None:
    """Atomowy, best-effort zapis pliku postępu. Na Windowsie `os.replace` rzuca
    PermissionError (sharing violation), gdy czytelnik (polling UI) trzyma otwarty plik
    w tej mikrochwili — retry to obsługuje, a plik postępu jest telemetrią, więc pojedynczy
    nieudany zapis pomijamy (następny nadpisze). NIGDY nie może wywrócić importu."""
    path = _import_jobs_dir(project_id) / f"{job['job_id']}.json"
    temp = path.with_suffix(".tmp")
    try:
        temp.write_text(json.dumps(job, ensure_ascii=False, default=str), encoding="utf-8")
    except OSError:
        return
    for _ in range(25):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            time.sleep(0.02)
        except OSError:
            break
    temp.unlink(missing_ok=True)


def _init_import_job(project_id: str, job_id: str, total: int) -> None:
    _write_import_job(project_id, _new_import_job(project_id, job_id, total))


def _count_importable(preview: dict[str, Any], decisions: list[Any]) -> int:
    resolved = _resolve_preview_decisions(preview, decisions)
    skip = {key for key, item in resolved.items() if getattr(item, "action", None) == "skip"}
    return sum(1 for package in preview.get("packages", []) if _package_decision_key(package) not in skip)


def _run_import_job(
    project_id: str,
    job_id: str,
    preview: dict[str, Any],
    decisions: list[Any],
    skip_complete: bool = False,
) -> None:
    """Wątek roboczy: katalogowanie scen + tożsamość + piramidy, z postępem do pliku job.

    Anulowanie jest kooperatywne — sprawdzamy Event między scenami (nie da się przerwać
    pojedynczego odczytu w GDAL, ale po fixie JP2 każda scena jest krótka). `skip_complete`
    (wznawianie) pomija sceny już gotowe."""
    key = f"{project_id}:{job_id}"
    cancel_event = threading.Event()
    with ACTIVE_IMPORT_JOBS_LOCK:
        ACTIVE_IMPORT_JOBS[key] = cancel_event
    job = _new_import_job(project_id, job_id, _count_importable(preview, decisions))
    _write_import_job(project_id, job)
    try:
        report, pending_identity, pending_overviews = _catalogue_scenes(
            project_id, preview, decisions, job,
            cancel_check=cancel_event.is_set, skip_complete=skip_complete,
        )
        project_data = load_json(project_id, "project", default={})
        project_data["scene_count"] = report["total"]
        save_json(project_id, "project", project_data)
        rebuild_scenes_index(project_id)

        if report.get("cancelled"):
            # Przerwano w trakcie katalogowania — sceny dotąd dodane zostają, reszta pominięta.
            job["state"] = "cancelled"
            job["phase"] = "complete"
            job["current"] = None
            job["report"] = report
            job["updated_at"] = utc_now()
            _write_import_job(project_id, job)
            return

        # Faza tożsamości: sha256 pełnego pliku źródłowego — długi ogon, teraz widoczny.
        # #6: NIE przebudowujemy indeksu scen po każdej scenie (O(N²)); raz po całej fazie.
        job["phase"] = "identity"
        job["identity_total"] = len(pending_identity)
        job["current"] = None
        job["updated_at"] = utc_now()
        _write_import_job(project_id, job)
        for scene_id, log_name in pending_identity:
            if cancel_event.is_set():
                break
            job["current"] = {"filename": log_name, "stage": "identity", "started_at": utc_now()}
            _write_import_job(project_id, job)
            try:
                compute_scene_package_identity(project_id, scene_id, rebuild_index=False)
            except Exception as exc:  # tożsamość best-effort — nie przerywa importu
                job["errors"].append({"filename": log_name, "message": f"identity: {exc}"})
            job["identity_done"] += 1
            job["current"] = None
            job["updated_at"] = utc_now()
            _write_import_job(project_id, job)
        rebuild_scenes_index(project_id)  # #6: jednorazowo po całej fazie tożsamości

        if cancel_event.is_set():
            job["state"] = "cancelled"
            job["phase"] = "complete"
            job["current"] = None
            job["report"] = report
            job["updated_at"] = utc_now()
            _write_import_job(project_id, job)
            return

        # === #1: IMPORT UŻYWALNY — piramidy NIE blokują "done" ===
        # Piramidy to optymalizacja WYŚWIETLANIA (do czasu ich zbudowania kafle serwują się
        # decymowane ze źródła). Dlatego oznaczamy import jako "done" już tu — użytkownik może
        # otworzyć projekt i pracować — a piramidy dobudowują się w tle w tym samym wątku.
        job["state"] = "done"
        job["phase"] = "overviews"
        job["overviews_total"] = len(pending_overviews)
        job["report"] = report
        job["current"] = None
        job["updated_at"] = utc_now()
        _write_import_job(project_id, job)

        for scene_id, log_name in pending_overviews:
            if cancel_event.is_set():  # cancel zatrzymuje piramidy, ale stan pozostaje "done"
                break
            job["current"] = {"filename": log_name, "scene_id": scene_id, "stage": "overviews", "started_at": utc_now()}
            _write_import_job(project_id, job)
            _build_scene_display_overviews(project_id, scene_id)
            job["overviews_done"] += 1
            job["current"] = None
            job["updated_at"] = utc_now()
            _write_import_job(project_id, job)

        job["phase"] = "complete"
        job["current"] = None
        job["updated_at"] = utc_now()
        _write_import_job(project_id, job)
    except Exception as exc:
        job["state"] = "error"
        job["error"] = str(exc)
        job["current"] = None
        job["updated_at"] = utc_now()
        _write_import_job(project_id, job)
    finally:
        with ACTIVE_IMPORT_JOBS_LOCK:
            ACTIVE_IMPORT_JOBS.pop(key, None)


class SceneImportCancelled(RuntimeError):
    """Internal signal translated by the common worker into a cancelled Job state."""


def run_scene_preparation_common_job(spec: dict[str, Any], context: Any) -> dict[str, Any]:
    project_id = str(spec["project_id"])
    payload = spec.get("payload") or {}
    scene_id = str(payload.get("scene_id") or "")
    rgb_bands = [int(value) for value in (payload.get("rgb_bands") or [])]
    if not scene_id or len(rgb_bands) != 3:
        raise RuntimeError("Scene preparation job has invalid immutable inputs")

    started = time.perf_counter()
    context.update(phase="scene_preparation:prepare_inputs", current=0, total=3, scene_id=scene_id)
    manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
    if not manifest:
        raise RuntimeError("Scene manifest not found")
    if context.cancel_requested():
        raise SceneImportCancelled("Scene preparation cancelled before pansharpening")

    context.update(phase="scene_preparation:pansharpen", current=1, total=3)
    try:
        result = prepare_pansharpened_cog(
            project_id,
            scene_id,
            rgb_bands,
            cancel_check=context.cancel_requested,
        )
    except InterruptedError as exc:
        raise SceneImportCancelled("Scene preparation cancelled during pansharpening") from exc
    if context.cancel_requested():
        raise SceneImportCancelled("Scene preparation cancelled before manifest activation")

    context.update(phase="scene_preparation:manifest", current=2, total=3)
    _activate_prepared_result(project_id, scene_id, result)
    elapsed = round(time.perf_counter() - started, 6)
    context.update(phase="scene_preparation:complete", current=3, total=3)
    return {
        "scene_id": scene_id,
        "variant_id": result["variant_id"],
        "result": result,
        "_job_performance": {
            "scene_id": scene_id,
            "wall_seconds": elapsed,
            "operation": "pansharpened_cog",
        },
    }


def run_scene_fullres_derivative_job(spec: dict[str, Any], context: Any) -> dict[str, Any]:
    """Build, validate and atomically activate one full-resolution COG v2."""
    from db.storage import project_dir
    from services.scene_packages.fullres_cog_builder import (
        BuildCancelled,
        FULLRES_COG_NAME,
        build_fullres_cog,
        cleanup_build_intermediates,
        fullres_dir,
        fullres_state_path,
        validate_fullres_candidate,
    )
    from services.scene_packages.fullres_derivative import may_activate
    from services.scene_packages.fullres_publication import (
        STATE_ACTIVE,
        STATE_CANDIDATE_READY,
        STATE_VALIDATING,
        STEP_ARTIFACT,
        STEP_MANIFEST,
        STEP_SCENE,
        PublicationRecord,
        advance,
        mark_failed,
        mark_step,
        write_publication_record,
    )
    from services.scene_raster_resolver import (
        SceneRasterResolver,
        invalidate_scene_render_caches,
    )
    from services.scene_raster_session import invalidate_all as invalidate_raster_sessions

    project_id = str(spec["project_id"])
    payload = dict(spec.get("payload") or {})
    scene_id = str(payload.get("scene_id") or "")
    if not scene_id:
        raise RuntimeError("Fullres derivative job has no scene_id")
    scene = load_scene_json(project_id, scene_id, "scene", default={}) or {}
    if not scene:
        raise RuntimeError(f"Scene not found: {scene_id}")
    handle = SceneRasterResolver.resolve(project_id, scene_id)
    info = scene.get("scene_info") or {}
    import numpy as np

    dtype = str(info.get("dtype") or "uint16")
    band_count = int(info.get("channels") or 1)
    nodata = info.get("nodata")
    nodata_values = (
        list(nodata)
        if isinstance(nodata, (list, tuple))
        else [nodata for _ in range(band_count)]
    )
    scene_profile = {
        "width": info.get("width"),
        "height": info.get("height"),
        "band_count": band_count,
        "itemsize": int(np.dtype(dtype).itemsize),
        "gdal_dtype": {
            "uint16": "UInt16", "uint8": "Byte", "int16": "Int16",
            "uint32": "UInt32", "int32": "Int32", "float32": "Float32",
            "float64": "Float64",
        }.get(dtype, "UInt16"),
        "crs_wkt": info.get("crs"),
        "transform": info.get("transform"),
        "color_interpretation": list(info.get("color_interpretation") or []),
        "nodata_values": nodata_values,
    }
    if not scene_profile["transform"]:
        raise RuntimeError("Scene has no geotransform; refusing to build a derivative")

    target = fullres_dir(project_dir(project_id), scene_id, handle.working_variant_id)
    target.mkdir(parents=True, exist_ok=True)
    state_path = fullres_state_path(project_dir(project_id), scene_id, handle.working_variant_id)
    record = PublicationRecord(payload={**payload, "job_id": spec.get("job_id")})
    write_publication_record(state_path, record)
    result = None
    context.update(phase="fullres:building", current=0, total=100, scene_id=scene_id)

    def _progress(update: dict[str, Any]) -> None:
        done = int(update.get("rows_done") or 0)
        total = max(1, int(update.get("rows_total") or 1))
        context.update(
            phase="fullres:building",
            current=int(done / total * 85),
            total=100,
            scene_id=scene_id,
        )

    try:
        result = build_fullres_cog(
            source=handle.path,
            target_dir=target,
            scene_profile=scene_profile,
            source_bytes=int(info.get("file_size") or handle.path.stat().st_size),
            cancel_check=context.cancel_requested,
            progress=_progress,
        )
        advance(record, STATE_VALIDATING)
        write_publication_record(state_path, record)
        context.update(phase="fullres:validating", current=90, total=100, scene_id=scene_id)
        validation = validate_fullres_candidate(result, scene_profile=scene_profile)
        record.payload["validation"] = validation
        record.payload["candidate_bytes"] = result.cog_path.stat().st_size
        advance(record, STATE_CANDIDATE_READY)
        write_publication_record(state_path, record)

        fresh_scene = load_scene_json(project_id, scene_id, "scene", default={}) or {}
        fresh_manifest = load_scene_json(
            project_id, scene_id, "scene_manifest", default={}
        ) or {}
        identity = fresh_manifest.get("source_identity") or {}
        working = fresh_manifest.get("working_view") or {}
        check = may_activate(
            payload,
            source_fingerprint=identity.get("source_scene_fingerprint")
            or identity.get("source_package_fingerprint"),
            variant_id=working.get("variant_id") or fresh_scene.get("working_variant_id"),
            source_revision=fresh_scene.get("overview_fingerprint"),
        )
        if not check.may_activate:
            raise RuntimeError(f"validation_failed: {check.reason}")

        # Close thread-affine readers before Windows rename and publish only the
        # already validated candidate.
        invalidate_raster_sessions()
        active_path = target / FULLRES_COG_NAME
        active_path.unlink(missing_ok=True)
        os.replace(result.cog_path, active_path)
        result.cog_path = active_path
        mark_step(record, STEP_ARTIFACT)
        write_publication_record(state_path, record)

        relative = str(active_path.relative_to(project_dir(project_id))).replace("\\", "/")
        working = fresh_manifest.setdefault("working_view", {})
        working["fullres_derivative"] = {
            "status": "active",
            "raster_ref": {"storage": "project", "relative_path": relative},
            "source_fingerprint": payload.get("source_fingerprint"),
            "variant_id": payload.get("variant_id"),
            "cog_profile_version": payload.get("cog_profile_version"),
            "bytes": active_path.stat().st_size,
        }
        save_scene_json(project_id, scene_id, "scene_manifest", fresh_manifest)
        mark_step(record, STEP_MANIFEST)
        write_publication_record(state_path, record)

        fresh_scene.update(
            {
                "fullres_derivative_status": "ready",
                "fullres_cog_profile_version": payload.get("cog_profile_version"),
                "fullres_source_fingerprint": payload.get("source_fingerprint"),
            }
        )
        save_scene_json(project_id, scene_id, "scene", fresh_scene)
        mark_step(record, STEP_SCENE)
        write_publication_record(state_path, record)

        cleanup_build_intermediates(result)
        invalidate_scene_render_caches(project_id, scene_id)
        invalidate_raster_sessions()
        advance(record, STATE_ACTIVE)
        write_publication_record(state_path, record)
        context.update(phase="fullres:complete", current=100, total=100, scene_id=scene_id)
        return {
            **result.as_dict(),
            "validation": validation,
            "_job_performance": {
                "scene_id": scene_id,
                "operation": "fullres_derivative_v2",
                "wall_seconds": result.seconds,
                "peak_rss_bytes": result.peak_rss_bytes,
                "decode_mode": result.decode_mode,
            },
        }
    except BuildCancelled as exc:
        if result is not None:
            cleanup_build_intermediates(result, remove_candidate=True)
        mark_failed(record, error=str(exc), error_code="cancelled")
        write_publication_record(state_path, record)
        raise SceneImportCancelled(str(exc)) from exc
    except BaseException as exc:
        if result is not None:
            cleanup_build_intermediates(result, remove_candidate=True)
        error_code = getattr(exc, "error_code", None)
        if error_code is None:
            text = str(exc).lower()
            error_code = "validation_failed" if "validation_failed" in text else "build_failed"
        mark_failed(record, error=f"{type(exc).__name__}: {exc}", error_code=error_code)
        write_publication_record(state_path, record)
        raise


def run_scene_overview_common_job(spec: dict[str, Any], context: Any) -> dict[str, Any]:
    """Build one display pyramid as an interactive, process-isolated durable job."""

    project_id = str(spec["project_id"])
    scene_id = str((spec.get("payload") or {}).get("scene_id") or "")
    if not scene_id:
        raise RuntimeError("Scene overview job has no scene_id")
    if not load_scene_json(project_id, scene_id, "scene", default={}):
        raise RuntimeError(f"Scene not found: {scene_id}")
    if context.cancel_requested():
        raise SceneImportCancelled("Scene overview cancelled before start")
    context.update(
        phase="scene_overview:building",
        current=0,
        total=1,
        scene_id=scene_id,
    )
    result = _build_scene_display_overviews(
        project_id,
        scene_id,
        _select_overview_profile(1, "on_demand"),
    )
    if context.cancel_requested():
        raise SceneImportCancelled("Scene overview cancelled")
    if result.get("status") == "error":
        raise RuntimeError(str(result.get("error") or "Display overview build failed"))
    context.update(phase="scene_overview:complete", current=1, total=1, scene_id=scene_id)
    return {
        **result,
        "_job_performance": {
            "scene_id": scene_id,
            "operation": "display_overview",
            "wall_seconds": result.get("wall_seconds"),
            "overview_bytes": result.get("overview_bytes", 0),
        },
    }


def _select_overview_profile(
    scene_count: int,
    import_mode: SceneImportMode = DEFAULT_SCENE_IMPORT_MODE,
) -> dict[str, Any]:
    import_mode = _normalize_import_mode(import_mode)
    cpu_count = os.cpu_count() or 1
    available_memory = 0
    try:
        import psutil  # type: ignore

        available_memory = int(psutil.virtual_memory().available)
    except Exception:
        pass

    override = os.environ.get("GEOTILE_OVERVIEW_WORKERS", "auto").strip().lower()
    if override != "auto":
        try:
            requested_workers = max(1, min(4, int(override)))
            selection = "environment_override"
        except ValueError:
            requested_workers = 1
            selection = "invalid_override_fallback"
    elif cpu_count >= 8 and available_memory >= 24 * 1024**3:
        requested_workers = 2
        selection = "cpu_and_memory_budget"
    else:
        requested_workers = 1
        selection = "conservative_memory_or_cpu_budget"

    compression = os.environ.get("GEOTILE_OVERVIEW_COMPRESSION", "ZSTD").strip().upper()
    if compression not in {"ZSTD", "DEFLATE", "LZW"}:
        compression = "ZSTD"
    effective_workers = requested_workers
    if import_mode == "on_demand":
        effective_workers = 1
        selection = "interactive_on_demand"
    elif import_mode == "background" and override == "auto":
        # Background preparation must not monopolise a laptop or compete with tile reads.
        # The measured two-process profile remains available in prepare_all mode.
        effective_workers = 1
        selection = "background_responsiveness_budget"
    return {
        "schema_version": 1,
        "import_mode": import_mode,
        "workers": min(max(1, scene_count), effective_workers),
        "requested_workers": requested_workers,
        "worker_selection": selection,
        "cpu_count": cpu_count,
        "available_memory_bytes": available_memory or None,
        "compression": compression,
        "compression_fallback": "DEFLATE",
        "predictor": "auto",
        "gdal_threads": "1",
        # JP2 preparation runs in a bounded process lane, so it can use half of
        # the machine for OpenJPEG without recreating tile-request oversubscription.
        "jp2_gdal_threads": max(2, min(8, max(1, cpu_count // 2))),
        "resampling": "AVERAGE",
    }


def _overview_process_entry(payload: dict[str, Any]) -> dict[str, Any]:
    return _build_scene_display_overviews(
        str(payload["project_id"]),
        str(payload["scene_id"]),
        dict(payload.get("profile") or {}),
    )


def _run_adaptive_overviews(
    project_id: str,
    pending: list[tuple[str, str]],
    *,
    profile: dict[str, Any],
    cancel_check,
    on_started,
    on_finished,
    priority_scene_ids=None,
) -> list[dict[str, Any]]:
    if not pending:
        return []
    remaining = list(pending)

    def take_next() -> tuple[str, str] | None:
        if not remaining:
            return None
        requested = list(priority_scene_ids() or []) if priority_scene_ids else []
        order = {str(scene_id): index for index, scene_id in enumerate(requested)}
        best_index = min(
            range(len(remaining)),
            key=lambda index: order.get(str(remaining[index][0]), len(order) + index),
        )
        return remaining.pop(best_index)

    workers = max(1, min(int(profile.get("workers") or 1), len(pending)))
    if workers == 1:
        results: list[dict[str, Any]] = []
        while remaining:
            if cancel_check():
                raise SceneImportCancelled("Scene import cancelled during display overview preparation")
            item = take_next()
            if item is None:
                break
            scene_id, log_name = item
            on_started(scene_id, log_name)
            result = _build_scene_display_overviews(project_id, scene_id, profile)
            results.append(result)
            on_finished(scene_id, log_name, result)
        return results

    executor = concurrent.futures.ProcessPoolExecutor(
        max_workers=workers,
        mp_context=multiprocessing.get_context("spawn"),
    )
    futures: dict[concurrent.futures.Future, tuple[str, str]] = {}
    cancelled = False

    def fill_slots() -> None:
        while len(futures) < workers:
            item = take_next()
            if item is None:
                return
            scene_id, log_name = item
            on_started(scene_id, log_name)
            future = executor.submit(
                _overview_process_entry,
                {"project_id": project_id, "scene_id": scene_id, "profile": profile},
            )
            futures[future] = (scene_id, log_name)

    results = []
    try:
        fill_slots()
        while futures:
            if cancel_check():
                cancelled = True
                for future in futures:
                    future.cancel()
                raise SceneImportCancelled("Scene import cancelled during display overview preparation")
            completed, _ = concurrent.futures.wait(
                futures,
                timeout=0.5,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in completed:
                scene_id, log_name = futures.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    _set_scene_overview_status(project_id, scene_id, "error")
                    result = {
                        "scene_id": scene_id,
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                results.append(result)
                on_finished(scene_id, log_name, result)
            fill_slots()
    finally:
        executor.shutdown(wait=not cancelled, cancel_futures=cancelled)
    return results


def _sync_common_import_state(context: Any, job: dict[str, Any], *, catalogue_ready: bool) -> None:
    phase = str(job.get("phase") or "catalogue")
    if phase == "identity":
        current, total = int(job.get("identity_done") or 0), int(job.get("identity_total") or 0)
    elif phase in {"overviews", "complete"}:
        current, total = int(job.get("overviews_done") or 0), int(job.get("overviews_total") or 0)
    else:
        current, total = int(job.get("done") or 0), int(job.get("total") or 0)
    context.update(
        phase=f"scene_import:{phase}",
        current=current,
        total=total,
        catalogue_ready=bool(catalogue_ready),
        import_phase=phase,
        import_state=job.get("state") or "running",
        import_total=int(job.get("total") or 0),
        import_done=int(job.get("done") or 0),
        import_added=int(job.get("added") or 0),
        import_updated=int(job.get("updated") or 0),
        import_blocked=int(job.get("blocked") or 0),
        import_failed=int(job.get("failed") or 0),
        identity_total=int(job.get("identity_total") or 0),
        identity_done=int(job.get("identity_done") or 0),
        overviews_total=int(job.get("overviews_total") or 0),
        overviews_done=int(job.get("overviews_done") or 0),
        import_current=job.get("current"),
        import_recent=list(job.get("recent") or []),
        import_errors=list(job.get("errors") or []),
        import_report=job.get("report"),
        overview_active_scene_ids=list(job.get("overview_active_scene_ids") or []),
        overview_pending_scene_ids=list(job.get("overview_pending_scene_ids") or []),
        overview_profile=job.get("overview_profile"),
        import_mode=_normalize_import_mode(job.get("import_mode")),
        overviews_deferred=bool(job.get("overviews_deferred")),
    )


def _io_counters() -> dict[str, int | None]:
    """Licznik bajtow procesu; brak `psutil` nie moze wywrocic importu."""
    try:
        import psutil  # type: ignore

        counters = psutil.Process().io_counters()
        return {"read_bytes": int(counters.read_bytes), "write_bytes": int(counters.write_bytes)}
    except Exception:
        return {"read_bytes": None, "write_bytes": None}


def _io_delta(after: dict[str, int | None], before: dict[str, int | None]) -> dict[str, int | None]:
    return {
        key: (None if after.get(key) is None or before.get(key) is None
              else max(0, int(after[key]) - int(before[key])))
        for key in ("read_bytes", "write_bytes")
    }


def _finalize_import_report(
    project_id: str,
    scan_id: str,
    *,
    phase_seconds: dict[str, float] | None = None,
    io_bytes: dict[str, Any] | None = None,
    scan_cache: list[dict[str, Any]] | None = None,
    overview_results: list[dict[str, Any]] | None = None,
    identity_scene_ids: list[str] | None = None,
) -> None:
    """Dopisz do zapisanego raportu wynik faz, ktore koncza sie PO katalogowaniu.

    Tozsamosc i piramidy licza sie dlugo po tym, jak scena zostala skatalogowana, wiec bez
    tego kroku raport opisywalby wylacznie pierwsza faze importu.
    """
    report = load_import_report(project_dir(project_id), scan_id)
    if not report:
        return
    if phase_seconds:
        report["phase_seconds"] = dict(sorted(phase_seconds.items()))
    if io_bytes:
        report["io_bytes"] = io_bytes
    if scan_cache is not None:
        report["scan_cache"] = scan_cache

    overview_by_scene = {
        str(item.get("scene_id")): item for item in (overview_results or [])
    }
    refresh_ids = set(identity_scene_ids or []) | set(overview_by_scene)
    for entry in report.get("scenes") or []:
        scene_id = str(entry.get("scene_id") or "")
        if scene_id not in refresh_ids:
            continue
        entry.update(report_manifest_annotations(
            load_scene_json(project_id, scene_id, "scene_manifest", default={}),
            load_scene_json(project_id, scene_id, "scene", default={}),
        ))
        overview = overview_by_scene.get(scene_id)
        if overview:
            entry["overview"] = {
                "status": overview.get("status"),
                "wall_seconds": overview.get("wall_seconds"),
                "overview_bytes": overview.get("overview_bytes"),
                "error": overview.get("error"),
            }
            if overview.get("status") == "error":
                report.setdefault("errors", []).append({
                    "scene_id": scene_id,
                    "filename": entry.get("filename"),
                    "status": "overview_error",
                    "message": overview.get("error"),
                })
    report["updated_at"] = utc_now()
    (import_reports_dir(project_dir(project_id)) / f"{scan_id}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )


def _catalogue_is_ready(project_id: str, report: dict[str, Any]) -> bool:
    if report.get("cancelled") or int(report.get("failed") or 0) or int(report.get("blocked") or 0):
        return False
    not_ready = {"invalid", "decision_required", "missing_source", "relink_pending"}
    for scene_id in list_scene_ids(project_id):
        scene = load_scene_json(project_id, scene_id, "scene", default={})
        if str(scene.get("preparation_status") or "") in not_ready:
            return False
    return True


def run_scene_import_common_job(spec: dict[str, Any], context: Any) -> dict[str, Any]:
    """Catalogue, identify and prepare scene rasters inside a durable IO-heavy job."""

    project_id = str(spec["project_id"])
    payload = spec.get("payload") or {}
    skip_complete = bool(payload.get("resume"))
    import_mode = _normalize_import_mode(payload.get("import_mode"))
    raw_decisions = payload.get("decisions") or []
    decisions = [SceneSelectionDecision.model_validate(item) for item in raw_decisions]
    phase_seconds: dict[str, float] = {"scan": 0.0}
    io_before = _io_counters()
    # Wznowienie importu idzie ze snapshotu preview i wtedy nie ma swiezego skanu, wiec
    # statystyki cache po prostu nie istnieja — `None` jest tu informacja, nie brakiem.
    scan: dict[str, Any] | None = None

    if skip_complete:
        sources = [
            item for item in load_scene_sources(project_id).get("sources", [])
            if item.get("enabled", True)
        ]
        if not sources:
            raise RuntimeError("No enabled scene sources to resume")
        project = load_json(project_id, "project", default={})
        context.update(
            phase="scene_import:catalogue",
            import_phase="catalogue",
            import_current={"filename": "", "stage": "scanning", "started_at": utc_now()},
        )
        scan_started = time.perf_counter()
        scan = _scan_source_records(sources, (project.get("profile") or {}).get("modality"))
        phase_seconds["scan"] = round(time.perf_counter() - scan_started, 6)
        scanned_at = utc_now()
        source_payload = load_scene_sources(project_id)
        enabled_ids = {str(item.get("source_id") or "") for item in sources}
        for item in source_payload.get("sources", []):
            if str(item.get("source_id") or "") in enabled_ids:
                item["last_scan_at"] = scanned_at
                item["last_scan_status"] = "ok"
        save_scene_sources(project_id, source_payload)
        preview = {"sources": sources, "packages": scan["packages"]}
    else:
        preview = payload.get("preview") or {}
        if not preview.get("sources"):
            raise RuntimeError("Scene import snapshot has no sources")

    job = _new_import_job(project_id, str(spec["job_id"]), _count_importable(preview, decisions))
    job["import_mode"] = import_mode
    job["overviews_deferred"] = False
    _sync_common_import_state(context, job, catalogue_ready=False)
    context.emit("scene_import_phase", {"import_phase": "catalogue"})

    def flush(current: dict[str, Any]) -> None:
        _sync_common_import_state(context, current, catalogue_ready=False)

    catalogue_started = time.perf_counter()
    report, pending_identity, pending_overviews = _catalogue_scenes(
        project_id,
        preview,
        decisions,
        job,
        cancel_check=context.cancel_requested,
        skip_complete=skip_complete,
        progress_callback=flush,
    )
    phase_seconds["catalogue"] = round(time.perf_counter() - catalogue_started, 6)
    finalization_started = time.perf_counter()
    project_data = load_json(project_id, "project", default={})
    project_data["scene_count"] = report["total"]
    save_json(project_id, "project", project_data)
    rebuild_scenes_index(project_id)
    phase_seconds["catalogue_finalization"] = round(time.perf_counter() - finalization_started, 6)
    if report.get("cancelled") or context.cancel_requested():
        raise SceneImportCancelled("Scene import cancelled during catalogue")
    catalogue_ready = _catalogue_is_ready(project_id, report)

    job["phase"] = "identity"
    job["identity_total"] = len(pending_identity)
    job["current"] = None
    job["report"] = report
    _sync_common_import_state(context, job, catalogue_ready=False)
    context.emit("scene_import_phase", {"import_phase": "identity"})
    identity_started = time.perf_counter()
    for scene_id, log_name in pending_identity:
        if context.cancel_requested():
            raise SceneImportCancelled("Scene import cancelled during identity")
        job["current"] = {"filename": log_name, "scene_id": scene_id, "stage": "identity", "started_at": utc_now()}
        _sync_common_import_state(context, job, catalogue_ready=False)
        try:
            compute_scene_package_identity(project_id, scene_id, rebuild_index=False)
        except Exception as exc:
            job["errors"].append({"filename": log_name, "message": f"identity: {exc}"})
        job["identity_done"] += 1
        job["current"] = None
        _sync_common_import_state(context, job, catalogue_ready=False)
    rebuild_scenes_index(project_id)
    phase_seconds["identity"] = round(time.perf_counter() - identity_started, 6)

    if context.cancel_requested():
        raise SceneImportCancelled("Scene import cancelled after identity")

    # The project is usable after identity. Depending on the selected mode, display pyramids
    # are deferred, prepared conservatively in the background, or completed before the create
    # workflow reports success.
    job["state"] = "done"
    job["phase"] = "overviews"
    job["overviews_total"] = len(pending_overviews)
    job["current"] = None
    job["overview_active_scene_ids"] = []
    job["overview_pending_scene_ids"] = [scene_id for scene_id, _name in pending_overviews]
    job["overview_profile"] = _select_overview_profile(len(pending_overviews), import_mode)
    job["overviews_deferred"] = import_mode == "on_demand"
    catalogue_ready_during_overviews = catalogue_ready and import_mode != "prepare_all"
    _sync_common_import_state(
        context,
        job,
        catalogue_ready=catalogue_ready_during_overviews,
    )
    context.emit(
        "scene_import_phase",
        {
            "import_phase": "overviews",
            "catalogue_ready": catalogue_ready_during_overviews,
            "overview_profile": job["overview_profile"],
            "import_mode": import_mode,
        },
    )

    if import_mode == "on_demand":
        job["phase"] = "complete"
        _sync_common_import_state(context, job, catalogue_ready=catalogue_ready)
        context.emit(
            "scene_import_phase",
            {
                "import_phase": "complete",
                "catalogue_ready": catalogue_ready,
                "overviews_deferred": True,
            },
        )
        return {
            "report": report,
            "catalogue_ready": catalogue_ready,
            "identity_total": job["identity_total"],
            "identity_done": job["identity_done"],
            "overviews_total": job["overviews_total"],
            "overviews_done": 0,
            "overviews_deferred": True,
            "overview_profile": job["overview_profile"],
            "overview_results": [],
            "_job_performance": {
                "phase_seconds": {**phase_seconds, "overviews": 0.0},
                "overview_profile": job["overview_profile"],
                "overview_bytes": 0,
                "overview_failures": 0,
            },
        }

    active_overviews: dict[str, dict[str, Any]] = {}

    def overview_started(scene_id: str, log_name: str) -> None:
        current = {
            "filename": log_name,
            "scene_id": scene_id,
            "stage": "overviews",
            "started_at": utc_now(),
        }
        active_overviews[scene_id] = current
        job["overview_pending_scene_ids"] = [
            value for value in job.get("overview_pending_scene_ids", []) if value != scene_id
        ]
        job["overview_active_scene_ids"] = sorted(active_overviews)
        job["current"] = current
        _sync_common_import_state(
            context,
            job,
            catalogue_ready=catalogue_ready_during_overviews,
        )

    def overview_finished(scene_id: str, log_name: str, result: dict[str, Any]) -> None:
        active_overviews.pop(scene_id, None)
        job["overview_active_scene_ids"] = sorted(active_overviews)
        job["overviews_done"] += 1
        if result.get("status") == "error":
            job["errors"].append({
                "filename": log_name,
                "message": f"overview: {result.get('error') or 'unknown error'}",
            })
        job["current"] = next(iter(active_overviews.values()), None)
        _sync_common_import_state(
            context,
            job,
            catalogue_ready=catalogue_ready_during_overviews,
        )

    def priority_scene_ids() -> list[str]:
        try:
            state = read_state(project_id, str(spec["job_id"]))
        except Exception:
            return []
        return [str(value) for value in (state.get("overview_priority_scene_ids") or [])]

    overviews_started = time.perf_counter()
    overview_results = _run_adaptive_overviews(
        project_id,
        pending_overviews,
        profile=job["overview_profile"],
        cancel_check=context.cancel_requested,
        on_started=overview_started,
        on_finished=overview_finished,
        priority_scene_ids=priority_scene_ids,
    )
    phase_seconds["overviews"] = round(time.perf_counter() - overviews_started, 6)

    job["phase"] = "complete"
    job["current"] = None
    _sync_common_import_state(context, job, catalogue_ready=catalogue_ready)
    _finalize_import_report(
        project_id,
        str(report.get("scan_id") or ""),
        phase_seconds=phase_seconds,
        io_bytes=_io_delta(_io_counters(), io_before),
        scan_cache=scan.get("scan_cache") if isinstance(scan, dict) else None,
        overview_results=overview_results,
        identity_scene_ids=[scene_id for scene_id, _name in pending_identity],
    )
    context.emit("scene_import_phase", {"import_phase": "complete", "catalogue_ready": catalogue_ready})
    return {
        "report": report,
        "catalogue_ready": catalogue_ready,
        "identity_total": job["identity_total"],
        "identity_done": job["identity_done"],
        "overviews_total": job["overviews_total"],
        "overviews_done": job["overviews_done"],
        "overviews_deferred": False,
        "overview_profile": job["overview_profile"],
        "overview_results": overview_results,
        "_job_performance": {
            "phase_seconds": phase_seconds,
            "overview_profile": job["overview_profile"],
            "overview_bytes": sum(int(item.get("overview_bytes") or 0) for item in overview_results),
            "overview_failures": sum(item.get("status") == "error" for item in overview_results),
        },
    }


def _apply_preview(
    project_id: str,
    preview: dict[str, Any],
    decisions: list[Any],
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Ścieżka synchroniczna (dodawanie źródła do istniejącego projektu): katalogowanie
    inline, a tożsamość/piramidy zlecone jako BackgroundTasks jak dotąd."""
    report, pending_identity, pending_overviews = _catalogue_scenes(project_id, preview, decisions)
    for scene_id, _log_name in pending_identity:
        background_tasks.add_task(compute_scene_package_identity, project_id, scene_id)
    for scene_id, _log_name in pending_overviews:
        background_tasks.add_task(_build_scene_display_overviews, project_id, scene_id)
    return report


def _flush_catalogue_progress(
    project_id: str,
    job: dict[str, Any],
    progress_callback=None,
) -> None:
    if progress_callback is not None:
        progress_callback(job)
    else:
        _write_import_job(project_id, job)


def _scene_characterization_matches_selection(
    scene_doc: dict[str, Any],
    package: dict[str, Any],
    selection: dict[str, Any],
    previous_package: dict[str, Any] | None = None,
) -> bool:
    """Validate characterization against every selected scene-defining asset.

    Mosaic and derived products cannot use the old single-raster ``size + mtime`` key.
    Their persisted package snapshot is compared across all measurement parts and
    defining metadata. Browse/preview assets are intentionally outside this scope.
    """

    info = scene_doc.get("scene_info") or {}
    if scene_doc.get("preparation_status") == "relink_pending":
        return False
    if not info or scene_doc.get("scene_info_error"):
        return False
    if scene_doc.get("scene_info_version") != SCENE_INFO_VERSION:
        return False
    if previous_package is not None:
        candidate_package = {
            **package,
            "identity_asset_ids": selection.get("identity_asset_ids") or selection.get("asset_ids") or [],
            "defining_metadata_asset_ids": scene_identity_scope({
                **package,
                "identity_asset_ids": selection.get("identity_asset_ids") or selection.get("asset_ids") or [],
                "selection": selection,
            })["defining_metadata_asset_ids"],
            "selection": selection,
        }
        if _identity_asset_snapshot(previous_package) != _identity_asset_snapshot(candidate_package):
            return False
    if selection.get("raster_kind") != "direct":
        return previous_package is not None
    selected_ids = set(selection.get("asset_ids") or [])
    selected = [
        asset for asset in (package.get("assets") or []) if asset.get("asset_id") in selected_ids
    ]
    if len(selected) != 1:
        return True
    asset_size = selected[0].get("size")
    asset_mtime = selected[0].get("mtime_ns")
    if asset_size is None or asset_mtime is None:
        return True
    return (
        int(info.get("characterization_source_size") or -1) == int(asset_size)
        and int(info.get("characterization_source_mtime_ns") or -1) == int(asset_mtime)
    )


def _catalogue_scenes(
    project_id: str,
    preview: dict[str, Any],
    decisions: list[Any],
    job: dict[str, Any] | None = None,
    cancel_check=None,
    skip_complete: bool = False,
    progress_callback=None,
) -> tuple[dict[str, Any], list[tuple[str, str]], list[tuple[str, str]]]:
    """Wspólny rdzeń: katalog scen z preview. Zwraca (raport, pending_identity, pending_overviews)
    zamiast planować zadania — dzięki temu ta sama pętla obsługuje tryb sync (BackgroundTasks)
    i tryb job (wątek + plik postępu). Gdy `job` podany, po każdej scenie flushuje postęp.
    `cancel_check` (opcjonalny) sprawdzany między scenami — pozwala przerwać import.
    `skip_complete` (wznawianie) pomija sceny już mające poprawne scene_info, dokładając tylko
    brakującą tożsamość/piramidy."""
    decisions_by_package = _resolve_preview_decisions(preview, decisions)
    existing = {
        (scene.get("source_id"), scene.get("package_id")): scene_id
        for scene_id in list_scene_ids(project_id)
        if (scene := load_scene_json(project_id, scene_id, "scene", default={}))
    }
    seen_keys: set[tuple[str, str]] = set()
    added = 0
    updated = 0
    blocked = 0
    failed = 0
    archived = 0
    pending_identity: list[tuple[str, str]] = []
    pending_overviews: list[tuple[str, str]] = []
    ignored_by_source: dict[str, set[str]] = {}
    cancelled = False
    # P1.6: raport zbierany scena po scenie, rownolegle do licznikow. Licznikow nie ruszamy —
    # sa kontraktem P0.8 i UI — raport je tylko uzupelnia o przyczyny.
    report_builder = ImportReportBuilder()
    report_builder.record_sources(preview.get("sources") or [])
    for package in preview.get("packages", []):
        if cancel_check is not None and cancel_check():
            cancelled = True
            break
        key = (package["source_id"], package["package_id"])
        selection = dict(package.get("selection") or {})
        # Archiwum jest widoczne w preview, ale nie jest scena (P1.3a). Pomijamy je PRZED
        # `seen_keys`, bo nigdy nie bylo katalogowane — dopisanie go tam nie mialoby czego
        # chronic przed `missing_source`, a licznik `archived` jest rozlaczny z reszta raportu.
        if str(selection.get("status") or "") in ARCHIVE_STATUSES:
            archived += 1
            report_builder.record_scene(build_report_scene_entry(
                scene_id=str(package.get("package_id") or ""),
                filename=str(package.get("package_root_relative") or ""),
                package=package,
                selection=selection,
                status="archived",
                elapsed_ms=0,
            ))
            continue
        decision = decisions_by_package.get(_package_decision_key(package))
        if decision and decision.action == "skip":
            ignored_by_source.setdefault(package["source_id"], set()).add(package["package_id"])
            continue
        seen_keys.add(key)
        if decision:
            known_asset_ids = {asset.get("asset_id") for asset in package.get("assets") or []}
            if not decision.asset_ids or any(asset_id not in known_asset_ids for asset_id in decision.asset_ids):
                raise HTTPException(400, f"Invalid asset selection for package {package['package_id']}")
            selection["asset_ids"] = decision.asset_ids
            selection["identity_asset_ids"] = decision.asset_ids
            selection["rgb_bands"] = decision.rgb_bands
            selection["selected_by"] = "user"
            selection.setdefault(
                "diagnostics",
                {"warnings": [], "errors": [], "metadata_conflicts": []},
            )
            # Decyzja w kreatorze importu musi przejsc przez ten sam hook co pozniejszy
            # `select-asset`. Inaczej typ produktu i sidecary pozostawaly z automatycznej
            # selekcji, mimo ze uzytkownik wskazal inny wariant.
            get_resolver(package.get("provider") or "generic").refine_manual_selection(
                package,
                selection,
            )
            derived = selection.get("product_type") in {"MUL+PAN", "MS-FS_RGB+PAN"}
            selection["status"] = (
                "prepare_required" if derived and decision.rgb_bands and len(decision.asset_ids) > 1
                else "decision_required" if derived and len(decision.asset_ids) > 1
                else "ready"
            )
            selection["raster_kind"] = "derived" if derived else "direct" if len(decision.asset_ids) == 1 else "virtual_mosaic"
        scene_id = existing.get(key) or _logical_scene_id(*key)
        log_name = _selected_filename(package, selection) or package.get("package_root_relative") or scene_id
        if skip_complete:
            scene_doc = load_scene_json(project_id, scene_id, "scene", default={})
            previous_manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
            if _scene_characterization_matches_selection(
                scene_doc,
                package,
                selection,
                previous_manifest.get("source_package") or None,
            ):
                scene_doc, _overview_changed = sync_scene_source_overviews(
                    project_id,
                    scene_id,
                    scene_doc,
                    force=True,
                )
                # Scena już gotowa — nie przetwarzamy jej ponownie, tylko dokładamy brakujące fazy.
                manifest = previous_manifest
                if (manifest.get("source_identity") or {}).get("status") != "complete" and selection.get("identity_asset_ids"):
                    pending_identity.append((scene_id, log_name))
                if selection.get("status") == "ready" and selection.get("raster_kind") in DISPLAY_OVERVIEW_RASTER_KINDS:
                    pending_overviews.append((scene_id, log_name))
                if key in existing:
                    updated += 1
                else:
                    added += 1
                report_builder.record_scene({
                    **build_report_scene_entry(
                        scene_id=scene_id,
                        filename=log_name,
                        package=package,
                        selection=selection,
                        status="skipped",
                        elapsed_ms=0,
                    ),
                    **report_manifest_annotations(manifest, scene_doc),
                })
                if job is not None:
                    job["done"] += 1
                    job["added"], job["updated"], job["blocked"] = added, updated, blocked
                    job["recent"] = (job["recent"] + [{"filename": log_name, "status": "skipped", "ms": 0}])[-20:]
                    job["updated_at"] = utc_now()
                    _flush_catalogue_progress(project_id, job, progress_callback)
                continue
        if job is not None:
            job["current"] = {"filename": log_name, "stage": "reading", "started_at": utc_now()}
            _flush_catalogue_progress(project_id, job, progress_callback)
        started = time.monotonic()
        is_blocked = selection.get("status") == "decision_required" and not decision
        try:
            _save_package_scene(project_id, scene_id, package, selection)
            scene_status: str = "ok"
            scene_error: str | None = None
        except HTTPException:
            raise
        except Exception as exc:
            scene_status, scene_error = "error", str(exc)
        if scene_status == "error":
            failed += 1
        elif is_blocked:
            blocked += 1
            scene_status = "blocked"
        elif key in existing:
            updated += 1
        else:
            added += 1
        if scene_status == "ok" and not is_blocked and selection.get("identity_asset_ids"):
            pending_identity.append((scene_id, log_name))
        # Piramidy wyświetlania dla gotowych produktów direct bez overviews
        # (DESIGN_DECISIONS.md, tile-serving P1). W tle — import nie czeka; do czasu zbudowania
        # kafle serwują się ze źródła jak dotąd. Funkcja sama pomija sceny z overviews.
        if scene_status == "ok" and selection.get("status") == "ready" and selection.get("raster_kind") in DISPLAY_OVERVIEW_RASTER_KINDS:
            pending_overviews.append((scene_id, log_name))
        report_builder.record_scene({
            **build_report_scene_entry(
                scene_id=scene_id,
                filename=log_name,
                package=package,
                selection=selection,
                status="blocked" if is_blocked else scene_status,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                error=scene_error,
            ),
            **report_manifest_annotations(
                load_scene_json(project_id, scene_id, "scene_manifest", default={}),
                load_scene_json(project_id, scene_id, "scene", default={}),
            ),
        })
        if job is not None:
            job["done"] += 1
            job["added"], job["updated"], job["blocked"], job["failed"] = added, updated, blocked, failed
            if scene_status == "error":
                job["errors"].append({"filename": log_name, "message": scene_error})
            job["recent"] = (job["recent"] + [{
                "filename": log_name,
                "status": scene_status,
                "ms": int((time.monotonic() - started) * 1000),
            }])[-20:]
            job["current"] = None
            job["updated_at"] = utc_now()
            _flush_catalogue_progress(project_id, job, progress_callback)

    if ignored_by_source:
        source_payload = load_scene_sources(project_id)
        for source in source_payload.get("sources", []):
            package_ids = ignored_by_source.get(str(source.get("source_id") or ""))
            if package_ids:
                source["ignored_package_ids"] = sorted(
                    set(source.get("ignored_package_ids") or []) | package_ids
                )
        save_scene_sources(project_id, source_payload)

    # Detekcja scen „zniknęłych" (są w projekcie, nie ma ich w preview) ma sens tylko po
    # PEŁNYM przejściu preview. Przy anulowaniu połowa scen jeszcze nie została odwiedzona,
    # więc pominięcie tego kroku zapobiega błędnemu oznaczeniu ich jako missing_source.
    missing = 0
    if not cancelled:
        preview_source_ids = {source["source_id"] for source in preview.get("sources", [])}
        for scene_id in list_scene_ids(project_id):
            scene = load_scene_json(project_id, scene_id, "scene", default={})
            key = (scene.get("source_id"), scene.get("package_id"))
            if scene.get("source_id") in preview_source_ids and key not in seen_keys:
                if scene.get("preparation_status") != "missing_source":
                    scene["preparation_status"] = "missing_source"
                    save_scene_json(project_id, scene_id, "scene", scene)
                manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
                if manifest:
                    manifest.setdefault("working_view", {})["preparation_status"] = "missing_source"
                    save_scene_json(project_id, scene_id, "scene_manifest", manifest)
                missing += 1

    report_builder.cancelled = cancelled
    report_builder.counters = {
        "added": added,
        "updated": updated,
        "missing": missing,
        "blocked": blocked,
        "failed": failed,
        "archived": archived,
        "considered": added + updated + blocked + failed,
        "total": len(list_scene_ids(project_id)),
    }
    report_builder.save(project_dir(project_id))
    # Wolajacy dostaje TEN SAM ksztalt co dotad (liczniki + `scan_id`), zeby kontrakt P0.8
    # i UI postepu sie nie zmienily. Pelna tresc raportu lezy w pliku i jest do pobrania
    # osobnym endpointem — inaczej odpowiedz HTTP rosla by z liczba scen.
    report = {
        "scan_id": report_builder.scan_id,
        "created_at": report_builder.created_at,
        **report_builder.counters,
        "cancelled": cancelled,
    }
    return report, pending_identity, pending_overviews


def _save_package_scene(
    project_id: str,
    scene_id: str,
    package: dict[str, Any],
    selection: dict[str, Any],
    *,
    accept_source_change: bool = False,
) -> None:
    manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
    previous_package = manifest.get("source_package") or {}
    previous_working = manifest.get("working_view") or {}
    assets = [dict(asset) for asset in package.get("assets") or []]
    selected_ids = set(selection.get("asset_ids") or [])
    for asset in assets:
        if asset.get("asset_id") in selected_ids and asset.get("role") == "raster_candidate":
            asset["role"] = "primary_raster"
    source_package = {
        "package_id": package["package_id"],
        "source_id": package["source_id"],
        "provider": package["provider"],
        "provider_scene_id": selection.get("provider_scene_id"),
        "package_root_relative": package.get("package_root_relative"),
        "product_type": selection.get("product_type"),
        "processing_level": selection.get("processing_level"),
        "spectral_processing": selection.get("spectral_processing"),
        "polarization_or_bands": selection.get("polarization_or_bands") or [],
        "assets": assets,
        "identity_asset_ids": selection.get("identity_asset_ids") or selection.get("asset_ids") or [],
        "mosaic_parts_order": selection.get("mosaic_parts_order") or [],
        "resolver": {
            "name": package["provider"],
            "version": get_resolver(package["provider"]).version,
            "diagnostics": selection.get("diagnostics") or {"warnings": [], "errors": [], "metadata_conflicts": []},
        },
        "selection_provenance": {
            "selected_by": selection.get("selected_by") or "resolver",
            "selected_at": utc_now(),
            "alternatives": selection.get("alternatives") or [],
        },
        "selection": selection,
    }
    source_package["defining_metadata_asset_ids"] = scene_identity_scope(source_package)[
        "defining_metadata_asset_ids"
    ]
    source_identity = manifest.get("source_identity") or {
        "schema_version": 3,
        "source_scene_uid": None,
        "source_scene_fingerprint": None,
        "source_scene_candidate_uid": None,
        "source_scene_candidate_fingerprint": None,
        "source_package_fingerprint": None,
        "source_package_fingerprint_strength": None,
        "delivery_inventory_fingerprint": None,
        "scene_candidate_fingerprint": None,
        "working_variant_fingerprint": None,
        "identity_method": None,
        "identity_strength": None,
        "provider_scene_id": selection.get("provider_scene_id"),
        "status": "pending" if source_package["identity_asset_ids"] else "unavailable",
        "computed_at": None,
    }
    existing_scene = load_scene_json(project_id, scene_id, "scene", default={})
    source_changed = bool(previous_package) and _identity_asset_snapshot(previous_package) != _identity_asset_snapshot(source_package)
    if source_changed:
        invalidate_scene_source_derivatives(project_id, scene_id, manifest=manifest)
        source_identity = {
            "schema_version": 3,
            "source_scene_uid": None,
            "source_scene_fingerprint": None,
            "source_scene_candidate_uid": None,
            "source_scene_candidate_fingerprint": None,
            "source_package_fingerprint": None,
            "source_package_fingerprint_strength": None,
            "delivery_inventory_fingerprint": None,
            "scene_candidate_fingerprint": None,
            "working_variant_fingerprint": None,
            "identity_method": None,
            "identity_strength": None,
            "provider_scene_id": selection.get("provider_scene_id"),
            "status": "pending" if source_package["identity_asset_ids"] else "unavailable",
            "computed_at": None,
        }
    filename = _selected_filename(package, selection) or existing_scene.get("filename") or selection.get("display_name") or scene_id
    scene = Scene(
        **{
            **existing_scene,
            "id": scene_id,
            "filename": filename,
            "display_name": selection.get("display_name") or selection.get("provider_scene_id") or filename,
            "source_id": package["source_id"],
            "package_id": package["package_id"],
            "provider_scene_id": selection.get("provider_scene_id"),
            "provider": package["provider"],
            "modality": package.get("modality"),
            "preparation_status": "source_changed" if source_changed and previous_working.get("locked") else selection.get("status") or "invalid",
        }
    ).model_dump()
    if source_changed:
        # Do not expose characterization or display products computed for the old
        # bytes, even when a locked working view requires an explicit user decision.
        scene.pop("scene_info", None)
        scene.pop("scene_info_version", None)
        scene.pop("scene_info_error", None)
        scene["overview_status"] = "pending"
    if source_changed and previous_working.get("locked") and not accept_source_change:
        manifest["source_change_candidate"] = source_package
        previous_working["preparation_status"] = "source_changed"
        manifest["working_view"] = previous_working
        save_scene_json(project_id, scene_id, "scene", scene)
        save_scene_json(project_id, scene_id, "scene_manifest", manifest)
        return
    if source_changed:
        # The on-disk prepared product may be retained for recovery, but an unlocked
        # manifest must not reuse lineage, grid identity, or a processing pointer
        # computed for the previous source bytes.
        previous_working["working_variant_fingerprint"] = None
        previous_working["working_grid_uid"] = None
        previous_working["processing_manifest"] = None
        manifest["working_variant_fingerprint"] = None
    manifest.update({
        "source_package": source_package,
        "source_identity": source_identity,
        "working_view": manifest.get("working_view") or {},
    })
    _configure_working_view(project_id, scene_id, source_package, manifest, selection)
    scene["raster_ref"] = (manifest.get("working_view") or {}).get("raster_ref")
    scene["raster_kind"] = (manifest.get("working_view") or {}).get("raster_kind")
    scene["working_variant_id"] = (manifest.get("working_view") or {}).get("variant_id")
    save_scene_json(project_id, scene_id, "scene", scene)
    save_scene_json(project_id, scene_id, "annotations", load_scene_json(project_id, scene_id, "annotations", default=[]))
    save_scene_json(project_id, scene_id, "scene_manifest", manifest)
    if selection.get("status") in {"decision_required", "prepare_required"}:
        # A decision-required package is a valid catalogue entry, but deliberately
        # has no resolvable working raster yet.  Do not turn that state into an I/O
        # error by attempting characterization.
        #
        # `prepare_required` ma DOKLADNIE te sama wlasnosc i brakowalo go tutaj: produkt
        # pochodny (MUL+PAN) powstaje dopiero przy jawnym przygotowaniu, wiec proba
        # charakterystyki konczyla sie `RuntimeError`, scena dostawala
        # `preparation_status=invalid`, a import liczyl ja jako `failed`. Ujawnil to raport
        # P1.6 na rzeczywistej dostawie WV2: `failed=1` bez zadnej mozliwej do odczytania
        # przyczyny w starym raporcie.
        return
    _refresh_scene_from_manifest(project_id, scene_id)


def _persist_migrated_package_scene(
    project_id: str,
    scene_id: str,
    package: dict[str, Any],
    selection: dict[str, Any],
) -> None:
    """Persist an explicitly approved migration through the canonical scene writer."""
    _save_package_scene(
        project_id,
        scene_id,
        package,
        selection,
        accept_source_change=True,
    )


def _identity_asset_snapshot(package: dict[str, Any]) -> list[tuple[str, str, int, int]]:
    scoped_ids = set(scene_identity_scope(package)["candidate_asset_ids"])
    return sorted(
        (
            str(asset.get("asset_id") or ""),
            str(asset.get("relative_path") or ""),
            int(asset.get("size") or 0),
            int(asset.get("mtime_ns") or 0),
        )
        for asset in package.get("assets") or []
        if asset.get("asset_id") in scoped_ids
    )


def _invalidate_scene_source_derivatives(project_id: str, scene_id: str) -> None:
    """Backward-compatible router shim for the shared invalidation service."""

    invalidate_scene_source_derivatives(project_id, scene_id)


def _configure_working_view(
    project_id: str,
    scene_id: str,
    package: dict[str, Any],
    manifest: dict[str, Any],
    selection: dict[str, Any],
) -> None:
    asset_ids = selection.get("asset_ids") or []
    assets = {asset.get("asset_id"): asset for asset in package.get("assets") or []}
    status = selection.get("status") or "invalid"
    raster_kind = selection.get("raster_kind") or ("direct" if len(asset_ids) == 1 else "virtual_mosaic")
    variant_definition = build_working_variant_definition({**selection, "raster_kind": raster_kind})
    variant_id = working_variant_id(variant_definition)
    raster_ref = None
    mosaic_geometry: dict[str, Any] | None = None
    if status == "ready" and len(asset_ids) == 1:
        asset = assets.get(asset_ids[0])
        if asset:
            raster_ref = {"storage": "source", "source_id": package.get("source_id"), "relative_path": asset["relative_path"]}
            if asset.get("format") == "jp2" and not _jp2_available():
                status = "runtime_unsupported"
    elif status == "ready" and len(asset_ids) > 1:
        paths = [resolve_source_asset(project_id, package.get("source_id"), assets[asset_id]["relative_path"]) for asset_id in asset_ids if asset_id in assets]
        paths = [path for path in paths if path]
        diagnostics = selection.setdefault(
            "diagnostics", {"warnings": [], "errors": [], "metadata_conflicts": []}
        )
        try:
            # Geometria czesci jest badana RAZ i opisana (P1.4). Nakladki, duplikaty i luki
            # w pokryciu nie blokuja mozaiki — sa stanem dostawy i maja byc widoczne, tak samo
            # jak `partial_delivery` z manifestu TIL.
            report = inspect_mosaic_parts(paths)
            diagnostics.setdefault("warnings", []).extend(report.warnings)
            mosaic_geometry = {"parts": len(report.parts), **report.coverage}
            vrt = create_vrt(project_id, scene_id, variant_id, paths, report=report)
            raster_ref = {"storage": "project", "relative_path": vrt.relative_to(project_dir(project_id)).as_posix()}
        except Exception as exc:
            status = "prepare_required"
            diagnostics["errors"].append({
                "code": "vrt_unavailable",
                "message": str(exc),
            })
    previous_working = manifest.get("working_view") or {}
    same_variant = previous_working.get("variant_id") == variant_id
    manifest["working_view"] = {
        "variant_id": variant_id,
        "variant_definition": variant_definition,
        "working_variant_fingerprint": (
            previous_working.get("working_variant_fingerprint") if same_variant else None
        ),
        "raster_kind": raster_kind,
        "raster_ref": raster_ref,
        "mosaic_geometry": mosaic_geometry,
        "working_grid_uid": previous_working.get("working_grid_uid") if same_variant else None,
        "preparation_status": status,
        "locked": bool(previous_working.get("locked")),
        "locked_at": previous_working.get("locked_at"),
        "lock_reason": previous_working.get("lock_reason"),
        "processing_manifest": previous_working.get("processing_manifest") if same_variant else None,
    }


def _refresh_scene_from_manifest(project_id: str, scene_id: str) -> None:
    scene = load_scene_json(project_id, scene_id, "scene", default={})
    try:
        path = SceneRasterResolver.resolve(project_id, scene_id).path
        project = load_json(project_id, "project", default={})
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        source_package = manifest.get("source_package") or {}
        modality = (
            manifest.get("modality")
            or scene.get("modality")
            or (project.get("profile") or {}).get("modality")
        )
        info = get_scene_info(
            path,
            modality=modality,
            product_type=source_package.get("product_type"),
            selected_sensors=(project.get("profile") or {}).get("sensors") or [],
        )
        scene["scene_info"] = info.model_dump()
        source_overviews = scene["scene_info"].get("source_overviews") or {}
        apply_source_overview_metadata(scene, source_overviews)
        if source_overviews.get("usable"):
            scene["overview_status"] = (
                "native"
                if _scene_source_overviews_display_ready(scene)
                else "pending"
            )
        scene["scene_info_version"] = SCENE_INFO_VERSION
        scene.pop("scene_info_error", None)
        save_scene_json(project_id, scene_id, "scene", scene)
        write_scene_manifest(project_id, scene_id, project, scene, path)
    except Exception as exc:
        scene["scene_info_error"] = str(exc)
        scene["preparation_status"] = "invalid"
        save_scene_json(project_id, scene_id, "scene", scene)
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        manifest.setdefault("working_view", {})["preparation_status"] = "invalid"
        save_scene_json(project_id, scene_id, "scene_manifest", manifest)
        project = load_json(project_id, "project", default={})
        write_scene_manifest(project_id, scene_id, project, scene, None)
        raise RuntimeError(f"Scene raster could not be characterized: {exc}") from exc


def _activate_prepared_result(project_id: str, scene_id: str, result: dict[str, Any]) -> None:
    manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
    target = Path(result["path"])
    working = manifest.setdefault("working_view", {})
    working.update({
        "variant_id": result["variant_id"],
        "variant_definition": result.get("variant_definition") or working.get("variant_definition"),
        "raster_kind": "derived",
        "raster_ref": {"storage": "project", "relative_path": target.relative_to(project_dir(project_id)).as_posix()},
        "working_grid_uid": None,
        "preparation_status": "ready",
        "processing_manifest": {"storage": "project", "relative_path": Path(result["processing_manifest"]).relative_to(project_dir(project_id)).as_posix()},
    })
    working_fingerprint = compute_working_variant_fingerprint(
        manifest.get("source_identity") or {},
        working,
    )
    working["working_variant_fingerprint"] = working_fingerprint
    manifest["working_variant_fingerprint"] = working_fingerprint
    save_scene_json(project_id, scene_id, "scene_manifest", manifest)
    _refresh_scene_from_manifest(project_id, scene_id)


def _regenerate_project_vrts(project_id: str, source_id: str) -> None:
    for scene_id in list_scene_ids(project_id):
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        package = manifest.get("source_package") or {}
        working = manifest.get("working_view") or {}
        if package.get("source_id") != source_id or working.get("raster_kind") != "virtual_mosaic":
            continue
        selection = package.get("selection") or {}
        _configure_working_view(project_id, scene_id, package, manifest, selection)
        save_scene_json(project_id, scene_id, "scene_manifest", manifest)
        _refresh_scene_from_manifest(project_id, scene_id)


def _preview_required_asset_ids(
    package: dict[str, Any],
    decision: Any | None,
) -> set[str]:
    if decision is not None and decision.action == "skip":
        return set()
    selection = copy.deepcopy(package.get("selection") or {})
    if decision is not None:
        selection["asset_ids"] = list(decision.asset_ids)
        selection["identity_asset_ids"] = list(decision.asset_ids)
        selection["rgb_bands"] = decision.rgb_bands
        selection["selected_by"] = "user"
        selection.setdefault(
            "diagnostics",
            {"warnings": [], "errors": [], "metadata_conflicts": []},
        )
        get_resolver(package.get("provider") or "generic").refine_manual_selection(
            package,
            selection,
        )
    scoped = scene_identity_scope({
        **package,
        "identity_asset_ids": selection.get("identity_asset_ids") or selection.get("asset_ids") or [],
        "selection": selection,
    })
    required = set(scoped["candidate_asset_ids"])
    # TIL/RPC files define part completeness or geometry even when a provider's
    # metadata binder does not classify them as ordinary product metadata.
    required.update(
        str(asset.get("asset_id") or "")
        for asset in package.get("assets") or []
        if asset.get("asset_role") in {"tile_manifest", "rpc"}
    )
    required.discard("")
    return required


def _validate_preview_inputs(preview: dict[str, Any], decisions: list[Any] | None = None) -> None:
    source_roots = {
        source["source_id"]: _resolve_input_root(source["root_path"])
        for source in preview.get("sources", [])
    }
    decisions_by_uid = _resolve_preview_decisions(preview, decisions or [])
    for package in preview.get("packages", []):
        root = source_roots.get(package.get("source_id"))
        snapshot = package.get("source_snapshot") or {}
        if root is None:
            raise HTTPException(409, "Scene source is no longer available")
        required_ids = _preview_required_asset_ids(
            package,
            decisions_by_uid.get(_package_decision_key(package)),
        )
        required_paths = {
            str(asset.get("relative_path") or "")
            for asset in package.get("assets") or []
            if str(asset.get("asset_id") or "") in required_ids
        }
        for asset in snapshot.get("assets") or []:
            # Old preview schema did not persist asset_id in the snapshot. Relative
            # path fallback keeps it compatible while still limiting validation.
            if (
                str(asset.get("asset_id") or "") not in required_ids
                and str(asset.get("relative_path") or "") not in required_paths
            ):
                continue
            path = (root / str(asset.get("relative_path") or "")).resolve(strict=False)
            try:
                path.relative_to(root)
                stat = path.stat()
            except (ValueError, OSError):
                raise HTTPException(409, "Source changed after preview; scan it again")
            if stat.st_size != asset.get("size") or stat.st_mtime_ns != asset.get("mtime_ns"):
                raise HTTPException(409, "Source changed after preview; scan it again")


def _validate_source_modalities(sources: list[dict[str, Any]], modality: str | None) -> None:
    for source in sources:
        expected = PROVIDER_MODALITY.get(source.get("provider"))
        if expected and modality and expected != modality:
            raise HTTPException(400, f"Provider {source['provider']} is incompatible with {modality} project")


def _validate_requested_sources(preview: dict[str, Any], requested_sources: list[SceneSourceCreate]) -> None:
    preview_values = sorted(
        (
            str(source.get("provider") or ""),
            str(Path(source.get("root_path") or "").expanduser().resolve(strict=False)).casefold(),
            bool(source.get("enabled", True)),
        )
        for source in preview.get("sources", [])
    )
    requested_values = sorted(
        (
            source.provider,
            str(Path(source.root_path).expanduser().resolve(strict=False)).casefold(),
            source.enabled,
        )
        for source in requested_sources
    )
    if preview_values != requested_values:
        raise HTTPException(409, "Scene sources changed after preview; scan them again")


def _validate_preview_decisions(preview: dict[str, Any], decisions: list[Any]) -> None:
    decisions_by_uid = _resolve_preview_decisions(preview, decisions)
    packages_by_uid = {
        _package_decision_key(package): package
        for package in preview.get("packages", [])
    }
    for uid, decision in decisions_by_uid.items():
        package_id = str(decision.package_id or "")
        package = packages_by_uid[uid]
        if decision.action == "skip":
            if decision.asset_ids or decision.rgb_bands:
                raise HTTPException(400, f"Skipped package {package_id} cannot contain an asset selection")
            continue
        known_asset_ids = {
            str(asset.get("asset_id") or "")
            for asset in package.get("assets", [])
        }
        selected_ids = [str(asset_id) for asset_id in decision.asset_ids]
        if not selected_ids or len(selected_ids) != len(set(selected_ids)):
            raise HTTPException(400, f"Asset selection for package {package_id} must be non-empty and unique")
        if any(asset_id not in known_asset_ids for asset_id in selected_ids):
            raise HTTPException(400, f"Invalid asset selection for package {package_id}")
        product_type = str((package.get("selection") or {}).get("product_type") or "")
        if product_type in {"MUL+PAN", "MS-FS_RGB+PAN"} and len(selected_ids) > 1:
            rgb_bands = decision.rgb_bands or []
            if len(rgb_bands) != 3 or len(set(rgb_bands)) != 3 or any(band < 1 for band in rgb_bands):
                raise HTTPException(400, f"Three unique positive RGB band indexes are required for package {package_id}")


def _selected_filename(package: dict[str, Any], selection: dict[str, Any]) -> str | None:
    selected = set(selection.get("asset_ids") or [])
    for asset in package.get("assets") or []:
        if asset.get("asset_id") in selected:
            return Path(asset.get("relative_path", "")).name
    return None


def _logical_scene_id(source_id: str, package_id: str) -> str:
    return hashlib.sha256(f"{source_id}:{package_id}".encode()).hexdigest()[:12]


def _public_preview(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in payload.items()
        if key not in {"internal"}
    }


def _load_classes(path_value: str | None) -> list[dict[str, Any]]:
    if not path_value:
        return []
    path = Path(path_value).expanduser().resolve(strict=False)
    if not path.is_file():
        raise HTTPException(400, "classes_file not found")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"Invalid classes file: {exc}") from exc
    if not isinstance(value, list):
        raise HTTPException(400, "Classes file must contain a JSON list")
    return value


def _dataset_defaults(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "train_ratio": 0.7,
        "val_ratio": 0.2,
        "test_ratio": 0.1,
        "min_box_fraction": 0.3,
        "negative_ratio": 0.1,
        "split_mode": profile.get("default_split_strategy", "scene_split"),
        "split_seed": 42,
        "block_size_tiles": 5,
        "preprocessing_profile_id": profile.get("default_preprocessing_profile", "eo_rgb_percentile"),
    }


def _require_project(project_id: str) -> None:
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")


@router.delete("/projects/{project_id}/scenes/{scene_id}")
def remove_managed_scene(project_id: str, scene_id: str):
    """Remove a logical scene from the project without touching source files."""
    _require_project(project_id)
    scene = load_scene_json(project_id, scene_id, "scene", default={})
    if not scene:
        raise HTTPException(404, "Scene not found")
    source_id = str(scene.get("source_id") or "")
    package_id = str(scene.get("package_id") or "")
    if not source_id or not package_id:
        raise HTTPException(400, "Only scenes imported from a managed source can be removed here")

    sources = load_scene_sources(project_id)
    source = next((item for item in sources.get("sources", []) if item.get("source_id") == source_id), None)
    if source is not None:
        source["ignored_package_ids"] = sorted(
            set(source.get("ignored_package_ids") or []) | {package_id}
        )
        save_scene_sources(project_id, sources)

    delete_scene_data(project_id, scene_id)
    shutil.rmtree(project_dir(project_id) / "derived_scenes" / scene_id, ignore_errors=True)
    project = load_json(project_id, "project", default={})
    project["scene_count"] = len(list_scene_ids(project_id))
    save_json(project_id, "project", project)
    rebuild_scenes_index(project_id)
    return {
        "status": "removed",
        "scene_id": scene_id,
        "source_files_deleted": False,
        "scene_count": project["scene_count"],
    }


def _jp2_available() -> bool:
    try:
        import rasterio

        with rasterio.Env() as env:
            return "JP2OpenJPEG" in env.drivers()
    except Exception:
        return False
