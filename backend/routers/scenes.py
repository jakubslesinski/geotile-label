"""Scene listing, thumbnails, and tile serving per scene."""

import asyncio
import hashlib
import io
import math
import os
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from PIL import Image
from pydantic import BaseModel

from db.storage import (
    load_json, load_scene_json, save_json, save_scene_json, list_scene_ids,
    project_dir, project_exists, scene_dir, SCENES_ROOT,
)
from services.scene_loader import SCENE_INFO_VERSION, generate_thumbnail, get_scene_info
from services.scene_manifest import (
    SCENE_MANIFEST_VERSION,
    refresh_scene_identity,
    rebuild_scenes_index,
    scenes_index_is_fresh,
    write_scene_manifest,
)
from services.scene_identity import identity_is_complete, identity_is_exact
from services.json_index.queries import (
    InvalidCursorError,
    get_scenes_index as get_scenes_index_v2,
    get_scenes_page as get_scenes_page_v2,
)
from services.image_preprocessor import apply_display_params
from services.scene_raster_resolver import (
    SceneRasterResolver,
    repair_blacksky_auxiliary_selection,
    resolve_scene_raster,
    sync_scene_source_overviews,
)
from services.scene_packages.working_view import (
    DISPLAY_OVERVIEW_RASTER_KINDS,
    direct_overview_vrt,
    direct_preview_asset,
)
from services.scene_overviews import apply_source_overview_metadata, source_overviews_are_display_ready
from services.scene_raster_session import display_raster_session, invalidate_all as invalidate_raster_sessions
from services.single_flight import tile_single_flight
from services.scene_packages.fullres_cog_builder import published_fullres_cog
from services.scene_display_contract import (
    classify_display_assets,
    compute_native_xyz_zoom,
    compute_zoom_contract,
    derive_display_statuses,
)
from utils.image import ensure_rgb_uint8, render_display_window

router = APIRouter()

