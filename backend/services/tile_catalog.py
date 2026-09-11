"""Metadata-only tile catalogs with lazy image materialization."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from db.storage import (
    SCENES_ROOT,
    list_scene_ids,
    load_json,
    load_scene_json,
    project_dir,
    save_scene_json,
)
from models.annotation import Annotation
from models.tiling_config import TileInfo, TilingConfig
from services.annotation_propagator import propagate_annotations_with_attributes
from services.attribute_engine import ATTRIBUTE_VERSION, recompute_scene_attributes
from services.attribute_engine.engine import pixel_to_native, transform_points
from services.parquet_io import scan_parquet_rows, scan_parquet_table, write_parquet
from services.preprocessing_profiles import resolve_preprocessing_profile, write_preprocessed_tile
from services.scene_loader import get_scene_info
from services.scene_raster_resolver import resolve_scene_raster
from services.sensor_geometry import try_scene_geo_model
from services.tiler import compute_grid

TILE_CATALOG_SCHEMA_VERSION = 3
PROPAGATION_VERSION = 2

TILE_JSON_FIELDS = frozenset({
    "geometry_px", "geometry_scene_px", "geometry_native", "geometry_wgs84", "bbox_lonlat",
})
LINK_JSON_FIELDS = frozenset({
    "bbox_tile_px", "geometry_tile_px", "obb_tile_px", "bbox_yolo_norm",
    "bbox_coco_xywh", "front_vector_scene_px",
})
DATASET_CATALOG_TILE_COLUMNS = (
    "tile_id", "grid_cell_id", "scene_id", "filename", "col", "row",
    "x0", "y0", "x1", "y1",
)
DATASET_PREPARATION_LINK_COLUMNS = (
    "scene_id", "tile_filename", "class_id", "source_annotation_id",
    "bbox_yolo_norm", "exportable_yolo", "annotator_email", "annotation_source",
)


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    """Immutable, run-scoped view of one tile catalog revision.

    Python rows expose only the requested projection and are grouped once. An
    optional retained Arrow table lets a long-running dataset build defer expensive
    full provenance decoding without issuing another Parquet read.
    """

    project_id: str
    catalog_id: str | None
    schema_version: int | None
    snapshot_id: str
    loaded_at: str
    scene_ids: tuple[str, ...]
    tile_columns: tuple[str, ...]
    link_columns: tuple[str, ...]
    tiles_by_scene: Mapping[str, tuple[Mapping[str, Any], ...]]
    links_by_scene: Mapping[str, tuple[Mapping[str, Any], ...]]
    tile_count: int
    link_count: int
    manifest: Mapping[str, Any]
    _retained_link_table: Any = field(default=None, repr=False, compare=False)

    def tiles_for_scene(self, scene_id: str) -> tuple[Mapping[str, Any], ...]:
        return self.tiles_by_scene.get(str(scene_id), ())

    def links_for_scene(self, scene_id: str) -> tuple[Mapping[str, Any], ...]:
        return self.links_by_scene.get(str(scene_id), ())

    def materialize_links(
        self,
        *,
        columns: Sequence[str] | None = None,
        json_fields: set[str] | frozenset[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Materialize retained link columns without reading Parquet again."""

        table = self._retained_link_table
        if table is None or table.num_columns == 0:
            return [
                _thaw_mapping(row)
                for scene_id in self.scene_ids
                for row in self.links_for_scene(scene_id)
            ]
        if columns is not None:
            available = set(table.column_names)
            selected = [str(column) for column in columns if str(column) in available]
            table = table.select(selected) if selected else table.slice(0, 0)
        rows = table.to_pylist() if table.num_columns else []
        _decode_json_rows(rows, LINK_JSON_FIELDS if json_fields is None else json_fields)
        return rows


def ensure_tile_catalog(project_id: str) -> dict[str, Any]:
    expected_id, _payload = compute_catalog_identity(project_id)
    current = get_active_catalog_manifest(project_id, auto_migrate=True)
    if current and current.get("catalog_id") == expected_id:
        return current
    existing = catalog_dir(project_id, expected_id) / "tile_catalog_manifest.json"
    if existing.is_file():
        set_active_catalog(project_id, expected_id)
        return read_json(existing, {})
    return build_tile_catalog(project_id)


def build_tile_catalog(project_id: str) -> dict[str, Any]:
    catalog_id, identity_payload = compute_catalog_identity(project_id)
    current = get_active_catalog_manifest(project_id, auto_migrate=False)
    previous_state = collect_previous_review_state(project_id, current)
    config = TilingConfig(**load_json(project_id, "tiling_config", default={"tile_size": 640, "buffer": 0}))
    rows: list[dict[str, Any]] = []
    scene_summaries: list[dict[str, Any]] = []

    for scene_id in list_scene_ids(project_id):
        scene = load_scene_json(project_id, scene_id, "scene", default={})
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        image = manifest.get("image") or {}
        width = image.get("width")
        height = image.get("height")
        if not width or not height:
            source_path = resolve_project_scene_path(project_id, scene_id)
            if not source_path:
                raise FileNotFoundError(f"Scene file not found: {scene.get('filename') or scene_id}")
            info = get_scene_info(source_path)
            width, height = info.width, info.height
        preview = compute_grid(int(width), int(height), config)
        scene_name = Path(scene.get("filename") or scene_id).stem
        source_uid = manifest.get("source_scene_uid") or f"legacy:{scene_id}"
        geo_model = try_scene_geo_model(manifest)  # once per scene (TPS owns a transformer)
        for index, (x0, y0, x1, y1) in enumerate(preview.tile_rects):
            row_index = index // preview.num_cols + 1
            col_index = index % preview.num_cols + 1
            filename = f"{col_index}_{row_index}_{scene_name}.png"
            tile_id = stable_tile_id(scene_id, filename, x0, y0, config.tile_size)
            rows.append({
                "tile_id": tile_id,
                "grid_cell_id": tile_id,
                "scene_id": scene_id,
                "source_scene_uid": source_uid,
                "filename": filename,
                "col": col_index,
                "row": row_index,
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
                "tile_size": config.tile_size,
                **tile_geospatial_fields(manifest, x0, y0, x1, y1, geo_model),
            })
        scene_summaries.append({
            "scene_id": scene_id,
            "source_scene_uid": source_uid,
            "filename": scene.get("filename"),
            "width": int(width),
            "height": int(height),
            "tile_count": preview.total_tiles,
            **_scene_metadata_summary(scene, manifest),
        })
    return persist_tile_catalog(
        project_id,
        catalog_id,
        identity_payload,
        rows,
        scene_summaries,
        previous_state,
        migrated_from_legacy=False,
    )


