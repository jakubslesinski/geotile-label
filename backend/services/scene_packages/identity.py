"""Content identities for logical multi-file scene products.

Fast sampled signatures are useful for discovery and duplicate candidates, but they
are not hashes of the complete file. This module keeps that heuristic evidence
separate from full SHA-256 so downstream import and lineage code can make safe choices.
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from db.storage import load_scene_json, project_dir, save_scene_json, scene_dir
from services.scene_identity import (
    compute_working_variant_fingerprint,
    is_full_sha256,
    manifest_identity_strength,
)
from services.scene_packages.base import canonical_hash
from services.scene_packages.working_view import clear_all_scene_display_overviews
from services.scene_sources import resolve_source_asset


IDENTITY_SCHEMA_VERSION = 3
SAMPLED_SIGNATURE_METHOD = "sampled_head_middle_tail_v1"
EXACT_IDENTITY_METHOD = "multi_asset_sha256_v2"


class IdentityHashCancelled(RuntimeError):
    """Raised when a caller cancels hashing mid-file."""


def sha256_file(
    path: Path,
    chunk_size: int = 8 * 1024 * 1024,
    *,
    progress: Callable[[int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> str:
    """Hash a stable file snapshot, retrying once if it changes during the read.

    ``progress`` receives the number of bytes added by each chunk, so a caller can
    show movement inside a single multi-gigabyte raster instead of only between
    scenes. ``should_cancel`` is checked per chunk: a several-GB read is the longest
    single step of package preparation and must stay interruptible. Retrying the read
    replays the progress for that file, so callers report bytes per attempt rather
    than assuming a monotonic total.
    """

    for _attempt in range(2):
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(chunk_size):
                if should_cancel is not None and should_cancel():
                    raise IdentityHashCancelled(f"Hashing cancelled: {path}")
                digest.update(chunk)
                if progress is not None:
                    progress(len(chunk))
        after = path.stat()
        if before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns:
            return digest.hexdigest()
    raise RuntimeError(f"Scene asset changed while hashing: {path}")


def sampled_content_signature(path: Path, sample_bytes: int = 8 * 1024 * 1024) -> str:
    """Return a bounded-cost size + head/middle/tail content signature.

    The ``sig1:`` prefix is intentionally not a SHA-256 digest declaration. A change
    outside the sampled ranges can remain invisible, so this value may only support a
    probable match.
    """

    size = path.stat().st_size
    digest = hashlib.sha256()
    digest.update(str(size).encode())
    with path.open("rb") as handle:
        digest.update(handle.read(sample_bytes))
        if size > 2 * sample_bytes:
            handle.seek(size // 2)
            digest.update(handle.read(sample_bytes))
        if size > sample_bytes:
            handle.seek(max(0, size - sample_bytes))
            digest.update(handle.read(sample_bytes))
    return f"sig1:{digest.hexdigest()}"


def content_signature(path: Path, sample_bytes: int = 8 * 1024 * 1024) -> str:
    """Backward-compatible alias for callers predating identity schema v2."""

    return sampled_content_signature(path, sample_bytes=sample_bytes)


def _migrate_legacy_asset_identity(asset: dict[str, Any]) -> None:
    """Move legacy sampled values out of ``sha256`` without discarding evidence."""

    legacy = asset.get("sha256")
    if str(legacy or "").startswith("sig1:"):
        asset.setdefault("content_signature", legacy)
        asset.setdefault("content_signature_method", SAMPLED_SIGNATURE_METHOD)
        asset["sha256"] = None
    elif legacy and not is_full_sha256(legacy):
        # Unknown historical values are preserved for diagnostics but may not be used
        # as either exact or sampled evidence.
        asset.setdefault("legacy_sha256_value", legacy)
        asset["sha256"] = None


def _asset_record(asset: dict[str, Any], stat_size: int) -> dict[str, Any]:
    record = {"relative_path": asset.get("relative_path"), "size": stat_size}
    if is_full_sha256(asset.get("sha256")):
        record.update({
            "identity_method": "sha256",
            "identity_strength": "exact",
            "identity_value": str(asset["sha256"]).casefold(),
        })
    else:
        record.update({
            "identity_method": asset.get("content_signature_method") or SAMPLED_SIGNATURE_METHOD,
            "identity_strength": "heuristic",
            "identity_value": asset.get("content_signature"),
        })
    return record


def _scene_record(
    asset: dict[str, Any],
    stat_size: int,
    value: str,
    method: str,
    strength: str,
) -> dict[str, Any]:
    return {
        "role": asset.get("role"),
        "part_id": asset.get("part_id"),
        "size": stat_size,
        "identity_method": method,
        "identity_strength": strength,
        "identity_value": value,
    }


def scene_identity_scope(source_package: dict[str, Any]) -> dict[str, list[str]]:
    """Return explicit measurement and defining-metadata scopes for one scene."""

    selection = source_package.get("selection") or {}
    measurement_ids = list(
        dict.fromkeys(source_package.get("identity_asset_ids") or selection.get("identity_asset_ids") or [])
    )
    metadata_ids: list[str] = list(
        dict.fromkeys(
            source_package.get("defining_metadata_asset_ids")
            or selection.get("metadata_asset_ids")
            or []
        )
    )
    for values in (selection.get("component_metadata_asset_ids") or {}).values():
        for asset_id in values or []:
            if asset_id not in metadata_ids:
                metadata_ids.append(asset_id)
    candidate_ids = list(dict.fromkeys([*measurement_ids, *metadata_ids]))
    return {
        "measurement_asset_ids": measurement_ids,
        "defining_metadata_asset_ids": metadata_ids,
        "candidate_asset_ids": candidate_ids,
    }


def _inventory_record(asset: dict[str, Any], *, size: int | None, mtime_ns: int | None, missing: bool) -> dict[str, Any]:
    return {
        "asset_id": asset.get("asset_id"),
        "relative_path": asset.get("relative_path"),
        "role": asset.get("role"),
        "asset_role": asset.get("asset_role"),
        "size": size,
        "mtime_ns": mtime_ns,
        "missing": missing,
    }


def invalidate_scene_source_derivatives(
    project_id: str,
    scene_id: str,
    *,
    manifest: dict[str, Any] | None = None,
) -> None:
    """Remove cached state that may describe obsolete selected source bytes.

    Prepared products are intentionally retained so the explicit source-change
    workflow can decide what to do with a locked variant. A virtual mosaic VRT is
    only a cheap pointer graph, however, and must not survive a changed mosaic part.
    """

    current = manifest or load_scene_json(project_id, scene_id, "scene_manifest", default={})
    working = current.get("working_view") or {}
    raster_ref = working.get("raster_ref") or {}
    if working.get("raster_kind") == "virtual_mosaic" and raster_ref.get("storage") == "project":
        root = project_dir(project_id).resolve()
        candidate = (root / str(raster_ref.get("relative_path") or "")).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            candidate = None
        if candidate is not None and candidate.suffix.casefold() == ".vrt":
            candidate.unlink(missing_ok=True)
            Path(str(candidate) + ".ovr").unlink(missing_ok=True)
            candidate.with_name(f".{candidate.stem}.partial.vrt").unlink(missing_ok=True)

    clear_all_scene_display_overviews(project_id, scene_id)
    directory = scene_dir(project_id, scene_id)
    (directory / "histogram.json").unlink(missing_ok=True)
    shutil.rmtree(directory / "geo_tile_cache", ignore_errors=True)
    for thumbnail in directory.glob("scene_thumbnail*.png"):
        thumbnail.unlink(missing_ok=True)


def compute_scene_package_identity(
    project_id: str,
    scene_id: str,
    rebuild_index: bool = True,
    *,
    require_exact: bool = False,
    progress: Callable[[int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Compute scoped scene identity and a metadata-only delivery inventory.

    The normal path samples only selected measurement and defining metadata assets.
    ``require_exact`` fully hashes every selected measurement (never browse, preview,
    or alternative products); cached hashes are reused for unchanged snapshots.

    ``progress`` and ``should_cancel`` only reach the full-hash read — the sampled
    signature is bounded to a few megabytes and needs neither.
    """

    manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
    previous_identity = dict(manifest.get("source_identity") or {})
    previous_strength = manifest_identity_strength(manifest)
    previous_candidate_uid = previous_identity.get("source_scene_candidate_uid")

    source_package = manifest.get("source_package") or {}
    source_id = source_package.get("source_id")
    assets = source_package.get("assets") or []
    scope = scene_identity_scope(source_package)
    measurement_ids = set(scope["measurement_asset_ids"])
    candidate_ids = set(scope["candidate_asset_ids"])
    inventory_records: list[dict[str, Any]] = []
    exact_scene_records: list[dict[str, Any]] = []
    candidate_scene_records: list[dict[str, Any]] = []
    primary_hash: str | None = None
    primary_signature: str | None = None
    missing_assets: list[str] = []
    missing_candidate_assets: list[str] = []

    for asset in assets:
        _migrate_legacy_asset_identity(asset)
        asset_id = str(asset.get("asset_id") or "")
        relative_path = str(asset.get("relative_path") or "")
        path = resolve_source_asset(project_id, str(source_id or ""), relative_path)
        if path is None:
            missing_assets.append(relative_path)
            if asset_id in candidate_ids:
                missing_candidate_assets.append(relative_path)
            inventory_records.append(_inventory_record(asset, size=None, mtime_ns=None, missing=True))
            continue

        stat = path.stat()
        snapshot_matches = (
            asset.get("size") == stat.st_size
            and asset.get("mtime_ns") == stat.st_mtime_ns
        )
        if not snapshot_matches:
            asset["sha256"] = None
            asset["content_signature"] = None
            asset["content_signature_method"] = None

        asset["size"] = stat.st_size
        asset["mtime_ns"] = stat.st_mtime_ns
        inventory_records.append(
            _inventory_record(asset, size=stat.st_size, mtime_ns=stat.st_mtime_ns, missing=False)
        )

        # Inventory is metadata-only. Content reads start only after this scope gate.
        if asset_id not in candidate_ids:
            continue

        if not str(asset.get("content_signature") or "").startswith("sig1:"):
            asset["content_signature"] = sampled_content_signature(path)
            asset["content_signature_method"] = SAMPLED_SIGNATURE_METHOD
        if require_exact and asset_id in measurement_ids and not is_full_sha256(asset.get("sha256")):
            asset["sha256"] = sha256_file(path, progress=progress, should_cancel=should_cancel)
            # sha256_file validates the snapshot; persist the verified stat.
            stat = path.stat()
            asset["size"] = stat.st_size
            asset["mtime_ns"] = stat.st_mtime_ns

        asset["sha256_method"] = "sha256" if is_full_sha256(asset.get("sha256")) else None
        if asset.get("content_signature"):
            asset["content_signature_method"] = (
                asset.get("content_signature_method") or SAMPLED_SIGNATURE_METHOD
            )
        asset["identity_strength"] = "exact" if is_full_sha256(asset.get("sha256")) else "heuristic"

        if asset_id in measurement_ids and is_full_sha256(asset.get("sha256")):
            exact_scene_records.append(_scene_record(
                asset,
                stat.st_size,
                str(asset["sha256"]).casefold(),
                "sha256",
                "exact",
            ))
            if len(measurement_ids) == 1:
                primary_hash = str(asset["sha256"]).casefold()
        signature = str(asset.get("content_signature") or "")
        if signature.startswith("sig1:"):
            candidate_scene_records.append(_scene_record(
                asset,
                stat.st_size,
                signature,
                asset.get("content_signature_method") or SAMPLED_SIGNATURE_METHOD,
                "heuristic",
            ))
            if len(measurement_ids) == 1 and asset_id in measurement_ids:
                primary_signature = signature

    all_measurements_exact = bool(measurement_ids) and len(exact_scene_records) == len(measurement_ids)
    all_candidates_sampled = bool(candidate_ids) and len(candidate_scene_records) == len(candidate_ids)

    delivery_inventory_fingerprint = canonical_hash(
        sorted(inventory_records, key=lambda item: str(item.get("relative_path")))
    )
    exact_scene_fingerprint = canonical_hash(
        sorted(
            exact_scene_records,
            key=lambda item: (
                str(item.get("role")),
                str(item.get("part_id")),
                str(item.get("identity_value")),
            ),
        )
    ) if all_measurements_exact else None
    candidate_scene_fingerprint = canonical_hash(
        sorted(
            candidate_scene_records,
            key=lambda item: (
                str(item.get("role")),
                str(item.get("part_id")),
                str(item.get("identity_value")),
            ),
        )
    ) if all_candidates_sampled else None

    now = datetime.now(timezone.utc).isoformat()
    common = {
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "delivery_inventory_fingerprint": delivery_inventory_fingerprint,
        "delivery_inventory_asset_count": len(inventory_records),
        "scene_candidate_fingerprint": candidate_scene_fingerprint,
        "source_package_fingerprint": delivery_inventory_fingerprint,
        "source_package_fingerprint_strength": "inventory",
        "identity_scope": scope,
        "provider_scene_id": source_package.get("provider_scene_id"),
        "computed_at": now,
    }
    if not measurement_ids or missing_candidate_assets:
        identity = {
            **common,
            "source_scene_uid": None,
            "source_scene_fingerprint": None,
            "source_scene_candidate_uid": None,
            "source_scene_candidate_fingerprint": None,
            "identity_method": None,
            "identity_strength": None,
            "status": "error",
            "error": "Scene identity assets are missing: " + ", ".join(missing_candidate_assets or missing_assets),
        }
    else:
        strength = "exact" if all_measurements_exact else "heuristic"
        identity = {
            **common,
            "source_scene_uid": (
                f"scene-sha256:{exact_scene_fingerprint}" if exact_scene_fingerprint else None
            ),
            "source_scene_fingerprint": exact_scene_fingerprint,
            "source_scene_candidate_uid": (
                f"scene-signature-v1:{candidate_scene_fingerprint}"
                if candidate_scene_fingerprint else None
            ),
            "source_scene_candidate_fingerprint": candidate_scene_fingerprint,
            "identity_method": EXACT_IDENTITY_METHOD if all_measurements_exact else SAMPLED_SIGNATURE_METHOD,
            "identity_strength": strength,
            "status": "complete",
        }
        if missing_assets:
            identity["delivery_warning"] = "Non-scene delivery assets are missing: " + ", ".join(missing_assets)

    working_view = manifest.setdefault("working_view", {})
    working_fingerprint = compute_working_variant_fingerprint(identity, working_view)
    working_view["working_variant_fingerprint"] = working_fingerprint
    identity["working_variant_fingerprint"] = working_fingerprint

    manifest["source_package"] = source_package
    manifest["source_identity"] = identity
    manifest["source_scene_uid"] = identity.get("source_scene_uid")
    manifest["source_scene_candidate_uid"] = identity.get("source_scene_candidate_uid")
    manifest["source_identity_status"] = identity.get("status")
    manifest["source_identity_method"] = identity.get("identity_method")
    manifest["source_identity_strength"] = identity.get("identity_strength")
    manifest["source_file_sha256"] = primary_hash
    manifest["source_file_content_signature"] = primary_signature
    manifest["source_file_content_signature_method"] = (
        SAMPLED_SIGNATURE_METHOD if primary_signature else None
    )
    manifest["working_variant_fingerprint"] = working_fingerprint

    scene = load_scene_json(project_id, scene_id, "scene", default={})
    previous_uid = previous_identity.get("source_scene_uid")
    changed = False
    change_strength: str | None = None
    if (
        previous_strength == "exact"
        and previous_uid
        and identity.get("source_scene_uid")
        and previous_uid != identity.get("source_scene_uid")
    ):
        changed = True
        change_strength = "exact"
        identity["previous_source_scene_uid"] = previous_uid
    elif (
        previous_candidate_uid
        and identity.get("source_scene_candidate_uid")
        and previous_candidate_uid != identity.get("source_scene_candidate_uid")
    ):
        changed = True
        change_strength = "heuristic"
        identity["previous_source_scene_candidate_uid"] = previous_candidate_uid
    if changed:
        invalidate_scene_source_derivatives(project_id, scene_id, manifest=manifest)
        identity["status"] = "changed"
        identity["change_detection_strength"] = change_strength
        manifest["source_identity"] = identity
        manifest["source_identity_status"] = "changed"
        changed_working = manifest.setdefault("working_view", {})
        changed_working["preparation_status"] = "source_changed"
        changed_working["working_grid_uid"] = None
        scene.pop("scene_info", None)
        scene.pop("scene_info_version", None)
        scene.pop("scene_info_error", None)
        scene["overview_status"] = "pending"
        scene["preparation_status"] = "source_changed"
        scene["working_grid_uid"] = None

    save_scene_json(project_id, scene_id, "scene_manifest", manifest)
    scene["source_scene_uid"] = identity.get("source_scene_uid")
    scene["source_scene_candidate_uid"] = identity.get("source_scene_candidate_uid")
    scene["source_identity_status"] = identity.get("status")
    scene["source_identity_method"] = identity.get("identity_method")
    scene["source_identity_strength"] = identity.get("identity_strength")
    scene["working_variant_fingerprint"] = working_fingerprint
    save_scene_json(project_id, scene_id, "scene", scene)

    if rebuild_index:
        from services.scene_manifest import rebuild_scenes_index

        rebuild_scenes_index(project_id)
    return identity


def ensure_exact_scene_package_identity(
    project_id: str,
    scene_id: str,
    rebuild_index: bool = True,
) -> dict[str, Any]:
    """Upgrade a package scene to exact identity, reusing valid cached hashes."""

    return compute_scene_package_identity(
        project_id,
        scene_id,
        rebuild_index=rebuild_index,
        require_exact=True,
    )
