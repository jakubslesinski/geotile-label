"""Minimal Parquet and GeoParquet writers backed by PyArrow."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any


def require_pyarrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(
            "PyArrow is required for Parquet/GeoParquet export. Rebuild the desktop backend runtime."
        ) from exc
    return pa, pq


def scan_parquet_table(
    path: str | Path,
    *,
    columns: list[str] | tuple[str, ...] | None = None,
    scene_ids: set[str] | tuple[str, ...] | list[str] | None = None,
):
    """Read one Parquet dataset with projection and an optional scene predicate.

    ``pyarrow.dataset.Scanner`` pushes both operations into the Parquet read instead
    of materializing a complete Python row list and filtering it afterwards. Missing
    requested columns are ignored for compatibility with older catalog schemas.
    """

    source = Path(path)
    if not source.is_file():
        return None
    pa, _pq = require_pyarrow()
    import pyarrow.dataset as ds

    dataset = ds.dataset(source, format="parquet")
    available = set(dataset.schema.names)
    if available == {"_empty"}:
        return pa.table({})

    selected_columns = None
    if columns is not None:
        selected_columns = [str(column) for column in columns if str(column) in available]
        if not selected_columns:
            return pa.table({})

    predicate = None
    if scene_ids is not None and "scene_id" in available:
        normalized_scene_ids = sorted({str(value) for value in scene_ids})
        predicate = ds.field("scene_id").isin(normalized_scene_ids)

    scanner = dataset.scanner(columns=selected_columns, filter=predicate)
    return scanner.to_table()


def scan_parquet_rows(
    path: str | Path,
    *,
    columns: list[str] | tuple[str, ...] | None = None,
    scene_ids: set[str] | tuple[str, ...] | list[str] | None = None,
) -> list[dict[str, Any]]:
    """Python-row compatibility wrapper around :func:`scan_parquet_table`."""

    table = scan_parquet_table(path, columns=columns, scene_ids=scene_ids)
    if table is None or table.num_columns == 0:
        return []
    return table.to_pylist()


def write_parquet(path: str | Path, rows: list[dict[str, Any]]) -> Path:
    pa, pq = require_pyarrow()
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_rows(rows)
    if normalized:
        table = pa.Table.from_pylist(normalized)
    else:
        table = pa.table({"_empty": pa.array([], type=pa.bool_())})
    pq.write_table(table, output, compression="zstd")
    return output


def write_geoparquet(
    path: str | Path,
    rows: list[dict[str, Any]],
    *,
    geometry_key: str = "geometry",
    crs: dict[str, Any] | None | str = "default_wgs84",
) -> Path:
    pa, pq = require_pyarrow()
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_rows(rows, binary_keys={geometry_key})
    if normalized:
        table = pa.Table.from_pylist(normalized)
    else:
        table = pa.table({geometry_key: pa.array([], type=pa.binary())})
    geo_column: dict[str, Any] = {
        "encoding": "WKB",
        "geometry_types": ["Polygon"],
    }
    if crs != "default_wgs84":
        geo_column["crs"] = crs
    geo_metadata = {
        "version": "1.1.0",
        "primary_column": geometry_key,
        "columns": {geometry_key: geo_column},
    }
    metadata = dict(table.schema.metadata or {})
    metadata[b"geo"] = json.dumps(geo_metadata, separators=(",", ":")).encode("utf-8")
    table = table.replace_schema_metadata(metadata)
    pq.write_table(table, output, compression="zstd")
    return output


def polygon_wkb(geometry: dict[str, Any] | list[list[float]] | None) -> bytes | None:
    points = polygon_points(geometry)
    if len(points) < 3:
        return None
    if points[0] != points[-1]:
        points.append(points[0])
    payload = bytearray()
    payload.extend(struct.pack("<BII", 1, 3, 1))
    payload.extend(struct.pack("<I", len(points)))
    for x_value, y_value in points:
        payload.extend(struct.pack("<dd", float(x_value), float(y_value)))
    return bytes(payload)


def polygon_points(geometry: dict[str, Any] | list[list[float]] | None) -> list[list[float]]:
    if isinstance(geometry, dict):
        coordinates = geometry.get("coordinates") or []
        if geometry.get("type") == "Polygon" and coordinates:
            geometry = coordinates[0]
        else:
            return []
    if not isinstance(geometry, list):
        return []
    result = []
    for point in geometry:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            try:
                result.append([float(point[0]), float(point[1])])
            except (TypeError, ValueError):
                continue
    return result


def normalize_rows(
    rows: list[dict[str, Any]],
    *,
    binary_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    binary_keys = binary_keys or set()
    keys = sorted({str(key) for row in rows for key in row})
    result = []
    for row in rows:
        normalized = {}
        for key in keys:
            value = row.get(key)
            if key in binary_keys:
                normalized[key] = value
            elif isinstance(value, (dict, list, tuple, set)):
                normalized[key] = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            elif isinstance(value, (str, int, float, bool, bytes)) or value is None:
                normalized[key] = value
            else:
                normalized[key] = str(value)
        result.append(normalized)
    for key in keys:
        if key in binary_keys:
            continue
        values = [row[key] for row in result if row.get(key) is not None]
        kinds = {type(value) for value in values}
        if len(kinds) > 1 and not kinds.issubset({int, float}):
            for row in result:
                if row.get(key) is not None:
                    row[key] = str(row[key])
    return result