def migrate_legacy_tile_catalog(project_id: str) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    scene_summaries: list[dict[str, Any]] = []
    config = TilingConfig(**load_json(project_id, "tiling_config", default={"tile_size": 640, "buffer": 0}))
    previous_state: dict[str, dict[str, bool]] = {}
    for scene_id in list_scene_ids(project_id):
        legacy_tiles = load_scene_json(project_id, scene_id, "tiles", default=[])
        if not legacy_tiles:
            continue
        scene = load_scene_json(project_id, scene_id, "scene", default={})
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        source_uid = manifest.get("source_scene_uid") or f"legacy:{scene_id}"
        geo_model = try_scene_geo_model(manifest)  # once per scene (TPS owns a transformer)
        for item in legacy_tiles:
            filename = str(item.get("filename") or "")
            x0, y0 = int(item.get("x0", 0)), int(item.get("y0", 0))
            tile_id = stable_tile_id(scene_id, filename, x0, y0, config.tile_size)
            rows.append({
                "tile_id": tile_id,
                "grid_cell_id": tile_id,
                "scene_id": scene_id,
                "source_scene_uid": source_uid,
                "filename": filename,
                "col": int(item.get("col", 0)),
                "row": int(item.get("row", 0)),
                "x0": x0,
                "y0": y0,
                "x1": x0 + config.tile_size,
                "y1": y0 + config.tile_size,
                "tile_size": config.tile_size,
                **tile_geospatial_fields(
                    manifest,
                    x0,
                    y0,
                    x0 + config.tile_size,
                    y0 + config.tile_size,
                    geo_model,
                ),
            })
            previous_state[tile_id] = {
                "reviewed": bool(item.get("reviewed", False)),
                "excluded": bool(item.get("excluded", False)),
            }
        scene_summaries.append({
            "scene_id": scene_id,
            "source_scene_uid": source_uid,
            "filename": scene.get("filename"),
            "tile_count": len(legacy_tiles),
            **_scene_metadata_summary(scene, manifest),
        })
    if not rows:
        return None
    catalog_id, identity_payload = compute_catalog_identity(project_id)
    return persist_tile_catalog(
        project_id,
        catalog_id,
        identity_payload,
        rows,
        scene_summaries,
        previous_state,
        migrated_from_legacy=True,
    )


