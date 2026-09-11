"""RGB band selection for local multiband scenes.

Provider packages pick their RGB bands during preparation; plain local multiband
rasters (a folder .tif/.jp2 with >3 bands) had no such choice and always used
bands 1–3. This builds a band-selecting VRT (GDAL Translate, works even without
georeferencing) and points the scene's working raster at it, so every read path
(display tiles, dataset generation) sees the chosen 3 bands as bands 1–3 — no
read-path changes needed.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from db.storage import (
    SCENES_ROOT,
    load_json,
    load_scene_json,
    project_dir,
    save_scene_json,
    scene_dir,
)
from utils.scene_paths import resolve_scene_file


def _original_source(project: dict[str, Any], scene: dict[str, Any]) -> Path | None:
    """The underlying folder raster (never the derived bands VRT)."""
    return resolve_scene_file(
        SCENES_ROOT, project.get("scene_folder", ""), scene.get("filename", "")
    )


def set_local_scene_rgb_bands(
    project_id: str, scene_id: str, rgb_bands: list[int]
) -> dict[str, Any]:
    """Point a local multiband scene at a band-selecting VRT for the chosen R,G,B."""
    project = load_json(project_id, "project", default={})

    scene = load_scene_json(project_id, scene_id, "scene", default={})
    if not scene:
        raise FileNotFoundError("Scene not found")
    manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
    if manifest.get("source_package") or (manifest.get("geometry") or {}).get("model") == "gcp_tps":
        # Packages already select bands during prepare; NITF is single-band pan.
        raise ValueError("Band selection applies only to local multiband scenes.")

    source = _original_source(project, scene)
    if source is None or not source.is_file():
        raise FileNotFoundError("Original scene raster not found")

    import rasterio

    with rasterio.open(source) as src:
        band_count = src.count
    if len(rgb_bands) != 3 or any(int(b) < 1 or int(b) > band_count for b in rgb_bands):
        raise ValueError(f"rgb_bands must be three band indices within 1..{band_count}")
    rgb_bands = [int(b) for b in rgb_bands]

    from osgeo import gdal

    gdal.UseExceptions()
    variant_id = "bands_" + "_".join(str(b) for b in rgb_bands)
    target_dir = scene_dir(project_id, scene_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    vrt_path = target_dir / f"{Path(scene.get('filename', scene_id)).stem}_{variant_id}.vrt"
    partial = vrt_path.with_name(f".{vrt_path.name}.partial")
    partial.unlink(missing_ok=True)
    dataset = gdal.Translate(
        str(partial), str(source), options=gdal.TranslateOptions(format="VRT", bandList=rgb_bands)
    )
    if dataset is None:
        raise RuntimeError("GDAL could not build the band-selection VRT.")
    dataset.FlushCache()
    dataset = None
    os.replace(partial, vrt_path)

    relative = vrt_path.resolve().relative_to(project_dir(project_id).resolve()).as_posix()
    raster_ref = {"storage": "project", "relative_path": relative}

    working = manifest.setdefault("working_view", {})
    working.update({
        "variant_id": variant_id,
        "raster_kind": "direct",
        "raster_ref": raster_ref,
        "preparation_status": "ready",
        "rgb_bands": rgb_bands,
    })
    save_scene_json(project_id, scene_id, "scene_manifest", manifest)

    scene.update({
        "raster_ref": raster_ref,
        "raster_kind": "direct",
        "working_variant_id": variant_id,
        "rgb_bands": rgb_bands,
    })
    # Force scene_info + histogram to recompute for the new 3-band view.
    scene.pop("scene_info", None)
    scene.pop("scene_info_version", None)
    save_scene_json(project_id, scene_id, "scene", scene)
    _invalidate_scene_caches(project_id, scene_id)

    return {"rgb_bands": rgb_bands, "band_count": band_count, "variant_id": variant_id}


def _invalidate_scene_caches(project_id: str, scene_id: str) -> None:
    directory = scene_dir(project_id, scene_id)
    (directory / "histogram.json").unlink(missing_ok=True)
    shutil.rmtree(directory / "geo_tile_cache", ignore_errors=True)
