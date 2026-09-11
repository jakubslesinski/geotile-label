"""Canonical scene manifest helpers.

The manifest is intentionally lightweight in this first implementation:
it normalizes the metadata already available in ``scene_info`` and adds
stable fields required by later dataset sidecars.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from db.storage import (
    APP_VERSION,
    list_scene_ids,
    load_json,
    load_scene_json,
    project_dir,
    save_json,
    save_scene_json,
)
from services.attribute_engine import recompute_scene_attributes
from services.metadata_parser import parse_scene_metadata
from services.scene_identity import build_scene_identity
from services.scene_name_metadata import infer_scene_name_metadata
from services.scene_sources import resolve_source_asset
from services.scene_packages.radiometry import describe as describe_radiometry
from services.scene_packages.working_view import working_grid_uid

SCENE_MANIFEST_VERSION = 6


def write_scene_manifest(
    project_id: str,
    scene_id: str,
    project_data: dict[str, Any],
    scene_data: dict[str, Any],
    scene_path: Path | None = None,
    *,
    force_identity: bool = False,
) -> dict[str, Any]:
    """Build and save ``scene_manifest.json`` for a scene."""

    existing_manifest = load_scene_json(
        project_id, scene_id, "scene_manifest", default={}
    )
    manifest = build_scene_manifest(
        project_id,
        scene_id,
        project_data,
        scene_data,
        scene_path,
        existing_manifest=existing_manifest,
        force_identity=force_identity,
    )
    save_scene_json(project_id, scene_id, "scene_manifest", manifest)
    recompute_scene_attributes(project_id, scene_id)

    summary = scene_summary_from_manifest(manifest)
    changed = False
    for key, value in summary.items():
        if scene_data.get(key) != value:
            scene_data[key] = value
            changed = True
    if changed:
        save_scene_json(project_id, scene_id, "scene", scene_data)
    return manifest


def build_scene_manifest(
    project_id: str,
    scene_id: str,
    project_data: dict[str, Any],
    scene_data: dict[str, Any],
    scene_path: Path | None = None,
    *,
    existing_manifest: dict[str, Any] | None = None,
    force_identity: bool = False,
) -> dict[str, Any]:
    existing_manifest = existing_manifest or {}
    scene_info = scene_data.get("scene_info") or {}
    # NITF sensor scenes carry a GCP-TPS geometry block + NITF metadata the generic
    # builder does not know about. Never rebuild over them: the generic path would
    # drop `geometry`/`metadata` and flip georeferencing to GEO. Keep the manifest,
    # only refreshing volatile identity/timestamp fields.
    if (existing_manifest.get("geometry") or {}).get("model") == "gcp_tps":
        manifest = dict(existing_manifest)
        manifest["schema_version"] = SCENE_MANIFEST_VERSION
        manifest["app_version"] = APP_VERSION
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        if scene_path is not None:
            manifest["source_exists"] = scene_path.exists()
        if scene_info:
            image = manifest.setdefault("image", {})
            image["color_interpretation"] = scene_info.get("color_interpretation") or []
            image["data_band_indexes"] = scene_info.get("data_band_indexes") or []
            image["native_overviews"] = scene_info.get("native_overviews")
            image["source_overviews"] = scene_info.get("source_overviews")
            manifest["spectral"] = {
                "layout": scene_info.get("spectral_layout"),
                "processing": scene_info.get("spectral_processing"),
                "classification_source": scene_info.get("classification_source"),
                "classification_confidence": scene_info.get("classification_confidence"),
            }
            display = manifest.setdefault("display", {})
            display["profile_version"] = scene_info.get("display_profile_version")
            display["stats"] = scene_info.get("display_stats")
            display["overview_type"] = scene_data.get("overview_type")
            display["overview_factors"] = scene_data.get("overview_factors") or []
            display["overview_fingerprint"] = scene_data.get("overview_fingerprint")
        return manifest
    profile = project_data.get("profile") or {}
    filename = scene_data.get("filename", "")
    # P1.7: nazwa jest analizowana raz i wynik jest współdzielony przez sensor,
    # modalność, datę oraz późniejszą charakterystykę spektralną.
    name_metadata = scene_info.get("name_metadata") or infer_scene_name_metadata(
        filename,
        profile.get("sensors") or [],
    )
    sensor = name_metadata.get("sensor")
    acquisition_datetime = name_metadata.get("acquisition_datetime_utc")
    has_scene_info = bool(scene_info)
    has_geo = bool(scene_info.get("has_geo")) if has_scene_info else profile.get("georeferencing") == "GEO"
    source_package = existing_manifest.get("source_package") or scene_data.get("source_package")
    working_view = existing_manifest.get("working_view") or scene_data.get("working_view") or {}
    metadata_files = _package_metadata_files(project_id, source_package)
    if not metadata_files:
        metadata_files = find_metadata_sidecars(scene_path) if scene_path else []
    provider_metadata = parse_scene_metadata(scene_path, metadata_files, filename)
    metadata_conflicts = (provider_metadata.get("parser_diagnostics") or {}).get("metadata_conflicts") or []
    if source_package and metadata_conflicts:
        resolver_diagnostics = source_package.setdefault("resolver", {}).setdefault(
            "diagnostics", {"warnings": [], "errors": [], "metadata_conflicts": []}
        )
        resolver_diagnostics["metadata_conflicts"] = metadata_conflicts
    sensor = provider_metadata.get("sensor") or sensor
    acquisition_datetime = provider_metadata.get("acquisition_datetime_utc") or acquisition_datetime
    provider_mismatch = _provider_mismatch(source_package, provider_metadata)
    modality = provider_metadata.get("modality") or profile.get("modality") or name_metadata.get("modality")

    metadata_status = "ok" if has_scene_info else "missing_scene_info"
    if has_geo and (not scene_info.get("crs") or not scene_info.get("transform")):
        metadata_status = "partial_georeferencing"
    if has_scene_info and metadata_status == "ok":
        metadata_status = provider_metadata.get("metadata_status") or metadata_status

    now = datetime.now(timezone.utc).isoformat()
    source_identity = existing_manifest.get("source_identity") or scene_data.get("source_identity")
    if source_package:
        source_identity = source_identity or {
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
            "provider_scene_id": source_package.get("provider_scene_id"),
            "status": "pending",
            "computed_at": None,
        }
        identity = {
            "source_scene_uid": source_identity.get("source_scene_uid"),
            "source_scene_candidate_uid": source_identity.get("source_scene_candidate_uid"),
            "source_identity_status": source_identity.get("status", "pending"),
            "source_identity_method": source_identity.get("identity_method"),
            "source_identity_strength": source_identity.get("identity_strength"),
            "source_file_sha256": existing_manifest.get("source_file_sha256"),
            "source_file_content_signature": existing_manifest.get("source_file_content_signature"),
            "source_file_content_signature_method": existing_manifest.get("source_file_content_signature_method"),
            "source_file_size": scene_info.get("file_size"),
        }
    else:
        identity = build_scene_identity(
            scene_path,
            filename,
            cached_manifest=existing_manifest,
            fallback_file_size=scene_info.get("file_size"),
            force=force_identity,
        )

    if working_view and scene_info:
        variant_id = working_view.get("variant_id")
        working_view["working_grid_uid"] = working_view.get("working_grid_uid") or working_grid_uid(
            scene_info,
            variant_id,
            (working_view.get("processing_manifest") or {}).get("parameters"),
        )

    manifest = {
        "schema_name": "geotile_scene_manifest",
        "schema_version": SCENE_MANIFEST_VERSION,
        "app_version": APP_VERSION,
        "created_at": existing_manifest.get("created_at") or now,
        "updated_at": now,
        "project_id": project_id,
        "scene_id": scene_id,
        "filename": filename,
        "source_path": None if source_package else (str(scene_path.resolve(strict=False)) if scene_path else None),
        "source_exists": bool(scene_path and scene_path.exists()),
        **identity,
        "modality": modality,
        "georeferencing": "GEO" if has_geo else "NO_GEO",
        "sensor": sensor,
        "provider": (source_package or {}).get("provider") or provider_metadata.get("provider"),
        "acquisition_datetime_utc": acquisition_datetime,
        "metadata_status": metadata_status,
        "metadata_files": metadata_files,
        "metadata_sources": provider_metadata.get("metadata_sources") or [],
        "parser": {
            "name": provider_metadata.get("parser_name"),
            "version": provider_metadata.get("parser_version"),
            "diagnostics": provider_metadata.get("parser_diagnostics") or {"warnings": [], "errors": []},
        },
        "acquisition": {
            "datetime_utc": acquisition_datetime,
            "source": "sidecar" if provider_metadata.get("acquisition_datetime_utc") else "filename",
        },
        "sar": provider_metadata.get("sar"),
        "eo": provider_metadata.get("eo"),
        # P0.5: dostawca ZADEKLAROWANY przy dodawaniu zrodla i dostawca WYKRYTY z metadanych
        # to dwie rozne rzeczy (sekcja 3.4). Gdy sie roznia, manifest mowi o tym wprost —
        # zamiast po cichu zapisac PHR jako Pleiades Neo.
        "provider_mismatch": provider_mismatch,
        # P1.5: czym SA wartosci pikseli. Blok jest addytywny i nigdy nie zgaduje —
        # brak deklaracji dostawcy daje `unknown`, co jest osobnym stanem, a nie „surowe DN".
        "radiometry": describe_radiometry(
            modality,
            provider_metadata,
            scene_info,
            working_view,
            (source_package or {}).get("selection"),
        ),
        # GSD top-level: metadane EO mają priorytet, w razie braku — z transformu (scene_info).
        # Zasila filtry gsd_min/max, normalizację GSD przy buildzie i stratyfikację po GSD.
        "gsd_m": (provider_metadata.get("eo") or {}).get("gsd_m") or scene_info.get("gsd_m"),
        "image": {
            "width": scene_info.get("width"),
            "height": scene_info.get("height"),
            "channels": scene_info.get("channels"),
            "dtype": scene_info.get("dtype"),
            "file_size": scene_info.get("file_size"),
            "color_interpretation": scene_info.get("color_interpretation") or [],
            "data_band_indexes": scene_info.get("data_band_indexes") or [],
            "nodata": scene_info.get("nodata"),
            "mask_flags": scene_info.get("mask_flags") or [],
            "native_overviews": scene_info.get("native_overviews"),
            "source_overviews": scene_info.get("source_overviews"),
        },
        "spectral": {
            "layout": scene_info.get("spectral_layout"),
            "processing": (source_package or {}).get("spectral_processing") or scene_info.get("spectral_processing"),
            "classification_source": scene_info.get("classification_source"),
            "classification_confidence": scene_info.get("classification_confidence"),
        },
        "geospatial": {
            "has_geo": has_geo,
            "crs": scene_info.get("crs"),
            "crs_proj4": scene_info.get("crs_proj4"),
            "transform": scene_info.get("transform"),
            "bounds_wgs84": scene_info.get("bounds"),
        },
        "display": {
            "profile_version": scene_info.get("display_profile_version"),
            "display_min": scene_info.get("display_min"),
            "display_max": scene_info.get("display_max"),
            "display_mode": scene_info.get("display_mode"),
            "stats": scene_info.get("display_stats"),
            "overview_type": scene_data.get("overview_type"),
            "overview_factors": scene_data.get("overview_factors") or [],
            "overview_fingerprint": scene_data.get("overview_fingerprint"),
        },
        "source_package": source_package,
        "source_identity": source_identity,
        "working_view": working_view or None,
    }
    manifest["profile_validation"] = validate_scene_against_profile(manifest, profile)
    return manifest


#: Misja wykryta w metadanych → dostawca, ktorego deklaracji sie spodziewamy.
_EXPECTED_PROVIDER_FOR_MISSION = {"PNEO": "pleiades_neo", "PHR": "pleiades_neo"}


def _provider_mismatch(
    source_package: dict[str, Any] | None,
    provider_metadata: dict[str, Any],
) -> dict[str, Any] | None:
    """Czy sensor wykryty w metadanych zaprzecza deklaracji zrodla.

    Dotyczy przede wszystkim Airbusa: oba produkty przychodza jako DIMAP i obsluguje je ten
    sam resolver, ale `PHR1A`/`PHR1B` to NIE jest Pleiades Neo. Bez tego pola dostawa PHR
    zapisywalaby sie jako PNEO bez sladu, ze cokolwiek sie nie zgadza (sekcja 4.5).
    """
    declared = str((source_package or {}).get("provider") or "")
    if not declared:
        return None
    mission = str(((provider_metadata.get("eo") or {}).get("mission") or "")).upper()
    if not mission:
        return None
    expected = _EXPECTED_PROVIDER_FOR_MISSION.get(mission)
    detected_sensor = provider_metadata.get("sensor")
    if expected is None or declared != expected:
        return None
    # Deklaracja i misja pasuja do tego samego resolvera; sprzecznosc jest dopiero wtedy,
    # gdy misja to PHR, a uzytkownik zadeklarowal zrodlo Pleiades NEO.
    if declared == "pleiades_neo" and mission == "PHR":
        return {
            "declared_provider": declared,
            "detected_mission": mission,
            "detected_sensor": detected_sensor,
            "code": "phr_declared_as_pneo",
            "message": (
                f"Źródło zadeklarowano jako Pleiades Neo, a metadane opisują {detected_sensor}"
                " (misja PHR). Sensor i dostawca sceny pochodzą z różnych źródeł."
            ),
        }
    return None


def refresh_scene_identity(
    project_id: str,
    scene_id: str,
    project_data: dict[str, Any],
    scene_data: dict[str, Any],
    scene_path: Path,
    *,
    force: bool = False,
    progress: Callable[[int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Refresh only source identity when the rest of the manifest is current.

    ``progress``/``should_cancel`` reach only the packaged-scene branch, which is the
    one that can spend minutes hashing multi-gigabyte measurement assets.
    """

    manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
    if not manifest:
        return write_scene_manifest(
            project_id,
            scene_id,
            project_data,
            scene_data,
            scene_path,
            force_identity=force,
        )

    if manifest.get("source_package"):
        from services.scene_packages.identity import compute_scene_package_identity

        compute_scene_package_identity(
            project_id,
            scene_id,
            require_exact=force,
            progress=progress,
            should_cancel=should_cancel,
        )
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        summary = scene_summary_from_manifest(manifest)
        if any(scene_data.get(key) != value for key, value in summary.items()):
            scene_data.update(summary)
            save_scene_json(project_id, scene_id, "scene", scene_data)
        recompute_scene_attributes(project_id, scene_id)
        return manifest

    identity = build_scene_identity(
        scene_path,
        scene_data.get("filename", ""),
        cached_manifest=manifest,
        fallback_file_size=(scene_data.get("scene_info") or {}).get("file_size"),
        force=force,
    )
    manifest.update(identity)
    manifest["schema_name"] = "geotile_scene_manifest"
    manifest["schema_version"] = SCENE_MANIFEST_VERSION
    manifest["app_version"] = APP_VERSION
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    manifest["source_path"] = str(scene_path.resolve(strict=False))
    manifest["source_exists"] = scene_path.exists()
    save_scene_json(project_id, scene_id, "scene_manifest", manifest)

    summary = scene_summary_from_manifest(manifest)
    if any(scene_data.get(key) != value for key, value in summary.items()):
        scene_data.update(summary)
        save_scene_json(project_id, scene_id, "scene", scene_data)
    recompute_scene_attributes(project_id, scene_id)
    return manifest