def persist_tile_catalog(
    project_id: str,
    catalog_id: str,
    identity_payload: dict[str, Any],
    rows: list[dict[str, Any]],
    scene_summaries: list[dict[str, Any]],
    previous_state: dict[str, dict[str, Any]],
    *,
    migrated_from_legacy: bool,
) -> dict[str, Any]:
    root = catalog_dir(project_id, catalog_id)
    root.mkdir(parents=True, exist_ok=True)
    config = TilingConfig(**load_json(project_id, "tiling_config", default={"tile_size": 640, "buffer": 0}))
    dataset_config = load_json(project_id, "dataset_config", default={})
    min_box_fraction = float(dataset_config.get("min_box_fraction", 0.3))
    review_state = {
        "schema_name": "geotile_review_grid_state",
        "schema_version": 2,
        "catalog_id": catalog_id,
        "updated_at": utc_now(),
        "tiles": {
            row["tile_id"]: normalize_review_state_item(previous_state.get(row["tile_id"]))
            for row in rows
        },
    }
    links: list[dict[str, Any]] = []
    classes = load_json(project_id, "classes", default=[])
    class_names = {int(item["id"]): item.get("name") for item in classes if "id" in item}
    rows_by_scene: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_scene[str(row["scene_id"])].append(row)
    for scene_id, scene_rows in rows_by_scene.items():
        recompute_scene_attributes(project_id, scene_id)
        raw_annotations = load_scene_json(project_id, scene_id, "annotations", default=[])
        annotations = [Annotation(**item) for item in raw_annotations]
        annotations_by_id = {
            str(item.get("source_annotation_id") or item.get("id")): item
            for item in raw_annotations
        }
        tiles = [tile_info_from_row(row, review_state) for row in scene_rows]
        _tile_annotations, tile_links = propagate_annotations_with_attributes(
            annotations,
            tiles,
            config,
            min_box_fraction,
        )
        catalog_tile_ids = {row["filename"]: row["tile_id"] for row in scene_rows}
        for filename, values in tile_links.items():
            for value in values:
                link = dict(value)
                link["catalog_id"] = catalog_id
                link["catalog_tile_id"] = catalog_tile_ids.get(filename)
                source = annotations_by_id.get(str(link.get("source_annotation_id")), {})
                link["class_name"] = class_names.get(int(link.get("class_id", -1)))
                link["annotator_email"] = source.get("annotator_email")
                link["annotation_source"] = source.get("annotation_source") or "manual"
                link["import_id"] = source.get("import_id")
                link["source_package_id"] = source.get("source_package_id")
                links.append(link)

    write_parquet(root / "tiles.parquet", rows)
    write_parquet(root / "tile_annotation_links.parquet", links)
    write_json(root / "review_state.json", review_state)
    positive_tile_ids = {str(link.get("catalog_tile_id")) for link in links if link.get("exportable_yolo")}
    statistics = {
        "schema_name": "geotile_tile_catalog_statistics",
        "schema_version": 1,
        "catalog_id": catalog_id,
        "scene_count": len(rows_by_scene),
        "tile_count": len(rows),
        "positive_tile_count": len(positive_tile_ids),
        "annotation_link_count": len(links),
        "reviewed_count": sum(1 for item in review_state["tiles"].values() if item.get("reviewed")),
        "excluded_count": sum(1 for item in review_state["tiles"].values() if item.get("excluded")),
        "class_counts": count_values(links, "class_name"),
        "author_counts": count_values(links, "annotator_email"),
        "annotation_source_counts": count_values(links, "annotation_source"),
    }
    write_json(root / "catalog_statistics.json", statistics)
    manifest = {
        "schema_name": "geotile_tile_catalog",
        "schema_version": TILE_CATALOG_SCHEMA_VERSION,
        "catalog_id": catalog_id,
        "created_at": utc_now(),
        "status": "ready",
        "semantic_role": "review_grid",
        "materialization": "metadata_only",
        "migrated_from_legacy": migrated_from_legacy,
        "identity": identity_payload,
        "tiling_config": config.model_dump(),
        "min_box_fraction": min_box_fraction,
        "propagation_version": PROPAGATION_VERSION,
        "scene_count": len(rows_by_scene),
        "tile_count": len(rows),
        "annotation_link_count": len(links),
        "scenes": scene_summaries,
        "filter_options": build_filter_options(scene_summaries, classes, links),
        "artifacts": {
            "tiles": "tiles.parquet",
            "tile_annotation_links": "tile_annotation_links.parquet",
            "review_state": "review_state.json",
            "statistics": "catalog_statistics.json",
        },
    }
    write_json(root / "tile_catalog_manifest.json", manifest)
    register_catalog(project_id, manifest)
    for scene_id, scene_rows in rows_by_scene.items():
        scene = load_scene_json(project_id, scene_id, "scene", default={})
        scene["status"] = "cataloged"
        scene["tile_count"] = len(scene_rows)
        scene["tile_catalog_id"] = catalog_id
        save_scene_json(project_id, scene_id, "scene", scene)
    return manifest


def compute_catalog_identity(project_id: str) -> tuple[str, dict[str, Any]]:
    config = TilingConfig(**load_json(project_id, "tiling_config", default={"tile_size": 640, "buffer": 0}))
    dataset_config = load_json(project_id, "dataset_config", default={})
    classes = load_json(project_id, "classes", default=[])
    scenes = []
    for scene_id in list_scene_ids(project_id):
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        annotations = load_scene_json(project_id, scene_id, "annotations", default=[])
        annotation_payload = [
            {
                "source_annotation_id": item.get("source_annotation_id") or item.get("id"),
                "class_id": item.get("class_id"),
                "geometry_type": item.get("geometry_type"),
                "bbox": item.get("bbox"),
                "polygon_scene_px": item.get("polygon_scene_px"),
                "orientation_angle_deg": item.get("orientation_angle_deg"),
                "updated_at": item.get("updated_at"),
            }
            for item in annotations
        ]
        scenes.append({
            "scene_id": scene_id,
            "source_scene_uid": manifest.get("source_scene_uid") or f"legacy:{scene_id}",
            "annotation_hash": canonical_hash(annotation_payload),
            "attribute_context_hash": canonical_hash({
                "schema_version": manifest.get("schema_version"),
                "source_scene_uid": manifest.get("source_scene_uid"),
                "provider": manifest.get("provider"),
                "sensor": manifest.get("sensor"),
                "modality": manifest.get("modality"),
                "acquisition_datetime_utc": manifest.get("acquisition_datetime_utc"),
                "geospatial": manifest.get("geospatial"),
                "geometry": manifest.get("geometry"),
            }),
        })
    payload = {
        "catalog_schema_version": TILE_CATALOG_SCHEMA_VERSION,
        "source_scenes": sorted(scenes, key=lambda item: str(item["source_scene_uid"])),
        "tile_size": config.tile_size,
        "buffer": config.buffer,
        "min_box_fraction": float(dataset_config.get("min_box_fraction", 0.3)),
        "propagation_version": PROPAGATION_VERSION,
        "attribute_version": ATTRIBUTE_VERSION,
        "classes": sorted(
            [
                {"id": item.get("id"), "name": item.get("name")}
                for item in classes
                if item.get("id") is not None
            ],
            key=lambda item: str(item.get("id")),
        ),
    }
    return canonical_hash(payload)[:24], payload


