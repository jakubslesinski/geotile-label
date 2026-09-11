from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from models.dataset_config import DatasetConfig
from services.tile_similarity import pair_descriptions, scan_cross_split_near_duplicates

SAFE_SPLIT_MODES = {
    "scene_split",
    "image_block_split",
    "spatial_block_split",
    "class_balanced_spatial",
}
SPATIAL_SPLIT_MODES = {"spatial_block_split", "class_balanced_spatial"}


def validate_dataset_split(
    dataset_dir: str | Path,
    config: DatasetConfig,
    project_profile: dict[str, Any],
    scene_manifests: dict[str, dict[str, Any]],
    tile_annotation_links: list[dict[str, Any]],
) -> dict[str, Any]:
    split_map = dataset_split_map(Path(dataset_dir))
    issues: list[dict[str, Any]] = []
    split_counts = {
        split: sum(1 for assigned in split_map.values() if assigned == split)
        for split in ("train", "val", "test")
    }

    expected_ratios = {
        "train": config.train_ratio,
        "val": config.val_ratio,
        "test": config.test_ratio,
    }
    empty_splits = [
        split for split, ratio in expected_ratios.items()
        if ratio > 0 and split_counts[split] == 0
    ]
    if empty_splits:
        issues.append(issue(
            "empty_splits",
            "warning",
            f"Expected splits without images: {', '.join(empty_splits)}.",
            count=len(empty_splits),
            values=empty_splits,
        ))

    project_georeferencing = project_profile.get("georeferencing")
    scene_georeferencing = {
        scene_id: manifest.get("georeferencing")
        or ("GEO" if (manifest.get("geospatial") or {}).get("has_geo") else "NO_GEO")
        for scene_id, manifest in scene_manifests.items()
    }
    non_geo_scenes = sorted(
        scene_id for scene_id, value in scene_georeferencing.items() if value != "GEO"
    )
    if config.split_mode in SPATIAL_SPLIT_MODES and (
        project_georeferencing == "NO_GEO" or non_geo_scenes
    ):
        issues.append(issue(
            "spatial_split_without_georeferencing",
            "warning",
            "Spatial split selected for a NO_GEO project or scenes; affected scenes use image-block fallback.",
            count=len(non_geo_scenes),
            values=non_geo_scenes,
        ))

    if config.split_mode == "image_block_split" and project_georeferencing == "GEO":
        issues.append(issue(
            "image_split_for_geo_project",
            "warning",
            "GEO project uses image_block_split; spatial_block_split better protects overlapping geographic areas.",
        ))

    if config.split_mode == "random_tile":
        issues.append(issue(
            "random_tile_leakage_risk",
            "warning",
            "random_tile can place neighboring or overlapping tiles in different splits.",
        ))

    source_splits: dict[str, set[str]] = defaultdict(set)
    for link in tile_annotation_links:
        filename = link.get("dataset_tile_filename")
        source_id = link.get("source_annotation_id")
        assigned_split = split_map.get(str(filename)) if filename else None
        if source_id and assigned_split:
            source_splits[str(source_id)].add(assigned_split)
    leaking_sources = sorted(
        source_id for source_id, splits in source_splits.items() if len(splits) > 1
    )
    if leaking_sources:
        severity = "error" if config.split_mode in SAFE_SPLIT_MODES else "warning"
        issues.append(issue(
            "cross_split_source_annotations",
            severity,
            f"{len(leaking_sources)} source annotations occur in more than one split.",
            count=len(leaking_sources),
            values=leaking_sources[:100],
        ))

    near_duplicates = scan_cross_split_near_duplicates(dataset_dir)
    if near_duplicates["cross_split_pair_count"]:
        issues.append(issue(
            "near_duplicate_tiles",
            "error",
            (
                f"Detected {near_duplicates['cross_split_pair_count']} visually similar "
                "tile pairs in different splits."
            ),
            count=near_duplicates["cross_split_pair_count"],
            values=pair_descriptions(near_duplicates),
        ))
    if near_duplicates["scan_failure_count"]:
        issues.append(issue(
            "near_duplicate_scan_errors",
            "warning",
            f"Could not fingerprint {near_duplicates['scan_failure_count']} tiles.",
            count=near_duplicates["scan_failure_count"],
            values=near_duplicates["scan_failures"],
        ))
    if near_duplicates["candidate_scan_truncated"]:
        issues.append(issue(
            "near_duplicate_scan_truncated",
            "warning",
            "Similarity candidate buckets were capped to keep validation bounded on a large dataset.",
        ))

    errors = [item["message"] for item in issues if item["severity"] == "error"]
    warnings = [item["message"] for item in issues if item["severity"] == "warning"]
    return {
        "schema_name": "geotile_dataset_validation_report",
        "schema_version": 1,
        "status": "error" if errors else "warning" if warnings else "ok",
        "split_mode": config.split_mode,
        "split_seed": config.split_seed,
        "split_counts": split_counts,
        "empty_splits": empty_splits,
        "cross_split_source_annotation_count": len(leaking_sources),
        "cross_split_source_annotation_ids": leaking_sources[:100],
        "near_duplicate_summary": near_duplicates,
        "issues": issues,
        "errors": errors,
        "warnings": warnings,
        "checks": {
            "split_mode_compatibility": "complete",
            "empty_splits": "complete",
            "source_annotation_leakage": "complete",
            "near_duplicate_tiles": (
                "partial"
                if near_duplicates["scan_failure_count"] or near_duplicates["candidate_scan_truncated"]
                else "complete"
            ),
        },
    }


def dataset_split_map(dataset_dir: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for split in ("train", "val", "test"):
        images_dir = dataset_dir / split / "images"
        if not images_dir.exists():
            continue
        for image_path in images_dir.iterdir():
            if image_path.is_file():
                result[image_path.name] = split
    return result


def issue(
    code: str,
    severity: str,
    message: str,
    count: int | None = None,
    values: list[str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "code": code,
        "severity": severity,
        "message": message,
    }
    if count is not None:
        result["count"] = count
    if values is not None:
        result["values"] = values
    return result
