"""Metadata-only tile catalog, review state and lazy tile previews."""

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from db.storage import load_json, list_scene_ids, project_exists
from models.tiling_config import TilingConfig
from services.scene_loader import get_scene_info
from services.scene_raster_resolver import resolve_scene_raster
from services.scene_packages.working_view import lock_working_view
from services.tile_catalog import (
    build_tile_catalog,
    clear_legacy_tile_cache,
    clear_preview_cache,
    preview_cache_info,
    compute_tile_progress,
    get_active_catalog_manifest,
    get_catalog_tiles,
    legacy_cache_info,
    migrate_legacy_tile_catalog,
    render_tile_preview,
    update_review_state,
)
from db.storage import load_scene_json, save_json

router = APIRouter()


class ReviewRequest(BaseModel):
    tile_indices: list[int]
    reviewed: bool = True


class ExcludeRequest(BaseModel):
    tile_indices: list[int]
    excluded: bool = True


def _get_scene_path(project_id: str, scene_id: str):
    try:
        return resolve_scene_raster(project_id, scene_id)
    except FileNotFoundError:
        return None


@router.get("/config")
async def get_tiling_config(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    return load_json(project_id, "tiling_config", default={"tile_size": 640, "buffer": 0})


@router.put("/config")
async def update_tiling_config(project_id: str, body: TilingConfig):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    if body.stride <= 0:
        raise HTTPException(400, "Buffer must be smaller than tile size")
    save_json(project_id, "tiling_config", body.model_dump())
    return body.model_dump()


@router.get("/preview")
async def get_tiling_preview(project_id: str, scene_id: Optional[str] = Query(None)):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    if not scene_id:
        scene_ids = list_scene_ids(project_id)
        if not scene_ids:
            raise HTTPException(404, "No scenes found")
        scene_id = scene_ids[0]
    scene_path = _get_scene_path(project_id, scene_id)
    if not scene_path:
        raise HTTPException(404, "Scene not found")
    info = get_scene_info(scene_path)
    config = TilingConfig(**load_json(project_id, "tiling_config", default={"tile_size": 640, "buffer": 0}))
    from services.tiler import compute_grid

    return compute_grid(info.width, info.height, config).model_dump()


@router.post("/execute")
async def execute_tiling_endpoint(
    project_id: str,
    scene_id: Optional[str] = Query(None),
):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    scene_ids = list_scene_ids(project_id)
    if not scene_ids:
        raise HTTPException(400, "No scenes found")

    async def generate():
        yield {"event": "progress", "data": json.dumps({
            "done": 0,
            "total": len(scene_ids),
            "mode": "metadata_only",
        })}
        try:
            manifest = await asyncio.to_thread(build_tile_catalog, project_id)
        except Exception as exc:
            yield {"event": "error", "data": json.dumps({"error": str(exc)})}
            return
        for current_scene_id in scene_ids:
            lock_working_view(project_id, current_scene_id, "first_tiling")
        yield {"event": "progress", "data": json.dumps({
            "done": len(scene_ids),
            "total": len(scene_ids),
            "mode": "metadata_only",
            "tile_count": manifest.get("tile_count", 0),
        })}
        yield {"event": "complete", "data": json.dumps(manifest)}

    return EventSourceResponse(generate())


@router.get("/catalog")
def get_active_catalog(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    manifest = get_active_catalog_manifest(project_id, auto_migrate=True)
    if not manifest:
        raise HTTPException(404, "No tile catalog found")
    return manifest


@router.post("/catalog/migrate")
def migrate_legacy_catalog(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    manifest = migrate_legacy_tile_catalog(project_id)
    if not manifest:
        raise HTTPException(404, "No legacy tiles.json metadata found")
    return manifest


@router.get("/tiles")
def list_tiles(project_id: str, scene_id: Optional[str] = Query(None)):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    return get_catalog_tiles(project_id, scene_id)


@router.patch("/tiles/review")
def review_tiles(
    project_id: str,
    body: ReviewRequest,
    scene_id: str = Query(...),
):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    try:
        result = update_review_state(
            project_id,
            scene_id,
            body.tile_indices,
            reviewed=body.reviewed,
        )
        if body.reviewed:
            lock_working_view(project_id, scene_id, "first_reviewed_cell")
        return result
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.patch("/tiles/exclude")
def exclude_tiles(
    project_id: str,
    body: ExcludeRequest,
    scene_id: str = Query(...),
):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    try:
        return update_review_state(
            project_id,
            scene_id,
            body.tile_indices,
            excluded=body.excluded,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/tiles/progress")
def tile_progress(project_id: str, scene_id: str = Query(...)):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    return compute_tile_progress(project_id, scene_id)


@router.get("/tiles/{tile_id}/preview")
def tile_preview(project_id: str, tile_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    try:
        path = render_tile_preview(project_id, tile_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, media_type="image/png", filename=path.name)


@router.get("/legacy-cache")
def get_legacy_cache_info(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    return legacy_cache_info(project_id)


@router.delete("/legacy-cache")
def delete_legacy_cache(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    return clear_legacy_tile_cache(project_id)


@router.get("/preview-cache")
def get_preview_cache(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    return preview_cache_info(project_id)


@router.delete("/preview-cache")
def delete_preview_cache(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    return clear_preview_cache(project_id)