def get_active_catalog_manifest(project_id: str, *, auto_migrate: bool = True) -> dict[str, Any] | None:
    index = read_json(catalog_index_path(project_id), {})
    catalog_id = index.get("active_catalog_id")
    if catalog_id:
        path = catalog_dir(project_id, str(catalog_id)) / "tile_catalog_manifest.json"
        if path.is_file():
            return read_json(path, {})
    if auto_migrate:
        return migrate_legacy_tile_catalog(project_id)
    return None


def load_catalog_snapshot(
    project_id: str,
    scene_ids: Sequence[str] | set[str] | None = None,
    tile_columns: Sequence[str] | None = None,
    link_columns: Sequence[str] | None = None,
    *,
    retain_link_table: bool = False,
) -> CatalogSnapshot:
    """Load and index one immutable catalog view for a complete operation.

    ``None`` means every column, while an empty sequence deliberately skips a table.
    A scene predicate is pushed into both Parquet scanners. Review state is read and
    merged exactly once. ``retain_link_table`` is used by dataset generation to defer
    full provenance conversion until after image materialization without a second scan.
    """

    manifest = get_active_catalog_manifest(project_id, auto_migrate=True)
    requested_scene_ids = None if scene_ids is None else tuple(sorted({str(value) for value in scene_ids}))
    if not manifest:
        return CatalogSnapshot(
            project_id=project_id,
            catalog_id=None,
            schema_version=None,
            snapshot_id=canonical_hash({"project_id": project_id, "catalog_id": None}),
            loaded_at=utc_now(),
            scene_ids=requested_scene_ids or (),
            tile_columns=tuple(tile_columns or ()),
            link_columns=tuple(link_columns or ()),
            tiles_by_scene=MappingProxyType({}),
            links_by_scene=MappingProxyType({}),
            tile_count=0,
            link_count=0,
            manifest=MappingProxyType({}),
        )

    root = catalog_dir(project_id, str(manifest["catalog_id"]))
    scene_filter = None if requested_scene_ids is None else set(requested_scene_ids)
    tile_path = root / "tiles.parquet"
    link_path = root / "tile_annotation_links.parquet"
    review_path = root / "review_state.json"

    tile_request = None if tile_columns is None else tuple(dict.fromkeys(str(v) for v in tile_columns))
    link_request = None if link_columns is None else tuple(dict.fromkeys(str(v) for v in link_columns))
    tile_scan_columns = _projection_with_required(tile_request, ("scene_id", "tile_id"))
    link_scan_columns = None if retain_link_table else _projection_with_required(
        link_request, ("scene_id",)
    )

    tile_table = None
    if tile_request != ():
        tile_table = scan_parquet_table(
            tile_path,
            columns=tile_scan_columns,
            scene_ids=scene_filter,
        )
    retained_link_table = None
    if link_request != () or retain_link_table:
        retained_link_table = scan_parquet_table(
            link_path,
            columns=link_scan_columns,
            scene_ids=scene_filter,
        )

    review_document = read_json(review_path, {}) if tile_table is not None else {}
    review_state = review_document.get("tiles") or {}
    tile_rows = tile_table.to_pylist() if tile_table is not None and tile_table.num_columns else []
    _decode_json_rows(tile_rows, TILE_JSON_FIELDS)
    merged_tiles: list[dict[str, Any]] = []
    for row in tile_rows:
        item = dict(row)
        item.setdefault("grid_cell_id", item.get("tile_id"))
        if "geometry_px" not in item and "geometry_scene_px" in item:
            item["geometry_px"] = item.get("geometry_scene_px")
        normalized_state = normalize_review_state_item(
            review_state.get(str(item.get("tile_id")), {})
        )
        item.update(normalized_state)
        item["reviewed"] = normalized_state["review_status"] == "reviewed"
        item["excluded"] = bool(normalized_state["exclude_from_dataset"])
        merged_tiles.append(item)

    link_rows: list[dict[str, Any]] = []
    if retained_link_table is not None and retained_link_table.num_columns and link_request != ():
        projected = retained_link_table
        if link_request is not None:
            requested = _projection_with_required(link_request, ("scene_id",)) or ()
            available = set(projected.column_names)
            selected = [column for column in requested if column in available]
            projected = projected.select(selected) if selected else projected.slice(0, 0)
        link_rows = projected.to_pylist() if projected.num_columns else []
        _decode_json_rows(link_rows, LINK_JSON_FIELDS)

    tiles_by_scene = _freeze_grouped_rows(merged_tiles, sort_tiles=True)
    links_by_scene = _freeze_grouped_rows(link_rows, sort_tiles=False)
    resolved_scene_ids = requested_scene_ids or tuple(sorted(set(tiles_by_scene) | set(links_by_scene)))
    revision = {
        "catalog_id": manifest.get("catalog_id"),
        "catalog_schema_version": manifest.get("schema_version"),
        "tiles": _file_revision(tile_path),
        "links": _file_revision(link_path),
        "review_state_hash": canonical_hash({
            str(row.get("tile_id")): review_state.get(str(row.get("tile_id")), {})
            for row in tile_rows
            if row.get("tile_id") is not None
        }),
        "scene_ids": list(resolved_scene_ids),
    }
    resolved_tile_columns = tuple(tile_table.column_names) if tile_table is not None else ()
    resolved_link_columns = (
        tuple(link_rows[0].keys()) if link_rows else tuple(link_request or ())
    )
    return CatalogSnapshot(
        project_id=project_id,
        catalog_id=str(manifest.get("catalog_id")),
        schema_version=manifest.get("schema_version"),
        snapshot_id=canonical_hash(revision),
        loaded_at=utc_now(),
        scene_ids=resolved_scene_ids,
        tile_columns=resolved_tile_columns,
        link_columns=resolved_link_columns,
        tiles_by_scene=MappingProxyType(tiles_by_scene),
        links_by_scene=MappingProxyType(links_by_scene),
        tile_count=sum(len(rows) for rows in tiles_by_scene.values()),
        link_count=sum(len(rows) for rows in links_by_scene.values()),
        manifest=_freeze_mapping(manifest),
        _retained_link_table=retained_link_table if retain_link_table else None,
    )


