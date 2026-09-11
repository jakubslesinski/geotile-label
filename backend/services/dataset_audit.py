"""Quality-control audit for a versioned GeoTile Label dataset run."""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models.project import APP_VERSION
from services.tile_similarity import pair_descriptions, scan_cross_split_near_duplicates

AUDIT_SCHEMA_VERSION = 1

CORE_ARTIFACTS = {
    "dataset_run_manifest": "dataset_run_manifest.json",
    "dataset_stats": "dataset_stats.json",
    "split_manifest": "split_manifest.json",
    "tile_annotations": "tile_annotations.json",
    "tile_annotation_links": "tile_annotation_links.json",
    "source_annotations": "source_annotations.json",
    "scene_manifests": "scene_manifests.json",
    "preprocessing_profile": "preprocessing_profile.json",
    "split_validation": "validation_report.json",
}

EXPORT_SIDECARS = {
    "export_manifest": "geotile_export_manifest.json",
    "tile_metadata": "metadata/tile_metadata.csv",
    "tile_metadata_parquet": "metadata/tile_metadata.parquet",
    "annotation_links": "metadata/annotation_links.csv",
    "annotation_links_parquet": "metadata/annotation_links.parquet",
    "scenes_parquet": "metadata/scenes.parquet",
    "annotations_wgs84": "metadata/annotations_wgs84.geoparquet",
    "annotations_native": "metadata/annotations_native.geoparquet",
}

AUDIT_CSV_FIELDS = [
    "check_id",
    "category",
    "status",
    "title",
    "message",
    "count",
]


def generate_dataset_audit(
    project_id: str,
    dataset_dir: str | Path,
    *,
    persist: bool = True,
    sidecar_dir: str | Path | None = None,
    persist_dir: str | Path | None = None,
) -> dict[str, Any]:
    run_dir = Path(dataset_dir)
    sidecar_root = Path(sidecar_dir) if sidecar_dir is not None else run_dir
    run_manifest = read_json(run_dir / "dataset_run_manifest.json", {})
    stats = read_json(run_dir / "dataset_stats.json", {})
    split_validation = read_json(run_dir / "validation_report.json", {})
    source_annotations_by_scene = read_json(run_dir / "source_annotations.json", {})
    scene_manifests = read_json(run_dir / "scene_manifests.json", {})
    tiles = read_json(run_dir / "tiles.json", [])
    tile_annotations = read_json(run_dir / "tile_annotations.json", {})
    split_manifest = read_json(run_dir / "split_manifest.json", {})
    tile_link_manifest = read_json(run_dir / "tile_annotation_links.json", {})
    tile_links = (
        tile_link_manifest.get("annotations", [])
        if isinstance(tile_link_manifest, dict)
        else []
    )
    export_manifest = read_json(sidecar_root / "geotile_export_manifest.json", {})

    source_annotations = flatten_source_annotations(source_annotations_by_scene)
    checks: list[dict[str, Any]] = []

    add_artifact_checks(checks, run_dir)
    add_split_checks(checks, split_validation)
    near_duplicate_summary = add_near_duplicate_checks(checks, run_dir)
    review_summary = add_review_grid_checks(
        checks,
        stats,
        tiles,
        tile_annotations,
        split_manifest,
        run_manifest,
    )
    add_class_and_scene_checks(checks, stats)
    annotation_summary = add_annotation_checks(checks, source_annotations, tile_links)
    metadata_summary = add_scene_metadata_checks(checks, scene_manifests)
    add_acquisition_split_checks(checks, scene_manifests, tiles, split_manifest)
    add_spatial_adjacency_checks(checks, tiles, split_manifest, scene_manifests)
    sidecar_summary = add_sidecar_checks(checks, sidecar_root, export_manifest)
    add_preprocessing_check(checks, run_manifest)

    distributions = build_distributions(
        stats,
        scene_manifests,
        run_manifest.get("preprocessing_profile") or {},
    )
    summary = summarize_checks(checks)
    issues = [item for item in checks if item["status"] in {"warning", "error"}]
    report = {
        "schema_name": "geotile_dataset_audit",
        "schema_version": AUDIT_SCHEMA_VERSION,
        "generated_at": utc_now(),
        "app_version": APP_VERSION,
        "project_id": project_id,
        "run_id": run_manifest.get("run_id"),
        "status": summary["status"],
        "readiness": summary["readiness"],
        "quality_score": summary["quality_score"],
        "summary": summary,
        "dataset_summary": {
            "total_tiles": stats.get("total_tiles", 0),
            "positive_tiles": stats.get("positive_tiles", 0),
            "negative_tiles": stats.get("negative_tiles", 0),
            "total_annotations": stats.get("total_annotations", 0),
            "scene_count": len(scene_manifests),
            "class_count": len(stats.get("class_stats") or []),
            "split_mode": stats.get("split_mode")
            or (run_manifest.get("dataset_config") or {}).get("split_mode"),
        },
        "annotation_summary": annotation_summary,
        "metadata_summary": metadata_summary,
        "sidecar_summary": sidecar_summary,
        "near_duplicate_summary": near_duplicate_summary,
        "review_summary": review_summary,
        "distributions": distributions,
        "checks": checks,
        "issues": issues,
        "recommendations": recommendations_for(checks),
    }

    if persist:
        output_root = Path(persist_dir) if persist_dir is not None else run_dir
        metadata_dir = output_root / "metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        write_json(metadata_dir / "dataset_audit.json", report)
        write_audit_csv(metadata_dir / "dataset_audit.csv", checks)
    return report


