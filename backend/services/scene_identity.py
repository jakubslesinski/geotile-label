"""Stable source-scene identity and identity-evidence classification.

``sha256`` fields are cryptographic evidence: they may contain only a complete
SHA-256 digest.  Sampled content signatures used by package scenes are deliberately
classified as heuristic evidence and live in separate fields.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCENE_IDENTITY_VERSION = 2
HASH_CHUNK_SIZE = 4 * 1024 * 1024
FULL_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def is_full_sha256(value: Any) -> bool:
    """Return whether ``value`` is a complete lowercase/uppercase SHA-256 digest."""

    return bool(FULL_SHA256_PATTERN.fullmatch(str(value or "").casefold()))


def compute_working_variant_fingerprint(
    source_identity: dict[str, Any] | None,
    working_view: dict[str, Any] | None,
) -> str | None:
    """Bind a working raster variant to source bytes and processing parameters."""

    identity = source_identity or {}
    working = working_view or {}
    exact_fingerprint = identity.get("source_scene_fingerprint")
    candidate_fingerprint = (
        identity.get("scene_candidate_fingerprint")
        or identity.get("source_scene_candidate_fingerprint")
    )
    definition = working.get("variant_definition")
    if not (exact_fingerprint or candidate_fingerprint) or not definition:
        return None
    payload = {
        "schema_version": 2,
        # Exact evidence covers every selected measurement. Candidate evidence is
        # retained alongside it because it also covers defining metadata, which is
        # intentionally outside the expensive full-hash lineage scope.
        "source_identity": {
            "measurement_exact_fingerprint": exact_fingerprint,
            "scene_candidate_fingerprint": candidate_fingerprint,
        },
        "variant_definition": definition,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def manifest_identity_strength(manifest: dict[str, Any] | None) -> str | None:
    """Classify identity evidence without trusting legacy field names.

    Older package manifests could store ``sig1:*`` in ``sha256``.  Therefore a
    ``scene-sha256:*`` prefix or a field named ``sha256`` is not sufficient evidence
    on its own.  Explicit v2 declarations are accepted only together with a full
    fingerprint; legacy single-file/NITF manifests are exact when their file digest is
    a real 64-hex SHA-256.  Legacy package assets are inspected to distinguish full
    hashes from sampled signatures.
    """

    value = manifest or {}
    identity = value.get("source_identity") if isinstance(value.get("source_identity"), dict) else {}
    declared = identity.get("identity_strength") or value.get("source_identity_strength")

    if is_full_sha256(value.get("source_file_sha256")):
        return "exact"
    if (
        declared == "exact"
        and is_full_sha256(
            identity.get("source_scene_fingerprint") or value.get("source_scene_fingerprint")
        )
        and (identity.get("identity_method") or value.get("source_identity_method"))
        in {"sha256", "multi_asset_sha256_v1", "multi_asset_sha256_v2"}
    ):
        return "exact"

    source_package = value.get("source_package") if isinstance(value.get("source_package"), dict) else {}
    identity_ids = set(source_package.get("identity_asset_ids") or [])
    identity_assets = [
        asset
        for asset in (source_package.get("assets") or [])
        if isinstance(asset, dict) and asset.get("asset_id") in identity_ids
    ]
    if identity_assets:
        if all(is_full_sha256(asset.get("sha256")) for asset in identity_assets):
            return "exact"
        if all(
            str(asset.get("content_signature") or asset.get("sha256") or "").startswith("sig1:")
            for asset in identity_assets
        ):
            return "heuristic"

    if declared == "heuristic":
        return "heuristic"
    if (
        identity.get("source_scene_candidate_uid")
        or value.get("source_scene_candidate_uid")
        or value.get("source_file_content_signature")
    ):
        return "heuristic"
    return None


def identity_is_exact(manifest: dict[str, Any] | None) -> bool:
    """Whether a manifest carries cryptographically exact source identity evidence."""

    return manifest_identity_strength(manifest) == "exact"


def compute_file_sha256(path: str | Path) -> tuple[str, int, int]:
    """Hash a stable file snapshot and return digest, size and mtime_ns."""

    source_path = Path(path)
    for _attempt in range(2):
        before = source_path.stat()
        digest = hashlib.sha256()
        with source_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(HASH_CHUNK_SIZE), b""):
                digest.update(chunk)
        after = source_path.stat()
        if (
            before.st_size == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns
        ):
            return digest.hexdigest(), after.st_size, after.st_mtime_ns
    raise RuntimeError(f"Scene file changed while hashing: {source_path}")


def build_scene_identity(
    scene_path: str | Path | None,
    filename: str,
    *,
    cached_manifest: dict[str, Any] | None = None,
    fallback_file_size: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Build or reuse identity fields stored at the scene-manifest root."""

    cached = cached_manifest or {}
    if scene_path is None:
        return _identity_without_source(filename, cached, fallback_file_size)

    source_path = Path(scene_path)
    if not source_path.is_file():
        identity = _identity_without_source(filename, cached, fallback_file_size)
        identity["source_identity_status"] = "missing"
        identity["source_identity_error"] = f"Scene file not found: {source_path}"
        return identity

    try:
        stat = source_path.stat()
        if not force and _cache_matches(cached, source_path.name, stat.st_size, stat.st_mtime_ns):
            return {
                **_cached_identity(cached),
                "source_filename": source_path.name,
                "source_file_size": stat.st_size,
                "source_file_mtime_ns": stat.st_mtime_ns,
                "source_identity_status": "complete",
                "source_identity_error": None,
            }

        digest, file_size, mtime_ns = compute_file_sha256(source_path)
        return {
            "source_scene_uid": f"sha256:{digest}",
            "source_file_sha256": digest,
            "source_file_size": file_size,
            "source_file_mtime_ns": mtime_ns,
            "source_filename": source_path.name,
            "source_identity_version": SCENE_IDENTITY_VERSION,
            "source_identity_method": "sha256",
            "source_identity_strength": "exact",
            "source_identity_status": "complete",
            "source_identity_computed_at": datetime.now(timezone.utc).isoformat(),
            "source_identity_error": None,
        }
    except (OSError, RuntimeError) as exc:
        identity = _identity_without_source(filename, cached, fallback_file_size)
        identity["source_identity_status"] = "error"
        identity["source_identity_error"] = str(exc)
        return identity