def get_catalog_tiles(project_id: str, scene_id: str | None = None) -> list[dict[str, Any]]:
    snapshot = load_catalog_snapshot(
        project_id,
        scene_ids=[scene_id] if scene_id else None,
        tile_columns=None,
        link_columns=(),
    )
    scene_keys = [scene_id] if scene_id else snapshot.scene_ids
    return [
        _thaw_mapping(row)
        for key in scene_keys
        for row in snapshot.tiles_for_scene(str(key))
    ]


def get_catalog_filter_options(project_id: str) -> dict[str, Any]:
    manifest = get_active_catalog_manifest(project_id, auto_migrate=True)
    if not manifest:
        return {
            "catalog_id": None,
            "scenes": [],
            "classes": [],
            "authors": [],
            "annotation_sources": [],
            "metadata_ranges": {
                "gsd_m": None,
                "incidence_angle_deg": None,
                "acquisition_datetime_utc": None,
            },
        }
    manifest = ensure_tile_catalog(project_id)
    options = manifest.get("filter_options") or {}
    return {"catalog_id": manifest.get("catalog_id"), **options}


def get_catalog_links(project_id: str, scene_id: str | None = None) -> list[dict[str, Any]]:
    snapshot = load_catalog_snapshot(
        project_id,
        scene_ids=[scene_id] if scene_id else None,
        tile_columns=(),
        link_columns=None,
    )
    scene_keys = [scene_id] if scene_id else snapshot.scene_ids
    return [
        _thaw_mapping(row)
        for key in scene_keys
        for row in snapshot.links_for_scene(str(key))
    ]


def update_review_state(
    project_id: str,
    scene_id: str,
    tile_indices: list[int],
    *,
    reviewed: bool | None = None,
    excluded: bool | None = None,
) -> dict[str, Any]:
    manifest = get_active_catalog_manifest(project_id, auto_migrate=True)
    if not manifest:
        raise FileNotFoundError("No tile catalog found")
    root = catalog_dir(project_id, manifest["catalog_id"])
    path = root / "review_state.json"
    state = read_json(path, {})
    states = state.setdefault("tiles", {})
    reviewer = project_reviewer(project_id)
    tiles = get_catalog_tiles(project_id, scene_id)
    for index in tile_indices:
        if index < 0 or index >= len(tiles):
            continue
        tile_state = normalize_review_state_item(states.get(tiles[index]["tile_id"]))
        if reviewed is not None:
            tile_state["review_status"] = "reviewed" if reviewed else "unreviewed"
            tile_state["reviewed"] = reviewed
            if reviewed:
                tile_state["reviewed_at"] = utc_now()
                tile_state["reviewed_by"] = reviewer
                tile_state["exclude_from_dataset"] = False
                tile_state["excluded"] = False
            else:
                tile_state["reviewed_at"] = None
                tile_state["reviewed_by"] = None
        if excluded is not None:
            tile_state["exclude_from_dataset"] = excluded
            tile_state["excluded"] = excluded
            if excluded:
                tile_state["review_status"] = "unreviewed"
                tile_state["reviewed"] = False
                tile_state["reviewed_at"] = None
                tile_state["reviewed_by"] = None
        states[tiles[index]["tile_id"]] = normalize_review_state_item(tile_state)
    state["updated_at"] = utc_now()
    write_json(path, state)
    update_catalog_statistics(project_id, manifest["catalog_id"])
    return compute_tile_progress(project_id, scene_id)


def compute_tile_progress(project_id: str, scene_id: str) -> dict[str, int]:
    tiles = get_catalog_tiles(project_id, scene_id)
    annotations = load_scene_json(project_id, scene_id, "annotations", default=[])
    tile_size = int((get_active_catalog_manifest(project_id) or {}).get("tiling_config", {}).get("tile_size", 640))
    positive_indices = set()
    for index, tile in enumerate(tiles):
        for annotation in annotations:
            bbox = annotation.get("bbox") or [0, 0, 0, 0]
            if (
                bbox[0] < tile["x0"] + tile_size and bbox[2] > tile["x0"]
                and bbox[1] < tile["y0"] + tile_size and bbox[3] > tile["y0"]
            ):
                positive_indices.add(index)
                break
    excluded_count = sum(1 for tile in tiles if tile.get("excluded"))
    reviewed_indices = [
        index for index, tile in enumerate(tiles)
        if tile.get("review_status") == "reviewed" and not tile.get("exclude_from_dataset")
    ]
    positive = sum(1 for index in reviewed_indices if index in positive_indices)
    return {
        "total_all": len(tiles),
        "total": len(tiles) - excluded_count,
        "reviewed": len(reviewed_indices),
        "positive": positive,
        "negative": len(reviewed_indices) - positive,
        "excluded": excluded_count,
    }


