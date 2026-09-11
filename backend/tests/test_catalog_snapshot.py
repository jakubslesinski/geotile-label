"""P0.2 regression tests for the run-scoped catalog snapshot.

Run: python backend/tests/test_catalog_snapshot.py
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

TEST_ROOT = pathlib.Path(tempfile.mkdtemp(prefix="geotile-catalog-snapshot-"))
os.environ["DATA_DIR"] = str(TEST_ROOT / "data")

from db.storage import project_dir  # noqa: E402
from services.parquet_io import write_parquet  # noqa: E402
from services import tile_catalog as catalog  # noqa: E402


PROJECT_ID = "snapshot-project"
CATALOG_ID = "catalog-v1"


def _prepare_catalog() -> pathlib.Path:
    root = project_dir(PROJECT_ID) / "tile_catalogs" / CATALOG_ID
    root.mkdir(parents=True, exist_ok=True)
    write_parquet(root / "tiles.parquet", [
        {
            "tile_id": "tile-a",
            "grid_cell_id": "tile-a",
            "scene_id": "a",
            "filename": "1_1_a.png",
            "col": 1,
            "row": 1,
            "x0": 0,
            "y0": 0,
            "x1": 640,
            "y1": 640,
            "geometry_scene_px": [[0, 0], [640, 0], [640, 640], [0, 640]],
        },
        {
            "tile_id": "tile-b",
            "grid_cell_id": "tile-b",
            "scene_id": "b",
            "filename": "1_1_b.png",
            "col": 1,
            "row": 1,
            "x0": 0,
            "y0": 0,
            "x1": 640,
            "y1": 640,
            "geometry_scene_px": [[0, 0], [640, 0], [640, 640], [0, 640]],
        },
    ])
    write_parquet(root / "tile_annotation_links.parquet", [
        {
            "scene_id": "a",
            "tile_filename": "1_1_a.png",
            "class_id": 1,
            "source_annotation_id": "ann-a",
            "bbox_yolo_norm": [0.5, 0.5, 0.25, 0.25],
            "geometry_tile_px": [[1, 1], [2, 1], [2, 2], [1, 2]],
            "exportable_yolo": True,
            "annotation_source": "manual",
        },
        {
            "scene_id": "b",
            "tile_filename": "1_1_b.png",
            "class_id": 2,
            "source_annotation_id": "ann-b",
            "bbox_yolo_norm": [0.25, 0.25, 0.1, 0.1],
            "geometry_tile_px": [[3, 3], [4, 3], [4, 4], [3, 4]],
            "exportable_yolo": True,
            "annotation_source": "imported",
        },
    ])
    catalog.write_json(root / "review_state.json", {
        "schema_name": "geotile_review_grid_state",
        "schema_version": 2,
        "updated_at": "2026-08-28T00:00:00+00:00",
        "tiles": {
            "tile-a": {"review_status": "reviewed", "exclude_from_dataset": False},
            "tile-b": {"review_status": "unreviewed", "exclude_from_dataset": False},
        },
    })
    catalog.write_json(root / "tile_catalog_manifest.json", {
        "schema_name": "geotile_tile_catalog",
        "schema_version": 3,
        "catalog_id": CATALOG_ID,
        "status": "ready",
        "tile_count": 2,
        "annotation_link_count": 2,
    })
    catalog.write_json(project_dir(PROJECT_ID) / "tile_catalogs" / "index.json", {
        "schema_name": "geotile_tile_catalogs_index",
        "schema_version": 1,
        "active_catalog_id": CATALOG_ID,
        "catalogs": [],
    })
    return root


def test_snapshot_reads_each_table_once_and_pushes_scene_filter():
    root = _prepare_catalog()
    original_scan = catalog.scan_parquet_table
    calls: list[tuple[str, tuple[str, ...] | None]] = []

    def counted_scan(path, *, columns=None, scene_ids=None):
        calls.append((pathlib.Path(path).name, None if scene_ids is None else tuple(sorted(scene_ids))))
        return original_scan(path, columns=columns, scene_ids=scene_ids)

    catalog.scan_parquet_table = counted_scan
    try:
        snapshot = catalog.load_catalog_snapshot(
            PROJECT_ID,
            scene_ids=["a"],
            tile_columns=("scene_id", "tile_id", "filename", "row", "col"),
            link_columns=(
                "scene_id", "tile_filename", "class_id", "source_annotation_id",
                "bbox_yolo_norm", "exportable_yolo",
            ),
            retain_link_table=True,
        )
    finally:
        catalog.scan_parquet_table = original_scan

    assert calls.count(("tiles.parquet", ("a",))) == 1
    assert calls.count(("tile_annotation_links.parquet", ("a",))) == 1
    assert snapshot.tile_count == 1
    assert snapshot.link_count == 1
    assert snapshot.scene_ids == ("a",)
    assert snapshot.tiles_for_scene("b") == ()
    assert snapshot.tiles_for_scene("a")[0]["review_status"] == "reviewed"
    assert snapshot.links_for_scene("a")[0]["bbox_yolo_norm"] == [0.5, 0.5, 0.25, 0.25]

    try:
        snapshot.links_for_scene("a")[0]["class_id"] = 9
        raise AssertionError("snapshot row must be read-only")
    except TypeError:
        pass

    # The retained Arrow table was read once and can provide full provenance later.
    full_links = snapshot.materialize_links()
    assert full_links[0]["geometry_tile_px"] == [[1, 1], [2, 1], [2, 2], [1, 2]]

    # A later review-state write cannot mutate an already loaded run snapshot.
    catalog.write_json(root / "review_state.json", {
        "schema_version": 2,
        "updated_at": "2026-08-28T01:00:00+00:00",
        "tiles": {"tile-a": {"review_status": "unreviewed"}},
    })
    assert snapshot.tiles_for_scene("a")[0]["review_status"] == "reviewed"


def test_legacy_wrappers_keep_list_and_json_shapes():
    _prepare_catalog()
    tiles = catalog.get_catalog_tiles(PROJECT_ID, "a")
    links = catalog.get_catalog_links(PROJECT_ID, "a")
    assert isinstance(tiles, list) and len(tiles) == 1
    assert isinstance(tiles[0]["geometry_scene_px"], list)
    assert tiles[0]["grid_cell_id"] == "tile-a"
    assert isinstance(links, list) and len(links) == 1
    assert links[0]["bbox_yolo_norm"] == [0.5, 0.5, 0.25, 0.25]
    assert links[0]["geometry_tile_px"] == [[1, 1], [2, 1], [2, 2], [1, 2]]


if __name__ == "__main__":
    for name, function in list(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
            print(f"OK {name}")
    print("all catalog snapshot tests passed")