def rebuild_scenes_index(project_id: str) -> dict[str, Any]:
    """Create project-level ``scenes_index.json`` from scene manifests."""

    project_data = load_json(project_id, "project", default={})
    entries: list[dict[str, Any]] = []
    for scene_id in list_scene_ids(project_id):
        scene_data = load_scene_json(project_id, scene_id, "scene", default={})
        if not scene_data:
            continue
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default=None)
        if not manifest:
            manifest = build_scene_manifest(
                project_id,
                scene_id,
                project_data,
                scene_data,
                None,
            )
            save_scene_json(project_id, scene_id, "scene_manifest", manifest)
        summary = scene_summary_from_manifest(manifest)
        if any(scene_data.get(key) != value for key, value in summary.items()):
            scene_data.update(summary)
            save_scene_json(project_id, scene_id, "scene", scene_data)
        entries.append(scene_index_entry(manifest, scene_data))

    entries.sort(key=lambda item: item.get("filename") or "")
    index = {
        "schema_name": "geotile_scenes_index",
        "schema_version": SCENE_MANIFEST_VERSION,
        "project_id": project_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "scene_count": len(entries),
        "scenes": entries,
    }
    save_json(project_id, "scenes_index", index)
    return index


def scenes_index_is_fresh(project_id: str) -> bool:
    """Czy `scenes_index.json` jest nie starszy niż wszystkie pliki scen (DESIGN_DECISIONS.md, performance-audit E8b).

    `rebuild_scenes_index` czyta i parsuje `scene.json` + `scene_manifest.json` KAŻDEJ sceny, więc na
    dużym projekcie kosztuje sekundy (zmierzone: DOTA, 2423 sceny → 5,1 s) i na ścieżce `list_scenes`
    powtarza pracę, którą przejście równoległe właśnie wykonało. Ten test kosztuje tylko 2N `stat`
    (zmierzone: 510 ms na tym samym projekcie), więc pozwala pominąć przebudowę, gdy nic się nie zmieniło.

    Zwraca False „w razie wątpliwości" — brak indeksu, brak któregokolwiek pliku sceny (wtedy
    przebudowa musi dobudować manifest) albo błąd odczytu. Rozbieżność manifest↔scene.json nie może
    się tu ukryć: gdyby istniała, jej źródłem byłby zapis nowszy od indeksu, a wtedy test i tak
    zwraca False. Wołać PO ewentualnych zapisach do scen w tym samym żądaniu, nie przed.
    """
    root = project_dir(project_id)
    index_path = root / "scenes_index.json"
    try:
        index_mtime = index_path.stat().st_mtime
    except OSError:
        return False

    for scene_id in list_scene_ids(project_id):
        scene_dir_path = root / "scenes" / scene_id
        for name in ("scene.json", "scene_manifest.json"):
            try:
                if scene_dir_path.joinpath(name).stat().st_mtime > index_mtime:
                    return False
            except OSError:
                return False  # brakujący manifest = przebudowa ma go dobudować
    return True


