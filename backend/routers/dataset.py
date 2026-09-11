"""Dataset split + generation with SSE progress — multi-scene."""

import json
import os
import shutil
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from db.storage import (
    load_json,
    save_json,
    load_scene_json,
    save_scene_json,
    list_scene_ids,
    project_exists,
    project_dir,
)
from models.dataset_config import DatasetConfig
from models.job import JobCreateRequest, JobType, PriorityClass, ResourceClass
from models.preprocessing import PreprocessingProfile
from models.project import APP_VERSION
from models.tiling_config import TilingConfig, TileInfo
from services.dataset_builder import DatasetBuildCancelled, build_dataset
from services.gsd_tiler import gsd_normalized_scene_tiles
from services.dataset_audit import generate_dataset_audit, read_cached_audit
from services.dataset_content import (
    content_summary,
    list_samples,
    persist_content_index,
    sample_image_path,
)
from services.tile_catalog import (
    DATASET_CATALOG_TILE_COLUMNS,
    DATASET_PREPARATION_LINK_COLUMNS,
    ensure_tile_catalog,
    get_catalog_filter_options,
    get_catalog_links,
    get_catalog_tiles,
    load_catalog_snapshot,
)
from services.dataset_runs import (
    DATASET_RUN_SCHEMA_VERSION,
    DatasetPublicationError,
    create_dataset_run_id,
    cleanup_dataset_run_partial,
    dataset_run_dependents,
    dataset_run_dir,
    delete_dataset_run,
    get_dataset_run_manifest,
    list_dataset_runs,
    prepare_dataset_run_partial,
    publish_dataset_run,
    read_publication,
    read_run_json,
    register_dataset_run,
    resolve_dataset_path,
    set_dataset_run_publication,
    sync_latest_dataset_cache,
    utc_now,
    write_run_json,
)
from services.preprocessing_profiles import (
    ensure_preprocessing_profiles,
    profile_warnings,
    resolve_preprocessing_profile,
    upsert_preprocessing_profile,
)
from services.training_runs import training_run_summary
from services.model_registry import read_registry
from services.export_yolo import generate_data_yaml, write_obb_labels
from services.split_validation import validate_dataset_split
from services.scene_loader import get_scene_info
from services.scene_manifest import write_scene_manifest
from services.scene_identity import identity_is_complete
from services.scene_raster_resolver import SceneRasterResolver
from services.scene_packages.radiometry import (
    MixedRadiometryError,
    ensure_consistent as ensure_consistent_radiometry,
)
from services.scene_packages.working_view import lock_working_view
from services.async_bridge import (
    WorkerCancelled,
    next_generator_in_threadpool,
    run_blocking,
)
from services.jobs.events import stream_job_events
from services.jobs.scheduler import cancel_job, submit_job
from services.jobs.store import JobStoreError, find_active_job

router = APIRouter()

ACTIVE_DATASET_BUILDS: dict[str, threading.Event] = {}

LEGACY_DATASET_CONFIG_DEFAULT = {
    "train_ratio": 0.7,
    "val_ratio": 0.2,
    "test_ratio": 0.1,
    "min_box_fraction": 0.3,
    "negative_ratio": 0.1,
}

class DatasetAuditSaveRequest(BaseModel):
    output_path: str
    format: str = "json"
    run_id: str | None = None


class DatasetStatsSaveRequest(BaseModel):
    output_path: str
    run_id: str | None = None
    section: str = "classes"  # classes | geometry


