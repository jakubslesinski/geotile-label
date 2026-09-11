"""Single dispatch point for every scene raster read."""

from __future__ import annotations

import logging
import re
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.storage import SCENES_ROOT, load_json, load_scene_json, project_dir, save_scene_json, scene_dir
from services.scene_sources import resolve_source_asset
from services.scene_packages.working_view import (
    build_working_variant_definition,
    working_variant_id,
)
from services.scene_overviews import (
    SOURCE_OVERVIEW_SCHEMA_VERSION,
    apply_source_overview_metadata,
    inspect_source_overviews,
    source_overviews_are_display_ready,
    source_overview_sidecar_snapshot,
)
from utils.scene_paths import resolve_scene_file


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RasterHandle:
    path: Path
    raster_kind: str
    raster_ref: dict[str, Any]
    working_variant_id: str | None = None


_OVERVIEW_SYNC_TTL_SECONDS = 2.0
_OVERVIEW_SYNC_CACHE_MAX = 4096
_overview_sync_guard = threading.Lock()
_overview_sync_checked_at: dict[tuple[str, str], float] = {}
_overview_scene_locks: dict[tuple[str, str], threading.Lock] = {}


def _overview_scene_lock(project_id: str, scene_id: str) -> threading.Lock:
    key = (project_id, scene_id)
    with _overview_sync_guard:
        lock = _overview_scene_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _overview_scene_locks[key] = lock
        return lock


def _overview_sync_due(project_id: str, scene_id: str, force: bool) -> bool:
    if force:
        return True
    with _overview_sync_guard:
        checked_at = _overview_sync_checked_at.get((project_id, scene_id), 0.0)
    return time.monotonic() - checked_at >= _OVERVIEW_SYNC_TTL_SECONDS


def _mark_overview_sync_checked(project_id: str, scene_id: str) -> None:
    key = (project_id, scene_id)
    with _overview_sync_guard:
        _overview_sync_checked_at[key] = time.monotonic()
        if len(_overview_sync_checked_at) > _OVERVIEW_SYNC_CACHE_MAX:
            oldest = min(_overview_sync_checked_at, key=_overview_sync_checked_at.get)
            _overview_sync_checked_at.pop(oldest, None)


def invalidate_scene_render_caches(project_id: str, scene_id: str) -> None:
    """Remove regenerable render products after the effective overview changes."""

    directory = scene_dir(project_id, scene_id)
    (directory / "histogram.json").unlink(missing_ok=True)
    shutil.rmtree(directory / "geo_tile_cache", ignore_errors=True)
    for thumbnail in directory.glob("scene_thumbnail*.png"):
        thumbnail.unlink(missing_ok=True)


def _display_overview_status(
    project_id: str,
    scene_id: str,
    scene: dict[str, Any],
    state: dict[str, Any],
    variant_id: str | None,
) -> tuple[str, str]:
    """Return effective status/type for the interactive display pyramid."""

    info = scene.get("scene_info") or {}
    if source_overviews_are_display_ready(
        state,
        width=info.get("width"),
        height=info.get("height"),
    ):
        return "native", str(state.get("type") or "native")

    from services.scene_packages.working_view import direct_overview_vrt

    if direct_overview_vrt(project_id, scene_id, variant_id) is not None:
        return "ready", "project_vrt_ovr"
    if state.get("sidecar_present") and state.get("read_error"):
        return "error", str(state.get("type") or "unreadable")
    return "pending", str(state.get("type") or "none")