def scene_index_entry(manifest: dict[str, Any], scene_data: dict[str, Any]) -> dict[str, Any]:
    image = manifest.get("image") or {}
    geospatial = manifest.get("geospatial") or {}
    source_package = manifest.get("source_package") or {}
    working_view = manifest.get("working_view") or {}
    summary = scene_summary_from_manifest(manifest)
    return {
        "scene_id": manifest.get("scene_id"),
        "source_scene_uid": manifest.get("source_scene_uid"),
        "source_scene_candidate_uid": manifest.get("source_scene_candidate_uid"),
        "source_identity_status": manifest.get("source_identity_status"),
        "source_identity_method": manifest.get("source_identity_method"),
        "source_identity_strength": manifest.get("source_identity_strength"),
        "source_file_sha256": manifest.get("source_file_sha256"),
        "source_file_content_signature": manifest.get("source_file_content_signature"),
        "source_file_content_signature_method": manifest.get("source_file_content_signature_method"),
        "source_file_size": manifest.get("source_file_size"),
        "filename": manifest.get("filename") or scene_data.get("filename"),
        "display_name": scene_data.get("display_name") or source_package.get("provider_scene_id") or manifest.get("filename"),
        "source_id": source_package.get("source_id"),
        "package_id": source_package.get("package_id"),
        "provider_scene_id": source_package.get("provider_scene_id"),
        "product_type": summary.get("product_type"),
        "raster_format": summary.get("raster_format"),
        "raster_part_count": summary.get("raster_part_count"),
        "gsd_m": summary.get("gsd_m"),
        "raster_kind": working_view.get("raster_kind"),
        "working_variant_id": working_view.get("variant_id"),
        "working_variant_fingerprint": working_view.get("working_variant_fingerprint"),
        "working_grid_uid": working_view.get("working_grid_uid"),
        "working_asset_locked": bool(working_view.get("locked")),
        "preparation_status": working_view.get("preparation_status", "ready"),
        "modality": manifest.get("modality"),
        "georeferencing": manifest.get("georeferencing"),
        "sensor": manifest.get("sensor"),
        "acquisition_datetime_utc": manifest.get("acquisition_datetime_utc"),
        "metadata_status": manifest.get("metadata_status"),
        "provider": manifest.get("provider"),
        "profile_warning_count": len((manifest.get("profile_validation") or {}).get("warnings") or []),
        "width": image.get("width"),
        "height": image.get("height"),
        "channels": image.get("channels"),
        "dtype": image.get("dtype"),
        "has_geo": geospatial.get("has_geo"),
        "crs": geospatial.get("crs"),
        "bbox_lonlat": geospatial.get("bounds_wgs84"),
        "status": scene_data.get("status"),
        "overview_status": scene_data.get("overview_status"),
        "overview_type": scene_data.get("overview_type"),
        "overview_factors": scene_data.get("overview_factors") or [],
        "overview_fingerprint": scene_data.get("overview_fingerprint"),
        "overview_sidecar_fingerprint": scene_data.get("overview_sidecar_fingerprint"),
        "overview_changed_at": scene_data.get("overview_changed_at"),
        "manifest_schema_version": manifest.get("schema_version"),
        "scene_info": scene_data.get("scene_info"),
        "created_at": scene_data.get("created_at"),
        "review_status": scene_data.get("review_status", "none"),
        "review_comment": scene_data.get("review_comment"),
        "reviewed_by": scene_data.get("reviewed_by"),
        "reviewed_at": scene_data.get("reviewed_at"),
        "review_round": scene_data.get("review_round"),
        "review_pins": scene_data.get("review_pins") or [],
        "annotation_count": scene_data.get("annotation_count", 0),
        "tile_count": scene_data.get("tile_count", 0),
    }