def identity_is_complete(manifest: dict[str, Any] | None) -> bool:
    value = manifest or {}
    package_identity = value.get("source_identity") or {}
    if package_identity:
        if package_identity.get("status") != "complete":
            return False
        strength = manifest_identity_strength(value)
        if strength == "exact":
            return bool(
                package_identity.get("source_scene_uid")
                and package_identity.get("source_scene_fingerprint")
            )
        if strength == "heuristic":
            return bool(
                package_identity.get("source_scene_candidate_uid")
                or package_identity.get("source_scene_uid")  # legacy sig1-derived UID
            )
        return False
    return bool(
        value.get("source_identity_status") == "complete"
        and value.get("source_scene_uid")
        and is_full_sha256(value.get("source_file_sha256"))
    )


def _cache_matches(
    cached: dict[str, Any],
    filename: str,
    file_size: int,
    mtime_ns: int,
) -> bool:
    return bool(
        identity_is_complete(cached)
        and cached.get("source_filename") == filename
        and cached.get("source_file_size") == file_size
        and cached.get("source_file_mtime_ns") == mtime_ns
    )


def _cached_identity(cached: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "source_scene_uid",
        "source_file_sha256",
        "source_file_size",
        "source_file_mtime_ns",
        "source_filename",
        "source_identity_version",
        "source_identity_method",
        "source_identity_strength",
        "source_identity_status",
        "source_identity_computed_at",
        "source_identity_error",
    )
    result = {key: cached.get(key) for key in keys}
    result["source_identity_version"] = SCENE_IDENTITY_VERSION
    result["source_identity_method"] = result.get("source_identity_method") or "sha256"
    result["source_identity_strength"] = manifest_identity_strength(cached)
    return result


def _identity_without_source(
    filename: str,
    cached: dict[str, Any],
    fallback_file_size: int | None,
) -> dict[str, Any]:
    if identity_is_complete(cached):
        return _cached_identity(cached)
    return {
        "source_scene_uid": None,
        "source_file_sha256": None,
        "source_file_size": cached.get("source_file_size", fallback_file_size),
        "source_file_mtime_ns": cached.get("source_file_mtime_ns"),
        "source_filename": cached.get("source_filename") or filename,
        "source_identity_version": SCENE_IDENTITY_VERSION,
        "source_identity_method": "sha256",
        "source_identity_strength": None,
        "source_identity_status": "pending",
        "source_identity_computed_at": cached.get("source_identity_computed_at"),
        "source_identity_error": None,
    }
