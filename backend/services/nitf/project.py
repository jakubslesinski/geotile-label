"""Wire NITF ingest into GeoTile Label project/scene storage.

Creates a scene from a ``.ntf`` file: materializes the sensor working raster into
project storage, persists a scene manifest carrying the GCP-TPS ``geometry`` block
(so M3 geo derivation uses TPS), and registers the working raster as the scene's
``direct`` raster so ``SceneRasterResolver`` serves it.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.storage import APP_VERSION, project_dir, save_scene_json, scene_dir
from services.nitf.ingest import ingest_nitf
from services.scene_loader import SCENE_INFO_VERSION, get_scene_info
from services.scene_manifest import SCENE_MANIFEST_VERSION, scene_summary_from_manifest

NITF_EXTENSIONS = {".ntf", ".nitf"}


def scan_nitf_folder(folder: Path) -> list[tuple[Path, str | None]]:
    """Return ``(nitf_path, package_id)`` for a folder of .ntf files or of
    package subdirectories each containing .ntf files."""
    results: list[tuple[Path, str | None]] = []
    for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if entry.is_file() and entry.suffix.lower() in NITF_EXTENSIONS:
            results.append((entry, None))
    for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if entry.is_dir():
            for nitf in sorted(entry.iterdir(), key=lambda p: p.name.lower()):
                if nitf.is_file() and nitf.suffix.lower() in NITF_EXTENSIONS:
                    results.append((nitf, entry.name))
    return results


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _footprint_bbox(geometry: dict[str, Any]) -> list[float] | None:
    footprint = geometry.get("footprint_wgs84") or []
    if not footprint:
        return None
    lons = [float(point[0]) for point in footprint]
    lats = [float(point[1]) for point in footprint]
    return [min(lons), min(lats), max(lons), max(lats)]


def build_nitf_scene_manifest(
    project_id: str,
    scene_id: str,
    ingest_manifest: dict[str, Any],
    working_ref: dict[str, Any],
    scene_info: dict[str, Any],
    *,
    package_id: str | None = None,
) -> dict[str, Any]:
    sha = ingest_manifest.get("source_file_sha256") or ""
    source_scene_uid = f"nitf-sha256:{sha}" if sha else None
    variant_identity = source_scene_uid or f"nitf:{scene_id}"
    variant_id = "nitf_" + hashlib.sha256(variant_identity.encode("utf-8")).hexdigest()[:16]
    geometry = ingest_manifest.get("geometry") or {}
    now = _utc_now()
    image = ingest_manifest.get("image") or {}
    return {
        "schema_name": "geotile_scene_manifest",
        "schema_version": SCENE_MANIFEST_VERSION,
        "app_version": APP_VERSION,
        "created_at": now,
        "updated_at": now,
        "project_id": project_id,
        "scene_id": scene_id,
        "filename": ingest_manifest.get("filename"),
        "source_path": ingest_manifest.get("source_path"),
        "source_exists": True,
        "source_scene_uid": source_scene_uid,
        "source_identity_status": "complete" if sha else "pending",
        "source_identity_method": "sha256" if sha else None,
        "source_identity_strength": "exact" if sha else None,
        "source_identity_version": 2,
        "source_file_sha256": sha or None,
        "source_file_size": scene_info.get("file_size"),
        "modality": "AERIAL_EO",
        "georeferencing": "SENSOR_GEO",
        "sensor": ingest_manifest.get("sensor"),
        "provider": ingest_manifest.get("provider"),
        "acquisition_datetime_utc": ingest_manifest.get("acquisition_datetime_utc"),
        # B1: identity / provenance surfaced for dataset filtering and the scene list.
        "display_name": ingest_manifest.get("display_name"),
        "production_datetime_utc": ingest_manifest.get("production_datetime_utc"),
        "mission_id": ingest_manifest.get("mission_id"),
        "flight_no": ingest_manifest.get("flight_no"),
        "scene_number": ingest_manifest.get("scene_number"),
        "target_area_id": ingest_manifest.get("target_area_id"),
        "platform_altitude_m": ingest_manifest.get("platform_altitude_m"),
        "focal_length_mm": ingest_manifest.get("focal_length_mm"),
        # B5: approximate ground sample distance.
        "gsd_m": ingest_manifest.get("gsd_m"),
        "gsd_col_m": ingest_manifest.get("gsd_col_m"),
        "gsd_row_m": ingest_manifest.get("gsd_row_m"),
        "gsd_approximate": ingest_manifest.get("gsd_approximate", True),
        "metadata_status": "ok",
        "package_id": package_id,
        "image": {
            **image,  # width/height/bands/dtype/abpp/block/irep/icat/fbkgc + B3 radiometry
            "channels": image.get("bands"),
            "file_size": scene_info.get("file_size"),
            "color_interpretation": scene_info.get("color_interpretation") or [],
            "data_band_indexes": scene_info.get("data_band_indexes") or [],
            "native_overviews": scene_info.get("native_overviews"),
        },
        "spectral": {
            "layout": scene_info.get("spectral_layout"),
            "processing": scene_info.get("spectral_processing"),
            "classification_source": scene_info.get("classification_source"),
            "classification_confidence": scene_info.get("classification_confidence"),
        },
        # No affine transform for sensor geometry; geo lives in `geometry`.
        "geospatial": {"has_geo": False, "bounds_wgs84": _footprint_bbox(geometry)},
        "geometry": geometry,
        "metadata": ingest_manifest.get("metadata"),
        "display": {
            "profile_version": scene_info.get("display_profile_version"),
            "display_min": scene_info.get("display_min"),
            "display_max": scene_info.get("display_max"),
            "display_mode": scene_info.get("display_mode"),
            "stats": scene_info.get("display_stats"),
        },
        "sar": None,
        "eo": None,
        "source_package": None,
        # No package identity for NITF — use file-based identity (top-level
        # source_identity_status/uid/sha256), which identity_is_complete accepts.
        # A source_identity block here would require a fingerprint we don't compute
        # and would make identity_is_complete() return False everywhere.
        "source_identity": None,
        "working_view": {
            "variant_id": variant_id,
            "raster_kind": "direct",
            "raster_ref": working_ref,
            "working_grid_uid": None,
            "preparation_status": "ready",
            "locked": True,  # sensor raster is authoritative; not reprocessed
            "locked_at": now,
            "lock_reason": "nitf_sensor",
            "processing_manifest": None,
        },
        "profile_validation": {"status": "ok", "warnings": []},
    }


def create_nitf_scene(
    project_id: str,
    scene_id: str,
    source_path: str | Path,
    *,
    package_id: str | None = None,
) -> dict[str, Any]:
    """Ingest one NITF file into project storage and persist scene + manifest."""
    workspace = scene_dir(project_id, scene_id)
    workspace.mkdir(parents=True, exist_ok=True)
    result = ingest_nitf(source_path, workspace)

    working_path = Path(result.working_raster)
    relative = working_path.resolve().relative_to(project_dir(project_id).resolve()).as_posix()
    working_ref = {"storage": "project", "relative_path": relative}

    scene_info = get_scene_info(
        working_path,
        modality="AERIAL_EO",
        product_type="PAN",
    ).model_dump()
    # The AERIAL_EO/PAN context already selects a linear p2-p98 profile from the
    # bounded characterization sample; do not decode the scene again for a histogram.
    manifest = build_nitf_scene_manifest(
        project_id, scene_id, result.manifest, working_ref, scene_info, package_id=package_id
    )
    save_scene_json(project_id, scene_id, "scene_manifest", manifest)

    summary = scene_summary_from_manifest(manifest)
    scene_record = {
        "id": scene_id,
        "filename": manifest.get("filename"),
        "raster_kind": "direct",
        "modality": "AERIAL_EO",
        "georeferencing": "SENSOR_GEO",
        "package_id": package_id,
        "preparation_status": "ready",
        "status": "pending",
        # Persist scene_info AND its version now so the tile-render path treats the
        # scene as up-to-date and stays read-only. Without the version, every tile
        # recomputes + rewrites scene.json, which races under the render thread pool
        # (Windows os.replace WinError 5) and thrashes the render-context cache.
        # Shared version prevents drift between importers and the tile router.
        "scene_info": scene_info,
        "scene_info_version": SCENE_INFO_VERSION,
        **summary,
        # Override after summary: NITF has no source_package, so surface the
        # image id (IID1) and the approximate GSD for the scene list.
        "display_name": manifest.get("display_name") or manifest.get("filename"),
        "gsd_m": manifest.get("gsd_m"),
    }
    save_scene_json(project_id, scene_id, "scene", scene_record)
    save_scene_json(project_id, scene_id, "annotations", [])
    return scene_record