def scene_summary_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    profile_warning_count = len((manifest.get("profile_validation") or {}).get("warnings") or [])
    source_package = manifest.get("source_package") or {}
    source_identity = manifest.get("source_identity") or {}
    working_view = manifest.get("working_view") or {}
    selected_ids = set(source_package.get("identity_asset_ids") or [])
    selected_assets = [
        asset for asset in source_package.get("assets") or []
        if asset.get("asset_id") in selected_ids
    ]
    formats = sorted({str(asset.get("format")) for asset in selected_assets if asset.get("format")})
    sar = manifest.get("sar") or {}
    eo = manifest.get("eo") or {}
    gsd_m = (
        eo.get("gsd_m")
        or sar.get("pixel_spacing_m")
        or sar.get("pixel_spacing_row_m")
        or sar.get("pixel_spacing_col_m")
    )
    return {
        "manifest_schema_version": manifest.get("schema_version"),
        "source_scene_uid": source_identity.get("source_scene_uid") or manifest.get("source_scene_uid"),
        "source_scene_candidate_uid": source_identity.get("source_scene_candidate_uid") or manifest.get("source_scene_candidate_uid"),
        "source_identity_status": source_identity.get("status") or manifest.get("source_identity_status"),
        "source_identity_method": source_identity.get("identity_method") or manifest.get("source_identity_method"),
        "source_identity_strength": source_identity.get("identity_strength") or manifest.get("source_identity_strength"),
        "display_name": source_package.get("provider_scene_id") or manifest.get("filename"),
        "source_id": source_package.get("source_id"),
        "package_id": source_package.get("package_id"),
        "provider_scene_id": source_package.get("provider_scene_id"),
        "product_type": source_package.get("product_type"),
        "raster_format": "+".join(formats) if formats else None,
        "raster_part_count": len(selected_assets) if selected_assets else None,
        "gsd_m": gsd_m,
        "raster_ref": working_view.get("raster_ref"),
        "raster_kind": working_view.get("raster_kind"),
        "working_variant_id": working_view.get("variant_id"),
        "working_variant_fingerprint": working_view.get("working_variant_fingerprint"),
        "working_grid_uid": working_view.get("working_grid_uid"),
        "working_asset_locked": bool(working_view.get("locked")),
        "preparation_status": working_view.get("preparation_status", "ready"),
        "modality": manifest.get("modality"),
        "georeferencing": manifest.get("georeferencing"),
        "sensor": manifest.get("sensor"),
        "provider": manifest.get("provider"),
        "acquisition_datetime_utc": manifest.get("acquisition_datetime_utc"),
        "metadata_status": manifest.get("metadata_status"),
        "profile_warning_count": profile_warning_count,
    }