@router.get("/config")
def get_dataset_config(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    data = load_json(project_id, "dataset_config", default=LEGACY_DATASET_CONFIG_DEFAULT)
    config = DatasetConfig(**data)
    project = load_json(project_id, "project", default={})
    if not config.preprocessing_profile_id:
        selected = resolve_preprocessing_profile(
            project_id, None, project.get("profile") or {}
        )
        config.preprocessing_profile_id = selected.profile_id
    if config.model_dump() != data:
        save_json(project_id, "dataset_config", config.model_dump())
    return config.model_dump()


@router.put("/config")
def update_dataset_config(project_id: str, body: DatasetConfig):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    project = load_json(project_id, "project", default={})
    try:
        selected = resolve_preprocessing_profile(
            project_id, body.preprocessing_profile_id, project.get("profile") or {}
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    body.preprocessing_profile_id = selected.profile_id
    save_json(project_id, "dataset_config", body.model_dump())
    return body.model_dump()


@router.get("/filter-options")
def get_dataset_filter_options(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    return get_catalog_filter_options(project_id)


@router.get("/preprocessing-profiles")
def get_preprocessing_profiles(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    return ensure_preprocessing_profiles(project_id)


@router.put("/preprocessing-profiles/{profile_id}")
def put_preprocessing_profile(
    project_id: str,
    profile_id: str,
    body: PreprocessingProfile,
):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    if body.profile_id != profile_id:
        body = body.model_copy(update={"profile_id": profile_id})
    try:
        return upsert_preprocessing_profile(project_id, body).model_dump()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/generate")
async def generate_dataset(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    try:
        submitted = submit_job(
            project_id,
            JobCreateRequest(
                job_type=JobType.DATASET_BUILD,
                resource_class=ResourceClass.IO_HEAVY,
                priority_class=PriorityClass.USER_BACKGROUND,
                payload={
                    "dataset_config": load_json(
                        project_id,
                        "dataset_config",
                        default=LEGACY_DATASET_CONFIG_DEFAULT,
                    ),
                    "tiling_config": load_json(project_id, "tiling_config", default={}),
                },
                dedupe_key="dataset_build",
            ),
        )
    except JobStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    job_id = submitted["job"]["job_id"]
    return EventSourceResponse(stream_job_events(project_id, job_id, legacy_only=True))


def prepare_dataset_build(
    project_id: str,
    cancel_event: threading.Event,
    job_payload: dict | None = None,
):
    """Prepare immutable build inputs outside FastAPI's event-loop thread."""

    if cancel_event.is_set():
        raise WorkerCancelled("Dataset build cancelled during preparation")

    project_data = load_json(project_id, "project", default={})
    job_payload = job_payload or {}
    tiling_cfg_data = (
        job_payload["tiling_config"]
        if "tiling_config" in job_payload
        else load_json(project_id, "tiling_config")
    )
    tiling_config = TilingConfig(**tiling_cfg_data)

    ds_cfg_data = (
        job_payload["dataset_config"]
        if "dataset_config" in job_payload
        else load_json(
            project_id,
            "dataset_config",
            default=LEGACY_DATASET_CONFIG_DEFAULT,
        )
    )
    ds_config = DatasetConfig(**ds_cfg_data)
    project_profile = project_data.get("profile") or {}
    try:
        preprocessing_profile = resolve_preprocessing_profile(
            project_id,
            ds_config.preprocessing_profile_id,
            project_profile,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    ds_config.preprocessing_profile_id = preprocessing_profile.profile_id
    # A queued job owns an immutable configuration snapshot. Do not write its
    # resolved defaults back over settings changed while it waited for an IO slot.
    if "dataset_config" not in job_payload:
        save_json(project_id, "dataset_config", ds_config.model_dump())
    preprocessing_warnings = profile_warnings(preprocessing_profile, project_profile)

    all_classes = load_json(project_id, "classes", default=[])
    selected_class_ids = {int(value) for value in ds_config.class_ids}
    classes = [
        item for item in all_classes
        if not selected_class_ids or int(item.get("id", -1)) in selected_class_ids
    ]
    if selected_class_ids and not classes:
        raise HTTPException(400, "No project classes match the dataset class filter")

    # Nieniszczące scalenia klas: adnotacje klas wcielanych są remapowane na klasę wiodącą
    # przy składaniu kafli poniżej. Lista klas projektu zostaje pełna (indeks = class_id, tak
    # jak w data.yaml/preflight/worker) — klasa wcielana pozostaje, ale bez własnych instancji
    # (ich liczności trafiają do wiodącej). To jest w pełni nieniszczące i odwracalne wersją.
    class_remap = build_class_remap(ds_config.class_merges)
    class_names = {int(c["id"]): c["name"] for c in classes}

    all_tiles: list[TileInfo] = []
    all_tile_annotations: dict[str, list[list[float]]] = {}
    all_tile_attribute_links: list[dict] = []
    tile_source_map: dict[str, str] = {}
    scene_context: dict[str, dict] = {}
    source_annotations_by_scene: dict[str, list[dict]] = {}
    scene_manifests: dict[str, dict] = {}
    dataset_scene_manifests: dict[str, dict] = {}
    scene_source_map: dict[str, str] = {}
    generated_attribute_links: list[dict] = []
    catalog_link_scene_ids: set[str] = set()

    # Identity participates in the tile-catalog fingerprint and lineage. Upgrade any
    # incomplete legacy identity before asking the catalog service for its snapshot;
    # doing this inside the later per-scene loop would make the first dataset run point
    # at a catalog built from stale identifiers and the second run rebuild it.
    selected_scene_ids = set(ds_config.scene_ids)
    project_scene_ids = tuple(list_scene_ids(project_id))
    run_scene_ids = tuple(
        scene_id
        for scene_id in project_scene_ids
        if not selected_scene_ids or scene_id in selected_scene_ids
    )
    for identity_scene_id in run_scene_ids:
        if cancel_event.is_set():
            raise WorkerCancelled("Dataset build cancelled during preparation")
        identity_scene = load_scene_json(project_id, identity_scene_id, "scene", default={})
        identity_manifest = load_scene_json(
            project_id, identity_scene_id, "scene_manifest", default={}
        )
        try:
            identity_path = SceneRasterResolver.resolve(project_id, identity_scene_id).path
        except (FileNotFoundError, ValueError):
            identity_path = None
        identity_geo_ready = bool((identity_manifest.get("geospatial") or {}).get("transform")) or (
            (identity_manifest.get("geometry") or {}).get("model") == "gcp_tps"
        )
        if identity_path and (
            not identity_manifest
            or not identity_geo_ready
            or not identity_is_complete(identity_manifest)
        ):
            if not identity_scene.get("scene_info"):
                identity_scene["scene_info"] = get_scene_info(identity_path).model_dump()
                save_scene_json(project_id, identity_scene_id, "scene", identity_scene)
            write_scene_manifest(
                project_id,
                identity_scene_id,
                project_data,
                identity_scene,
                identity_path,
            )

    catalog_manifest = ensure_tile_catalog(project_id)
    snapshot_enabled = os.getenv("GEOTILE_CATALOG_SNAPSHOT", "1").strip() != "0"
    catalog_snapshot = None
    catalog_links: list[dict] = []
    if snapshot_enabled:
        catalog_snapshot = load_catalog_snapshot(
            project_id,
            scene_ids=run_scene_ids if selected_scene_ids else None,
            tile_columns=DATASET_CATALOG_TILE_COLUMNS,
            link_columns=DATASET_PREPARATION_LINK_COLUMNS,
            retain_link_table=True,
        )
    else:
        catalog_links = get_catalog_links(project_id)
    gsd_skipped_scenes: list[str] = []
    metadata_filtered_scenes: list[dict] = []

    for sid in run_scene_ids:
        if cancel_event.is_set():
            raise WorkerCancelled("Dataset build cancelled during preparation")
        tiles_data = (
            list(catalog_snapshot.tiles_for_scene(sid))
            if catalog_snapshot is not None
            else get_catalog_tiles(project_id, sid)
        )
        if not tiles_data:
            continue

        scene_data = load_scene_json(project_id, sid, "scene", default={})
        try:
            source_path = SceneRasterResolver.resolve(project_id, sid).path
        except (FileNotFoundError, ValueError):
            source_path = None
        if source_path:
            scene_source_map[sid] = str(source_path)
        scene_manifest = load_scene_json(project_id, sid, "scene_manifest", default={})
        # NITF sensor scenes carry geo in the `geometry` (TPS) block, not an affine
        # transform — treat them as geo-ready so we don't rewrite the manifest each build.
        manifest_geo_ready = bool((scene_manifest.get("geospatial") or {}).get("transform")) or (
            (scene_manifest.get("geometry") or {}).get("model") == "gcp_tps"
        )
        if source_path and (
            not scene_manifest
            or not manifest_geo_ready
            or not identity_is_complete(scene_manifest)
        ):
            if not scene_data.get("scene_info"):
                scene_data["scene_info"] = get_scene_info(source_path).model_dump()
                save_scene_json(project_id, sid, "scene", scene_data)
            scene_manifest = write_scene_manifest(
                project_id, sid, project_data, scene_data, source_path
            )
        scene_manifests[sid] = scene_manifest
        meta_ok, meta_reason = scene_matches_metadata_filters(
            scene_data, scene_manifest, ds_config
        )
        if not meta_ok:
            metadata_filtered_scenes.append(
                {"scene_id": sid, "filename": scene_data.get("filename", sid), "reason": meta_reason}
            )
            scene_manifests.pop(sid, None)
            scene_source_map.pop(sid, None)
            continue
        scene_modality = scene_manifest.get("modality")
        if scene_modality and scene_modality != preprocessing_profile.modality:
            preprocessing_warnings.append(
                f"Scene {scene_data.get('filename', sid)} modality {scene_modality} "
                f"does not match profile modality {preprocessing_profile.modality}."
            )
        scene_context[sid] = {
            "filename": scene_data.get("filename", sid),
            "source_scene_uid": scene_manifest.get("source_scene_uid"),
            "source_scene_candidate_uid": scene_manifest.get("source_scene_candidate_uid"),
            "source_scene_fingerprint": (scene_manifest.get("source_identity") or {}).get("source_scene_fingerprint"),
            "source_file_sha256": scene_manifest.get("source_file_sha256"),
            "source_file_content_signature": scene_manifest.get("source_file_content_signature"),
            "source_identity_method": scene_manifest.get("source_identity_method"),
            "source_identity_strength": scene_manifest.get("source_identity_strength"),
            "status": scene_data.get("status", ""),
            "manifest_schema_version": scene_manifest.get("schema_version"),
            "provider": scene_manifest.get("provider"),
            "sensor": scene_manifest.get("sensor"),
            "modality": scene_manifest.get("modality"),
            "georeferencing": scene_manifest.get("georeferencing"),
            "gsd_m": scene_manifest.get("gsd_m"),
            "acquisition_datetime_utc": scene_manifest.get("acquisition_datetime_utc"),
            "incidence_angle_deg": (
                (scene_manifest.get("sar") or {}).get("incidence_angle_deg")
                or (scene_manifest.get("sar") or {}).get("incidence_angle")
                or (scene_manifest.get("sar") or {}).get("incidence_center_deg")
                or (scene_manifest.get("sar") or {}).get("look_angle_deg")
            ),
        }

        anns_data = [
            item
            for item in load_scene_json(project_id, sid, "annotations", default=[])
            if annotation_matches_dataset_filters(item, ds_config)
        ]
        if class_remap:
            # Remapuj u źródła — propagacja GSD i statystyki źródłowe użyją
            # wtedy klasy wiodącej. Ścieżka katalogowa jest remapowana przy składaniu.
            for item in anns_data:
                item["class_id"] = class_remap.get(int(item.get("class_id", -1)), item.get("class_id"))
        source_annotations_by_scene[sid] = anns_data

        # GSD-normalized mode: re-tile from source annotations at a common ground
        # resolution (opt-in). Scenes without a known GSD are skipped and reported.
        gsd_result = None
        if ds_config.target_gsd_m:
            scene_gsd = scene_data.get("gsd_m") or scene_manifest.get("gsd_m")
            image_meta = scene_manifest.get("image") or {}
            g_width = image_meta.get("width") or (scene_data.get("scene_info") or {}).get("width")
            g_height = image_meta.get("height") or (scene_data.get("scene_info") or {}).get("height")
            if scene_gsd and g_width and g_height:
                gsd_tiles, gsd_anns, gsd_links = gsd_normalized_scene_tiles(
                    sid,
                    Path(scene_data.get("filename", sid)).stem,
                    int(g_width),
                    int(g_height),
                    float(scene_gsd),
                    float(ds_config.target_gsd_m),
                    tiling_config,
                    anns_data,
                    ds_config.min_box_fraction,
                )
                gsd_attributes: defaultdict = defaultdict(list)
                for link in gsd_links:
                    gsd_attributes[str(link.get("tile_filename") or "")].append(link)
                gsd_result = (gsd_tiles, gsd_anns, gsd_attributes)
            else:
                gsd_skipped_scenes.append(scene_data.get("filename", sid))

        if gsd_result is not None:
            tiles, scene_tile_anns, scene_tile_attributes = gsd_result
            dataset_manifest = scene_manifest
            uses_catalog_links = False
        else:
            tiles = [TileInfo(**t) for t in tiles_data]
            scene_tile_anns = defaultdict(list)
            scene_tile_attributes = defaultdict(list)
            dataset_manifest = scene_manifest
            uses_catalog_links = True
            catalog_link_scene_ids.add(sid)
            scene_catalog_links = (
                catalog_snapshot.links_for_scene(sid)
                if catalog_snapshot is not None
                else catalog_links
            )
            for link in scene_catalog_links:
                if catalog_snapshot is None and str(link.get("scene_id")) != sid:
                    continue
                if not annotation_matches_dataset_filters(link, ds_config):
                    continue
                filename = str(link.get("tile_filename") or "")
                scene_tile_attributes[filename].append(link)
                if link.get("exportable_yolo"):
                    bbox = link.get("bbox_yolo_norm") or []
                    if len(bbox) == 4:
                        scene_tile_anns[filename].append([link.get("class_id"), *bbox])
        dataset_scene_manifests[sid] = dataset_manifest

        for tile in tiles:
            prefixed = f"{sid}__{tile.filename}"
            tile_anns = scene_tile_anns.get(tile.filename, [])
            if class_remap:
                # Ścieżka katalogowa niesie class_id z linków (nie z anns_data) — remap tutaj.
                # Idempotentne dla ścieżek już zremapowanych u źródła.
                tile_anns = [
                    [class_remap.get(int(ann[0]), ann[0]), *ann[1:]] for ann in tile_anns
                ]
            all_tile_annotations[prefixed] = tile_anns
            for link in scene_tile_attributes.get(tile.filename, []):
                enriched_link = dict(link)
                enriched_link["dataset_tile_filename"] = prefixed
                all_tile_attribute_links.append(enriched_link)
                if not uses_catalog_links:
                    generated_attribute_links.append(enriched_link)
            all_tiles.append(
                TileInfo(
                    tile_id=tile.tile_id,
                    scene_id=sid,
                    filename=prefixed,
                    col=tile.col,
                    row=tile.row,
                    x0=tile.x0,
                    y0=tile.y0,
                    x1=tile.x1,
                    y1=tile.y1,
                    window_px=tile.window_px,  # preserve GSD source window
                    review_status=tile.review_status,
                    exclude_from_dataset=tile.exclude_from_dataset,
                    reviewed=tile.reviewed,
                    excluded=tile.excluded,
                )
            )

    if not all_tiles:
        if metadata_filtered_scenes:
            raise HTTPException(
                400,
                f"All {len(metadata_filtered_scenes)} scene(s) were excluded by the metadata "
                "filters (GSD / incidence / acquisition date). Widen the ranges and rebuild.",
            )
        raise HTTPException(400, "No tile catalog found. Build the tile catalog first.")

    if ds_config.target_gsd_m and gsd_skipped_scenes:
        shown = ", ".join(gsd_skipped_scenes[:5]) + ("…" if len(gsd_skipped_scenes) > 5 else "")
        preprocessing_warnings.append(
            f"GSD normalization skipped {len(gsd_skipped_scenes)} scene(s) without a known GSD: {shown}"
        )

    if metadata_filtered_scenes:
        shown = ", ".join(item["filename"] for item in metadata_filtered_scenes[:5]) + (
            "…" if len(metadata_filtered_scenes) > 5 else ""
        )
        preprocessing_warnings.append(
            f"Metadata filters excluded {len(metadata_filtered_scenes)} scene(s): {shown}"
        )

    save_json(project_id, "tile_annotations", all_tile_annotations)

    catalog_snapshot_info = {
        "mode": "snapshot" if catalog_snapshot is not None else "legacy",
        "snapshot_id": catalog_snapshot.snapshot_id if catalog_snapshot is not None else None,
        "loaded_at": catalog_snapshot.loaded_at if catalog_snapshot is not None else None,
        "scene_count": len(catalog_snapshot.scene_ids) if catalog_snapshot is not None else len(run_scene_ids),
        "tile_count": catalog_snapshot.tile_count if catalog_snapshot is not None else len(all_tiles),
        "link_count": catalog_snapshot.link_count if catalog_snapshot is not None else len(catalog_links),
        "tile_columns": list(catalog_snapshot.tile_columns) if catalog_snapshot is not None else None,
        "link_columns": list(catalog_snapshot.link_columns) if catalog_snapshot is not None else None,
    }

    def materialize_run_links() -> list[dict]:
        if catalog_snapshot is None:
            return [dict(item) for item in all_tile_attribute_links]
        result = [dict(item) for item in generated_attribute_links]
        for link in catalog_snapshot.materialize_links():
            sid = str(link.get("scene_id") or "")
            if sid not in catalog_link_scene_ids:
                continue
            if not annotation_matches_dataset_filters(link, ds_config):
                continue
            enriched_link = dict(link)
            enriched_link["dataset_tile_filename"] = (
                f"{sid}__{str(link.get('tile_filename') or '')}"
            )
            result.append(enriched_link)
        return result

    # P1.5: semantyka wartosci pikseli musi byc jawna PRZED zbudowaniem datasetu. Zmieszanie
    # scen o roznej kalibracji (np. nieskalibrowana amplituda ICEYE ze skalibrowanym sigma0
    # Capelli) daje dane treningowe, w ktorych ta sama liczba znaczy co innego w roznych
    # scenach. Blokujemy tylko SPRZECZNOSC (skalibrowane obok nieskalibrowanych); `unknown`
    # jest raportowany, ale nie blokuje, bo blokada oparta na niewiedzy zatrzymywalaby takze
    # poprawne zestawy.
    try:
        radiometry_summary = ensure_consistent_radiometry(
            scene_manifests, allow_mixed=ds_config.allow_mixed_radiometry
        )
    except MixedRadiometryError as exc:
        raise HTTPException(400, str(exc)) from exc

    input_payload = {
        "tile_catalog_id": catalog_manifest.get("catalog_id"),
        "tile_catalog_schema_version": catalog_manifest.get("schema_version"),
        "project_id": project_id,
        "project_profile": project_data.get("profile") or {},
        "tiling_config": tiling_config.model_dump(),
        "dataset_config": ds_config.model_dump(),
        "classes": classes,
        "scene_manifests": scene_manifests,
        "source_annotations": source_annotations_by_scene,
        "tiles": [tile.model_dump() for tile in all_tiles],
        "preprocessing_profile": preprocessing_profile.model_dump(),
    }
    run_id, input_hash = create_dataset_run_id(input_payload)
    run_dir = dataset_run_dir(project_id, run_id)
    work_run_dir = prepare_dataset_run_partial(project_id, run_id)
    created_at = utc_now()
    dataset_cache_dir = project_dir(project_id) / "dataset"

    generator_holder: dict[str, object] = {}
    pipeline_metrics: dict = {}

    async def _generate_events():
        stats = None
        gen = build_dataset(
            all_tiles,
            all_tile_annotations,
            tile_source_map,
            work_run_dir,
            ds_config,
            class_names,
            scene_context=scene_context,
            source_annotations_by_scene=source_annotations_by_scene,
            scene_source_map=scene_source_map,
            preprocessing_profile=preprocessing_profile.model_dump(),
            tile_size=tiling_config.tile_size,
            tile_annotation_links=all_tile_attribute_links,
            scene_manifests=dataset_scene_manifests,
            should_cancel=cancel_event.is_set,
            pipeline_metrics=pipeline_metrics,
        )
        generator_holder["generator"] = gen
        try:
            while True:
                step = await next_generator_in_threadpool(
                    gen,
                    max_progress_hz=4.0,
                    should_cancel=cancel_event.is_set,
                )
                if not step.has_value:
                    stats = step.value
                    break
                progress = step.value
                progress["run_id"] = run_id
                yield {"event": "progress", "data": json.dumps(progress)}
        except (DatasetBuildCancelled, WorkerCancelled):
            await run_blocking(cleanup_dataset_run_partial, project_id, run_id)
            yield {
                "event": "cancelled",
                "data": json.dumps({"status": "cancelled", "run_id": run_id}),
            }
            return
        except Exception as exc:
            await run_blocking(cleanup_dataset_run_partial, project_id, run_id)
            published_manifest = await run_blocking(
                read_run_json,
                run_dir,
                "dataset_run_manifest",
                {},
            )
            if published_manifest.get("status") == "complete":
                # Publikacja katalogu jest punktem commit. Awaria wtórnego cache/indexu
                # nie może przepisać poprawnego runu na `failed`.
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {
                            "run_id": run_id,
                            "error": str(exc),
                            "run_published": True,
                        }
                    ),
                }
                return
            failed_manifest = {
                "schema_name": "geotile_dataset_run_manifest",
                "schema_version": DATASET_RUN_SCHEMA_VERSION,
                "app_version": APP_VERSION,
                "run_id": run_id,
                "project_id": project_id,
                "created_at": created_at,
                "completed_at": utc_now(),
                "status": "failed",
                "storage_mode": "copy",
                "source_type": project_data.get("source_type", "local_scenes"),
                "input_hash": input_hash,
                "tile_catalog_id": catalog_manifest.get("catalog_id"),
                "tile_catalog": catalog_run_snapshot(catalog_manifest),
                "catalog_snapshot": catalog_snapshot_info,
                "error": str(exc),
                "project_profile": project_data.get("profile") or {},
                "tiling_config": tiling_config.model_dump(),
                "dataset_config": ds_config.model_dump(),
                "preprocessing_profile": preprocessing_profile.model_dump(),
                "preprocessing_warnings": preprocessing_warnings,
                "classes": classes,
                "statistics": {},
            }
            await run_blocking(cleanup_dataset_run_partial, project_id, run_id)
            await run_blocking(write_run_json, run_dir, "dataset_run_manifest", failed_manifest)
            await run_blocking(register_dataset_run, project_id, failed_manifest)
            yield {"event": "error", "data": json.dumps({"run_id": run_id, "error": str(exc)})}
            return

        if stats:
            stats.run_id = run_id
            stats.run_dir = str(run_dir)
            stats.storage_mode = "copy"
            stats.is_latest = True
            stats.preprocessing_profile_id = preprocessing_profile.profile_id
            stats.preprocessing_profile_hash = preprocessing_profile.profile_hash
            stats.dataset_dir = str(dataset_cache_dir)
            validation_report = await run_blocking(
                validate_dataset_split,
                work_run_dir,
                ds_config,
                project_profile,
                dataset_scene_manifests,
                all_tile_attribute_links,
            )
            stats.validation_report = validation_report
            stats_data = stats.model_dump()
            run_attribute_links = await run_blocking(materialize_run_links)
            link_snapshot = {
                "schema_name": "geotile_tile_annotation_links",
                "schema_version": 1,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "min_box_fraction": ds_config.min_box_fraction,
                "annotations": run_attribute_links,
            }
            selection_manifest = await run_blocking(
                build_dataset_selection_manifest,
                work_run_dir,
                ds_config,
                catalog_manifest,
                all_tiles,
                all_tile_annotations,
            )
            split_manifest = await run_blocking(
                build_split_manifest,
                work_run_dir,
                ds_config,
                validation_report,
                all_tiles,
            )
            completed_at = utc_now()
            run_manifest = {
                "schema_name": "geotile_dataset_run_manifest",
                "schema_version": DATASET_RUN_SCHEMA_VERSION,
                "app_version": APP_VERSION,
                "run_id": run_id,
                "project_id": project_id,
                "project_name": project_data.get("name"),
                "created_at": created_at,
                "completed_at": completed_at,
                "status": "complete",
                "storage_mode": "copy",
                "source_type": project_data.get("source_type", "local_scenes"),
                "input_hash": input_hash,
                "tile_catalog_id": catalog_manifest.get("catalog_id"),
                "tile_catalog": catalog_run_snapshot(catalog_manifest),
                "catalog_snapshot": catalog_snapshot_info,
                "selection": selection_manifest["summary"],
                "project_profile": project_data.get("profile") or {},
                "tiling_config": tiling_config.model_dump(),
                "dataset_config": ds_config.model_dump(),
                "preprocessing_profile": preprocessing_profile.model_dump(),
                "preprocessing_warnings": preprocessing_warnings,
                # Odpowiada wprost na pytanie bramki P1.5: czy dataset uzywa zrodla,
                # czy wariantu derived — i czym sa wartosci pikseli kazdej sceny.
                "radiometry": radiometry_summary,
                "classes": classes,
                "scenes": [
                    {"scene_id": scene_id, **context}
                    for scene_id, context in sorted(scene_context.items())
                ],
                "statistics": stats_data,
                "performance": pipeline_metrics,
                "artifacts": {
                    "dataset_stats": "dataset_stats.json",
                    "split_manifest": "split_manifest.json",
                    "dataset_selection": "dataset_selection.json",
                    "tile_annotations": "tile_annotations.json",
                    "tile_annotation_links": "tile_annotation_links.json",
                    "tiles": "tiles.json",
                    "source_annotations": "source_annotations.json",
                    "scene_manifests": "scene_manifests.json",
                    "dataset_scene_manifests": "dataset_scene_manifests.json",
                    "preprocessing_profile": "preprocessing_profile.json",
                    "validation_report": "validation_report.json",
                    "pipeline_performance": "pipeline_performance.json",
                    "dataset_audit": "metadata/dataset_audit.json",
                    "dataset_audit_csv": "metadata/dataset_audit.csv",
                },
            }
            await run_blocking(write_run_json, work_run_dir, "dataset_stats", stats_data)
            await run_blocking(write_run_json, work_run_dir, "split_manifest", split_manifest)
            await run_blocking(write_run_json, work_run_dir, "dataset_selection", selection_manifest)
            await run_blocking(write_run_json, work_run_dir, "tile_annotations", all_tile_annotations)
            await run_blocking(write_run_json, work_run_dir, "tile_annotation_links", link_snapshot)
            await run_blocking(
                write_run_json,
                work_run_dir,
                "tiles",
                [tile.model_dump() for tile in all_tiles],
            )
            await run_blocking(
                write_run_json,
                work_run_dir,
                "source_annotations",
                source_annotations_by_scene,
            )
            await run_blocking(write_run_json, work_run_dir, "scene_manifests", scene_manifests)
            await run_blocking(
                write_run_json,
                work_run_dir,
                "dataset_scene_manifests",
                dataset_scene_manifests,
            )
            await run_blocking(
                write_run_json,
                work_run_dir,
                "preprocessing_profile",
                preprocessing_profile.model_dump(),
            )
            await run_blocking(write_run_json, work_run_dir, "validation_report", validation_report)
            await run_blocking(write_run_json, work_run_dir, "pipeline_performance", pipeline_metrics)
            await run_blocking(write_run_json, work_run_dir, "dataset_run_manifest", run_manifest)
            # Ultralytics czyta data.yaml z katalogu runu (preflight też go wymaga). Zestaw
            # etykiet i plików yaml dyktuje ZADEKLAROWANA geometria projektu (annotation_mode),
            # żeby run był od razu trenowalny pod właściwe zadanie — bez osobnego kroku eksportu:
            #  - bbox         → labels/ (AABB) + data.yaml,
            #  - rotated_bbox → dodatkowo labels_obb/ (OBB) + data_obb.yaml.
            # AABB labels/ piszemy zawsze (fallback + baza dla konwersji COCO/VOC).
            annotation_mode = (project_profile or {}).get("annotation_mode", "bbox")

            def write_training_inputs() -> None:
                run_yaml_path = run_dir.resolve().as_posix()
                generate_data_yaml(work_run_dir, class_names, path=run_yaml_path)
                if annotation_mode == "rotated_bbox":
                    write_obb_labels(work_run_dir, run_attribute_links, tiling_config.tile_size)
                    generate_data_yaml(
                        work_run_dir,
                        class_names,
                        filename="data_obb.yaml",
                        path=run_yaml_path,
                    )

            try:
                await run_blocking(write_training_inputs)
            except Exception as exc:  # noqa: BLE001 — brak data.yaml nie może wywrócić buildu
                preprocessing_warnings.append(f"Could not write data.yaml: {exc}")
            audit_report = await run_blocking(
                generate_dataset_audit,
                project_id,
                work_run_dir,
                persist=True,
            )
            run_manifest["audit_summary"] = audit_report.get("summary") or {}
            await run_blocking(write_run_json, work_run_dir, "dataset_run_manifest", run_manifest)

            # Jedyny moment, w którym kompletny run staje się widoczny dla listy/API.
            await run_blocking(publish_dataset_run, project_id, run_id)

            # Prekomputuj indeks Content (best-effort) dopiero po publikacji, ponieważ
            # resolver celowo nie widzi katalogów `.partial`.
            await run_blocking(persist_content_index, project_id, run_id)
            await run_blocking(save_json, project_id, "tile_annotation_links", link_snapshot)
            await run_blocking(sync_latest_dataset_cache, project_id, run_dir)
            await run_blocking(save_json, project_id, "dataset_stats", stats_data)
            await run_blocking(register_dataset_run, project_id, run_manifest)

            def lock_working_views() -> None:
                for scene_id in scene_context:
                    lock_working_view(project_id, scene_id, "first_dataset_run")

            await run_blocking(lock_working_views)
            complete_data = {**stats_data, "performance": pipeline_metrics}
            yield {"event": "complete", "data": json.dumps(complete_data)}
        else:
            yield {"event": "complete", "data": json.dumps({"status": "done", "run_id": run_id})}

    async def generate():
        try:
            async for event in _generate_events():
                yield event
        except Exception as exc:
            await run_blocking(cleanup_dataset_run_partial, project_id, run_id)
            published_manifest = await run_blocking(
                read_run_json,
                run_dir,
                "dataset_run_manifest",
                {},
            )
            if published_manifest.get("status") == "complete":
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {"run_id": run_id, "error": str(exc), "run_published": True}
                    ),
                }
                return
            failed_manifest = {
                "schema_name": "geotile_dataset_run_manifest",
                "schema_version": DATASET_RUN_SCHEMA_VERSION,
                "app_version": APP_VERSION,
                "run_id": run_id,
                "project_id": project_id,
                "created_at": created_at,
                "completed_at": utc_now(),
                "status": "failed",
                "storage_mode": "copy",
                "source_type": project_data.get("source_type", "local_scenes"),
                "input_hash": input_hash,
                "tile_catalog_id": catalog_manifest.get("catalog_id"),
                "tile_catalog": catalog_run_snapshot(catalog_manifest),
                "catalog_snapshot": catalog_snapshot_info,
                "error": str(exc),
                "project_profile": project_data.get("profile") or {},
                "tiling_config": tiling_config.model_dump(),
                "dataset_config": ds_config.model_dump(),
                "preprocessing_profile": preprocessing_profile.model_dump(),
                "preprocessing_warnings": preprocessing_warnings,
                "classes": classes,
                "statistics": {},
            }
            await run_blocking(write_run_json, run_dir, "dataset_run_manifest", failed_manifest)
            await run_blocking(register_dataset_run, project_id, failed_manifest)
            yield {
                "event": "error",
                "data": json.dumps({"run_id": run_id, "error": str(exc)}),
            }
        finally:
            cancel_event.set()
            generator = generator_holder.pop("generator", None)
            if generator is not None:
                try:
                    await run_blocking(generator.close)
                except (RuntimeError, ValueError):
                    pass
            # Normalny sukces już przemianował katalog. Każda inna ścieżka (anulowanie
            # Job Managera, rozłączenie SSE, wyjątek) usuwa wyłącznie staging tego runu.
            await run_blocking(cleanup_dataset_run_partial, project_id, run_id)
            ACTIVE_DATASET_BUILDS.pop(project_id, None)

    return EventSourceResponse(generate())


@router.post("/generate/cancel")
async def cancel_dataset_build(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    active = find_active_job(
        project_id,
        job_type=JobType.DATASET_BUILD.value,
        dedupe_key="dataset_build",
    )
    if active is None:
        return {"status": "not_running"}
    state = cancel_job(project_id, active["job"]["job_id"])
    return {"status": state.get("status"), "job_id": active["job"]["job_id"]}


def build_split_manifest(
    run_dir,
    config: DatasetConfig,
    validation_report: dict,
    tiles: list[TileInfo] | None = None,
) -> dict:
    splits = {}
    for split in ("train", "val", "test"):
        images_dir = run_dir / split / "images"
        splits[split] = sorted(
            path.name for path in images_dir.iterdir() if path.is_file()
        ) if images_dir.exists() else []
    assignments = {
        filename: split
        for split, filenames in splits.items()
        for filename in filenames
    }
    tiles_by_name = {tile.filename: tile for tile in (tiles or [])}
    return {
        "schema_name": "geotile_split_manifest",
        "schema_version": 1,
        "generated_at": utc_now(),
        "split_mode": config.split_mode,
        "split_seed": config.split_seed,
        "block_size_tiles": config.block_size_tiles,
        "ratios": {
            "train": config.train_ratio,
            "val": config.val_ratio,
            "test": config.test_ratio,
        },
        "splits": splits,
        "assignments": assignments,
        "tile_id_assignments": {
            str(tiles_by_name[filename].tile_id): split
            for filename, split in assignments.items()
            if filename in tiles_by_name and tiles_by_name[filename].tile_id
        },
        "validation_status": validation_report.get("status", "unknown"),
    }


def build_dataset_selection_manifest(
    run_dir: Path,
    config: DatasetConfig,
    catalog_manifest: dict,
    tiles: list[TileInfo],
    tile_annotations: dict[str, list[list[float]]] | None = None,
) -> dict:
    assignments: dict[str, str] = {}
    for split in ("train", "val", "test"):
        images_dir = run_dir / split / "images"
        if not images_dir.is_dir():
            continue
        for path in images_dir.iterdir():
            if path.is_file():
                assignments[path.name] = split
    tiles_by_name = {tile.filename: tile for tile in tiles}
    eligible = [tile for tile in tiles if not _is_excluded_tile(tile)]
    used = [tiles_by_name[name] for name in assignments if name in tiles_by_name]
    reviewed = [tile for tile in eligible if _is_reviewed_tile(tile)]
    tile_annotations = tile_annotations or {}
    reviewed_empty = [
        tile
        for tile in reviewed
        if not tile_annotations.get(tile.filename)
    ]
    filters = dataset_filter_snapshot(config)
    return {
        "schema_name": "geotile_dataset_selection",
        "schema_version": 1,
        "generated_at": utc_now(),
        "catalog_id": catalog_manifest.get("catalog_id"),
        "catalog_schema_version": catalog_manifest.get("schema_version"),
        "filters": filters,
        "summary": {
            "catalog_tile_count": int(catalog_manifest.get("tile_count", 0)),
            "source_filtered_tile_count": max(0, int(catalog_manifest.get("tile_count", 0)) - len(tiles)),
            "candidate_tile_count": len(tiles),
            "eligible_tile_count": len(eligible),
            "reviewed_tile_count": len(reviewed),
            "reviewed_empty_candidate_count": len(reviewed_empty),
            "unreviewed_tile_count": max(0, len(eligible) - len(reviewed)),
            "excluded_tile_count": sum(1 for tile in tiles if _is_excluded_tile(tile)),
            "used_tile_count": len(used),
        },
        "candidate_tile_ids": [tile.tile_id for tile in tiles if tile.tile_id],
        "eligible_tile_ids": [tile.tile_id for tile in eligible if tile.tile_id],
        "used_tiles": [
            {
                "tile_id": tile.tile_id,
                "scene_id": tile.scene_id,
                "filename": tile.filename,
                "split": assignments[tile.filename],
            }
            for tile in used
        ],
    }


def _is_reviewed_tile(tile: TileInfo) -> bool:
    return getattr(tile, "review_status", None) == "reviewed" or bool(getattr(tile, "reviewed", False))


def _is_excluded_tile(tile: TileInfo) -> bool:
    return bool(getattr(tile, "exclude_from_dataset", False) or getattr(tile, "excluded", False))


def build_class_remap(class_merges) -> dict[int, int]:
    """Zbuduj remap member -> into ze scaleń klas, rozwiązując łańcuchy (a→b→c → a→c)."""
    direct: dict[int, int] = {}
    for merge in class_merges or []:
        into = int(getattr(merge, "into", merge.get("into") if isinstance(merge, dict) else None))
        members = getattr(merge, "members", merge.get("members") if isinstance(merge, dict) else []) or []
        for member in members:
            member = int(member)
            if member != into:
                direct[member] = into

    def resolve(cid: int) -> int:
        seen: set[int] = set()
        while cid in direct and cid not in seen:
            seen.add(cid)
            cid = direct[cid]
        return cid

    return {member: resolve(into) for member, into in direct.items()}


def dataset_filter_snapshot(config: DatasetConfig) -> dict:
    return {
        "tile_selection": getattr(config, "tile_selection", "reviewed_sampled"),
        "negative_ratio": config.negative_ratio,
        "scene_ids": sorted(set(config.scene_ids)),
        "class_ids": sorted(set(config.class_ids)),
        "class_merges": [
            {"into": m.into, "members": sorted(set(m.members))} for m in config.class_merges
        ],
        "annotator_emails": sorted({value.strip().lower() for value in config.annotator_emails if value.strip()}),
        "annotation_sources": sorted({value.strip() for value in config.annotation_sources if value.strip()}),
        "gsd_min_m": config.gsd_min_m,
        "gsd_max_m": config.gsd_max_m,
        "incidence_min_deg": config.incidence_min_deg,
        "incidence_max_deg": config.incidence_max_deg,
        "acquired_after": config.acquired_after,
        "acquired_before": config.acquired_before,
    }


def _parse_iso_datetime(value) -> datetime | None:
    """Parse an ISO date/datetime (Z or offset, date-only tolerated) as tz-aware UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    parsed = None
    for candidate in (text, text[:10]):
        try:
            parsed = datetime.fromisoformat(candidate)
            break
        except ValueError:
            continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def scene_matches_metadata_filters(
    scene_data: dict, scene_manifest: dict, config: DatasetConfig
) -> tuple[bool, str | None]:
    """Apply the Build-tab scene metadata range filters (GSD, SAR incidence, acquisition).

    A scene missing the metadata a filter needs is excluded (unverifiable == out);
    incidence is SAR-only, so an active incidence filter drops EO scenes by design.
    """
    if config.gsd_min_m is not None or config.gsd_max_m is not None:
        gsd = scene_data.get("gsd_m")
        if gsd is None:
            gsd = scene_manifest.get("gsd_m")
        if not isinstance(gsd, (int, float)):
            return False, "gsd_unknown"
        if config.gsd_min_m is not None and gsd < config.gsd_min_m:
            return False, "gsd_below_min"
        if config.gsd_max_m is not None and gsd > config.gsd_max_m:
            return False, "gsd_above_max"

    if config.incidence_min_deg is not None or config.incidence_max_deg is not None:
        inc = (scene_manifest.get("sar") or {}).get("incidence_angle_deg")
        if not isinstance(inc, (int, float)):
            return False, "incidence_unknown"
        if config.incidence_min_deg is not None and inc < config.incidence_min_deg:
            return False, "incidence_below_min"
        if config.incidence_max_deg is not None and inc > config.incidence_max_deg:
            return False, "incidence_above_max"

    if config.acquired_after or config.acquired_before:
        acquired = _parse_iso_datetime(
            scene_data.get("acquisition_datetime_utc")
            or scene_manifest.get("acquisition_datetime_utc")
        )
        if acquired is None:
            return False, "acquisition_unknown"
        after = _parse_iso_datetime(config.acquired_after)
        before = _parse_iso_datetime(config.acquired_before)
        if after is not None and acquired < after:
            return False, "acquired_before_range"
        if before is not None and acquired > before:
            return False, "acquired_after_range"

    return True, None


def catalog_run_snapshot(manifest: dict) -> dict:
    return {
        "catalog_id": manifest.get("catalog_id"),
        "schema_version": manifest.get("schema_version"),
        "created_at": manifest.get("created_at"),
        "materialization": manifest.get("materialization"),
        "tile_count": manifest.get("tile_count"),
        "scene_count": manifest.get("scene_count"),
        "annotation_link_count": manifest.get("annotation_link_count"),
        "propagation_version": manifest.get("propagation_version"),
    }


def annotation_matches_dataset_filters(item: dict, config: DatasetConfig) -> bool:
    if config.class_ids:
        try:
            if int(item.get("class_id", -1)) not in set(config.class_ids):
                return False
        except (TypeError, ValueError):
            return False
    if config.annotator_emails:
        allowed_authors = {value.strip().lower() for value in config.annotator_emails if value.strip()}
        author = str(item.get("annotator_email") or "").strip().lower()
        if author not in allowed_authors:
            return False
    if config.annotation_sources:
        allowed_sources = {value.strip() for value in config.annotation_sources if value.strip()}
        if str(item.get("annotation_source") or "manual") not in allowed_sources:
            return False
    return True


@router.get("/runs")
def get_dataset_runs(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    return list_dataset_runs(project_id)


@router.get("/runs/{run_id}")
def get_dataset_run(project_id: str, run_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    try:
        manifest = get_dataset_run_manifest(project_id, run_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not manifest:
        raise HTTPException(404, "Dataset run not found")
    return manifest


def dataset_run_delete_info(project_id: str, run_id: str) -> dict:
    """What deleting this dataset run would affect: dependent training runs, models
    trained from it, its publication state, and whether it is the latest run."""
    training = []
    for tid in dataset_run_dependents(project_id, run_id):
        summary = training_run_summary(project_id, tid)
        training.append({
            "training_run_id": tid,
            "base_model": summary.get("base_model"),
            "attempt": summary.get("attempt"),
            "status": summary.get("status"),
        })
    registry = read_registry(project_id)
    models = [
        {
            "model_id": model.get("model_id"),
            "training_run_id": model.get("training_run_id"),
            "is_current": model.get("model_id") == registry.get("current_model_id"),
        }
        for model in registry.get("models", [])
        if model.get("dataset_run_id") == run_id
    ]
    published = read_publication(project_id, run_id).get("status") == "published"
    index = list_dataset_runs(project_id)
    return {
        "run_id": run_id,
        "training_runs": training,
        "models": models,
        "published": published,
        "is_latest": index.get("latest_run_id") == run_id,
        "blocked": bool(training or models or published),
    }


@router.get("/runs/{run_id}/delete-info")
def get_dataset_run_delete_info(project_id: str, run_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    if not get_dataset_run_manifest(project_id, run_id):
        raise HTTPException(404, "Dataset run not found")
    return dataset_run_delete_info(project_id, run_id)


@router.delete("/runs/{run_id}")
def delete_dataset_run_endpoint(
    project_id: str,
    run_id: str,
    force: bool = Query(False),
):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    if not get_dataset_run_manifest(project_id, run_id):
        raise HTTPException(404, "Dataset run not found")
    info = dataset_run_delete_info(project_id, run_id)
    if info["blocked"] and not force:
        raise HTTPException(status_code=409, detail=info)
    result = delete_dataset_run(project_id, run_id)
    return {**result, "dependents": info}


class DatasetPublicationRequest(BaseModel):
    status: Literal["draft", "published", "deprecated"]
    label: str | None = None
    # Świadome potwierdzenie publikacji mimo błędów audytu/walidacji (decyzja człowieka).
    acknowledge_issues: bool = False


@router.get("/runs/{run_id}/publication")
def get_dataset_run_publication(project_id: str, run_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    if not get_dataset_run_manifest(project_id, run_id):
        raise HTTPException(404, "Dataset run not found")
    return {
        **read_publication(project_id, run_id),
        "dependent_training_runs": dataset_run_dependents(project_id, run_id),
    }


@router.put("/runs/{run_id}/publication")
def put_dataset_run_publication(
    project_id: str,
    run_id: str,
    body: DatasetPublicationRequest,
):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    profile = (load_json(project_id, "project", default={}) or {}).get("profile") or {}
    try:
        return set_dataset_run_publication(
            project_id,
            run_id,
            status=body.status,
            label=body.label,
            actor=profile.get("labeling_author_email"),
            acknowledge_issues=body.acknowledge_issues,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except DatasetPublicationError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/runs/{run_id}/stats")
def get_dataset_run_stats(project_id: str, run_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    try:
        run_dir = resolve_dataset_path(project_id, run_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc
    stats = read_run_json(run_dir, "dataset_stats", default={})
    if not stats:
        raise HTTPException(404, "Dataset run statistics not found")
    stats["is_latest"] = list_dataset_runs(project_id).get("latest_run_id") == run_id
    return stats


@router.get("/runs/{run_id}/content")
def get_dataset_run_content(project_id: str, run_id: str):
    """Splity i klasy opublikowanego runu z licznikami — do filtrów podglądu (DI-F)."""
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    try:
        return content_summary(project_id, run_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/runs/{run_id}/content/samples")
def get_dataset_run_samples(
    project_id: str,
    run_id: str,
    split: str = Query(...),
    class_id: int | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(60, ge=1, le=200),
):
    """Stronicowana lista kafli (opcjonalnie filtr klasy) z boxami i pochodzeniem."""
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    try:
        return list_samples(project_id, run_id, split, class_id, offset, limit)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/runs/{run_id}/content/samples/{split}/{filename}/image")
def get_dataset_run_sample_image(project_id: str, run_id: str, split: str, filename: str):
    """Obraz kafla z opublikowanego runu (gotowy render 8-bit)."""
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    try:
        path = sample_image_path(project_id, run_id, split, filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, media_type="image/png", filename=filename)


@router.get("/audit")
def get_dataset_audit(project_id: str, run_id: str | None = Query(None)):
    dataset_path = resolve_audit_dataset_path(project_id, run_id)
    # Run niezmienny → serwuj gotowy audyt (prekomputowany przy buildzie); licz tylko,
    # gdy go brak. Wymuszenie przeliczenia: POST /audit/refresh.
    cached = read_cached_audit(dataset_path)
    if cached is not None:
        return cached
    return generate_dataset_audit(project_id, dataset_path, persist=True)


@router.post("/audit/refresh")
def refresh_dataset_audit(project_id: str, run_id: str | None = Query(None)):
    dataset_path = resolve_audit_dataset_path(project_id, run_id)
    return generate_dataset_audit(project_id, dataset_path, persist=True)


@router.get("/audit/export")
def export_dataset_audit(
    project_id: str,
    format: str = Query("json"),
    run_id: str | None = Query(None),
):
    dataset_path = resolve_audit_dataset_path(project_id, run_id)
    report = generate_dataset_audit(project_id, dataset_path, persist=True)
    extension = validate_audit_format(format)
    path = dataset_path / "metadata" / f"dataset_audit.{extension}"
    return FileResponse(
        path,
        media_type="application/json" if extension == "json" else "text/csv",
        filename=f"GeoTileLabel_audit_{report.get('run_id') or 'latest'}.{extension}",
    )


@router.post("/audit/save")
def save_dataset_audit(project_id: str, body: DatasetAuditSaveRequest):
    dataset_path = resolve_audit_dataset_path(project_id, body.run_id)
    report = generate_dataset_audit(project_id, dataset_path, persist=True)
    extension = validate_audit_format(body.format)
    output_path = Path(body.output_path).expanduser()
    if not output_path.is_absolute():
        raise HTTPException(400, "output_path must be an absolute path")
    if output_path.suffix.lower() != f".{extension}":
        output_path = output_path.with_suffix(f".{extension}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dataset_path / "metadata" / f"dataset_audit.{extension}", output_path)
    return {
        "status": "ok",
        "run_id": report.get("run_id"),
        "format": extension,
        "output_path": str(output_path),
    }


@router.get("/stats")
def get_dataset_stats(project_id: str, run_id: str | None = Query(None)):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    if run_id:
        try:
            run_dir = resolve_dataset_path(project_id, run_id)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, str(exc)) from exc
        stats = read_run_json(run_dir, "dataset_stats", default={})
        if stats:
            stats["is_latest"] = list_dataset_runs(project_id).get("latest_run_id") == run_id
        return stats
    return load_json(project_id, "dataset_stats", default={})


def _load_dataset_stats(project_id: str, run_id: str | None) -> dict:
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    if run_id:
        try:
            run_dir = resolve_dataset_path(project_id, run_id)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, str(exc)) from exc
        return read_run_json(run_dir, "dataset_stats", default={})
    return load_json(project_id, "dataset_stats", default={})


def _dataset_stats_csv(stats: dict) -> str:
    """CSV raportu klas (Faza 1 niezbalansowania). Nagłówki po angielsku, wartości surowe."""
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "class_id", "name", "dataset_annotations", "share_pct", "rel_to_largest",
        "scenes", "tiles", "source_annotations", "train", "val", "test",
        "train_pct", "val_pct", "test_pct", "missing_in_splits", "status",
    ])
    for c in stats.get("class_stats", []) or []:
        status = "used" if c.get("used") else ("source_only" if c.get("source_only") else "unused")
        writer.writerow([
            c.get("class_id"), c.get("name"), c.get("dataset_annotations"),
            c.get("share_pct"), c.get("rel_to_largest"), c.get("scenes"), c.get("tiles"),
            c.get("source_annotations"), c.get("train"), c.get("val"), c.get("test"),
            c.get("train_pct"), c.get("val_pct"), c.get("test_pct"),
            ";".join(c.get("missing_in_splits") or []), status,
        ])
    return buf.getvalue()


def _geometry_stats_csv(stats: dict) -> str:
    """CSV rozmiarów geometrii per klasa (Faza 2)."""
    import csv
    import io

    geometry = stats.get("geometry_stats") or {}
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "class_id", "name", "count", "w_px_median", "h_px_median", "area_px_median",
        "area_px_p90", "area_frac_median", "aspect_median", "size_small", "size_medium", "size_large",
        "w_m_median", "h_m_median", "area_m2_median",
        "obb_count", "angle_median", "short_side_median", "long_side_median", "near_square_count",
    ])
    for c in geometry.get("per_class", []) or []:
        writer.writerow([
            c.get("class_id"), c.get("name"), c.get("count"),
            c.get("w_px_median"), c.get("h_px_median"), c.get("area_px_median"),
            c.get("area_px_p90"), c.get("area_frac_median"), c.get("aspect_median"),
            c.get("size_small"), c.get("size_medium"), c.get("size_large"),
            c.get("w_m_median"), c.get("h_m_median"), c.get("area_m2_median"),
            c.get("obb_count"), c.get("angle_median"), c.get("short_side_median"),
            c.get("long_side_median"), c.get("near_square_count"),
        ])
    return buf.getvalue()


def _cooccurrence_csv(stats: dict) -> str:
    """CSV macierzy współwystępowania (Faza 3): pierwsza kolumna = klasa, dalej #kafli z parą."""
    import csv
    import io

    co = stats.get("co_occurrence") or {}
    classes = co.get("classes", []) or []
    counts = co.get("counts", []) or []
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["class", *classes])
    for i, name in enumerate(classes):
        row = counts[i] if i < len(counts) else []
        writer.writerow([name, *row])
    return buf.getvalue()


def _acquisition_stats_csv(stats: dict) -> str:
    """CSV metadanych akwizycji (Faza 4): długi format metric/key/overall/train/val/test."""
    import csv
    import io

    acq = stats.get("acquisition_stats") or {}
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["metric", "key", "overall", "train", "val", "test"])
    for metric in ("sensor", "modality", "season"):
        d = acq.get(metric) or {}
        overall = d.get("overall") or {}
        for key in overall:
            writer.writerow([
                metric, key, overall.get(key, 0),
                (d.get("train") or {}).get(key, 0),
                (d.get("val") or {}).get(key, 0),
                (d.get("test") or {}).get(key, 0),
            ])
    for key, value in (acq.get("missing") or {}).items():
        writer.writerow(["missing", key, value, "", "", ""])
    writer.writerow(["total_tiles", "", acq.get("total_tiles", 0), "", "", ""])
    return buf.getvalue()


def _stats_csv_for_section(stats: dict, section: str) -> str:
    if section == "geometry":
        return _geometry_stats_csv(stats)
    if section == "cooccurrence":
        return _cooccurrence_csv(stats)
    if section == "acquisition":
        return _acquisition_stats_csv(stats)
    return _dataset_stats_csv(stats)


@router.get("/stats/export")
def export_dataset_stats(
    project_id: str,
    format: str = Query("csv"),
    run_id: str | None = Query(None),
    section: str = Query("classes"),
):
    if format != "csv":
        raise HTTPException(400, "Only csv is supported")
    stats = _load_dataset_stats(project_id, run_id)
    csv_text = _stats_csv_for_section(stats, section)
    filename = f"GeoTileLabel_{section}_stats_{run_id or 'latest'}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/stats/save")