def sync_scene_source_overviews(
    project_id: str,
    scene_id: str,
    scene_data: dict[str, Any] | None = None,
    *,
    force: bool = False,
) -> tuple[dict[str, Any], bool]:
    """Detect source sidecar changes and keep persisted display state coherent.

    The common path performs only sidecar ``stat`` calls and is TTL-deduplicated
    across concurrent tile requests. The raster is opened only when the cheap
    sidecar fingerprint differs from the persisted fingerprint.
    """

    if not _overview_sync_due(project_id, scene_id, force):
        return scene_data or load_scene_json(project_id, scene_id, "scene", default={}), False

    lock = _overview_scene_lock(project_id, scene_id)
    with lock:
        if not _overview_sync_due(project_id, scene_id, force):
            return scene_data or load_scene_json(project_id, scene_id, "scene", default={}), False
        scene = scene_data or load_scene_json(project_id, scene_id, "scene", default={})
        if not scene:
            _mark_overview_sync_checked(project_id, scene_id)
            return scene, False
        try:
            handle = SceneRasterResolver.resolve(project_id, scene_id)
        except FileNotFoundError:
            _mark_overview_sync_checked(project_id, scene_id)
            return scene, False
        if handle.raster_kind != "direct":
            _mark_overview_sync_checked(project_id, scene_id)
            return scene, False

        previous = ((scene.get("scene_info") or {}).get("source_overviews") or {})
        snapshot = source_overview_sidecar_snapshot(handle.path)
        if (
            previous.get("schema_version") == SOURCE_OVERVIEW_SCHEMA_VERSION
            and previous.get("sidecar_fingerprint") == snapshot.get("fingerprint")
        ):
            desired_status, _effective_type = _display_overview_status(
                project_id,
                scene_id,
                scene,
                previous,
                handle.working_variant_id,
            )
            # Do not trample a live job state, but migrate old ``native`` JP2
            # scenes to ``pending`` as soon as they are touched.
            if (
                scene.get("overview_status") not in {"queued", "building"}
                and scene.get("overview_status") != desired_status
            ):
                scene["overview_status"] = desired_status
                save_scene_json(project_id, scene_id, "scene", scene)
            _mark_overview_sync_checked(project_id, scene_id)
            return scene, False

        current = inspect_source_overviews(handle.path)
        changed = previous.get("fingerprint") != current.get("fingerprint")
        if not changed:
            _mark_overview_sync_checked(project_id, scene_id)
            return scene, False

        from services.scene_packages.working_view import (
            clear_all_scene_display_overviews,
        )

        if current.get("usable") and str(current.get("type") or "").startswith("external_"):
            # Prefer a reusable source-adjacent pyramid and remove the redundant
            # project-local VRT overview, without ever touching the source.
            clear_all_scene_display_overviews(project_id, scene_id)

        invalidate_scene_render_caches(project_id, scene_id)
        status, effective_type = _display_overview_status(
            project_id,
            scene_id,
            scene,
            current,
            handle.working_variant_id,
        )

        apply_source_overview_metadata(scene, current, effective_type=effective_type)
        scene["overview_status"] = status
        scene["overview_changed_at"] = datetime.now(timezone.utc).isoformat()
        save_scene_json(project_id, scene_id, "scene", scene)
        logger.info(
            "Scene overview state changed project=%s scene=%s type=%s factors=%s sidecar=%s",
            project_id,
            scene_id,
            scene.get("overview_type"),
            scene.get("overview_factors") or [],
            bool(current.get("sidecar_present")),
        )

        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        if manifest:
            image = manifest.setdefault("image", {})
            image["native_overviews"] = bool(current.get("usable"))
            image["source_overviews"] = current
            display = manifest.setdefault("display", {})
            display["overview_type"] = scene.get("overview_type")
            display["overview_factors"] = scene.get("overview_factors") or []
            display["overview_fingerprint"] = scene.get("overview_fingerprint")
            manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
            save_scene_json(project_id, scene_id, "scene_manifest", manifest)

        _mark_overview_sync_checked(project_id, scene_id)
        return scene, True