def _package_metadata_files(project_id: str, source_package: dict[str, Any] | None) -> list[str]:
    """Pliki metadanych do sparsowania dla tej sceny.

    Domyslnie: KAZDY asset o roli `metadata` w pakiecie. Dla pakietu odpowiadajacego jednemu
    produktowi to jest poprawne, ale przy pakiecie scalonym oznacza, ze do jednej sceny trafia
    metadane wszystkich pozostalych (sekcja 4.3 roadmapy: 611 sidecarow, 914 konfliktow,
    `product_level=VID` na produkcie GRD).

    Jesli resolver zwiazal metadane z wybranym produktem (`selection.metadata_asset_ids`,
    P0.3), uzywamy WYLACZNIE tej listy — plaska pula pakietu nie jest juz brana pod uwage.

    Gdy wiazanie nie znalazlo niczego, wolajacy siega po `find_metadata_sidecars()`. To NIE
    przywraca usterki z sekcji 4.3: tamto scalenie brało cala pule pakietu, a `find_metadata_
    sidecars()` dopasowuje po trzonie nazwy w katalogu samego rastra, wiec nie moze wciagnac
    sidecara innego produktu.
    """
    if not source_package:
        return []
    source_id = source_package.get("source_id")
    selection = source_package.get("selection") or {}
    bound_ids = selection.get("metadata_asset_ids")
    result: list[str] = []
    for asset in source_package.get("assets") or []:
        if bound_ids is not None:
            if asset.get("asset_id") not in set(bound_ids):
                continue
        elif asset.get("role") != "metadata":
            continue
        path = resolve_source_asset(
            project_id,
            str(source_id or ""),
            str(asset.get("relative_path") or ""),
        )
        if path:
            result.append(str(path))
    return result


