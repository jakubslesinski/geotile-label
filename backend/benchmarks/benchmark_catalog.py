"""Benchmark current tile-catalog loading and grouping without modifying a project."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import sys
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.common import add_common_arguments, configure_environment, output_path


OPERATION = "catalog"


def group_by_scene(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("scene_id") or "")].append(row)
    return dict(grouped)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser, project_required=True)
    parser.add_argument(
        "--legacy-scene-sample",
        type=int,
        default=0,
        help="Number of scenes for measuring the old per-scene full-table read (0 disables)",
    )
    return parser


def run(args: argparse.Namespace) -> Path:
    configure_environment(args)

    from services.performance_metrics import PerformanceRecorder
    from services.tile_catalog import (
        catalog_dir,
        get_active_catalog_manifest,
        get_catalog_links,
        get_catalog_tiles,
    )

    destination = output_path(args, OPERATION)
    manifest = get_active_catalog_manifest(args.project_id, auto_migrate=False)
    if not manifest:
        raise RuntimeError(
            "Project has no active tile catalog. Build it outside the benchmark first; "
            "the benchmark will not auto-migrate or mutate project data."
        )

    if args.cache_state == "warm":
        # Populate filesystem cache before the measured recorder starts. There is no
        # application-level table cache in the current implementation.
        get_catalog_tiles(args.project_id)
        get_catalog_links(args.project_id)

    root = catalog_dir(args.project_id, str(manifest["catalog_id"]))
    inputs = {
        "catalog_id": manifest.get("catalog_id"),
        "tiles_parquet_bytes": _file_size(root / "tiles.parquet"),
        "links_parquet_bytes": _file_size(root / "tile_annotation_links.parquet"),
        "review_state_bytes": _file_size(root / "review_state.json"),
        "legacy_scene_sample": max(0, args.legacy_scene_sample),
    }
    with PerformanceRecorder(
        OPERATION,
        output_path=destination,
        project_id=args.project_id,
        cache_state=args.cache_state,
        storage_profile=args.storage_profile,
        inputs=inputs,
        metadata_value={
            "read_only": True,
            "cache_semantics": (
                "cold means a new Python process; the operating-system disk cache is not flushed"
            ),
        },
    ) as recorder:
        with recorder.stage("tiles_load") as timer:
            tiles = get_catalog_tiles(args.project_id)
            timer.record(rows=len(tiles))

        with recorder.stage("links_load") as timer:
            links = get_catalog_links(args.project_id)
            timer.record(rows=len(links))

        with recorder.stage("group_by_scene") as timer:
            tiles_by_scene = group_by_scene(tiles)
            links_by_scene = group_by_scene(links)
            scene_ids = sorted(set(tiles_by_scene) | set(links_by_scene))
            timer.record(scenes=len(scene_ids), rows=len(tiles) + len(links))

        sample_count = min(max(0, args.legacy_scene_sample), len(scene_ids))
        if sample_count:
            sampled = scene_ids[:sample_count]
            with recorder.stage("legacy_per_scene_tile_reload") as timer:
                sampled_tile_rows = sum(
                    len(get_catalog_tiles(args.project_id, scene_id)) for scene_id in sampled
                )
                timer.record(scenes=sample_count, rows=sampled_tile_rows)
            with recorder.stage("legacy_per_scene_link_scan") as timer:
                sampled_link_rows = sum(
                    1
                    for scene_id in sampled
                    for link in links
                    if str(link.get("scene_id") or "") == scene_id
                )
                timer.record(
                    scenes=sample_count,
                    rows=sampled_link_rows,
                    comparisons=sample_count * len(links),
                )
            reload_seconds = recorder.report["stages"]["legacy_per_scene_tile_reload"][
                "wall_seconds"
            ]
            scan_seconds = recorder.report["stages"]["legacy_per_scene_link_scan"][
                "wall_seconds"
            ]
            recorder.set_metric(
                "legacy_tile_reload_projected_seconds",
                round(reload_seconds / sample_count * len(scene_ids), 3),
            )
            recorder.set_metric(
                "legacy_link_scan_projected_seconds",
                round(scan_seconds / sample_count * len(scene_ids), 3),
            )

        recorder.set_metric("tile_rows", len(tiles))
        recorder.set_metric("link_rows", len(links))
        recorder.set_metric("scene_count", len(scene_ids))
        recorder.set_metric(
            "catalog_bytes",
            inputs["tiles_parquet_bytes"]
            + inputs["links_parquet_bytes"]
            + inputs["review_state_bytes"],
        )
    return destination


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def main() -> None:
    args = build_parser().parse_args()
    destination = run(args)
    print(destination)


if __name__ == "__main__":
    main()