def render_tile_preview(project_id: str, tile_id: str) -> Path:
    manifest = get_active_catalog_manifest(project_id, auto_migrate=True)
    if not manifest:
        raise FileNotFoundError("No tile catalog found")
    row = next((item for item in get_catalog_tiles(project_id) if item.get("tile_id") == tile_id), None)
    if not row:
        raise FileNotFoundError("Tile not found")
    root = catalog_dir(project_id, manifest["catalog_id"])
    output = root / "preview_cache" / f"{tile_id}.png"
    if output.is_file():
        return output
    source_path = resolve_project_scene_path(project_id, str(row["scene_id"]))
    if not source_path:
        raise FileNotFoundError("Source scene not found")
    project = load_json(project_id, "project", default={})
    dataset_config = load_json(project_id, "dataset_config", default={})
    profile = resolve_preprocessing_profile(
        project_id,
        dataset_config.get("preprocessing_profile_id"),
        project.get("profile") or {},
    )
    write_preprocessed_tile(
        source_path,
        int(row["x0"]),
        int(row["y0"]),
        int(row.get("tile_size") or manifest["tiling_config"]["tile_size"]),
        profile,
        output,
    )
    return output


def legacy_cache_info(project_id: str) -> dict[str, int]:
    paths = [project_dir(project_id) / "scenes" / scene_id / "tiles" / "images" for scene_id in list_scene_ids(project_id)]
    files = [path for root in paths if root.is_dir() for path in root.rglob("*") if path.is_file()]
    return {"file_count": len(files), "reclaimable_bytes": sum(path.stat().st_size for path in files)}


def clear_legacy_tile_cache(project_id: str) -> dict[str, int]:
    before = legacy_cache_info(project_id)
    for scene_id in list_scene_ids(project_id):
        images = project_dir(project_id) / "scenes" / scene_id / "tiles" / "images"
        if images.is_dir():
            shutil.rmtree(images)
    return {**before, "removed_files": before["file_count"], "removed_bytes": before["reclaimable_bytes"]}


def _preview_cache_files(project_id: str) -> list[Path]:
    root = project_dir(project_id) / "tile_catalogs"
    caches = list(root.glob("*/preview_cache")) if root.is_dir() else []
    return [path for cache in caches for path in cache.rglob("*") if path.is_file()]


def preview_cache_info(project_id: str) -> dict[str, int]:
    """Rozmiar cache podglądów kafli (na żądanie renderowane PNG-i)."""
    files = _preview_cache_files(project_id)
    return {"file_count": len(files), "reclaimable_bytes": sum(path.stat().st_size for path in files)}


def clear_preview_cache(project_id: str) -> dict[str, int]:
    root = project_dir(project_id) / "tile_catalogs"
    caches = list(root.glob("*/preview_cache")) if root.is_dir() else []
    files = [path for cache in caches for path in cache.rglob("*") if path.is_file()]
    size = sum(path.stat().st_size for path in files)
    for cache in caches:
        shutil.rmtree(cache, ignore_errors=True)
    return {"removed_files": len(files), "removed_bytes": size}


def collect_previous_review_state(
    project_id: str,
    current_manifest: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if current_manifest:
        state = read_json(
            catalog_dir(project_id, current_manifest["catalog_id"]) / "review_state.json",
            {},
        ).get("tiles") or {}
        result.update({str(key): normalize_review_state_item(value) for key, value in state.items()})
    config = TilingConfig(**load_json(project_id, "tiling_config", default={"tile_size": 640, "buffer": 0}))
    for scene_id in list_scene_ids(project_id):
        for item in load_scene_json(project_id, scene_id, "tiles", default=[]):
            tile_id = stable_tile_id(
                scene_id,
                str(item.get("filename") or ""),
                int(item.get("x0", 0)),
                int(item.get("y0", 0)),
                config.tile_size,
            )
            result.setdefault(tile_id, normalize_review_state_item({
                "reviewed": bool(item.get("reviewed", False)),
                "excluded": bool(item.get("excluded", False)),
            }))
    return result


def update_catalog_statistics(project_id: str, catalog_id: str) -> None:
    root = catalog_dir(project_id, catalog_id)
    statistics = read_json(root / "catalog_statistics.json", {})
    states = [
        normalize_review_state_item(item)
        for item in (read_json(root / "review_state.json", {}).get("tiles") or {}).values()
    ]
    statistics["reviewed_count"] = sum(1 for item in states if item.get("review_status") == "reviewed")
    statistics["excluded_count"] = sum(1 for item in states if item.get("exclude_from_dataset"))
    write_json(root / "catalog_statistics.json", statistics)


TILE_TPS_DENSIFY_PX = 64.0


def tile_geospatial_fields(
    manifest: dict[str, Any],
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    geo_model: Any = None,
) -> dict[str, Any]:
    fields = {
        "geometry_px": [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]],
        "geometry_scene_px": [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]],
        "native_crs": None,
        "geometry_native": None,
        "geometry_wgs84": None,
        "bbox_lonlat": None,
        "centroid_lon": None,
        "centroid_lat": None,
    }

    # Sensor-geometry scenes (NITF): non-linear GCP TPS. No affine transform and
    # no metric "native" CRS — geometry is derived directly to WGS84 with edge
    # densification (spec §15). Build the model once per scene and pass it in.
    if (manifest.get("geometry") or {}).get("model") == "gcp_tps":
        if geo_model is None:
            geo_model = try_scene_geo_model(manifest)
        if geo_model is None:
            return fields
        try:
            corners = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
            ring = geo_model.pixel_to_wgs84(corners + [corners[0]], densify_px=TILE_TPS_DENSIFY_PX)
            corner_wgs84 = geo_model.pixel_to_wgs84(corners)
            longitudes = [float(point[0]) for point in ring]
            latitudes = [float(point[1]) for point in ring]
            fields.update({
                "native_crs": None,
                "geometry_native": None,
                "geometry_wgs84": ring,
                "bbox_lonlat": [min(longitudes), min(latitudes), max(longitudes), max(latitudes)],
                "centroid_lon": sum(point[0] for point in corner_wgs84) / len(corner_wgs84),
                "centroid_lat": sum(point[1] for point in corner_wgs84) / len(corner_wgs84),
            })
        except Exception:
            pass
        return fields

    # --- affine scenes (satellite): unchanged, byte-for-byte ---
    geospatial = manifest.get("geospatial") or {}
    transform_values = geospatial.get("transform")
    crs = geospatial.get("crs")
    if not geospatial.get("has_geo") or not transform_values or not crs:
        return fields
    try:
        pixel_polygon = fields["geometry_scene_px"]
        native = [pixel_to_native(point, transform_values) for point in pixel_polygon]
        wgs84 = transform_points(native, str(crs), "EPSG:4326")
        longitudes = [float(point[0]) for point in wgs84]
        latitudes = [float(point[1]) for point in wgs84]
        fields.update({
            "native_crs": str(crs),
            "geometry_native": native,
            "geometry_wgs84": wgs84,
            "bbox_lonlat": [min(longitudes), min(latitudes), max(longitudes), max(latitudes)],
            "centroid_lon": sum(longitudes[:-1]) / max(1, len(longitudes) - 1),
            "centroid_lat": sum(latitudes[:-1]) / max(1, len(latitudes) - 1),
        })
    except Exception:
        pass
    return fields


