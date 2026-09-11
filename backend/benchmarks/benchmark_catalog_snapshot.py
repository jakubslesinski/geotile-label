"""Benchmark P0.2 run-scoped catalog preparation without modifying the project."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.common import add_common_arguments, configure_environment, output_path


OPERATION = "catalog_snapshot"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser, project_required=True)
    parser.add_argument(
        "--materialize-provenance",
        action="store_true",
        help="Also time deferred conversion of the retained full link table",
    )
    return parser


def run(args: argparse.Namespace) -> Path:
    configure_environment(args)

    from services.performance_metrics import PerformanceRecorder
    from services.tile_catalog import (
        DATASET_CATALOG_TILE_COLUMNS,
        DATASET_PREPARATION_LINK_COLUMNS,
        catalog_dir,
        get_active_catalog_manifest,
        load_catalog_snapshot,
    )

    destination = output_path(args, OPERATION)
    manifest = get_active_catalog_manifest(args.project_id, auto_migrate=False)
    if not manifest:
        raise RuntimeError("Project has no active tile catalog")
    root = catalog_dir(args.project_id, str(manifest["catalog_id"]))

    def load_snapshot():
        return load_catalog_snapshot(
            args.project_id,
            tile_columns=DATASET_CATALOG_TILE_COLUMNS,
            link_columns=DATASET_PREPARATION_LINK_COLUMNS,
            retain_link_table=True,
        )

    if args.cache_state == "warm":
        load_snapshot()

    inputs = {
        "catalog_id": manifest.get("catalog_id"),
        "tiles_parquet_bytes": _file_size(root / "tiles.parquet"),
        "links_parquet_bytes": _file_size(root / "tile_annotation_links.parquet"),
        "review_state_bytes": _file_size(root / "review_state.json"),
        "tile_projection": list(DATASET_CATALOG_TILE_COLUMNS),
        "link_projection": list(DATASET_PREPARATION_LINK_COLUMNS),
        "retain_link_table": True,
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
                "cold means a new Python process; warm performs one unmeasured snapshot load"
            ),
        },
    ) as recorder:
        with recorder.stage("snapshot_load") as timer:
            snapshot = load_snapshot()
            timer.record(
                scenes=len(snapshot.scene_ids),
                tiles=snapshot.tile_count,
                links=snapshot.link_count,
            )
        if snapshot.tile_count != int(manifest.get("tile_count") or 0):
            raise RuntimeError("Snapshot tile count differs from catalog manifest")
        if snapshot.link_count != int(manifest.get("annotation_link_count") or 0):
            raise RuntimeError("Snapshot link count differs from catalog manifest")
        if args.materialize_provenance:
            with recorder.stage("deferred_provenance_materialization") as timer:
                links = snapshot.materialize_links()
                timer.record(rows=len(links))

        recorder.set_metric("snapshot_id", snapshot.snapshot_id)
        recorder.set_metric("scene_count", len(snapshot.scene_ids))
        recorder.set_metric("tile_rows", snapshot.tile_count)
        recorder.set_metric("link_rows", snapshot.link_count)
        recorder.set_metric("parquet_table_reads", 2)
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