def validate_scene_against_profile(manifest: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    warnings: list[dict[str, str]] = []
    profile_modality = profile.get("modality")
    scene_modality = manifest.get("modality")
    if profile_modality and scene_modality and profile_modality != scene_modality:
        warnings.append({
            "code": "modality_mismatch",
            "severity": "warning",
            "message": f"Project modality is {profile_modality}, but scene metadata indicates {scene_modality}.",
        })

    profile_georeferencing = profile.get("georeferencing")
    scene_georeferencing = manifest.get("georeferencing")
    if profile_georeferencing and scene_georeferencing and profile_georeferencing != scene_georeferencing:
        warnings.append({
            "code": "georeferencing_mismatch",
            "severity": "warning",
            "message": (
                f"Project georeferencing is {profile_georeferencing}, "
                f"but scene metadata indicates {scene_georeferencing}."
            ),
        })

    selected_sensors = [sensor for sensor in profile.get("sensors") or [] if sensor and sensor != "Other"]
    scene_sensor = manifest.get("sensor")
    if selected_sensors and scene_sensor and scene_sensor not in selected_sensors:
        warnings.append({
            "code": "sensor_not_selected",
            "severity": "warning",
            "message": f"Scene sensor {scene_sensor} is not selected in the project profile.",
        })

    metadata_status = manifest.get("metadata_status")
    if metadata_status not in {None, "ok"}:
        warnings.append({
            "code": "metadata_not_complete",
            "severity": "info",
            "message": f"Scene metadata status is {metadata_status}.",
        })

    parser_diagnostics = ((manifest.get("parser") or {}).get("diagnostics") or {})
    for message in parser_diagnostics.get("errors") or []:
        warnings.append({
            "code": "metadata_parser_error",
            "severity": "warning",
            "message": str(message),
        })

    return {
        "status": "warning" if warnings else "ok",
        "warnings": warnings,
    }


def infer_sensor(filename: str, selected_sensors: list[str]) -> str | None:
    return infer_scene_name_metadata(filename, selected_sensors).get("sensor")


def infer_modality(filename: str, sensor: str | None) -> str:
    metadata = infer_scene_name_metadata(filename, [sensor] if sensor else [])
    return str(metadata.get("modality") or "EO")


def infer_acquisition_datetime(filename: str) -> str | None:
    return infer_scene_name_metadata(filename).get("acquisition_datetime_utc")


def find_metadata_sidecars(scene_path: Path) -> list[str]:
    if not scene_path.exists():
        return []

    metadata_extensions = {".json", ".xml", ".dim", ".imd", ".txt", ".rpb", ".til"}
    stem_upper = scene_path.stem.upper()
    normalized_stem = normalize_sidecar_stem(stem_upper)
    parent = scene_path.parent
    matches: list[str] = []

    for candidate in parent.iterdir():
        if not candidate.is_file() or candidate.suffix.lower() not in metadata_extensions:
            continue
        name_upper = candidate.name.upper()
        candidate_stem = candidate.stem.upper()
        normalized_candidate = normalize_sidecar_stem(candidate_stem)
        if sidecar_stems_match(stem_upper, normalized_stem, candidate_stem, normalized_candidate, name_upper):
            matches.append(str(candidate.resolve(strict=False)))

    return sorted(set(matches))


def normalize_sidecar_stem(stem: str) -> str:
    normalized = re.sub(r"_R\d+C\d+", "", stem.upper())
    normalized = re.sub(r"^(IMG|DIM)[_-]", "", normalized)
    normalized = re.sub(r"[-_](TIL|RPB|XML|IMD|AUX|METADATA)$", "", normalized)
    return normalized


def sidecar_stems_match(
    scene_stem: str,
    normalized_scene_stem: str,
    candidate_stem: str,
    normalized_candidate_stem: str,
    candidate_name: str,
) -> bool:
    if candidate_stem == scene_stem or normalized_candidate_stem == normalized_scene_stem:
        return True
    if scene_stem in candidate_stem or candidate_stem in scene_stem:
        return True
    if normalized_scene_stem in normalized_candidate_stem or normalized_candidate_stem in normalized_scene_stem:
        return True

    generic_metadata = "METADATA" in candidate_name or candidate_name.startswith("DIM_")
    if not generic_metadata:
        return False

    scene_key = sidecar_match_key(normalized_scene_stem)
    candidate_key = sidecar_match_key(normalized_candidate_stem)
    return bool(scene_key and candidate_key and (scene_key.startswith(candidate_key) or candidate_key.startswith(scene_key)))


def sidecar_match_key(stem: str) -> str:
    key = re.sub(r"(METADATA|DIM|IMG|AUX|TIL|RPB|XML|IMD|GEC|GEO|ORTHO|PAN|MS)", "", stem.upper())
    return re.sub(r"[^A-Z0-9]", "", key)
