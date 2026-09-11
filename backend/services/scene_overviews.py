"""Source overview discovery and stable sidecar fingerprints.

The source raster remains the identity asset. Overview files are derivatives:
they are registered for display/cache coherency but never participate in the
scene identity hash.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


SOURCE_OVERVIEW_SCHEMA_VERSION = 1
DEFAULT_JP2_EXTERNAL_OVERVIEW_MIN_PIXELS = 256 * 1024 * 1024


def _env_enabled(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def source_overviews_are_display_ready(
    state: dict[str, Any],
    *,
    width: int | None = None,
    height: int | None = None,
) -> bool:
    """Whether source overviews are suitable for interactive random tile reads.

    Native JPEG2000 resolution levels are structurally valid overviews, but large
    lossless products can still require an expensive OpenJPEG code-block decode for
    every fine viewport tile.  Such products receive a project-local VRT/GTiff OVR;
    the source JP2 remains the canonical raster for export and analysis.
    """

    if not state.get("usable"):
        return False
    if str(state.get("type") or "") != "native_multiresolution":
        return True
    if not _env_enabled("GEOTILE_JP2_EXTERNAL_OVERVIEWS", True):
        return True

    resolved_width = int(width or state.get("width") or 0)
    resolved_height = int(height or state.get("height") or 0)
    if resolved_width <= 0 or resolved_height <= 0:
        # Missing dimensions are uncommon and should not silently classify a
        # potentially huge JP2 as an interactive-ready source.
        return False
    try:
        threshold = max(
            1,
            int(
                os.environ.get(
                    "GEOTILE_JP2_EXTERNAL_OVERVIEW_MIN_PIXELS",
                    str(DEFAULT_JP2_EXTERNAL_OVERVIEW_MIN_PIXELS),
                )
            ),
        )
    except ValueError:
        threshold = DEFAULT_JP2_EXTERNAL_OVERVIEW_MIN_PIXELS
    return resolved_width * resolved_height < threshold


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def source_overview_sidecar_candidates(source_path: Path) -> list[tuple[str, Path]]:
    """Return supported external overview paths in deterministic priority order."""

    source_path = Path(source_path)
    raw = str(source_path)
    candidates = [
        ("external_gtiff_ovr", Path(raw + ".ovr")),
        ("external_erdas_aux", Path(raw + ".aux")),
        ("external_erdas_rrd", Path(raw + ".rrd")),
        ("external_erdas_aux", source_path.with_suffix(".aux")),
        ("external_erdas_rrd", source_path.with_suffix(".rrd")),
    ]
    seen: set[str] = set()
    result: list[tuple[str, Path]] = []
    for artifact_type, path in candidates:
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            result.append((artifact_type, path))
    return result


def source_overview_sidecar_snapshot(source_path: Path) -> dict[str, Any]:
    """Cheap metadata fingerprint; does not open or hash the source raster."""

    artifacts: list[dict[str, Any]] = []
    for artifact_type, path in source_overview_sidecar_candidates(source_path):
        try:
            stat = path.stat()
        except OSError:
            continue
        if not path.is_file():
            continue
        artifacts.append(
            {
                "type": artifact_type,
                "path": str(path),
                "filename": path.name,
                "size": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
                "ctime_ns": int(stat.st_ctime_ns),
                "file_id": str(stat.st_ino),
            }
        )
    signature_payload = [
        {
            "type": item["type"],
            "path": item["path"],
            "filename": item["filename"],
            "size": item["size"],
            "mtime_ns": item["mtime_ns"],
            "ctime_ns": item["ctime_ns"],
            "file_id": item["file_id"],
        }
        for item in artifacts
    ]
    return {
        "schema_version": SOURCE_OVERVIEW_SCHEMA_VERSION,
        "artifacts": artifacts,
        "fingerprint": f"ovr1:{_canonical_hash(signature_payload)}",
    }


def _overview_type(driver: str, artifacts: list[dict[str, Any]], usable: bool) -> str:
    if artifacts:
        return str(artifacts[0]["type"])
    if not usable:
        return "none"
    normalized = driver.upper()
    if normalized in {"GTIFF", "COG"}:
        return "internal"
    if normalized.startswith("JP2") or "JPEG2000" in normalized:
        return "native_multiresolution"
    return "native"


def describe_open_dataset_overviews(src: Any, source_path: Path | None = None) -> dict[str, Any]:
    """Describe overview levels already exposed by an open rasterio dataset."""

    snapshot = source_overview_sidecar_snapshot(source_path) if source_path else {
        "schema_version": SOURCE_OVERVIEW_SCHEMA_VERSION,
        "artifacts": [],
        "fingerprint": f"ovr1:{_canonical_hash([])}",
    }
    factors_by_band: list[list[int]] = []
    read_error: str | None = None
    try:
        for band_index in range(1, int(getattr(src, "count", 0) or 0) + 1):
            factors_by_band.append([int(value) for value in src.overviews(band_index)])
    except Exception as exc:
        read_error = f"{type(exc).__name__}: {exc}"
    usable = bool(factors_by_band) and all(bool(factors) for factors in factors_by_band)
    driver = str(getattr(src, "driver", "") or "")
    overview_type = _overview_type(driver, snapshot["artifacts"], usable)
    factors = factors_by_band[0] if factors_by_band else []
    state_payload = {
        "type": overview_type,
        "driver": driver,
        "usable": usable,
        "factors_by_band": factors_by_band,
        "sidecar_fingerprint": snapshot["fingerprint"],
    }
    return {
        "schema_version": SOURCE_OVERVIEW_SCHEMA_VERSION,
        "type": overview_type,
        "driver": driver,
        "width": int(getattr(src, "width", 0) or 0),
        "height": int(getattr(src, "height", 0) or 0),
        "usable": usable,
        "factors": factors,
        "factors_by_band": factors_by_band,
        "sidecar_present": bool(snapshot["artifacts"]),
        "sidecar_fingerprint": snapshot["fingerprint"],
        "artifacts": snapshot["artifacts"],
        "fingerprint": f"ovrstate1:{_canonical_hash(state_payload)}",
        "read_error": read_error,
    }


def inspect_source_overviews(source_path: Path) -> dict[str, Any]:
    """Open a source read-only and return its effective source overview state."""

    import rasterio

    source_path = Path(source_path)
    try:
        with rasterio.open(source_path) as src:
            return describe_open_dataset_overviews(src, source_path)
    except Exception as exc:
        snapshot = source_overview_sidecar_snapshot(source_path)
        artifacts = snapshot["artifacts"]
        overview_type = str(artifacts[0]["type"]) if artifacts else "unreadable"
        state_payload = {
            "type": overview_type,
            "usable": False,
            "factors_by_band": [],
            "sidecar_fingerprint": snapshot["fingerprint"],
        }
        return {
            "schema_version": SOURCE_OVERVIEW_SCHEMA_VERSION,
            "type": overview_type,
            "driver": None,
            "width": 0,
            "height": 0,
            "usable": False,
            "factors": [],
            "factors_by_band": [],
            "sidecar_present": bool(artifacts),
            "sidecar_fingerprint": snapshot["fingerprint"],
            "artifacts": artifacts,
            "fingerprint": f"ovrstate1:{_canonical_hash(state_payload)}",
            "read_error": f"{type(exc).__name__}: {exc}",
        }


def apply_source_overview_metadata(
    scene_data: dict[str, Any],
    state: dict[str, Any],
    *,
    effective_type: str | None = None,
) -> dict[str, Any]:
    """Persist additive overview metadata while retaining legacy fields."""

    info = scene_data.setdefault("scene_info", {})
    info["source_overviews"] = state
    # Legacy name retained for API compatibility; historically rasterio also
    # reported external .ovr levels through this field.
    info["native_overviews"] = bool(state.get("usable"))
    display_stats = info.get("display_stats")
    if isinstance(display_stats, dict):
        display_stats["native_overview_factors"] = list(state.get("factors") or [])
    scene_data["overview_type"] = effective_type or state.get("type") or "none"
    scene_data["overview_factors"] = list(state.get("factors") or [])
    scene_data["overview_fingerprint"] = state.get("fingerprint")
    scene_data["overview_sidecar_fingerprint"] = state.get("sidecar_fingerprint")
    return scene_data