def read_cached_audit(dataset_dir: str | Path) -> dict[str, Any] | None:
    """Zwróć gotowy audyt runu z dysku, jeśli istnieje i zgadza się schematem.

    Run jest niezmienny, więc raz policzony audyt (prekomputowany przy buildzie) jest
    ważny bezterminowo. GET /audit używa tego zamiast liczyć od nowa przy każdym otwarciu.
    """
    path = Path(dataset_dir) / "metadata" / "dataset_audit.json"
    if not path.is_file():
        return None
    report = read_json(path, None)
    if not isinstance(report, dict) or report.get("schema_version") != AUDIT_SCHEMA_VERSION:
        return None
    return report


def add_artifact_checks(checks: list[dict[str, Any]], run_dir: Path) -> None:
    missing = [name for name, relative in CORE_ARTIFACTS.items() if not (run_dir / relative).is_file()]
    add_check(
        checks,
        "core_artifacts",
        "completeness",
        "error" if missing else "passed",
        "Canonical run artifacts",
        f"Missing canonical artifacts: {', '.join(missing)}." if missing else "All canonical run artifacts are present.",
        len(missing),
        missing,
    )


def add_split_checks(checks: list[dict[str, Any]], report: dict[str, Any]) -> None:
    if not report:
        add_check(
            checks,
            "split_validation_missing",
            "split",
            "error",
            "Split validation",
            "validation_report.json is missing or empty.",
        )
        return
    issues = report.get("issues") or []
    if not issues:
        add_check(
            checks,
            "split_validation",
            "split",
            "passed",
            "Split validation",
            "No split or source-annotation leakage issues were detected.",
        )
    for item in issues:
        add_check(
            checks,
            f"split_{item.get('code', 'issue')}",
            "split",
            normalize_status(item.get("severity")),
            "Split validation",
            str(item.get("message") or "Split validation issue."),
            item.get("count"),
            item.get("values") or [],
        )


def add_near_duplicate_checks(checks: list[dict[str, Any]], run_dir: Path) -> dict[str, Any]:
    summary = scan_cross_split_near_duplicates(run_dir)
    if summary["scan_failure_count"]:
        add_check(
            checks,
            "near_duplicate_scan_errors",
            "split",
            "warning",
            "Near-duplicate scan",
            f"Could not fingerprint {summary['scan_failure_count']} tiles.",
            summary["scan_failure_count"],
            summary["scan_failures"],
        )
    if summary["candidate_scan_truncated"]:
        add_check(
            checks,
            "near_duplicate_scan_truncated",
            "split",
            "warning",
            "Near-duplicate scan",
            "Similarity candidate buckets were capped to keep validation bounded on a large dataset.",
        )
    add_check(
        checks,
        "near_duplicate_tiles",
        "split",
        "error" if summary["cross_split_pair_count"] else "passed",
        "Near-duplicate tiles across splits",
        (
            f"Detected {summary['cross_split_pair_count']} visually similar tile pairs in different splits."
            if summary["cross_split_pair_count"]
            else f"No near-duplicate pairs were found among {summary['tile_count']} tiles."
        ),
        summary["cross_split_pair_count"],
        pair_descriptions(summary),
    )
    return summary