def count_values(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        value = row.get(key)
        if value not in (None, ""):
            counts[str(value)] += 1
    return dict(sorted(counts.items()))


def _scene_metadata_summary(scene: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    """Metadata used by the Build-tab range filters (GSD, SAR incidence, acquisition)."""
    sar = manifest.get("sar") or {}
    gsd = scene.get("gsd_m")
    if gsd is None:
        gsd = manifest.get("gsd_m")
    return {
        "gsd_m": gsd,
        "incidence_angle_deg": sar.get("incidence_angle_deg"),
        "acquisition_datetime_utc": scene.get("acquisition_datetime_utc")
        or manifest.get("acquisition_datetime_utc"),
    }


def _numeric_range(values: list[Any]) -> dict[str, Any] | None:
    nums = [float(value) for value in values if isinstance(value, (int, float))]
    if not nums:
        return None
    return {"min": min(nums), "max": max(nums), "count": len(nums)}


def _datetime_range(values: list[Any]) -> dict[str, Any] | None:
    strings = sorted({str(value) for value in values if isinstance(value, str) and value.strip()})
    if not strings:
        return None
    return {"min": strings[0], "max": strings[-1], "count": len(strings)}


def build_filter_options(
    scenes: list[dict[str, Any]],
    classes: list[dict[str, Any]],
    links: list[dict[str, Any]],
) -> dict[str, Any]:
    class_counts: dict[int, int] = defaultdict(int)
    scene_counts: dict[str, int] = defaultdict(int)
    author_counts: dict[str, int] = defaultdict(int)
    source_counts: dict[str, int] = defaultdict(int)
    for link in links:
        if link.get("class_id") is not None:
            class_counts[int(link["class_id"])] += 1
        scene_counts[str(link.get("scene_id") or "")] += 1
        if link.get("annotator_email"):
            author_counts[str(link["annotator_email"])] += 1
        source_counts[str(link.get("annotation_source") or "manual")] += 1
    return {
        "scenes": [
            {
                "scene_id": scene.get("scene_id"),
                "filename": scene.get("filename") or scene.get("scene_id"),
                "tile_count": scene.get("tile_count", 0),
                "annotation_link_count": scene_counts.get(str(scene.get("scene_id")), 0),
                "gsd_m": scene.get("gsd_m"),
                "incidence_angle_deg": scene.get("incidence_angle_deg"),
                "acquisition_datetime_utc": scene.get("acquisition_datetime_utc"),
            }
            for scene in scenes
        ],
        "metadata_ranges": {
            "gsd_m": _numeric_range([scene.get("gsd_m") for scene in scenes]),
            "incidence_angle_deg": _numeric_range(
                [scene.get("incidence_angle_deg") for scene in scenes]
            ),
            "acquisition_datetime_utc": _datetime_range(
                [scene.get("acquisition_datetime_utc") for scene in scenes]
            ),
        },
        "classes": [
            {
                "class_id": int(item["id"]),
                "name": item.get("name") or str(item["id"]),
                "annotation_link_count": class_counts.get(int(item["id"]), 0),
            }
            for item in classes
            if "id" in item
        ],
        "authors": [
            {"email": email, "annotation_link_count": count}
            for email, count in sorted(author_counts.items())
        ],
        "annotation_sources": [
            {"source": source, "annotation_link_count": count}
            for source, count in sorted(source_counts.items())
        ],
    }


def tile_info_from_row(row: dict[str, Any], review_state: dict[str, Any]) -> TileInfo:
    state = normalize_review_state_item((review_state.get("tiles") or {}).get(row["tile_id"], {}))
    return TileInfo(
        tile_id=row["tile_id"],
        grid_cell_id=row.get("grid_cell_id") or row["tile_id"],
        scene_id=row["scene_id"],
        filename=row["filename"],
        col=int(row["col"]),
        row=int(row["row"]),
        x0=int(row["x0"]),
        y0=int(row["y0"]),
        x1=int(row["x1"]) if row.get("x1") is not None else None,
        y1=int(row["y1"]) if row.get("y1") is not None else None,
        geometry_px=row.get("geometry_px"),
        geometry_scene_px=row.get("geometry_scene_px"),
        geometry_wgs84=row.get("geometry_wgs84"),
        review_status=state["review_status"],
        exclude_from_dataset=bool(state["exclude_from_dataset"]),
        reviewed_at=state.get("reviewed_at"),
        reviewed_by=state.get("reviewed_by"),
    )


def normalize_review_state_item(value: Any) -> dict[str, Any]:
    item = dict(value or {}) if isinstance(value, dict) else {}
    review_status = item.get("review_status")
    if review_status not in ("unreviewed", "reviewed"):
        review_status = "reviewed" if item.get("reviewed") else "unreviewed"
    exclude_from_dataset = bool(item.get("exclude_from_dataset", item.get("excluded", False)))
    if exclude_from_dataset:
        review_status = "unreviewed"
    return {
        "review_status": review_status,
        "exclude_from_dataset": exclude_from_dataset,
        "reviewed_at": item.get("reviewed_at") if review_status == "reviewed" else None,
        "reviewed_by": item.get("reviewed_by") if review_status == "reviewed" else None,
        "reviewed": review_status == "reviewed",
        "excluded": exclude_from_dataset,
    }


def project_reviewer(project_id: str) -> str | None:
    project = load_json(project_id, "project", default={})
    profile = project.get("profile") or {}
    reviewer = profile.get("labeling_author_email")
    return str(reviewer).strip().lower() if reviewer else None


def resolve_project_scene_path(project_id: str, scene_id: str) -> Path | None:
    try:
        return resolve_scene_raster(project_id, scene_id)
    except FileNotFoundError:
        return None


def register_catalog(project_id: str, manifest: dict[str, Any]) -> None:
    path = catalog_index_path(project_id)
    index = read_json(path, {
        "schema_name": "geotile_tile_catalogs_index",
        "schema_version": 1,
        "catalogs": [],
    })
    entries = [item for item in index.get("catalogs", []) if item.get("catalog_id") != manifest["catalog_id"]]
    entries.append({
        "catalog_id": manifest["catalog_id"],
        "created_at": manifest["created_at"],
        "tile_count": manifest["tile_count"],
        "scene_count": manifest["scene_count"],
        "status": manifest["status"],
    })
    index["catalogs"] = sorted(entries, key=lambda item: item.get("created_at", ""), reverse=True)
    index["active_catalog_id"] = manifest["catalog_id"]
    index["updated_at"] = utc_now()
    write_json(path, index)


def set_active_catalog(project_id: str, catalog_id: str) -> None:
    path = catalog_index_path(project_id)
    index = read_json(path, {"schema_name": "geotile_tile_catalogs_index", "schema_version": 1, "catalogs": []})
    index["active_catalog_id"] = catalog_id
    index["updated_at"] = utc_now()
    write_json(path, index)


def catalog_index_path(project_id: str) -> Path:
    return project_dir(project_id) / "tile_catalogs" / "index.json"


def catalog_dir(project_id: str, catalog_id: str) -> Path:
    return project_dir(project_id) / "tile_catalogs" / catalog_id


def read_parquet_rows(path: Path) -> list[dict[str, Any]]:
    return scan_parquet_rows(path)


def _projection_with_required(
    columns: tuple[str, ...] | None,
    required: tuple[str, ...],
) -> tuple[str, ...] | None:
    if columns is None:
        return None
    if columns == ():
        return ()
    return tuple(dict.fromkeys((*required, *columns)))


def _decode_json_rows(
    rows: list[dict[str, Any]],
    fields: set[str] | frozenset[str],
) -> None:
    for row in rows:
        for field_name in fields:
            value = row.get(field_name)
            if isinstance(value, str) and value.startswith(("[", "{")):
                try:
                    row[field_name] = json.loads(value)
                except json.JSONDecodeError:
                    pass


def _freeze_value(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_value(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})


def _thaw_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    return value


def _thaw_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _thaw_value(item) for key, item in value.items()}


def _freeze_grouped_rows(
    rows: list[dict[str, Any]],
    *,
    sort_tiles: bool,
) -> dict[str, tuple[Mapping[str, Any], ...]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("scene_id") or "")].append(row)
    result: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for scene_id, scene_rows in grouped.items():
        if sort_tiles:
            scene_rows.sort(key=lambda item: (int(item.get("row", 0)), int(item.get("col", 0))))
        # Rows are never mutated by snapshot consumers; the read-only mapping blocks
        # structural changes without recursively copying hundreds of thousands of
        # geometry values during preparation.
        result[scene_id] = tuple(MappingProxyType(dict(row)) for row in scene_rows)
    return result


def _file_revision(path: Path) -> dict[str, Any] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def stable_tile_id(scene_id: str, filename: str, x0: int, y0: int, tile_size: int) -> str:
    return hashlib.sha256(f"{scene_id}|{filename}|{x0}|{y0}|{tile_size}".encode("utf-8")).hexdigest()[:16]


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))