# --- Offload odczytu kafli z pętli zdarzeń (DESIGN_DECISIONS.md, tile-serving P2) ---
#
# Endpointy kafli są async, ale odczyt rastra + kodowanie to praca synchroniczna,
# która blokowała pętlę zdarzeń — viewport kilkunastu kafli szedł po kolei
# (P2 baseline: 16 kafli = 1,7 s przy zoom 3). Przenosimy renderowanie do
# dedykowanej, OGRANICZONEJ puli wątków: kafle renderują się równolegle, a limit
# workerów wiąże szczytowy RAM i liczbę wątków (kolejność z planu: piramidy z P1
# najpierw, żeby okna były małe). Pula jest dedykowana, żeby ciężkie kaflowanie nie
# głodziło innych zadań tła (budowa piramid, tożsamość).
_TILE_WORKERS = int(os.environ.get("GEOTILE_TILE_WORKERS", "0") or 0) or min(8, (os.cpu_count() or 4))
_JP2_TILE_WORKERS = max(1, int(os.environ.get("GEOTILE_JP2_TILE_WORKERS", "1") or 1))
_JP2_GDAL_THREADS = max(
    1,
    int(
        os.environ.get("GEOTILE_JP2_TILE_GDAL_THREADS", "0") or 0
    ) or min(8, max(2, (os.cpu_count() or 2) // 2)),
)
_CATALOG_WORKERS = max(
    1,
    int(os.environ.get("GEOTILE_CATALOG_WORKERS", "0") or 0) or min(4, (os.cpu_count() or 4)),
)
_tile_executor = ThreadPoolExecutor(max_workers=_TILE_WORKERS, thread_name_prefix="tile-render")
_jp2_tile_executor = ThreadPoolExecutor(
    max_workers=_JP2_TILE_WORKERS,
    thread_name_prefix="jp2-tile-render",
)
_catalog_executor = ThreadPoolExecutor(
    max_workers=_CATALOG_WORKERS,
    thread_name_prefix="scene-catalog",
)
_maintenance_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tile-maintenance")
_jp2_admission = asyncio.Semaphore(_JP2_TILE_WORKERS)

# Bufor bloków GDAL jako wartość BEZWZGLĘDNA (MB), dobrana do najsłabszej maszyny —
# nie procent RAM. Więcej wątków czytających naraz nie może rozdąć cache bez granicy.
os.environ.setdefault("GDAL_CACHEMAX", os.environ.get("GEOTILE_GDAL_CACHEMAX", "256"))


async def _render_tile_offloaded(fn, *args):
    """Uruchom synchroniczny renderer kafla w dedykowanej puli, poza pętlą zdarzeń."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_tile_executor, fn, *args)


async def _catalog_offloaded(fn, *args):
    """Run short scene/index work independently from expensive raster decodes."""

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_catalog_executor, fn, *args)


async def _acquire_jp2_slot(request: Request | None) -> None:
    """Wait without filling the executor queue; abandon disconnected image requests."""

    while True:
        try:
            await asyncio.wait_for(_jp2_admission.acquire(), timeout=0.25)
            return
        except asyncio.TimeoutError:
            if request is not None and await request.is_disconnected():
                raise HTTPException(499, "Tile request was cancelled")


async def _render_scene_work_offloaded(
    project_id: str,
    scene_id: str,
    fn,
    *args,
    request: Request | None = None,
):
    """Dispatch native JP2 reads through a serial, disconnect-aware lane."""

    ctx = await _catalog_offloaded(_scene_render_context, project_id, scene_id)
    # O pasie decyduje AKTYWNY GRAF ODCZYTU, nie nazwa pliku (R0.2). Test rozszerzenia
    # przestal dzialac, odkad sciezka jest `overview.vrt` opakowujacy JP2, a po publikacji
    # COG bylby blednu w druga strone: zrodlem dalej jest JP2, ale renderer go nie czyta.
    contract = _zoom_contract(project_id, scene_id, ctx)
    if not contract.assets.display_requires_jp2_decode:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_tile_executor, fn, *args)

    await _acquire_jp2_slot(request)
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_jp2_tile_executor, fn, *args)
    finally:
        _jp2_admission.release()


# --- Cache kontekstu sceny (DESIGN_DECISIONS.md, tile-serving P3) ---
#
# P2 pokazal, ze przy szybkich kaflach 85% czasu to redundantna resolucja metadanych
# per kafelek pod GIL: _ensure_scene_info (~8,8 ms), _get_scene_path (~5,1 ms) i
# resolve_display_read (~4,3 ms) — sam odczyt rastra to tylko ~2,8 ms. Cachujemy
# wynik resolucji keyed po mtime scene.json/scene_manifest.json, wiec kolejne kafle
# tego samego viewportu nie parsuja metadanych na nowo.
#
# Zakres zawezony przez pomiar: cache uchwytow rasterio (rasterio.open) POMINIETY —
# open+odczyt to laczne 2,8 ms, a wspoldzielenie uchwytu GDAL miedzy watki puli P2 nie
# jest thread-safe. Zysk maly, ryzyko duze; metadane to wlasciwy cel.
import threading

_SCENE_CTX_MAX = 64
_scene_ctx_lock = threading.Lock()
_scene_ctx_cache: "dict[tuple[str, str], tuple[tuple, dict]]" = {}


def _scene_mtime_key(project_id: str, scene_id: str) -> tuple:
    d = scene_dir(project_id, scene_id)
    key = []
    for name in ("scene.json", "scene_manifest.json"):
        try:
            key.append((d / name).stat().st_mtime_ns)
        except OSError:
            key.append(0)
    return tuple(key)


def _scene_render_context(project_id: str, scene_id: str) -> dict:
    """Zresolwowany kontekst sceny do renderu kafla, cachowany po mtime.

    Uniewaznia sie sam, gdy zmieni sie scene.json albo manifest (relink,
    source_changed, edycja) — bo mtime wchodzi w klucz. Sciezka piramidy NIE jest tu
    trzymana: sprawdza sie ja swiezo taniom `is_file` w wywolujacym, wiec swiezo
    zbudowana piramida (P1, w tle po imporcie) jest widoczna od razu.
    """
    cache_key = (project_id, scene_id)
    mtime_key = _scene_mtime_key(project_id, scene_id)
    with _scene_ctx_lock:
        hit = _scene_ctx_cache.get(cache_key)
        if hit is not None and hit[0] == mtime_key:
            return hit[1]

    scene_data = load_scene_json(project_id, scene_id, "scene")
    if not scene_data:
        raise HTTPException(404, "Scene not found")
    scene_data = _ensure_scene_info(project_id, scene_id, scene_data, strict=True)
    si = scene_data.get("scene_info") or {}
    handle = SceneRasterResolver.resolve(project_id, scene_id)
    manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={}) or {}
    identity = manifest.get("source_identity") or {}
    ctx = {
        "si": si,
        "source_path": handle.path,
        "raster_kind": handle.raster_kind,
        "variant": handle.working_variant_id,
        "scene_mtime_key": mtime_key,
        "overview_fingerprint": scene_data.get("overview_fingerprint"),
        "overview_factors": scene_data.get("overview_factors") or [],
        "source_fingerprint": identity.get("source_scene_fingerprint")
        or identity.get("source_package_fingerprint"),
        # `max_zoom` to POZIOM REFERENCYJNY ukladu wspolrzednych, nie limit zadan.
        # Wzgledem niego liczy sie `pixels_per_tile` i geometria adnotacji, wiec nie wolno
        # go obnizac, gdy piramida czegos nie obsluguje — od tego jest
        # `available_native_zoom` w kontrakcie (R0.1).
        "max_zoom": _compute_max_zoom(si.get("width", 0), si.get("height", 0)),
        "native_xyz_zoom": compute_native_xyz_zoom(si),
    }
    with _scene_ctx_lock:
        _scene_ctx_cache[cache_key] = (mtime_key, ctx)
        if len(_scene_ctx_cache) > _SCENE_CTX_MAX:
            # Wieku najstarszego nie sledzimy — usun dowolny wpis, cache jest maly i
            # odbudowuje sie tanio. Wystarczy ograniczyc wzrost.
            _scene_ctx_cache.pop(next(iter(_scene_ctx_cache)))
    return ctx


def _maybe_request_fullres_derivative(project_id: str, scene_id: str, ctx: dict) -> str | None:
    """Zglos budowe derywatu 1x, jesli scena tego wymaga i maszyna to udzwignie (R1.3).

    Wolane przy otwarciu sceny. Trzy zabezpieczenia sprawiaja, ze nie jest to kosztowne:
    kwalifikacja odsiewa wszystko poza problematycznym generic JP2, preflight pamieci
    i dysku odsiewa maszyny, ktore i tak by nie dokonczyly, a `dedupe_key` sprawia, ze
    kolejne otwarcia tej samej sceny nie kolejkuja drugiego zadania.

    Bledy sa polykane celowo: niemoznosc zgloszenia zadania w tle nie moze uniemozliwic
    otwarcia sceny.
    """
    try:
        from services.scene_packages.fullres_derivative import (
            qualifies_for_fullres_derivative,
        )

        qualification = qualifies_for_fullres_derivative(
            source_path=ctx["source_path"],
            display_path=_display_read_path_for(project_id, scene_id, ctx),
            raster_kind=ctx["raster_kind"],
            scene_info=ctx["si"],
            overview_factors=ctx.get("overview_factors"),
        )
        if not qualification.qualifies:
            return None

        import psutil

        from services.scene_packages.fullres_build_policy import check_host_memory

        memory = psutil.virtual_memory()
        if not check_host_memory(memory.total, memory.available).ok:
            # Swiadomie nie probujemy: §20 mowi, ze bezpieczenstwo pamieci ma
            # pierwszenstwo, a scena zostaje uzyteczna na poziomie 2x.
            return None

        from routers.scene_import import _submit_scene_fullres_job

        submitted = _submit_scene_fullres_job(project_id, scene_id)
        return str((submitted.get("job") or {}).get("job_id") or "") or None
    except Exception:
        return None


def _current_fullres_payload(project_id: str, scene_id: str) -> dict:
    from services.scene_packages.fullres_derivative import build_job_payload

    scene = load_scene_json(project_id, scene_id, "scene", default={}) or {}
    manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={}) or {}
    identity = manifest.get("source_identity") or {}
    working = manifest.get("working_view") or {}
    return build_job_payload(
        scene_id=scene_id,
        source_fingerprint=identity.get("source_scene_fingerprint")
        or identity.get("source_package_fingerprint"),
        variant_id=working.get("variant_id") or scene.get("working_variant_id"),
        source_revision=scene.get("overview_fingerprint"),
    )


def _job_matches_fullres_payload(item: dict, payload: dict) -> bool:
    spec_payload = (item.get("job") or {}).get("payload") or {}
    return all(
        spec_payload.get(key) == payload.get(key)
        for key in (
            "scene_id",
            "source_fingerprint",
            "variant_id",
            "cog_profile_version",
            "expected_source_revision",
        )
    )


def _fullres_error_code(error: str | None) -> str | None:
    lowered = str(error or "").lower()
    if "cancel" in lowered or "anulowan" in lowered:
        return "cancelled"
    for code in (
        "memory_limit_exceeded",
        "validation_failed",
        "insufficient_memory",
        "insufficient_disk",
        "source_fingerprint_changed",
        "working_variant_changed",
        "source_revision_changed",
    ):
        if code in lowered:
            return code
    return "build_failed" if lowered else None


def _fullres_auto_start_enabled(project_id: str) -> bool:
    value = os.environ.get("GEOTILE_AUTO_FULLRES_COG_V2", "1").strip().lower()
    if value in {"0", "false", "no", "off"}:
        return False
    from routers.scene_import import DEFAULT_AUTO_FULLRES_COG_ENABLED

    config = load_json(project_id, "scene_import_config", default={}) or {}
    # Domyslna wartosc MUSI byc ta sama co w `scene_import`: gdy tu bylo `True`, a tam
    # `False`, przelacznik pokazywalby OFF, a backend i tak startowalby budowe.
    project_value = config.get("auto_fullres_cog_enabled", DEFAULT_AUTO_FULLRES_COG_ENABLED)
    if isinstance(project_value, str):
        return project_value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(project_value)


def _resume_candidate_publication(
    project_id: str,
    scene_id: str,
    ctx: dict,
    payload: dict,
) -> bool:
    """Finish an interrupted, already validated activation without decoding JP2 again."""
    from services.scene_packages.fullres_cog_builder import (
        FULLRES_CANDIDATE_NAME,
        FULLRES_COG_NAME,
        cleanup_incomplete_fullres,
        fullres_dir,
        fullres_state_path,
    )
    from services.scene_packages.fullres_publication import (
        STATE_ACTIVE,
        STATE_CANDIDATE_READY,
        STEP_ARTIFACT,
        STEP_MANIFEST,
        STEP_SCENE,
        advance,
        mark_step,
        read_publication_record,
        write_publication_record,
    )

    root = project_dir(project_id)
    state_path = fullres_state_path(root, scene_id, ctx["variant"])
    record = read_publication_record(state_path)
    if record is None or record.state != STATE_CANDIDATE_READY:
        return False
    if any(
        record.payload.get(key) != payload.get(key)
        for key in ("source_fingerprint", "variant_id", "cog_profile_version")
    ):
        return False
    directory = fullres_dir(root, scene_id, ctx["variant"])
    candidate = directory / FULLRES_CANDIDATE_NAME
    active = directory / FULLRES_COG_NAME
    try:
        invalidate_raster_sessions()
        if STEP_ARTIFACT not in record.steps_done:
            if candidate.is_file():
                active.unlink(missing_ok=True)
                os.replace(candidate, active)
            elif not active.is_file():
                return False
            mark_step(record, STEP_ARTIFACT)
            write_publication_record(state_path, record)
        if not active.is_file():
            return False
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={}) or {}
        if STEP_MANIFEST not in record.steps_done:
            relative = str(active.relative_to(root)).replace("\\", "/")
            manifest.setdefault("working_view", {})["fullres_derivative"] = {
                "status": "active",
                "raster_ref": {"storage": "project", "relative_path": relative},
                "source_fingerprint": payload.get("source_fingerprint"),
                "variant_id": payload.get("variant_id"),
                "cog_profile_version": payload.get("cog_profile_version"),
                "bytes": active.stat().st_size,
            }
            save_scene_json(project_id, scene_id, "scene_manifest", manifest)
            mark_step(record, STEP_MANIFEST)
            write_publication_record(state_path, record)
        if STEP_SCENE not in record.steps_done:
            scene = load_scene_json(project_id, scene_id, "scene", default={}) or {}
            scene.update(
                {
                    "fullres_derivative_status": "ready",
                    "fullres_cog_profile_version": payload.get("cog_profile_version"),
                    "fullres_source_fingerprint": payload.get("source_fingerprint"),
                }
            )
            save_scene_json(project_id, scene_id, "scene", scene)
            mark_step(record, STEP_SCENE)
            write_publication_record(state_path, record)
        advance(record, STATE_ACTIVE)
        write_publication_record(state_path, record)
        from services.scene_raster_resolver import invalidate_scene_render_caches

        invalidate_scene_render_caches(project_id, scene_id)
        invalidate_raster_sessions()
        cleanup_incomplete_fullres(root, scene_id, ctx["variant"])
        return True
    except OSError:
        return False


def _fullres_derivative_view(project_id: str, scene_id: str, ctx: dict) -> dict:
    """Read-only public state for the explicit full-resolution workflow."""
    from models.job import ACTIVE_JOB_STATUSES, JobType
    from services.jobs.store import list_jobs
    from services.scene_packages.fullres_cog_builder import (
        cleanup_incomplete_fullres,
        cleanup_legacy_fullres,
        fullres_state_path,
        published_fullres_cog,
    )
    from services.scene_packages.fullres_derivative import qualifies_for_fullres_derivative
    from services.scene_packages.fullres_publication import (
        STATE_ACTIVE,
        STATE_BUILDING,
        STATE_CANDIDATE_READY,
        STATE_FAILED,
        STATE_VALIDATING,
        mark_failed,
        read_publication_record,
        write_publication_record,
    )

    payload = _current_fullres_payload(project_id, scene_id)
    root = project_dir(project_id)
    cleanup_legacy_fullres(root, scene_id, ctx["variant"])
    _resume_candidate_publication(project_id, scene_id, ctx, payload)
    active_cog = published_fullres_cog(
        root,
        scene_id,
        ctx["variant"],
        source_fingerprint=payload.get("source_fingerprint"),
    )
    qualification = qualifies_for_fullres_derivative(
        source_path=ctx["source_path"],
        display_path=_display_read_path_for(project_id, scene_id, ctx),
        raster_kind=ctx["raster_kind"],
        scene_info=ctx["si"],
        overview_factors=ctx.get("overview_factors"),
    )

    jobs = [
        item
        for item in list_jobs(project_id)
        if (item.get("job") or {}).get("job_type") == JobType.SCENE_FULLRES_DERIVATIVE.value
        and _job_matches_fullres_payload(item, payload)
    ]
    current = jobs[0] if jobs else None
    current_state = (current or {}).get("state") or {}
    job_status = str(current_state.get("status") or "")
    job_phase = str(current_state.get("phase") or "")
    publication = read_publication_record(
        fullres_state_path(root, scene_id, ctx["variant"])
    )
    if publication and publication.state == STATE_CANDIDATE_READY and any(
        publication.payload.get(key) != payload.get(key)
        for key in ("source_fingerprint", "variant_id", "cog_profile_version")
    ):
        cleanup_incomplete_fullres(root, scene_id, ctx["variant"])
        mark_failed(
            publication,
            error="Candidate no longer matches the current scene source or profile",
            error_code="source_fingerprint_changed",
        )
        write_publication_record(
            fullres_state_path(root, scene_id, ctx["variant"]), publication
        )
    if (
        publication
        and publication.state in {STATE_BUILDING, STATE_VALIDATING}
        and job_status in {"failed", "interrupted", "cancelled"}
    ):
        cleanup_incomplete_fullres(root, scene_id, ctx["variant"])
        mark_failed(
            publication,
            error=str(current_state.get("error") or "Build was interrupted"),
            error_code=_fullres_error_code(current_state.get("error")) or "interrupted",
        )
        write_publication_record(
            fullres_state_path(root, scene_id, ctx["variant"]), publication
        )

    status = "missing"
    if active_cog is not None:
        status = "ready"
    elif publication and publication.state == STATE_ACTIVE:
        status = "stale"
    elif job_status in ACTIVE_JOB_STATUSES:
        status = (
            "validating"
            if "validat" in job_phase
            else "building"
            if job_status in {"running", "starting"}
            else "queued"
        )
    elif publication and publication.state == STATE_VALIDATING:
        status = "validating"
    elif publication and publication.state == STATE_CANDIDATE_READY:
        status = "validating"
    elif job_status in {"failed", "interrupted", "cancelled"} or (
        publication and publication.state == STATE_FAILED
    ):
        status = "error"
    elif publication is not None:
        status = "stale"

    error = current_state.get("error") or (publication.error if publication else None)
    return {
        "fullres_eligible": bool(qualification.qualifies),
        "fullres_auto_start_enabled": _fullres_auto_start_enabled(project_id),
        "fullres_qualification_reason": qualification.reason,
        "fullres_derivative_status": status,
        "fullres_job_id": (current or {}).get("job", {}).get("job_id"),
        "fullres_error_code": _fullres_error_code(error),
        "fullres_error_message": error,
        "fullres_retryable": status == "error",
        "preview_factor": qualification.finest_display_factor,
    }


def _zoom_contract(project_id: str, scene_id: str, ctx: dict, *, revision: str | None = None):
    """Kontrakt zoomu dla sceny (R0.1) — liczony swiezo, nie z cache kontekstu.

    Dostepny poziom zalezy od tego, czy derywat wlasnie istnieje, a to sprawdza sie tanim
    `is_file` poza cache kontekstu. Gdyby siedzial w cache, scena po opublikowaniu COG
    czekalaby na uniewaznienie po mtime, zeby wpuscic zoom 1x.
    """
    display_path = _display_read_path_for(project_id, scene_id, ctx)
    assets = classify_display_assets(
        source_path=ctx["source_path"],
        display_path=display_path,
        raster_kind=ctx["raster_kind"],
        scene_info=ctx["si"],
        overview_factors=ctx.get("overview_factors"),
    )
    return compute_zoom_contract(
        scene_info=ctx["si"],
        assets=assets,
        display_asset_revision=revision,
        native_xyz_zoom=ctx.get("native_xyz_zoom"),
    )


def _display_read_path_for(project_id: str, scene_id: str, ctx: dict) -> Path:
    """Sciezka odczytu wyswietlania z cachowanego kontekstu + swiezy tani test piramidy."""
    if ctx["raster_kind"] in DISPLAY_OVERVIEW_RASTER_KINDS:
        # Kolejnosc musi byc identyczna jak w `resolve_display_read_path`, inaczej
        # kontrakt zoomu i faktyczny odczyt rozjechalyby sie po publikacji derywatu.
        cog = published_fullres_cog(
            project_dir(project_id),
            scene_id,
            ctx["variant"],
            source_fingerprint=ctx.get("source_fingerprint"),
        )
        if cog is not None:
            return cog
        preview = direct_preview_asset(project_id, scene_id, ctx["variant"])
        if preview is not None:
            return preview.path
        vrt = direct_overview_vrt(project_id, scene_id, ctx["variant"])
        if vrt is not None:
            return vrt
    return ctx["source_path"]


# --- Eksmisja cache kafli na dysku (DESIGN_DECISIONS.md, tile-serving P4) ---
#
# `geo_tile_cache` rosl bez ograniczen — wersjonowanie (GEO_TILE_CACHE_VERSION) tylko
# osieroca stare kafle, nie kasuje. Wprowadzamy limit rozmiaru per projekt z eksmisja
# najstarszych. Limit jest BEZWZGLEDNY (MB), dobrany do dysku, nie procent.
#
# Prune globuje wszystkie wersje cache, wiec przy okazji sprzata osierocone stare
# wersje (kasowane jako najstarsze). Wyzwalany licznikiem zapisow, w puli watkow —
# nie na sciezce zadania.
_TILE_CACHE_MAX_BYTES = int(os.environ.get("GEOTILE_TILE_CACHE_MAX_MB", "1024")) * 1024 * 1024
_TILE_CACHE_LOW_WATER = 0.9  # po eksmisji schodzimy do 90% limitu, zeby nie prune'owac co zapis
_TILE_CACHE_PRUNE_EVERY = 200
_tile_cache_counter_lock = threading.Lock()
_tile_cache_write_counter = 0


def _note_tile_cache_write(project_id: str) -> None:
    """Policz zapis do cache; co N zapisow zlec eksmisje w tle."""
    global _tile_cache_write_counter
    with _tile_cache_counter_lock:
        _tile_cache_write_counter += 1
        due = _tile_cache_write_counter % _TILE_CACHE_PRUNE_EVERY == 0
    if due and _TILE_CACHE_MAX_BYTES > 0:
        _maintenance_executor.submit(prune_project_tile_cache, project_id)


def prune_project_tile_cache(project_id: str, max_bytes: int | None = None) -> int:
    """Utnij cache kafli projektu do limitu, kasujac najstarsze. Zwraca usuniete bajty."""
    cap = _TILE_CACHE_MAX_BYTES if max_bytes is None else max_bytes
    if cap <= 0:
        return 0
    scenes_root = project_dir(project_id) / "scenes"
    if not scenes_root.is_dir():
        return 0

    entries: list[tuple[float, int, Path]] = []
    total = 0
    for cache_dir in scenes_root.glob("*/geo_tile_cache"):
        for png in cache_dir.rglob("*.png"):
            try:
                st = png.stat()
            except OSError:
                continue
            entries.append((st.st_mtime, st.st_size, png))
            total += st.st_size

    if total <= cap:
        return 0

    entries.sort(key=lambda item: item[0])  # najstarsze pierwsze
    target = int(cap * _TILE_CACHE_LOW_WATER)
    removed = 0
    for _mtime, size, png in entries:
        if total <= target:
            break
        try:
            png.unlink()
            total -= size
            removed += size
        except OSError:
            pass
    return removed

TILE_SIZE = 256
GEO_TILE_SIZE = 256
#: v9: okno rozciagniecia wchodzi do konwersji do uint8 (jedna kwantyzacja,
#: DESIGN_DECISIONS.md, display-stretch A) — ten sam klucz wariantu daje inne piksele niz w v8.
GEO_TILE_CACHE_VERSION = "v9"
SCENE_THUMBNAIL_VERSION = "v2"
_DISPLAY_VARIANT_CACHE = os.environ.get(
    "GEOTILE_DISPLAY_VARIANT_CACHE",
    "1",
).strip().lower() not in {"0", "false", "no", "off"}
_GEO_CACHE_LOCKS = [threading.Lock() for _ in range(64)]


def _display_cache_key(
    brightness: float,
    contrast: float,
    gamma: float,
    stretch_low: float,
    stretch_high: float,
    window: tuple[float, float] | None = None,
) -> str:
    """Klucz wariantu wyswietlania. Jawne okno (zakres „Widok") MUSI w nim byc — inaczej
    kafle roznych widokow nadpisywalyby sie nawzajem."""
    values = (brightness, contrast, gamma, stretch_low, stretch_high)
    if values == (1.0, 1.0, 1.0, 0.0, 100.0) and window is None:
        return "base"
    if window is not None:
        values = (*values, "window", *window)
    payload = "|".join(
        format(float(value), ".12g") if not isinstance(value, str) else value
        for value in values
    )
    return "display-" + hashlib.sha256(payload.encode("ascii")).hexdigest()[:16]


#: Okno rozciagniecia jest wlasnoscia SCENY, nie kafla — cache trzyma je miedzy kaflami.
_DISPLAY_WINDOW_CACHE: dict[tuple, tuple[float, float] | None] = {}
_DISPLAY_WINDOW_LIMIT = 512
_display_window_lock = threading.Lock()


def _compute_scene_display_window(
    project_id: str,
    scene_id: str,
    si: dict,
    stretch_low: float,
    stretch_high: float,
) -> tuple[float, float] | None:
    from services.scene_histogram import get_scene_histogram, percentile_to_value
    from utils.image import scene_display_window

    histogram = get_scene_histogram(project_id, scene_id)
    if not histogram.get("sample_count"):
        return None
    low_value = percentile_to_value(histogram, stretch_low)
    high_value = percentile_to_value(histogram, stretch_high)
    if low_value is None or high_value is None or not (high_value > low_value):
        return None

    # Histogram scen z `display_mode` jest liczony juz w dziedzinie wyswietlania, zeby
    # wykres odpowiadal ekranowi — wtedy progu nie wolno transformowac drugi raz.
    return scene_display_window(
        low_value,
        high_value,
        si.get("display_min"),
        si.get("display_max"),
        si.get("display_mode"),
        str(si.get("dtype") or "").lower() == "uint8",
        already_transformed=histogram.get("domain") == "display",
    )


def _resolve_scene_display_window(
    project_id: str,
    scene_id: str,
    ctx: dict,
    stretch_low: float,
    stretch_high: float,
) -> tuple[float, float] | None:
    """Percentyle z panelu -> okno konwersji do uint8, JEDNO dla calej sceny.

    Obie sciezki kafli (pikselowa i geo) biora okno z histogramu sceny, wiec etykieta
    w panelu („DN 0-162") i faktyczne odwzorowanie to ta sama liczba, a sasiednie kafle
    dostaja identyczne odwzorowanie. Okno jest w dziedzinie wyswietlania i zastepuje okno
    bazowe sceny w `render_display_window` — obraz jest kwantowany do uint8 tylko raz.

    `None` znaczy „nie da sie ustalic okna sceny" (brak histogramu albo niedeterministyczna
    konwersja do uint8) — wtedy wolajacy zostaje przy zachowaniu zapasowym.
    """
    if stretch_low <= 0.0 and stretch_high >= 100.0:
        return None
    key = (
        project_id,
        scene_id,
        ctx.get("scene_mtime_key"),
        round(float(stretch_low), 6),
        round(float(stretch_high), 6),
    )
    with _display_window_lock:
        if key in _DISPLAY_WINDOW_CACHE:
            return _DISPLAY_WINDOW_CACHE[key]
    try:
        window = _compute_scene_display_window(
            project_id, scene_id, ctx["si"], stretch_low, stretch_high
        )
    except Exception:  # noqa: BLE001 — brak histogramu nie moze przewrocic kafla
        window = None
    with _display_window_lock:
        if len(_DISPLAY_WINDOW_CACHE) >= _DISPLAY_WINDOW_LIMIT:
            _DISPLAY_WINDOW_CACHE.clear()
        _DISPLAY_WINDOW_CACHE[key] = window
    return window


#: Wylacznik zakresu rozciagniecia „Widok" (DESIGN_DECISIONS.md, display-stretch D). Wylaczony ukrywa
#: przelacznik we frontendzie (`view_stretch_available`) i zamyka `view-stats`.
_VIEW_STRETCH_ENABLED = os.environ.get("GEOTILE_VIEW_STRETCH", "1").strip().lower() not in {
    "0", "false", "no", "off",
}


def _explicit_display_window(
    stretch_min: float | None,
    stretch_max: float | None,
) -> tuple[float, float] | None:
    """Jawne okno z URL kafla (zakres „Widok"): oba progi albo zaden, skonczone, rosnace."""
    if stretch_min is None and stretch_max is None:
        return None
    if stretch_min is None or stretch_max is None:
        raise HTTPException(422, "stretch_min and stretch_max must be given together")
    low, high = float(stretch_min), float(stretch_max)
    if not (math.isfinite(low) and math.isfinite(high)) or not high > low:
        raise HTTPException(422, "stretch_max must be greater than stretch_min")
    return low, high


def _tile_display_window(
    project_id: str,
    scene_id: str,
    ctx: dict,
    brightness: float,
    contrast: float,
    gamma: float,
    stretch_low: float,
    stretch_high: float,
    explicit_window: tuple[float, float] | None = None,
) -> tuple[float, float] | None:
    """Okno, ktorym kafel zostanie skwantowany do uint8 RAZ, albo `None` (sciezka zapasowa).

    Jawne okno z URL (zakres „Widok") ma pierwszenstwo: frontend policzyl je ze statystyk
    widoku i podaje identyczne wszystkim kafelkom. Nastawa percentyli -> okno sceny. Same
    regulacje tonalne na scenie z oknem bazowym -> okno bazowe: obraz jest identyczny jak
    bez regulacji, ale jasnosc/kontrast/gamma nie ida juz na obraz raz skwantowany.
    """
    if explicit_window is not None:
        return explicit_window
    if stretch_low > 0.0 or stretch_high < 100.0:
        return _resolve_scene_display_window(
            project_id, scene_id, ctx, stretch_low, stretch_high
        )
    if (brightness, contrast, gamma) != (1.0, 1.0, 1.0):
        si = ctx["si"]
        display_min, display_max = si.get("display_min"), si.get("display_max")
        if display_min is not None and display_max is not None and display_max > display_min:
            return float(display_min), float(display_max)
    return None


def _scene_display_cache_revision(project_id: str, scene_id: str) -> str:
    """Stable key for the exact raster/overview/profile used by the renderer."""

    ctx = _scene_render_context(project_id, scene_id)
    read_path = _display_read_path_for(project_id, scene_id, ctx)

    def file_signature(path: Path) -> tuple[int, int]:
        try:
            stat = path.stat()
            return int(stat.st_size), int(stat.st_mtime_ns)
        except OSError:
            return 0, 0

    sidecar = read_path.with_name(read_path.name + ".ovr")
    si = ctx["si"]
    payload = repr(
        (
            str(read_path.resolve(strict=False)),
            file_signature(read_path),
            file_signature(sidecar),
            ctx.get("scene_mtime_key"),
            ctx.get("overview_fingerprint"),
            si.get("display_profile_version"),
            si.get("display_mode"),
            si.get("display_min"),
            si.get("display_max"),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _sync_scene_and_display_revision(project_id: str, scene_id: str) -> str:
    sync_scene_source_overviews(project_id, scene_id)
    return _scene_display_cache_revision(project_id, scene_id)


def _geo_cache_lock(path: Path) -> threading.Lock:
    digest = hashlib.blake2b(str(path).encode("utf-8"), digest_size=2).digest()
    return _GEO_CACHE_LOCKS[int.from_bytes(digest, "big") % len(_GEO_CACHE_LOCKS)]


class SceneIdentityRefreshRequest(BaseModel):
    force: bool = False


# --- Geo-tile helpers ---

def _tile_bounds_3857(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """XYZ tile -> (xmin, ymin, xmax, ymax) in EPSG:3857."""
    n = 2 ** z
    earth = 20037508.342789244
    tile_size = 2 * earth / n
    xmin = -earth + x * tile_size
    xmax = xmin + tile_size
    ymax = earth - y * tile_size
    ymin = ymax - tile_size
    return (xmin, ymin, xmax, ymax)


def _tile_bounds_4326(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """XYZ tile -> (west, south, east, north) in EPSG:4326."""
    n = 2 ** z
    west = x / n * 360.0 - 180.0
    east = (x + 1) / n * 360.0 - 180.0
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return (west, south, east, north)


def _scene_info_needs_refresh(scene_data: dict) -> bool:
    si = scene_data.get("scene_info")
    if not si:
        return True

    filename = scene_data.get("filename", "")
    suffix = Path(filename).suffix.lower()
    has_geo = bool(si.get("has_geo"))

    if has_geo and (
        not si.get("transform")
        or "crs_proj4" not in si
        or not si.get("bounds")
    ):
        return True

    if suffix in {".tif", ".tiff", ".jp2", ".vrt", ".ntf", ".nitf"} and scene_data.get("scene_info_version") != SCENE_INFO_VERSION:
        return True

    return False


def _ensure_scene_info(
    project_id: str,
    scene_id: str,
    scene_data: dict,
    *,
    strict: bool = False,
) -> dict:
    """Lazily load scene_info if not yet populated or missing new fields. Saves to disk."""
    scene_data = repair_blacksky_auxiliary_selection(project_id, scene_id, scene_data)
    if not _scene_info_needs_refresh(scene_data):
        return _ensure_scene_manifest(project_id, scene_id, scene_data, strict=strict)

    scene_path = _get_scene_path(project_id, scene_id)
    try:
        project_data = load_json(project_id, "project", default={})
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        source_package = manifest.get("source_package") or {}
        modality = (
            manifest.get("modality")
            or scene_data.get("modality")
            or (project_data.get("profile") or {}).get("modality")
        )
        info = get_scene_info(
            scene_path,
            modality=modality,
            product_type=source_package.get("product_type"),
            selected_sensors=(project_data.get("profile") or {}).get("sensors") or [],
        )
        scene_data["scene_info"] = info.model_dump()
        source_overviews = scene_data["scene_info"].get("source_overviews") or {}
        apply_source_overview_metadata(scene_data, source_overviews)
        if source_overviews.get("usable"):
            info_data = scene_data.get("scene_info") or {}
            if source_overviews_are_display_ready(
                source_overviews,
                width=info_data.get("width"),
                height=info_data.get("height"),
            ):
                scene_data["overview_status"] = "native"
            elif direct_overview_vrt(
                project_id,
                scene_id,
                scene_data.get("working_variant_id"),
            ) is not None:
                scene_data["overview_status"] = "ready"
                scene_data["overview_type"] = "project_vrt_ovr"
            else:
                scene_data["overview_status"] = "pending"
        scene_data["scene_info_version"] = SCENE_INFO_VERSION
        scene_data.pop("scene_info_error", None)
        # Utrwal odswiezone scene_info/wersje OD RAZU. Inaczej, gdy manifest jest juz
        # aktualny, _ensure_scene_manifest wraca bez zapisu, wersja zostaje nieaktualna
        # i kazde wejscie do projektu ponownie otwiera raster kazdej sceny (wolne).
        save_scene_json(project_id, scene_id, "scene", scene_data)
        scene_data = _ensure_scene_manifest(
            project_id,
            scene_id,
            scene_data,
            scene_path=scene_path,
            strict=strict,
        )
    except Exception as exc:
        scene_data["scene_info_error"] = str(exc)
        if strict:
            raise HTTPException(500, f"Could not read scene metadata: {exc}") from exc
        save_scene_json(project_id, scene_id, "scene", scene_data)
    return scene_data


def _ensure_scene_manifest(
    project_id: str,
    scene_id: str,
    scene_data: dict,
    *,
    scene_path: Path | None = None,
    strict: bool = False,
) -> dict:
    existing_manifest = load_scene_json(
        project_id, scene_id, "scene_manifest", default=None
    )
    if (
        scene_data.get("manifest_schema_version") == SCENE_MANIFEST_VERSION
        and identity_is_complete(existing_manifest)
    ):
        return scene_data

    try:
        project_data = load_json(project_id, "project")
        resolved_scene_path = scene_path or _get_scene_path(project_id, scene_id)
        write_scene_manifest(project_id, scene_id, project_data, scene_data, resolved_scene_path)
        updated = load_scene_json(project_id, scene_id, "scene", default=scene_data)
        return updated or scene_data
    except Exception as exc:
        scene_data["metadata_status"] = "manifest_error"
        scene_data["scene_manifest_error"] = str(exc)
        save_scene_json(project_id, scene_id, "scene", scene_data)
        if strict:
            raise HTTPException(500, f"Could not write scene manifest: {exc}") from exc
        return scene_data


def _get_scene_path(project_id: str, scene_id: str) -> Path:
    """Resolve full path to scene image file on disk."""
    scene_data = load_scene_json(project_id, scene_id, "scene")
    if not scene_data:
        raise HTTPException(404, "Scene not found")

    try:
        return resolve_scene_raster(project_id, scene_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc

    # (e.g., "host/c/Users/..." → "c/Users/..." for WSL2 volume mounts)
def _compute_max_zoom(width: int, height: int) -> int:
    max_dim = max(width, height)
    if max_dim <= TILE_SIZE:
        return 0
    return math.ceil(math.log2(max_dim / TILE_SIZE))


def _resolve_overview_status(project_id: str, scene_id: str, data: dict) -> str | None:
    """Status piramidy wyświetlania per-scena: ready/native/pending/error.
    - ready/native/error: zapisane przez fazę budowy piramid (best-effort).
    - w przeciwnym razie: scena nie-direct → native (piramida po stronie projektu nie dotyczy);
      scena direct → ready jeśli VRT+.ovr istnieje, inaczej pending. "building" dokłada frontend
      z bieżącego joba importu (job.current.scene_id)."""
    status = data.get("overview_status")
    if status == "native":
        info = data.get("scene_info") or {}
        state = info.get("source_overviews") or {}
        if not source_overviews_are_display_ready(
            state,
            width=info.get("width"),
            height=info.get("height"),
        ):
            status = None
    if status in ("ready", "native", "error"):
        return status
    if data.get("raster_kind") != "direct":
        return "native"
    try:
        from services.scene_packages.working_view import direct_overview_vrt

        if direct_overview_vrt(project_id, scene_id, data.get("working_variant_id")) is not None:
            return "ready"
    except Exception:
        pass
    return "pending"


def _load_scene_for_list(project_id: str, scene_id: str) -> dict | None:
    """Zaladuj i (leniwie) uzupelnij jedna scene do listy — praca synchroniczna, blokujaca."""
    data = load_scene_json(project_id, scene_id, "scene")
    if not data:
        return None
    try:
        data = _ensure_scene_info(project_id, scene_id, data)
        data, _overview_changed = sync_scene_source_overviews(project_id, scene_id, data)
    except HTTPException as exc:
        # A removable/external source may be temporarily unavailable. Keep the rest of
        # the project usable and expose the problem on the affected scene instead of
        # failing the whole catalog.
        data["source_available"] = False
        data["scene_info_error"] = str(exc.detail)
    data["overview_status"] = _resolve_overview_status(project_id, scene_id, data)
    # R0.4: `overview_status` zostaje bez zmian dla starszych klientow, ale obok niego
    # ida rozdzielone statusy. Bledy resolucji nie moga wywrocic listy scen — bez
    # kontraktu scena pokazuje sie dalej, tylko bez nowych pol.
    try:
        ctx = _scene_render_context(project_id, scene_id)
        contract = _zoom_contract(project_id, scene_id, ctx)
        data.update(
            derive_display_statuses(
                preview_status=data["overview_status"], contract=contract
            )
        )
    except Exception:
        pass
    return data


@router.get("/")
async def list_scenes(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    # Zmigruj projekt RAZ, jednowatkowo, przed rownolegla praca. Inaczej N watkow czytaloby
    # project.json i probowalo migrowac schemat jednoczesnie (kolizja snapshotu migracji).
    load_json(project_id, "project")
    sids = list_scene_ids(project_id)
    loop = asyncio.get_running_loop()
    # Praca per-scena (odczyt JSON, a przy pierwszym wejsciu otwarcie rastra) idzie do puli
    # watkow: event-loop pozostaje wolny, a pierwszy — jednorazowy — refresh N scen liczy
    # sie wspolbieznie zamiast sekwencyjnie. Zapisy dotycza osobnych plikow scen, wiec sa
    # bezpieczne rownolegle.
    results = await asyncio.gather(
        *(loop.run_in_executor(_catalog_executor, _load_scene_for_list, project_id, sid) for sid in sids)
    )
    scenes = [data for data in results if data]
    scenes.sort(key=lambda s: s.get("filename", ""))
    # Przebudowa indeksu czyta i parsuje `scene.json` + `scene_manifest.json` KAZDEJ sceny, czyli
    # powtarza prace, ktora przejscie rownolegle wyzej wlasnie wykonalo (zmierzone na DOTA, 2423
    # sceny: 5,1 s przejscia + 5,1 s przebudowy). Test swiezosci to tylko 2N `stat` (510 ms), wiec
    # gdy nic sie nie zmienilo — pomijamy. Sprawdzamy PO gather: jesli `_ensure_scene_info`
    # naprawilo w tym zadaniu jakas scene, jej mtime jest nowszy od indeksu i przebudowa rusza.
    await loop.run_in_executor(_catalog_executor, _rebuild_scenes_index_if_stale, project_id)
    return scenes


def _rebuild_scenes_index_if_stale(project_id: str) -> None:
    if scenes_index_is_fresh(project_id):
        return
    rebuild_scenes_index(project_id)


@router.get("/index")
async def get_scenes_index(
    project_id: str,
    cursor: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=500),
    sort: str = Query(default="filename"),
    order: str = Query(default="asc"),
    filter: str | None = Query(default=None),
    author: str | None = Query(default=None),
    class_id: int | None = Query(default=None),
    annotation_source: str | None = Query(default=None),
    import_id: str | None = Query(default=None),
    package_id: str | None = Query(default=None),
):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    loop = asyncio.get_running_loop()

    def query_index():
        legacy = lambda: rebuild_scenes_index(project_id)
        # Backward compatibility for callers that consume the complete index.
        if limit is None:
            return get_scenes_index_v2(project_id, legacy_fallback=legacy)
        return get_scenes_page_v2(
            project_id,
            legacy_fallback=legacy,
            cursor=cursor,
            limit=limit,
            sort=sort,
            order=order,
            filter=filter,
            author=author,
            class_id=class_id,
            annotation_source=annotation_source,
            import_id=import_id,
            package_id=package_id,
        )

    try:
        return await loop.run_in_executor(_catalog_executor, query_index)
    except InvalidCursorError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/identities/refresh")
def refresh_scene_identities(
    project_id: str,
    body: SceneIdentityRefreshRequest | None = None,
):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")

    request = body or SceneIdentityRefreshRequest()
    project_data = load_json(project_id, "project")
    results = []
    complete = 0
    exact = 0
    errors = 0
    for scene_id in list_scene_ids(project_id):
        scene_data = load_scene_json(project_id, scene_id, "scene", default={})
        if not scene_data:
            continue
        try:
            scene_path = _get_scene_path(project_id, scene_id)
            manifest = refresh_scene_identity(
                project_id,
                scene_id,
                project_data,
                scene_data,
                scene_path,
                force=request.force,
            )
            status = manifest.get("source_identity_status") or "pending"
            if status == "complete":
                complete += 1
            else:
                errors += 1
            if identity_is_exact(manifest):
                exact += 1
            results.append({
                "scene_id": scene_id,
                "filename": scene_data.get("filename"),
                "source_scene_uid": manifest.get("source_scene_uid"),
                "source_scene_candidate_uid": manifest.get("source_scene_candidate_uid"),
                "identity_method": manifest.get("source_identity_method"),
                "identity_strength": manifest.get("source_identity_strength"),
                "status": status,
                "error": manifest.get("source_identity_error"),
            })
        except Exception as exc:
            errors += 1
            results.append({
                "scene_id": scene_id,
                "filename": scene_data.get("filename"),
                "source_scene_uid": None,
                "status": "error",
                "error": str(exc),
            })

    rebuild_scenes_index(project_id)
    return {
        "project_id": project_id,
        "scene_count": len(results),
        "complete": complete,
        "exact": exact,
        "errors": errors,
        "force": request.force,
        "scenes": results,
    }


@router.get("/{scene_id}")
async def get_scene(project_id: str, scene_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    data = load_scene_json(project_id, scene_id, "scene")
    if not data:
        raise HTTPException(404, "Scene not found")
    data = _ensure_scene_info(project_id, scene_id, data, strict=True)
    data, _overview_changed = sync_scene_source_overviews(project_id, scene_id, data)
    return data


@router.get("/{scene_id}/manifest")
async def get_scene_manifest(project_id: str, scene_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    data = load_scene_json(project_id, scene_id, "scene")
    if not data:
        raise HTTPException(404, "Scene not found")
    data = _ensure_scene_info(project_id, scene_id, data, strict=True)
    sync_scene_source_overviews(project_id, scene_id, data)
    manifest = load_scene_json(project_id, scene_id, "scene_manifest", default=None)
    if not manifest:
        raise HTTPException(404, "Scene manifest not found")
    return manifest


def _ensure_scene_thumbnail(project_id: str, scene_id: str, thumb_path: Path) -> None:
    """Wygeneruj miniature, jesli brak — praca blokujaca."""
    if thumb_path.exists():
        return
    ctx = _scene_render_context(project_id, scene_id)
    scene_path = _display_read_path_for(project_id, scene_id, ctx)
    scene_data = load_scene_json(project_id, scene_id, "scene", default={})
    scene_data = _ensure_scene_info(project_id, scene_id, scene_data)
    generate_thumbnail(scene_path, thumb_path, scene_info=scene_data.get("scene_info"))


@router.get("/{scene_id}/thumbnail")
async def get_scene_thumbnail(project_id: str, scene_id: str, request: Request):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")

    await _catalog_offloaded(sync_scene_source_overviews, project_id, scene_id)
    sdir = scene_dir(project_id, scene_id)
    thumb_path = sdir / f"scene_thumbnail_{SCENE_THUMBNAIL_VERSION}.png"

    # Generacja jest leniwa i BLOKUJACA, wiec
    # idzie do puli watkow — inaczej N miniatur naraz zablokowaloby event-loop i caly widok.
    if not thumb_path.exists():
        await _render_scene_work_offloaded(
            project_id,
            scene_id,
            _ensure_scene_thumbnail,
            project_id,
            scene_id,
            thumb_path,
            request=request,
        )

    return FileResponse(thumb_path, media_type="image/png")


@router.get("/{scene_id}/scene-tiles/info")
async def get_tile_info(project_id: str, scene_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    scene_data = load_scene_json(project_id, scene_id, "scene")
    if not scene_data:
        raise HTTPException(404, "Scene not found")
    scene_data = _ensure_scene_info(project_id, scene_id, scene_data, strict=True)
    scene_data, _overview_changed = sync_scene_source_overviews(project_id, scene_id, scene_data)
    si = scene_data.get("scene_info") or {}
    w, h = si.get("width", 0), si.get("height", 0)
    max_zoom = _compute_max_zoom(w, h)
    ctx = _scene_render_context(project_id, scene_id)
    contract = _zoom_contract(
        project_id,
        scene_id,
        ctx,
        revision=_scene_display_cache_revision(project_id, scene_id),
    )
    # Otwarcie sceny jest momentem, w ktorym wiadomo, ze ktos jej faktycznie uzywa —
    # lepszym na uruchomienie dwudziestominutowej budowy niz import calej dostawy.
    fullres_view = _fullres_derivative_view(project_id, scene_id, ctx)
    return {
        "width": w,
        "height": h,
        "tile_size": TILE_SIZE,
        # `max_zoom` zostaje jako poziom REFERENCYJNY i jest rowne `source_max_zoom`.
        # Starsi klienci czytaja je dalej jako uklad wspolrzednych — i o to chodzi:
        # limitem zadan jest teraz osobne `available_native_zoom`.
        "max_zoom": max_zoom,
        "overview_type": scene_data.get("overview_type"),
        "overview_factors": scene_data.get("overview_factors") or [],
        "overview_fingerprint": scene_data.get("overview_fingerprint"),
        # Zakres rozciagniecia „Widok" (DESIGN_DECISIONS.md, display-stretch D); wylacznik
        # GEOTILE_VIEW_STRETCH=0 ukrywa przelacznik we frontendzie.
        "view_stretch_available": _VIEW_STRETCH_ENABLED,
        **contract.as_api_fields(),
        **derive_display_statuses(
            preview_status=_resolve_overview_status(project_id, scene_id, scene_data),
            contract=contract,
            job_status=fullres_view["fullres_derivative_status"],
        ),
        **fullres_view,
    }


class FullresDerivativeRequest(BaseModel):
    retry_failed: bool = False


@router.post("/{scene_id}/fullres-derivative")
def start_fullres_derivative(
    project_id: str,
    scene_id: str,
    body: FullresDerivativeRequest,
):
    """Explicitly start/retry a COG build; never auto-retry a durable failure."""
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    if not load_scene_json(project_id, scene_id, "scene", default={}):
        raise HTTPException(404, "Scene not found")

    from models.job import ACTIVE_JOB_STATUSES, JobType
    from services.jobs.store import JobStoreError, find_active_job, list_jobs
    from services.scene_packages.fullres_build_policy import check_host_memory
    from services.scene_packages.fullres_derivative import dedupe_key

    ctx = _scene_render_context(project_id, scene_id)
    view = _fullres_derivative_view(project_id, scene_id, ctx)
    if view["fullres_derivative_status"] == "ready":
        return {"created": False, **view}

    payload = _current_fullres_payload(project_id, scene_id)
    matching = [
        item
        for item in list_jobs(project_id)
        if (item.get("job") or {}).get("job_type") == JobType.SCENE_FULLRES_DERIVATIVE.value
        and (item.get("job") or {}).get("dedupe_key") == dedupe_key(project_id, scene_id)
        and _job_matches_fullres_payload(item, payload)
    ]
    active = next(
        (
            item for item in matching
            if str((item.get("state") or {}).get("status") or "") in ACTIVE_JOB_STATUSES
        ),
        None,
    )
    if active is not None:
        return {"created": False, **view, "fullres_job_id": active["job"]["job_id"]}
    failed = next(
        (
            item for item in matching
            if str((item.get("state") or {}).get("status") or "")
            in {"failed", "interrupted", "cancelled"}
        ),
        None,
    )
    if failed is not None and not body.retry_failed:
        raise HTTPException(
            409,
            {
                "code": "fullres_retry_requires_confirmation",
                "job_id": failed["job"]["job_id"],
                "error": (failed.get("state") or {}).get("error"),
            },
        )
    if not view["fullres_eligible"]:
        raise HTTPException(409, view["fullres_qualification_reason"])

    import psutil

    memory = psutil.virtual_memory()
    verdict = check_host_memory(memory.total, memory.available)
    if not verdict.ok:
        raise HTTPException(409, {"code": "insufficient_memory", **verdict.as_dict()})

    from routers.scene_import import _submit_scene_fullres_job

    try:
        submitted = _submit_scene_fullres_job(
            project_id,
            scene_id,
            retry_of=(failed or {}).get("job", {}).get("job_id"),
            attempt=int((failed or {}).get("job", {}).get("attempt") or 0) + 1,
        )
    except JobStoreError as exc:
        raced = find_active_job(
            project_id,
            job_type=JobType.SCENE_FULLRES_DERIVATIVE.value,
            dedupe_key=dedupe_key(project_id, scene_id),
        )
        if raced is None:
            raise HTTPException(409, str(exc)) from exc
        return {
            "created": False,
            **view,
            "fullres_derivative_status": "queued",
            "fullres_job_id": raced["job"]["job_id"],
        }
    return {
        "created": True,
        **view,
        "fullres_derivative_status": "queued",
        "fullres_job_id": submitted["job"]["job_id"],
        "job": submitted,
    }


class RgbBandsRequest(BaseModel):
    rgb_bands: list[int]


@router.post("/{scene_id}/rgb-bands")
def set_scene_rgb_bands(project_id: str, scene_id: str, body: RgbBandsRequest):
    """Select which 3 source bands render as RGB for a local multiband scene."""
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    from services.scene_bands import set_local_scene_rgb_bands

    try:
        return set_local_scene_rgb_bands(project_id, scene_id, body.rgb_bands)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc


@router.get("/{scene_id}/histogram")
async def get_scene_histogram_endpoint(project_id: str, scene_id: str):
    """Value histogram + statistics for the display panel (cached per scene)."""
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    from services.scene_histogram import get_scene_histogram

    try:
        await _catalog_offloaded(sync_scene_source_overviews, project_id, scene_id)
        return await _render_scene_work_offloaded(
            project_id,
            scene_id,
            get_scene_histogram,
            project_id,
            scene_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


# --- Statystyki biezacego widoku (DESIGN_DECISIONS.md, display-stretch D) ---
_VIEW_STATS_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_VIEW_STATS_CACHE_LIMIT = 256
_view_stats_lock = threading.Lock()


@router.get("/{scene_id}/view-stats")
async def get_scene_view_stats(
    project_id: str, scene_id: str, request: Request,
    x0: float = Query(...), y0: float = Query(...),
    x1: float = Query(...), y1: float = Query(...),
):
    """Histogram widocznego fragmentu sceny — zrodlo progow zakresu „Widok".

    Wspolrzedne w pikselach siatki referencyjnej sceny (te same co adnotacje). Odpowiedz ma
    ksztalt histogramu sceny, wiec panel pokazuje ja bez osobnej obslugi.
    """
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    if not _VIEW_STRETCH_ENABLED:
        raise HTTPException(404, "View stretch is disabled")
    if not all(math.isfinite(value) for value in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
        raise HTTPException(422, "The view window must satisfy x0 < x1 and y0 < y1")
    try:
        await _catalog_offloaded(sync_scene_source_overviews, project_id, scene_id)
        return await _render_scene_work_offloaded(
            project_id, scene_id, _produce_view_stats, project_id, scene_id, x0, y0, x1, y1,
            request=request,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


def _produce_view_stats(project_id, scene_id, x0, y0, x1, y1) -> dict:
    from services.scene_view_stats import (
        compute_window_stats,
        insufficient_view_stats,
        snap_window,
    )

    ctx = _scene_render_context(project_id, scene_id)
    si = ctx["si"]
    display_mode = si.get("display_mode")
    revision = _scene_display_cache_revision(project_id, scene_id)
    window = snap_window((x0, y0, x1, y1), si.get("width", 0), si.get("height", 0))
    if window is None:
        return {
            **insufficient_view_stats(None, reason="outside_scene", display_mode=display_mode),
            "display_revision": revision,
        }
    scene_path = ctx["source_path"]
    if scene_path.suffix.lower() not in {".tif", ".tiff", ".jp2", ".vrt"}:
        return {
            **insufficient_view_stats(list(window), reason="unsupported_source",
                                      display_mode=display_mode),
            "display_revision": revision,
        }
    contract = _zoom_contract(project_id, scene_id, ctx)
    if contract.assets.display_requires_jp2_decode and contract.assets.finest_display_factor > 1:
        raise HTTPException(409, "Display preview is unavailable; source JP2 decode is disabled")

    key = (project_id, scene_id, revision, window)
    with _view_stats_lock:
        cached = _VIEW_STATS_CACHE.get(key)
        if cached is not None:
            _VIEW_STATS_CACHE.move_to_end(key)
            return cached

    def compute() -> dict:
        # Ten sam graf odczytu co kafle pikselowe: piramida wyswietlania albo zrodlo.
        read_path = _display_read_path_for(project_id, scene_id, ctx)
        preview = direct_preview_asset(project_id, scene_id, ctx["variant"])
        coordinate_factor = (
            preview.base_factor if preview is not None and read_path == preview.path else 1
        )
        stats = compute_window_stats(
            read_path, window, coordinate_factor=coordinate_factor, display_mode=display_mode
        )
        return {**stats, "display_revision": revision}

    stats = tile_single_flight.do(("view-stats", *key), compute)
    with _view_stats_lock:
        _VIEW_STATS_CACHE[key] = stats
        _VIEW_STATS_CACHE.move_to_end(key)
        while len(_VIEW_STATS_CACHE) > _VIEW_STATS_CACHE_LIMIT:
            _VIEW_STATS_CACHE.popitem(last=False)
    return stats


@router.get("/{scene_id}/scene-tiles/{z}/{x}/{y}.png")
async def get_scene_tile(
    project_id: str, scene_id: str, z: int, x: int, y: int, request: Request,
    brightness: float = Query(1.0), contrast: float = Query(1.0),
    gamma: float = Query(1.0),
    stretch_low: float = Query(0.0), stretch_high: float = Query(100.0),
    stretch_min: float | None = Query(None), stretch_max: float | None = Query(None),
):
    """Serve a map tile from the scene image.

    `stretch_min`/`stretch_max` to jawne okno zakresu „Widok" w dziedzinie wyswietlania;
    gdy sa podane, zastepuja percentyle `stretch_low`/`stretch_high`.
    """
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    explicit_window = _explicit_display_window(stretch_min, stretch_max)

    # Całe ciało — wczytanie metadanych, odczyt rastra, kodowanie — poza pętlą zdarzeń
    # w ograniczonej puli, żeby kafle viewportu szły równolegle, a pętla obsługiwała
    # inne żądania w trakcie ciężkiego kaflowania (P2).
    png_bytes, cache_hdr = await _render_scene_work_offloaded(
        project_id, scene_id, _produce_scene_tile, project_id, scene_id, z, x, y,
        brightness, contrast, gamma, stretch_low, stretch_high, explicit_window,
        request=request,
    )
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": cache_hdr},
    )


def _produce_scene_tile(
    project_id, scene_id, z, x, y,
    brightness, contrast, gamma, stretch_low, stretch_high,
    explicit_window=None,
) -> tuple[bytes, str]:
    """Deduplikacja identycznych zadan (R0.5) — tryb pikselowy nie mial jej wcale."""
    key = (
        "scene-tile", project_id, scene_id, z, x, y,
        brightness, contrast, gamma, stretch_low, stretch_high, explicit_window,
        _scene_display_cache_revision(project_id, scene_id),
    )
    return tile_single_flight.do(
        key,
        lambda: _produce_scene_tile_uncoordinated(
            project_id, scene_id, z, x, y,
            brightness, contrast, gamma, stretch_low, stretch_high, explicit_window,
        ),
    )


def _produce_scene_tile_uncoordinated(
    project_id, scene_id, z, x, y,
    brightness, contrast, gamma, stretch_low, stretch_high,
    explicit_window=None,
) -> tuple[bytes, str]:
    """Synchroniczne wyprodukowanie kafla sceny → (bajty PNG, nagłówek cache).

    Wołane w puli wątków (P2). HTTPException podniesiony tutaj propaguje się do
    handlera async i jest obsłużony normalnie przez FastAPI.
    """
    sync_scene_source_overviews(project_id, scene_id)
    ctx = _scene_render_context(project_id, scene_id)
    si = ctx["si"]
    scene_w, scene_h = si.get("width", 0), si.get("height", 0)
    max_zoom = ctx["max_zoom"]

    if z < 0 or z > max_zoom:
        raise HTTPException(404, "Invalid zoom level")
    # Zadanie powyzej aktualnie serwowalnego poziomu jest odrzucane, ale okna NADAL licza
    # sie wzgledem `max_zoom` (poziomu referencyjnego). Rozdzielenie tych dwoch rol jest
    # cala tresci R0.1: wspolny licznik przesunalby okna odczytu i adnotacje.
    contract = _zoom_contract(project_id, scene_id, ctx)
    if z > contract.available_native_zoom:
        raise HTTPException(
            409,
            f"Zoom {z} is not available yet (highest servable level: "
            f"{contract.available_native_zoom})",
        )
    if contract.assets.display_requires_jp2_decode and contract.assets.finest_display_factor > 1:
        raise HTTPException(409, "Display preview is unavailable; source JP2 decode is disabled")

    pixels_per_tile = TILE_SIZE * (2 ** (max_zoom - z))
    src_x0 = max(0, x * pixels_per_tile)
    src_y0 = max(0, y * pixels_per_tile)
    src_x1 = min(scene_w, (x + 1) * pixels_per_tile)
    src_y1 = min(scene_h, (y + 1) * pixels_per_tile)

    if src_x0 >= scene_w or src_y0 >= scene_h or src_x1 <= src_x0 or src_y1 <= src_y0:
        empty = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0))
        buf = io.BytesIO()
        empty.save(buf, "PNG")
        return buf.getvalue(), "public, max-age=3600"

    out_w = max(1, min(TILE_SIZE, round((src_x1 - src_x0) / pixels_per_tile * TILE_SIZE)))
    out_h = max(1, min(TILE_SIZE, round((src_y1 - src_y0) / pixels_per_tile * TILE_SIZE)))

    scene_path = ctx["source_path"]
    suffix = scene_path.suffix.lower()
    display_window = _tile_display_window(
        project_id, scene_id, ctx, brightness, contrast, gamma, stretch_low, stretch_high,
        explicit_window,
    )
    window_applied = False

    if suffix in {".tif", ".tiff", ".jp2", ".vrt"}:
        # Piramida wyświetlania, jeśli zbudowana (P1) — te same piksele, szybszy
        # odczyt niskiego zoomu; źródło jest fallbackiem.
        read_path = _display_read_path_for(project_id, scene_id, ctx)
        preview = direct_preview_asset(project_id, scene_id, ctx["variant"])
        coordinate_factor = (
            preview.base_factor
            if preview is not None and read_path == preview.path
            else 1
        )
        tile_arr = _read_geotiff_window(
            read_path,
            si,
            src_x0 / coordinate_factor,
            src_y0 / coordinate_factor,
            src_x1 / coordinate_factor,
            src_y1 / coordinate_factor,
            TILE_SIZE,
            out_w,
            out_h,
            display_window=display_window,
            tonal=(brightness, contrast, gamma),
        )
        window_applied = display_window is not None
    else:
        tile_arr = _read_pil_window(
            scene_path, src_x0, src_y0, src_x1, src_y1, TILE_SIZE, out_w, out_h
        )

    has_adjustments = (
        brightness != 1.0 or contrast != 1.0 or gamma != 1.0
        or stretch_low > 0.0 or stretch_high < 100.0
        or explicit_window is not None
    )
    if has_adjustments and not window_applied:
        # Obraz PIL jest juz uint8 RGB — okno sceny jest wtedy w dziedzinie 0-255,
        # a LUT daje ta sama arytmetyke co `render_display_window`.
        tile_arr = apply_display_params(
            tile_arr, brightness, contrast, gamma, stretch_low, stretch_high,
            percentile_stretch=si.get("display_min") is None,
            stretch_bounds=display_window if si.get("display_min") is None else None,
        )

    tile_img = Image.fromarray(tile_arr)
    buf = io.BytesIO()
    tile_img.save(buf, "PNG")
    # Display parameters are part of the URL, so adjusted pixel tiles are safe to
    # cache in the browser just like the base variant.
    cache_hdr = "public, max-age=3600"
    return buf.getvalue(), cache_hdr


@router.get("/{scene_id}/geo-tiles/{z}/{x}/{y}.png")
async def get_geo_tile(
    project_id: str, scene_id: str, z: int, x: int, y: int, request: Request,
    brightness: float = Query(1.0), contrast: float = Query(1.0),
    gamma: float = Query(1.0),
    stretch_low: float = Query(0.0), stretch_high: float = Query(100.0),
    stretch_min: float | None = Query(None), stretch_max: float | None = Query(None),
):
    """Serve a Web Mercator map tile reprojected from the GeoTIFF scene.

    `stretch_min`/`stretch_max` to jawne okno zakresu „Widok" w dziedzinie wyswietlania;
    gdy sa podane, zastepuja percentyle `stretch_low`/`stretch_high`.
    """
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    explicit_window = _explicit_display_window(stretch_min, stretch_max)

    raster_revision = await _catalog_offloaded(
        _sync_scene_and_display_revision,
        project_id,
        scene_id,
    )

    has_adjustments = (
        brightness != 1.0 or contrast != 1.0 or gamma != 1.0
        or stretch_low > 0.0 or stretch_high < 100.0
        or explicit_window is not None
    )

    # Trafienie w cache dysku obsłuż tanio na pętli — sam odczyt pliku, bez metadanych
    # ani puli. Dopiero brak cache (reprojekcja WarpedVRT) idzie do puli wątków (P2).
    display_key = _display_cache_key(
        brightness,
        contrast,
        gamma,
        stretch_low,
        stretch_high,
        explicit_window,
    )
    cache_path = (
        scene_dir(project_id, scene_id)
        / "geo_tile_cache"
        / GEO_TILE_CACHE_VERSION
        / raster_revision
        / display_key
        / str(z)
        / str(x)
        / f"{y}.png"
    )
    cache_enabled = _geo_disk_cache_enabled(has_adjustments, explicit_window)
    if cache_enabled and cache_path.exists():
        return FileResponse(cache_path, media_type="image/png",
                            headers={"Cache-Control": "public, max-age=3600"})

    png_bytes, cache_hdr = await _render_scene_work_offloaded(
        project_id, scene_id, _produce_geo_tile,
        project_id, scene_id, z, x, y, str(cache_path),
        brightness, contrast, gamma, stretch_low, stretch_high, has_adjustments,
        explicit_window,
        request=request,
    )
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": cache_hdr},
    )


def _geo_disk_cache_enabled(
    has_adjustments: bool, explicit_window: tuple[float, float] | None
) -> bool:
    """Kafle z jawnym oknem (zakres „Widok") nie ida na dysk (display-stretch D): to warianty przejsciowe,
    ktore zapelnilyby limit cache. Zostaje deduplikacja i cache przegladarki."""
    if explicit_window is not None:
        return False
    return not has_adjustments or _DISPLAY_VARIANT_CACHE


def _produce_geo_tile(
    project_id, scene_id, z, x, y, cache_path_str,
    brightness, contrast, gamma, stretch_low, stretch_high, has_adjustments,
    explicit_window=None,
) -> tuple[bytes, str]:
    """Render one tile with in-flight deduplication and atomic variant caching."""

    cache_enabled = _geo_disk_cache_enabled(has_adjustments, explicit_window)
    if not cache_enabled:
        # Bez cache dyskowego nie ma blokady pliku, wiec deduplikacja musi byc jawna —
        # inaczej dwa identyczne zadania wariantu wyswietlania dekodowaly rownolegle
        # to samo okno (R0.5).
        key = ("geo-tile", cache_path_str)
        png_bytes, cache_hdr = tile_single_flight.do(
            key,
            lambda: _produce_geo_tile_uncached(
                project_id, scene_id, z, x, y, cache_path_str,
                brightness, contrast, gamma, stretch_low, stretch_high, has_adjustments,
                explicit_window,
            ),
        )
        if explicit_window is not None:
            # URL z oknem, rewizja i parametrami wyznacza piksele jednoznacznie.
            cache_hdr = "public, max-age=3600"
        return png_bytes, cache_hdr

    cache_path = Path(cache_path_str)
    with _geo_cache_lock(cache_path):
        try:
            if cache_path.is_file():
                return cache_path.read_bytes(), "public, max-age=3600"
        except OSError:
            pass

        png_bytes, _cache_hdr = _produce_geo_tile_uncached(
            project_id, scene_id, z, x, y, cache_path_str,
            brightness, contrast, gamma, stretch_low, stretch_high, has_adjustments,
        )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        partial = cache_path.with_name(
            f".{cache_path.name}.{os.getpid()}.{threading.get_ident()}.partial"
        )
        try:
            partial.write_bytes(png_bytes)
            os.replace(partial, cache_path)
            _note_tile_cache_write(project_id)
        finally:
            partial.unlink(missing_ok=True)
        return png_bytes, "public, max-age=3600"


def _produce_geo_tile_uncached(
    project_id, scene_id, z, x, y, cache_path_str,
    brightness, contrast, gamma, stretch_low, stretch_high, has_adjustments,
    explicit_window=None,
) -> tuple[bytes, str]:
    """Synchroniczna reprojekcja Web Mercator dla braku cache → (bajty PNG, nagłówek).

    Wołane w puli wątków (P2). Zapisuje do cache przy braku regulacji wyświetlania.
    """
    ctx = _scene_render_context(project_id, scene_id)
    si = ctx["si"]
    if not si.get("has_geo") or not si.get("bounds"):
        raise HTTPException(400, "Scene is not georeferenced")

    # W trybie geo limitem jest poziom XYZ, nie zoom sceny (R0.1). `None` znaczy, ze nie
    # da sie go policzyc (brak rozdzielczosci w metadanych) — wtedy nie ograniczamy,
    # bo zgadywanie limitu byloby gorsze niz jego brak.
    contract = _zoom_contract(project_id, scene_id, ctx)
    limit = contract.available_native_xyz_zoom
    if limit is not None and z > limit:
        raise HTTPException(
            409,
            f"Zoom {z} is not available yet (highest servable XYZ level: {limit})",
        )
    if contract.assets.display_requires_jp2_decode and contract.assets.finest_display_factor > 1:
        raise HTTPException(409, "Display preview is unavailable; source JP2 decode is disabled")

    tile_b = _tile_bounds_4326(z, x, y)  # (west, south, east, north)
    scene_b = si["bounds"]
    if (tile_b[0] > scene_b[2] or tile_b[2] < scene_b[0] or
            tile_b[1] > scene_b[3] or tile_b[3] < scene_b[1]):
        empty = Image.new("RGBA", (GEO_TILE_SIZE, GEO_TILE_SIZE), (0, 0, 0, 0))
        buf = io.BytesIO()
        empty.save(buf, "PNG")
        return buf.getvalue(), "public, max-age=3600"

    # Piramida wyświetlania, jeśli zbudowana (P1) — przyspiesza reprojekcję niskiego
    # zoomu; źródło jest fallbackiem.
    scene_path = _display_read_path_for(project_id, scene_id, ctx)

    # `MaskFlags`, `Resampling` i `WarpedVRT` przeniosly sie do sesji rastra (R0.3);
    # tutaj zostaje tylko przeliczenie okna kafla.
    from rasterio.windows import from_bounds

    tile_3857 = _tile_bounds_3857(z, x, y)

    # Liczba watkow GDAL zalezy od AKTYWNEGO grafu odczytu, nie od rozszerzenia pliku
    # (R0.2): `overview.vrt` opakowujacy JP2 dalej dekoduje JP2, a gotowy COG juz nie.
    env_options = (
        {"GDAL_NUM_THREADS": str(_JP2_GDAL_THREADS)}
        if contract.assets.display_requires_jp2_decode
        else {}
    )
    # Uchwyt i WarpedVRT nalezace do tego workera (R0.3). Wczesniej otwierane na kazdy
    # kafel, przez co cache blokow GDAL nie byl wspoldzielony miedzy sasiednimi kaflami:
    # zmierzone 21,5 s wobec 6,2 s na viewporcie 32 kafli.
    with display_raster_session(
        scene_path,
        revision=_scene_display_cache_revision(project_id, scene_id),
        env_options=env_options,
    ) as session:
        vrt = session.warped
        source_all_valid = session.source_all_valid
        window = from_bounds(*tile_3857, transform=vrt.transform)
        data_band_count = vrt.count - 1 if source_all_valid else vrt.count
        bands_to_read = min(data_band_count, 3)
        indexes = list(range(1, bands_to_read + 1))
        data, masks = _read_vrt_tile_window(
            vrt,
            window,
            indexes,
            bands_to_read,
            coverage_band_index=vrt.count if source_all_valid else None,
        )

    raw_valid_mask = np.any(np.isfinite(data) & (np.abs(data) > 1e-12), axis=0)
    arr = np.transpose(data, (1, 2, 0))  # (H, W, C)
    display_window = (
        _tile_display_window(
            project_id, scene_id, ctx, brightness, contrast, gamma, stretch_low, stretch_high,
            explicit_window,
        )
        if has_adjustments
        else None
    )
    if display_window is not None:
        # Ta sama semantyka co w sciezce pikselowej: okno sceny zamiast okna bazowego,
        # regulacje tonalne we float, jedna kwantyzacja do uint8.
        arr = render_display_window(
            arr, si.get("display_mode"), display_window, brightness, contrast, gamma
        )
    else:
        arr = ensure_rgb_uint8(
            arr,
            si.get("display_min"),
            si.get("display_max"),
            si.get("display_mode"),
        )
    if source_all_valid:
        valid_mask = np.any(masks > 0, axis=0)
    elif masks is not None and not np.all(masks > 0):
        valid_mask = np.any(masks > 0, axis=0) & raw_valid_mask
    else:
        valid_mask = raw_valid_mask

    if has_adjustments and display_window is None:
        # Sciezka zapasowa: scena bez histogramu albo bez deterministycznej konwersji.
        arr = apply_display_params(
            arr, brightness, contrast, gamma, stretch_low, stretch_high,
            percentile_stretch=si.get("display_min") is None,
        )

    alpha = np.where(valid_mask, 255, 0).astype(np.uint8)
    rgba = np.dstack([arr, alpha])
    tile_img = Image.fromarray(rgba, "RGBA")

    buf = io.BytesIO()
    tile_img.save(buf, "PNG")
    cache_hdr = "no-cache"
    return buf.getvalue(), cache_hdr


def _read_vrt_tile_window(
    vrt,
    window,
    indexes: list[int],
    bands_to_read: int,
    *,
    coverage_band_index: int | None = None,
):
    """Read a Web Mercator tile window without stretching clipped edge windows."""
    from rasterio.warp import Resampling
    from rasterio.windows import Window

    data = np.zeros(
        (bands_to_read, GEO_TILE_SIZE, GEO_TILE_SIZE),
        dtype=np.dtype(vrt.dtypes[0]),
    )
    masks = np.zeros(
        (bands_to_read, GEO_TILE_SIZE, GEO_TILE_SIZE),
        dtype=np.uint8,
    )

    clipped = _clip_window_to_dataset(window, vrt.width, vrt.height)
    if clipped is None:
        return data, masks

    dst_x0, dst_y0, dst_x1, dst_y1 = _window_to_tile_slice(window, clipped)
    dst_w = dst_x1 - dst_x0
    dst_h = dst_y1 - dst_y0
    if dst_w <= 0 or dst_h <= 0:
        return data, masks

    read_indexes = [*indexes]
    if coverage_band_index is not None:
        read_indexes.append(int(coverage_band_index))
    clipped_read = vrt.read(
        indexes=read_indexes,
        window=clipped,
        out_shape=(len(read_indexes), dst_h, dst_w),
        resampling=Resampling.bilinear,
    )
    clipped_data = clipped_read[:bands_to_read]
    data[:, dst_y0:dst_y1, dst_x0:dst_x1] = clipped_data

    if coverage_band_index is not None:
        coverage = np.where(clipped_read[-1] > 0, 255, 0).astype(np.uint8)
        masks[:, dst_y0:dst_y1, dst_x0:dst_x1] = coverage
    else:
        try:
            clipped_masks = vrt.read_masks(
                indexes=indexes,
                window=clipped,
                out_shape=(bands_to_read, dst_h, dst_w),
                resampling=Resampling.nearest,
            )
            masks[:, dst_y0:dst_y1, dst_x0:dst_x1] = clipped_masks
        except Exception:
            masks[:, dst_y0:dst_y1, dst_x0:dst_x1] = 255

    return data, masks


def _clip_window_to_dataset(window, width: int, height: int):
    from rasterio.windows import Window

    col0 = max(float(window.col_off), 0.0)
    row0 = max(float(window.row_off), 0.0)
    col1 = min(float(window.col_off + window.width), float(width))
    row1 = min(float(window.row_off + window.height), float(height))
    if col1 <= col0 or row1 <= row0:
        return None
    return Window(col0, row0, col1 - col0, row1 - row0)


def _window_to_tile_slice(window, clipped) -> tuple[int, int, int, int]:
    dst_x0 = int(np.floor((clipped.col_off - window.col_off) / window.width * GEO_TILE_SIZE))
    dst_y0 = int(np.floor((clipped.row_off - window.row_off) / window.height * GEO_TILE_SIZE))
    dst_x1 = int(np.ceil((clipped.col_off + clipped.width - window.col_off) / window.width * GEO_TILE_SIZE))
    dst_y1 = int(np.ceil((clipped.row_off + clipped.height - window.row_off) / window.height * GEO_TILE_SIZE))

    dst_x0 = max(0, min(GEO_TILE_SIZE, dst_x0))
    dst_y0 = max(0, min(GEO_TILE_SIZE, dst_y0))
    dst_x1 = max(0, min(GEO_TILE_SIZE, dst_x1))
    dst_y1 = max(0, min(GEO_TILE_SIZE, dst_y1))
    return dst_x0, dst_y0, dst_x1, dst_y1


def _read_geotiff_window(
    path: Path, scene_info: dict, x0: float, y0: float, x1: float, y1: float,
    tile_size: int, out_w: int, out_h: int,
    *,
    display_window: tuple[float, float] | None = None,
    tonal: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> np.ndarray:
    """Okno sceny -> kafel RGB uint8. Z `display_window` konwersja idzie przez
    `render_display_window` (okno rozciagniecia + `tonal` = jasnosc, kontrast, gamma,
    jedna kwantyzacja); bez niego — przez okno bazowe sceny, jak dotad."""
    import rasterio
    from rasterio.windows import Window

    env_options = (
        {"GDAL_NUM_THREADS": str(_JP2_GDAL_THREADS)}
        if path.suffix.lower() == ".jp2"
        else {}
    )
    with rasterio.Env(**env_options):
        with rasterio.open(path) as src:
            window = Window(col_off=x0, row_off=y0, width=x1 - x0, height=y1 - y0)
            data = src.read(
                window=window,
                out_shape=(src.count, out_h, out_w),
            )

    arr = np.transpose(data, (1, 2, 0))
    if display_window is not None:
        arr = render_display_window(
            arr, scene_info.get("display_mode"), display_window, *tonal
        )
    else:
        arr = ensure_rgb_uint8(
            arr,
            scene_info.get("display_min"),
            scene_info.get("display_max"),
            scene_info.get("display_mode"),
        )

    canvas = np.zeros((tile_size, tile_size, 3), dtype=np.uint8)
    canvas[:out_h, :out_w] = arr
    return canvas


def _read_pil_window(
    path: Path, x0: int, y0: int, x1: int, y1: int,
    tile_size: int, out_w: int, out_h: int,
) -> np.ndarray:
    img = Image.open(path).convert("RGB")
    crop = img.crop((x0, y0, x1, y1))
    crop = crop.resize((out_w, out_h), Image.LANCZOS)

    canvas = Image.new("RGB", (tile_size, tile_size), (0, 0, 0))
    canvas.paste(crop, (0, 0))
    return np.array(canvas)