def save_dataset_stats(project_id: str, body: DatasetStatsSaveRequest):
    stats = _load_dataset_stats(project_id, body.run_id)
    output_path = Path(body.output_path).expanduser()
    if not output_path.is_absolute():
        raise HTTPException(400, "output_path must be an absolute path")
    if output_path.suffix.lower() != ".csv":
        output_path = output_path.with_suffix(".csv")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_stats_csv_for_section(stats, body.section), encoding="utf-8")
    return {"status": "ok", "output_path": str(output_path)}


def resolve_audit_dataset_path(project_id: str, run_id: str | None) -> Path:
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    try:
        dataset_path = resolve_dataset_path(project_id, run_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc
    if not dataset_path.is_dir() or not (dataset_path / "dataset_run_manifest.json").is_file():
        raise HTTPException(404, "Dataset run not found. Generate a dataset first.")
    return dataset_path


def validate_audit_format(value: str) -> str:
    extension = value.lower().strip()
    if extension not in {"json", "csv"}:
        raise HTTPException(400, "Audit format must be json or csv")
    return extension


@router.get("/location")
def get_dataset_location(project_id: str, run_id: str | None = Query(None)):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    try:
        dataset_dir = resolve_dataset_path(project_id, run_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "exists": dataset_dir.exists(),
        "dataset_dir": str(dataset_dir),
        "run_id": run_id,
    }