class SceneRasterResolver:
    @staticmethod
    def resolve(project_id: str, scene_id: str) -> RasterHandle:
        project = load_json(project_id, "project", default={})
        scene = load_scene_json(project_id, scene_id, "scene", default={})
        if not scene:
            raise FileNotFoundError("Scene not found")

        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        working = manifest.get("working_view") or {}
        raster_ref = working.get("raster_ref") or scene.get("raster_ref")
        raster_kind = working.get("raster_kind") or scene.get("raster_kind")
        variant_id = working.get("variant_id") or scene.get("working_variant_id")

        if isinstance(raster_ref, dict):
            path = SceneRasterResolver._resolve_ref(project_id, raster_ref)
            if path is not None:
                return RasterHandle(path, raster_kind or "direct", raster_ref, variant_id)

        if scene.get("source_id") or manifest.get("source_package"):
            status = working.get("preparation_status") or scene.get("preparation_status") or "not_ready"
            raise FileNotFoundError(
                f"Managed scene raster is not available (status: {status}): "
                f"{scene.get('display_name') or scene.get('filename') or scene_id}"
            )

        legacy = resolve_scene_file(
            SCENES_ROOT,
            project.get("scene_folder", ""),
            scene.get("filename", ""),
        )
        if legacy is not None:
            return RasterHandle(
                legacy,
                "direct",
                {"storage": "legacy", "scene_folder": project.get("scene_folder", ""), "filename": scene.get("filename", "")},
                variant_id,
            )
        raise FileNotFoundError(f"Scene raster not found: {scene.get('filename') or scene_id}")

    @staticmethod
    def _resolve_ref(project_id: str, raster_ref: dict[str, Any]) -> Path | None:
        storage = raster_ref.get("storage")
        relative = str(raster_ref.get("relative_path") or "")
        if storage == "source":
            return resolve_source_asset(project_id, str(raster_ref.get("source_id") or ""), relative)
        if storage == "project":
            root = project_dir(project_id).resolve(strict=False)
            path = (root / Path(relative)).resolve(strict=False)
            try:
                path.relative_to(root)
            except ValueError:
                return None
            return path if path.is_file() else None
        return None


def resolve_scene_raster(project_id: str, scene_id: str) -> Path:
    return SceneRasterResolver.resolve(project_id, scene_id).path


def resolve_display_read_path(project_id: str, scene_id: str) -> Path:
    """Ścieżka odczytu **dla kafli wyświetlania** — VRT z piramidą, jeśli zbudowany,
    inaczej źródło.

    Przekierowanie confined do serwowania kafli (DESIGN_DECISIONS.md, tile-serving P1). Tożsamość,
    predykcja i eksport wołają `resolve_scene_raster` i czytają dalej źródło — piramida
    to tylko decymacje tych samych pikseli, więc przyspiesza wyświetlanie bez wpływu na
    poprawność tamtych ścieżek.
    """
    from services.scene_packages.working_view import (
        DISPLAY_OVERVIEW_RASTER_KINDS,
        direct_overview_vrt,
        direct_preview_asset,
    )

    handle = SceneRasterResolver.resolve(project_id, scene_id)
    if handle.raster_kind in DISPLAY_OVERVIEW_RASTER_KINDS:
        # Pelnorozdzielczy derywat ma pierwszenstwo: obsluguje 1x, czego VRT nad JP2 nie
        # potrafi. `.ovr` ZOSTAJE jako fallback (R1.5) — jest potrzebny, gdy derywat
        # zostanie uniewazniony, jest odbudowywany albo okaze sie uszkodzony.
        from services.scene_packages.fullres_cog_builder import published_fullres_cog

        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={}) or {}
        identity = manifest.get("source_identity") or {}
        source_fingerprint = identity.get("source_scene_fingerprint") or identity.get(
            "source_package_fingerprint"
        )
        cog = published_fullres_cog(
            project_dir(project_id),
            scene_id,
            handle.working_variant_id,
            source_fingerprint=source_fingerprint,
        )
        if cog is not None:
            return cog
        preview = direct_preview_asset(project_id, scene_id, handle.working_variant_id)
        if preview is not None:
            return preview.path
        vrt = direct_overview_vrt(project_id, scene_id, handle.working_variant_id)
        if vrt is not None:
            return vrt
    return handle.path


def ensure_scene_display_overviews(
    project_id: str,
    scene_id: str,
    cancel_check=None,
    profile: dict[str, Any] | None = None,
) -> Path | None:
    """Zbuduj piramidę wyświetlania dla gotowej sceny direct bez overviews.

    Idempotentne i best-effort; woła się w tle po imporcie. Zwraca ścieżkę VRT albo
    None (scena nie-direct, ma już overviews, albo GDAL niedostępny).
    """
    from services.scene_packages.working_view import (
        DISPLAY_OVERVIEW_RASTER_KINDS,
        build_direct_overviews,
    )

    handle = SceneRasterResolver.resolve(project_id, scene_id)
    if handle.raster_kind not in DISPLAY_OVERVIEW_RASTER_KINDS:
        return None
    return build_direct_overviews(
        project_id,
        scene_id,
        handle.path,
        handle.working_variant_id,
        cancel_check,
        profile,
    )