def _acquisition_key(manifest: dict[str, Any]) -> str | None:
    """Kubelek warunkow akwizycji sceny (EO/SAR) z metadanych manifestu.

    SAR grupuje po polaryzacji, kierunku spojrzenia i przedziale kata padania; EO po
    przetwarzaniu spektralnym i przedziale zachmurzenia. None, gdy brak metadanych.
    """
    sar = manifest.get("sar") or {}
    eo = manifest.get("eo") or {}
    if sar:
        inc = sar.get("incidence_angle_deg")
        if isinstance(inc, (int, float)):
            low = int(inc // 5) * 5
            inc_bin = f"{low}-{low + 5}deg"
        else:
            inc_bin = "inc?"
        return f"SAR pol={sar.get('polarization', '?')} look={sar.get('look_direction', '?')} {inc_bin}"
    if eo:
        cloud = eo.get("cloud_cover_percent")
        if isinstance(cloud, (int, float)):
            low = int(cloud // 25) * 25
            cloud_bin = f"cloud{low}-{low + 25}%"
        else:
            cloud_bin = "cloud?"
        return f"EO proc={eo.get('spectral_processing', '?')} {cloud_bin}"
    return None


def _tile_split_map(split_manifest: dict[str, Any]) -> dict[str, str]:
    assignments = split_manifest.get("assignments")
    return assignments if isinstance(assignments, dict) else {}


def add_acquisition_split_checks(
    checks: list[dict[str, Any]],
    scene_manifests: dict[str, Any],
    tiles: list[dict[str, Any]],
    split_manifest: dict[str, Any],
) -> None:
    """Rozklad warunkow akwizycji miedzy splitami (EO/SAR).

    Ostrzega, gdy konfiguracja akwizycji trafia do val/test, ale nie ma jej w train —
    model bylby oceniany na geometrii/warunkach, ktorych nie widzial (kluczowe dla SAR).
    """
    acq_by_scene = {
        scene_id: _acquisition_key(manifest)
        for scene_id, manifest in scene_manifests.items()
    }
    if not any(acq_by_scene.values()):
        return  # brak metadanych akwizycji

    assignments = _tile_split_map(split_manifest)
    per_split: dict[str, Counter] = {split: Counter() for split in ("train", "val", "test")}
    for tile in tiles:
        split = assignments.get(tile.get("filename"))
        if split not in per_split:
            continue
        acq = acq_by_scene.get(tile.get("scene_id"))
        if acq:
            per_split[split][acq] += 1

    train_keys = set(per_split["train"])
    holdout_only = sorted(
        (set(per_split["val"]) | set(per_split["test"])) - train_keys
    )
    distribution = {split: dict(counter) for split, counter in per_split.items()}
    add_check(
        checks,
        "acquisition_holdout_coverage",
        "split",
        "warning" if holdout_only else "passed",
        "Acquisition coverage across splits",
        (
            f"{len(holdout_only)} acquisition configuration(s) appear in val/test but not in train."
            if holdout_only
            else "Every acquisition configuration in val/test is also represented in train."
        ),
        len(holdout_only) or None,
        holdout_only,
        data={"per_split": distribution},
    )


def _infer_tile_size_px(tiles: list[dict[str, Any]]) -> int:
    """Rozmiar kafla w pikselach: z x1-x0, gdy jest, inaczej z rozstawu x0 na siatce."""
    for tile in tiles:
        x0, x1 = tile.get("x0"), tile.get("x1")
        if isinstance(x0, (int, float)) and isinstance(x1, (int, float)) and x1 > x0:
            return int(x1 - x0)
    xs = sorted({int(t["x0"]) for t in tiles if isinstance(t.get("x0"), (int, float))})
    diffs = [b - a for a, b in zip(xs, xs[1:]) if b > a]
    return min(diffs) if diffs else 640


def _tile_center_3857(
    tile: dict[str, Any],
    scene_manifests: dict[str, Any],
    tile_size: int,
    geo_models: dict[str, Any] | None = None,
) -> tuple[tuple[float, float], float] | None:
    """Srodek kafla i jego rozpietosc w EPSG:3857, z x0/y0 i geometrii sceny."""
    from services.attribute_engine.engine import pixel_to_native, transform_points

    manifest = scene_manifests.get(tile.get("scene_id")) or {}
    x0, y0 = float(tile["x0"]), float(tile["y0"])
    center_px = [x0 + tile_size / 2.0, y0 + tile_size / 2.0]
    corner_px = [x0, y0]

    # NITF sensor scenes: non-linear GCP TPS instead of an affine transform. Build the
    # model once per scene (it owns a transformer) and reuse across tiles.
    if (manifest.get("geometry") or {}).get("model") == "gcp_tps":
        from services.sensor_geometry import try_scene_geo_model

        scene_id = tile.get("scene_id")
        if geo_models is not None:
            if scene_id not in geo_models:
                geo_models[scene_id] = try_scene_geo_model(manifest)
            model = geo_models[scene_id]
        else:
            model = try_scene_geo_model(manifest)
        if model is None:
            return None
        try:
            wgs84 = model.pixel_to_wgs84([center_px, corner_px])
            center, corner = transform_points(wgs84, "EPSG:4326", "EPSG:3857")
            span = math.hypot(center[0] - corner[0], center[1] - corner[1]) * 2.0
            return (float(center[0]), float(center[1])), span
        except Exception:
            return None

    # affine (satellite) — unchanged
    geospatial = manifest.get("geospatial") or {}
    transform_values = geospatial.get("transform")
    crs = geospatial.get("crs")
    if not geospatial.get("has_geo") or not transform_values or not crs:
        return None
    try:
        native = [pixel_to_native(point, transform_values) for point in (center_px, corner_px)]
        center, corner = transform_points(native, crs, "EPSG:3857")
        span = math.hypot(center[0] - corner[0], center[1] - corner[1]) * 2.0
        return (float(center[0]), float(center[1])), span
    except Exception:
        return None


def add_spatial_adjacency_checks(
    checks: list[dict[str, Any]],
    tiles: list[dict[str, Any]],
    split_manifest: dict[str, Any],
    scene_manifests: dict[str, Any],
) -> None:
    """Policz kafle treningowe stykajace sie przestrzennie z kaflami val/test.

    Uzupelnia kontrole near-duplicate (podobienstwo wizualne) o przeciek czysto
    geometryczny: dwa sasiadujace kafle w roznych splitach to leakage niezaleznie od
    tresci. Splity przestrzenne z buforem powinny dawac tu zero.
    """
    assignments = _tile_split_map(split_manifest)
    if not assignments:
        return

    tile_size = _infer_tile_size_px(tiles)
    positions: dict[str, tuple[float, float]] = {}
    spans: list[float] = []
    geo_models: dict[str, Any] = {}  # one SceneGeoModel per scene (TPS owns a transformer)
    for tile in tiles:
        name = tile.get("filename")
        if assignments.get(name) not in ("train", "val", "test"):
            continue
        result = _tile_center_3857(tile, scene_manifests, tile_size, geo_models)
        if result is None:
            continue
        positions[name] = result[0]
        if result[1] > 0:
            spans.append(result[1])

    if not positions or not spans:
        return  # sceny bez georeferencji — kontrola nieaplikowalna

    from statistics import median

    threshold = float(median(spans)) * 1.5  # do ~1 pierscienia sasiedztwa
    cell = max(threshold, 1.0)
    threshold_sq = threshold * threshold

    holdout_buckets: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    for name, pos in positions.items():
        if assignments.get(name) in ("val", "test"):
            holdout_buckets[(int(pos[0] // cell), int(pos[1] // cell))].append(pos)

    adjacent = 0
    examples: list[str] = []
    for name, pos in positions.items():
        if assignments.get(name) != "train":
            continue
        cx, cy = int(pos[0] // cell), int(pos[1] // cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                hit = False
                for hx, hy in holdout_buckets.get((cx + dx, cy + dy), ()):  # noqa: E741
                    if (pos[0] - hx) ** 2 + (pos[1] - hy) ** 2 < threshold_sq:
                        adjacent += 1
                        if len(examples) < 100:
                            examples.append(name)
                        hit = True
                        break
                if hit:
                    break
            else:
                continue
            break

    add_check(
        checks,
        "cross_split_spatial_adjacency",
        "split",
        "warning" if adjacent else "passed",
        "Spatially adjacent tiles across splits",
        (
            f"{adjacent} train tile(s) sit within one tile-width of a val/test tile "
            f"(spatial leakage). Consider spatial_block_split with a buffer."
            if adjacent
            else "No train tile is spatially adjacent to a val/test tile."
        ),
        adjacent or None,
        examples,
    )


def add_class_and_scene_checks(checks: list[dict[str, Any]], stats: dict[str, Any]) -> None:
    unused = stats.get("unused_classes") or []
    source_only = stats.get("source_only_classes") or []
    scenes_without_annotations = stats.get("scenes_without_annotations") or []
    scenes_without_dataset_classes = stats.get("scenes_without_dataset_classes") or []
    add_count_check(
        checks,
        "unused_classes",
        "classes",
        "Unused classes",
        unused,
        "Every configured class is represented in the generated dataset.",
        "Configured classes absent from the generated dataset",
    )
    add_count_check(
        checks,
        "source_only_classes",
        "classes",
        "Source-only classes",
        source_only,
        "All classes present in source annotations remain represented after tiling.",
        "Classes present in source annotations but absent after dataset generation",
    )
    add_count_check(
        checks,
        "scenes_without_annotations",
        "scenes",
        "Scenes without annotations",
        scenes_without_annotations,
        "Every scene has at least one source annotation.",
        "Scenes without source annotations",
    )
    add_count_check(
        checks,
        "scenes_without_dataset_classes",
        "scenes",
        "Scenes without used classes",
        scenes_without_dataset_classes,
        "Every scene contributes at least one class to the generated dataset.",
        "Scenes that do not contribute a class to the generated dataset",
    )


def add_review_grid_checks(
    checks: list[dict[str, Any]],
    stats: dict[str, Any],
    tiles_value: Any,
    tile_annotations_value: Any,
    split_manifest: dict[str, Any],
    run_manifest: dict[str, Any],
) -> dict[str, Any]:
    tiles = [item for item in tiles_value if isinstance(item, dict)] if isinstance(tiles_value, list) else []
    tile_annotations = tile_annotations_value if isinstance(tile_annotations_value, dict) else {}
    assignments = split_manifest.get("assignments") or {}
    if not isinstance(assignments, dict):
        assignments = {}

    tile_summary = stats.get("tile_summary") or {}
    active_tiles = [tile for tile in tiles if not is_excluded_tile(tile)]
    reviewed_tiles = [tile for tile in active_tiles if is_reviewed_tile(tile)]
    unreviewed_tiles = [tile for tile in active_tiles if not is_reviewed_tile(tile)]
    used_tiles = [tile for tile in tiles if str(tile.get("filename")) in assignments]
    used_unreviewed = [tile for tile in used_tiles if not is_excluded_tile(tile) and not is_reviewed_tile(tile)]
    used_excluded = [tile for tile in used_tiles if is_excluded_tile(tile)]
    positive_tiles = [tile for tile in active_tiles if tile_annotations.get(str(tile.get("filename")))]
    reviewed_empty_tiles = [
        tile
        for tile in reviewed_tiles
        if not tile_annotations.get(str(tile.get("filename")))
    ]

    active_count = int(tile_summary.get("active") or len(active_tiles))
    reviewed_count = int(tile_summary.get("reviewed") or len(reviewed_tiles))
    unreviewed_count = int(tile_summary.get("unreviewed") or len(unreviewed_tiles))
    positive_count = len(positive_tiles)
    reviewed_empty_count = int(tile_summary.get("reviewed_empty") or len(reviewed_empty_tiles))
    coverage_pct = (reviewed_count / active_count * 100.0) if active_count else None
    threshold_pct = 80.0

    if active_count == 0:
        add_check(
            checks,
            "review_coverage_total",
            "review",
            "not_available",
            "Review grid coverage",
            "Review coverage cannot be calculated because the run has no active grid cells.",
            0,
            data={"coverage_pct": None, "reviewed": 0, "active": 0, "threshold_pct": threshold_pct},
        )
    else:
        add_check(
            checks,
            "review_coverage_total",
            "review",
            "warning" if coverage_pct is not None and coverage_pct < threshold_pct else "passed",
            "Review grid coverage",
            "Review grid coverage check completed.",
            unreviewed_count,
            data={
                "coverage_pct": round(float(coverage_pct or 0.0), 2),
                "reviewed": reviewed_count,
                "active": active_count,
                "threshold_pct": threshold_pct,
            },
        )

    scene_rows = stats.get("scene_stats") or []
    low_scene_coverage = []
    for scene in scene_rows if isinstance(scene_rows, list) else []:
        if not isinstance(scene, dict):
            continue
        scene_tiles = max(0, int(scene.get("tiles") or 0) - int(scene.get("excluded_tiles") or 0))
        scene_reviewed = int(scene.get("reviewed_tiles") or 0)
        if scene_tiles <= 0:
            continue
        scene_pct = scene_reviewed / scene_tiles * 100.0
        if scene_pct < threshold_pct:
            low_scene_coverage.append(
                f"{scene.get('filename') or scene.get('scene_id')}: {scene_pct:.1f}%"
            )
    add_check(
        checks,
        "review_coverage_by_scene",
        "review",
        "warning" if low_scene_coverage else "passed",
        "Scene review coverage",
        "Scene review coverage check completed.",
        len(low_scene_coverage),
        low_scene_coverage,
        data={"threshold_pct": threshold_pct},
    )

    add_check(
        checks,
        "unreviewed_grid_cells",
        "review",
        "warning" if unreviewed_count else "passed",
        "Unreviewed grid cells",
        "Unreviewed grid cell check completed.",
        unreviewed_count,
        tile_names(unreviewed_tiles),
        data={"unreviewed": unreviewed_count, "active": active_count},
    )
    add_check(
        checks,
        "review_positive_empty_cells",
        "review",
        "info",
        "Positive and checked-empty cells",
        "Review grid class balance check completed.",
        positive_count + reviewed_empty_count,
        data={
            "positive": positive_count,
            "reviewed_empty": reviewed_empty_count,
            "active": active_count,
        },
    )
    add_check(
        checks,
        "dataset_uses_unreviewed_tiles",
        "review",
        "warning" if used_unreviewed else "passed",
        "Dataset tiles from unchecked areas",
        "Dataset unchecked-area check completed.",
        len(used_unreviewed),
        tile_names(used_unreviewed),
    )
    add_check(
        checks,
        "dataset_uses_excluded_tiles",
        "review",
        "error" if used_excluded else "passed",
        "Dataset tiles marked as excluded",
        "Dataset excluded-cell check completed.",
        len(used_excluded),
        tile_names(used_excluded),
    )

    intersecting_excluded = find_used_tiles_intersecting_excluded(
        used_tiles,
        [tile for tile in tiles if is_excluded_tile(tile)],
        run_manifest,
    )
    add_check(
        checks,
        "dataset_intersects_excluded_tiles",
        "review",
        "error" if intersecting_excluded else "passed",
        "Dataset tiles intersect excluded areas",
        "Dataset excluded-area intersection check completed.",
        len(intersecting_excluded),
        intersecting_excluded,
    )

    return {
        "active_cells": active_count,
        "reviewed_cells": reviewed_count,
        "unreviewed_cells": unreviewed_count,
        "review_coverage_pct": round(float(coverage_pct or 0.0), 2) if coverage_pct is not None else None,
        "positive_cells": positive_count,
        "reviewed_empty_cells": reviewed_empty_count,
        "used_unreviewed_cells": len(used_unreviewed),
        "used_excluded_cells": len(used_excluded),
        "used_cells_intersecting_excluded": len(intersecting_excluded),
    }


def add_annotation_checks(
    checks: list[dict[str, Any]],
    annotations: list[dict[str, Any]],
    tile_links: list[dict[str, Any]],
) -> dict[str, Any]:
    sources = Counter(str(item.get("annotation_source") or "unknown") for item in annotations)
    missing_ids = [item.get("id") for item in annotations if not item.get("source_annotation_id")]
    missing_authors = [item.get("id") for item in annotations if not item.get("annotator_email")]
    unreviewed_predictions = [
        item.get("id")
        for item in annotations
        if str(item.get("annotation_source") or "").lower()
        in {"prediction", "model_prediction", "pseudo_label", "pseudo_labeled"}
        and item.get("review_status") not in {"accepted", "reviewed"}
        and item.get("reviewed") is not True
    ]
    partial_attributes = [
        item.get("id")
        for item in annotations
        if ((item.get("attributes") or {}).get("attribute_status") in {"partial", "error"})
    ]
    link_source_ids = {str(item.get("source_annotation_id")) for item in tile_links if item.get("source_annotation_id")}
    source_ids = {str(item.get("source_annotation_id")) for item in annotations if item.get("source_annotation_id")}
    orphan_links = sorted(link_source_ids - source_ids)

    add_id_check(checks, "missing_source_annotation_ids", "provenance", "Stable annotation IDs", missing_ids)
    add_id_check(checks, "missing_annotator_email", "provenance", "Annotation author", missing_authors)
    add_id_check(checks, "orphan_tile_annotation_links", "provenance", "Tile annotation provenance", orphan_links, error=True)
    add_id_check(checks, "unreviewed_prediction_annotations", "annotations", "Unreviewed model annotations", unreviewed_predictions, error=True)
    add_id_check(checks, "partial_computed_attributes", "annotations", "Computed annotation attributes", partial_attributes)

    return {
        "total_source_annotations": len(annotations),
        "by_source": dict(sorted(sources.items())),
        "model_assisted_accepted": sources.get("model_assisted", 0),
        "unreviewed_prediction_annotations": len(unreviewed_predictions),
        "missing_annotator_email": len(missing_authors),
        "partial_or_failed_attributes": len(partial_attributes),
    }


def add_scene_metadata_checks(
    checks: list[dict[str, Any]],
    scene_manifests: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    manifests = list(scene_manifests.values()) if isinstance(scene_manifests, dict) else []
    missing_sensor = [item.get("scene_id") for item in manifests if not item.get("sensor")]
    missing_modality = [item.get("scene_id") for item in manifests if not item.get("modality")]
    missing_time = [item.get("scene_id") for item in manifests if not item.get("acquisition_datetime_utc")]
    geo_scenes = [
        item for item in manifests
        if (item.get("geospatial") or {}).get("has_geo")
        or (item.get("geometry") or {}).get("model") == "gcp_tps"  # NITF: geo via TPS
    ]
    geo_without_bounds = [
        item.get("scene_id")
        for item in geo_scenes
        if not (item.get("geospatial") or {}).get("bounds_wgs84")
    ]
    add_id_check(checks, "missing_scene_sensor", "metadata", "Scene sensor metadata", missing_sensor)
    add_id_check(checks, "missing_scene_modality", "metadata", "Scene modality metadata", missing_modality, error=True)
    add_id_check(checks, "missing_acquisition_time", "metadata", "Acquisition time", missing_time)
    add_id_check(checks, "geo_scenes_without_bounds", "metadata", "Geospatial scene bounds", geo_without_bounds, error=True)
    return {
        "scene_count": len(manifests),
        "geo_scene_count": len(geo_scenes),
        "no_geo_scene_count": len(manifests) - len(geo_scenes),
        "missing_sensor_count": len(missing_sensor),
        "missing_modality_count": len(missing_modality),
        "missing_acquisition_time_count": len(missing_time),
        "geo_without_bounds_count": len(geo_without_bounds),
    }


def add_sidecar_checks(
    checks: list[dict[str, Any]],
    run_dir: Path,
    export_manifest: dict[str, Any],
) -> dict[str, Any]:
    present = {name: (run_dir / relative).is_file() for name, relative in EXPORT_SIDECARS.items()}
    present_count = sum(present.values())
    if present_count == 0:
        add_check(
            checks,
            "export_sidecars_pending",
            "sidecars",
            "info",
            "Export sidecars",
            "Export sidecars have not been generated yet. Run an export before packaging the dataset.",
            0,
        )
    elif present_count != len(present):
        missing = [name for name, available in present.items() if not available]
        add_check(
            checks,
            "export_sidecars_incomplete",
            "sidecars",
            "error",
            "Export sidecars",
            f"Export sidecars are incomplete: {', '.join(missing)}.",
            len(missing),
            missing,
        )
    else:
        validation = export_manifest.get("validation") or {}
        status = normalize_status(validation.get("status")) if validation else "passed"
        add_check(
            checks,
            "export_sidecars_complete",
            "sidecars",
            status,
            "Export sidecars",
            "Export manifest, tile metadata and annotation links are present.",
            len(present),
            validation.get("errors") or validation.get("warnings") or [],
        )
    return {
        "generated": present_count > 0,
        "complete": present_count == len(present),
        "files": present,
        "validation": export_manifest.get("validation") or {},
    }


def add_preprocessing_check(checks: list[dict[str, Any]], run_manifest: dict[str, Any]) -> None:
    profile = run_manifest.get("preprocessing_profile") or {}
    profile_id = profile.get("profile_id")
    profile_hash = profile.get("profile_hash")
    complete = bool(profile_id and profile_hash)
    add_check(
        checks,
        "preprocessing_traceability",
        "preprocessing",
        "passed" if complete else "error",
        "Preprocessing traceability",
        (
            f"Profile {profile_id} is versioned by hash {str(profile_hash)[:12]}."
            if complete
            else "Preprocessing profile ID or hash is missing."
        ),
    )


def build_distributions(
    stats: dict[str, Any],
    scene_manifests: dict[str, dict[str, Any]],
    preprocessing: dict[str, Any],
) -> dict[str, Any]:
    scene_stats = {item.get("scene_id"): item for item in stats.get("scene_stats") or []}
    sensor = defaultdict(lambda: {"scenes": 0, "used_tiles": 0, "annotations": 0})
    modality = defaultdict(lambda: {"scenes": 0, "used_tiles": 0, "annotations": 0})
    georeferencing = defaultdict(lambda: {"scenes": 0, "used_tiles": 0, "annotations": 0})
    locations = []
    for scene_id, manifest in (scene_manifests or {}).items():
        row = scene_stats.get(scene_id) or {}
        used_tiles = int(row.get("used_tiles", 0))
        annotations = int(row.get("annotations", 0))
        for group, key in (
            (sensor, manifest.get("sensor") or "unknown"),
            (modality, manifest.get("modality") or "unknown"),
            (georeferencing, manifest.get("georeferencing") or "unknown"),
        ):
            group[str(key)]["scenes"] += 1
            group[str(key)]["used_tiles"] += used_tiles
            group[str(key)]["annotations"] += annotations
        bounds = (manifest.get("geospatial") or {}).get("bounds_wgs84")
        locations.append({
            "scene_id": scene_id,
            "filename": manifest.get("filename") or row.get("filename") or scene_id,
            "sensor": manifest.get("sensor"),
            "modality": manifest.get("modality"),
            "acquisition_datetime_utc": manifest.get("acquisition_datetime_utc"),
            "bounds_wgs84": bounds,
            "centroid_lon": ((float(bounds[0]) + float(bounds[2])) / 2) if valid_bounds(bounds) else None,
            "centroid_lat": ((float(bounds[1]) + float(bounds[3])) / 2) if valid_bounds(bounds) else None,
            "used_tiles": used_tiles,
            "annotations": annotations,
        })
    return {
        "by_sensor": distribution_rows("sensor", sensor),
        "by_modality": distribution_rows("modality", modality),
        "by_georeferencing": distribution_rows("georeferencing", georeferencing),
        "by_preprocessing": [{
            "profile_id": preprocessing.get("profile_id") or "unknown",
            "profile_hash": preprocessing.get("profile_hash"),
            "tiles": stats.get("total_tiles", 0),
            "annotations": stats.get("total_annotations", 0),
        }],
        "by_split": stats.get("split_stats") or [],
        "by_scene": stats.get("scene_stats") or [],
        "locations": locations,
    }


def flatten_source_annotations(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [
            item
            for annotations in value.values()
            if isinstance(annotations, list)
            for item in annotations
            if isinstance(item, dict)
        ]
    return []


def summarize_checks(checks: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(item["status"] for item in checks)
    status = "error" if counts["error"] else "warning" if counts["warning"] else "ok"
    readiness = "not_ready" if counts["error"] else "ready_with_warnings" if counts["warning"] else "ready"
    quality_score = max(0, 100 - counts["error"] * 20 - counts["warning"] * 5)
    return {
        "status": status,
        "readiness": readiness,
        "quality_score": quality_score,
        "total_checks": len(checks),
        "passed": counts["passed"],
        "warnings": counts["warning"],
        "errors": counts["error"],
        "info": counts["info"],
        "not_available": counts["not_available"],
    }


def recommendations_for(checks: list[dict[str, Any]]) -> list[str]:
    messages = {
        "core_artifacts": "Regenerate the dataset run to restore missing canonical artifacts.",
        "split_random_tile_leakage_risk": "Use spatial_block_split or scene_split before model evaluation.",
        "split_cross_split_source_annotations": "Regenerate splits so every source annotation belongs to one split.",
        "unused_classes": "Remove unused classes or add representative annotations.",
        "source_only_classes": "Review clipping and min_box_fraction for source-only classes.",
        "missing_annotator_email": "Assign a labeling author before merging projects.",
        "unreviewed_prediction_annotations": "Review or remove prediction-only annotations before training.",
        "review_coverage_total": "Review more grid cells before final export.",
        "review_coverage_by_scene": "Review scenes with low checked-cell coverage.",
        "unreviewed_grid_cells": "Check remaining grid cells or regenerate the dataset after review.",
        "dataset_uses_unreviewed_tiles": "Review grid cells used by the dataset before final training.",
        "dataset_uses_excluded_tiles": "Regenerate the dataset after removing excluded cells from the selection.",
        "dataset_intersects_excluded_tiles": "Adjust excluded areas or tile settings and regenerate the dataset.",
        "export_sidecars_incomplete": "Regenerate export sidecars before packaging the dataset.",
        "preprocessing_traceability": "Select a versioned preprocessing profile and regenerate the run.",
    }
    result = []
    for item in checks:
        if item["status"] not in {"warning", "error"}:
            continue
        recommendation = messages.get(item["check_id"])
        if recommendation and recommendation not in result:
            result.append(recommendation)
    return result


def add_count_check(
    checks: list[dict[str, Any]],
    check_id: str,
    category: str,
    title: str,
    values: list[Any],
    success_message: str,
    issue_prefix: str,
) -> None:
    add_check(
        checks,
        check_id,
        category,
        "warning" if values else "passed",
        title,
        f"{issue_prefix}: {len(values)}." if values else success_message,
        len(values),
        [str(value) for value in values[:100]],
    )


def add_id_check(
    checks: list[dict[str, Any]],
    check_id: str,
    category: str,
    title: str,
    values: list[Any],
    *,
    error: bool = False,
) -> None:
    cleaned = [str(value) for value in values if value not in (None, "")]
    add_check(
        checks,
        check_id,
        category,
        ("error" if error else "warning") if values else "passed",
        title,
        f"Affected records: {len(values)}." if values else "Check passed.",
        len(values),
        cleaned[:100],
    )


def add_check(
    checks: list[dict[str, Any]],
    check_id: str,
    category: str,
    status: str,
    title: str,
    message: str,
    count: int | None = None,
    details: list[Any] | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    checks.append({
        "check_id": check_id,
        "category": category,
        "status": status,
        "title": title,
        "message": message,
        "count": count,
        "details": [str(value) for value in (details or [])[:100]],
        "data": data or {},
    })


def normalize_status(value: Any) -> str:
    text = str(value or "").lower()
    if text == "error":
        return "error"
    if text == "warning":
        return "warning"
    if text in {"info", "not_available"}:
        return text
    return "passed"


def distribution_rows(key_name: str, values: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    return [
        {key_name: key, **data}
        for key, data in sorted(values.items(), key=lambda item: item[0].lower())
    ]


def valid_bounds(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    try:
        return all(math.isfinite(float(item)) for item in value)
    except (TypeError, ValueError):
        return False


def is_reviewed_tile(tile: dict[str, Any]) -> bool:
    return str(tile.get("review_status") or "").lower() == "reviewed" or bool(tile.get("reviewed"))


def is_excluded_tile(tile: dict[str, Any]) -> bool:
    return bool(tile.get("exclude_from_dataset", tile.get("excluded", False)) or tile.get("excluded"))


def tile_names(tiles: list[dict[str, Any]], limit: int = 100) -> list[str]:
    return [str(tile.get("filename") or tile.get("tile_id") or "") for tile in tiles[:limit]]


def find_used_tiles_intersecting_excluded(
    used_tiles: list[dict[str, Any]],
    excluded_tiles: list[dict[str, Any]],
    run_manifest: dict[str, Any],
) -> list[str]:
    tile_size = int(((run_manifest.get("tiling_config") or {}).get("tile_size")) or 0)
    excluded_by_scene: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for tile in excluded_tiles:
        excluded_by_scene[tile_scene_id(tile)].append(tile)

    intersections: list[str] = []
    seen_used: set[str] = set()
    for tile in used_tiles:
        filename = str(tile.get("filename") or "")
        if is_excluded_tile(tile):
            continue
        bounds = tile_bounds(tile, tile_size)
        if not bounds:
            continue
        for excluded in excluded_by_scene.get(tile_scene_id(tile), []):
            excluded_filename = str(excluded.get("filename") or "")
            if excluded_filename == filename:
                continue
            excluded_bounds = tile_bounds(excluded, tile_size)
            if not excluded_bounds:
                continue
            if rectangles_intersect(bounds, excluded_bounds):
                if filename not in seen_used:
                    intersections.append(f"{filename} intersects {excluded_filename}")
                    seen_used.add(filename)
                break
    return intersections[:100]


def tile_scene_id(tile: dict[str, Any]) -> str:
    scene_id = tile.get("scene_id")
    if scene_id:
        return str(scene_id)
    filename = str(tile.get("filename") or "")
    return filename.split("__", 1)[0] if "__" in filename else ""


def tile_bounds(tile: dict[str, Any], tile_size: int) -> tuple[float, float, float, float] | None:
    try:
        x0 = float(tile.get("x0"))
        y0 = float(tile.get("y0"))
    except (TypeError, ValueError):
        return None
    try:
        x1 = float(tile.get("x1")) if tile.get("x1") is not None else x0 + tile_size
        y1 = float(tile.get("y1")) if tile.get("y1") is not None else y0 + tile_size
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def rectangles_intersect(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    return max(first[0], second[0]) < min(first[2], second[2]) and max(first[1], second[1]) < min(first[3], second[3])


def read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, default=str)


def write_audit_csv(path: Path, checks: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=AUDIT_CSV_FIELDS)
        writer.writeheader()
        for item in checks:
            writer.writerow({key: item.get(key) for key in AUDIT_CSV_FIELDS})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