def repair_blacksky_auxiliary_selection(project_id: str, scene_id: str, scene: dict[str, Any]) -> dict[str, Any]:
    manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
    package = manifest.get("source_package") or {}
    working = manifest.get("working_view") or {}
    selection = package.get("selection") or {}
    selected_ids = set(selection.get("asset_ids") or package.get("identity_asset_ids") or [])
    assets = package.get("assets") or []
    selected = [asset for asset in assets if asset.get("asset_id") in selected_ids]
    if len(selected) != 1:
        return scene

    selected_name = str(selected[0].get("package_relative_path") or "")
    if not re.search(r"_ortho-(?:mask|pan)\.(?:tif|tiff)$", selected_name, re.IGNORECASE):
        return scene
    if working.get("locked") or load_scene_json(project_id, scene_id, "annotations", default=[]):
        return scene

    is_blacksky = (
        str(scene.get("sensor") or manifest.get("sensor") or "").casefold() == "blacksky"
        or str(package.get("provider_scene_id") or "").upper().startswith("BSG-")
    )
    if not is_blacksky:
        return scene

    candidates = [
        asset for asset in assets
        if re.search(
            r"_ortho\.(?:tif|tiff)$",
            str(asset.get("package_relative_path") or ""),
            re.IGNORECASE,
        )
    ]
    if len(candidates) != 1:
        return scene

    asset = candidates[0]
    asset_id = asset.get("asset_id")
    source_id = package.get("source_id")
    if not asset_id or not source_id:
        return scene

    selection.update({
        "asset_ids": [asset_id],
        "identity_asset_ids": [asset_id],
        "product_type": "ORTHO_RGB",
        "status": "ready",
        "raster_kind": "direct",
        "rgb_bands": [1, 2, 3],
        "selected_by": "resolver_repair",
    })
    diagnostics = selection.setdefault("diagnostics", {"warnings": [], "errors": [], "metadata_conflicts": []})
    diagnostics.setdefault("warnings", []).append({
        "code": "auxiliary_asset_replaced",
        "message": "BlackSky auxiliary mask or panchromatic asset was replaced with the RGB ortho product.",
    })
    package["product_type"] = "ORTHO_RGB"
    package["identity_asset_ids"] = [asset_id]
    package.setdefault("selection_provenance", {}).update({
        "selected_by": "resolver_repair",
        "selected_at": datetime.now(timezone.utc).isoformat(),
    })

    variant_definition = build_working_variant_definition(selection)
    variant_id = working_variant_id(variant_definition)
    raster_ref = {"storage": "source", "source_id": source_id, "relative_path": asset["relative_path"]}
    manifest["source_identity"] = {
        "schema_version": 3,
        "delivery_inventory_fingerprint": None,
        "scene_candidate_fingerprint": None,
        "source_scene_uid": None,
        "source_scene_fingerprint": None,
        "source_scene_candidate_uid": None,
        "source_scene_candidate_fingerprint": None,
        "source_package_fingerprint": None,
        "source_package_fingerprint_strength": None,
        "working_variant_fingerprint": None,
        "identity_method": None,
        "identity_strength": None,
        "provider_scene_id": package.get("provider_scene_id"),
        "status": "pending",
        "computed_at": None,
    }
    manifest["working_view"] = {
        "variant_id": variant_id,
        "variant_definition": variant_definition,
        "working_variant_fingerprint": None,
        "raster_kind": "direct",
        "raster_ref": raster_ref,
        "working_grid_uid": None,
        "preparation_status": "ready",
        "locked": False,
        "locked_at": None,
        "lock_reason": None,
        "processing_manifest": None,
    }
    save_scene_json(project_id, scene_id, "scene_manifest", manifest)

    scene.update({
        "filename": Path(str(asset["relative_path"])).name,
        "product_type": "ORTHO_RGB",
        "raster_ref": raster_ref,
        "raster_kind": "direct",
        "working_variant_id": variant_id,
        "working_variant_fingerprint": None,
        "working_grid_uid": None,
        "preparation_status": "ready",
        "source_identity_status": "pending",
        "source_scene_uid": None,
    })
    scene.pop("scene_info", None)
    scene.pop("scene_info_version", None)
    scene.pop("scene_info_error", None)
    save_scene_json(project_id, scene_id, "scene", scene)
    shutil.rmtree(scene_dir(project_id, scene_id) / "geo_tile_cache", ignore_errors=True)
    return scene
