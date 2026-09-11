"""Controlled JPEG 2000 full-resolution benchmark and workload freezer.

Stage E0 freezes immutable source identities, exact 1x windows and a Web
Mercator navigation trace.  The ``run-arm`` and ``summarize`` commands execute
the shared W0-W8 runtime contract used by E2, E3 and E5.  Sources and project
data stay read-only; every dataset open is constrained to the requested GDAL
driver and an implicit fallback fails the run.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import random
import shutil
import socket
import statistics
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterable


SCHEMA_NAME = "geotile_jp2_fullres_benchmark_manifest"
SCHEMA_VERSION = 1
DEFAULT_SEED = 20260901
DEFAULT_WINDOW_SIZE = 1024
DEFAULT_GDAL_CACHE_MIB = 256
RANDOM_WINDOW_COUNT = 60
EDGE_WINDOW_COUNT = 20
HIGH_CONTRAST_WINDOW_COUNT = 20
PROGRESSION_ORDERS = {
    0: "LRCP",
    1: "RLCP",
    2: "RPCL",
    3: "PCRL",
    4: "CPRL",
}
RUNTIME_SCHEMA_NAME = "geotile_jp2_fullres_runtime_arm"
RUNTIME_SCHEMA_VERSION = 1
WEB_MERCATOR_HALF_WORLD = 20037508.342789244


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _sha256_file(path: Path, *, chunk_size: int = 16 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    total = path.stat().st_size
    processed = 0
    next_notice = 1024 * 1024 * 1024
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
            processed += len(chunk)
            if processed >= next_notice and total >= next_notice:
                print(f"  fingerprint: {processed / (1024**3):.1f}/{total / (1024**3):.1f} GiB")
                next_notice += 1024 * 1024 * 1024
    return digest.hexdigest()


def _source_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    print(f"Fingerprinting {path.name} ({stat.st_size / (1024**3):.2f} GiB)")
    started = time.perf_counter()
    sha256 = _sha256_file(path)
    return {
        "algorithm": "sha256",
        "sha256": sha256,
        "size_bytes": stat.st_size,
        "mtime_ns_observed": stat.st_mtime_ns,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
    }


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    value = stream.read(size)
    if len(value) != size:
        raise ValueError(f"Unexpected end of file while reading {size} bytes")
    return value


def _find_codestream(stream: BinaryIO, file_size: int) -> tuple[int, int, list[dict[str, Any]]]:
    """Return codestream offset/length and top-level JP2 box inventory."""
    stream.seek(0)
    if _read_exact(stream, 2) == b"\xff\x4f":
        return 0, file_size, [{"type": "raw_codestream", "offset": 0, "size": file_size}]

    boxes: list[dict[str, Any]] = []
    offset = 0
    while offset + 8 <= file_size:
        stream.seek(offset)
        lbox, box_type = struct.unpack(">I4s", _read_exact(stream, 8))
        header_size = 8
        if lbox == 1:
            box_size = struct.unpack(">Q", _read_exact(stream, 8))[0]
            header_size = 16
        elif lbox == 0:
            box_size = file_size - offset
        else:
            box_size = lbox
        if box_size < header_size or offset + box_size > file_size:
            raise ValueError(f"Invalid JP2 box at byte {offset}: size={box_size}")
        box_name = box_type.decode("ascii", errors="replace")
        boxes.append({"type": box_name, "offset": offset, "size": box_size})
        if box_type == b"jp2c":
            return offset + header_size, box_size - header_size, boxes
        offset += box_size
    raise ValueError("JP2 codestream box (jp2c) not found")


def _parse_siz(payload: bytes) -> dict[str, Any]:
    if len(payload) < 36:
        raise ValueError("SIZ marker is truncated")
    (
        capabilities,
        x_size,
        y_size,
        x_origin,
        y_origin,
        tile_width,
        tile_height,
        tile_x_origin,
        tile_y_origin,
        component_count,
    ) = struct.unpack(">HIIIIIIIIH", payload[:36])
    components: list[dict[str, int]] = []
    component_bytes = payload[36:]
    for index in range(component_count):
        start = index * 3
        if start + 3 > len(component_bytes):
            raise ValueError("SIZ component declaration is truncated")
        precision_and_sign, x_subsampling, y_subsampling = struct.unpack(
            ">BBB", component_bytes[start : start + 3]
        )
        components.append(
            {
                "index": index,
                "precision_bits": (precision_and_sign & 0x7F) + 1,
                "signed": bool(precision_and_sign & 0x80),
                "x_subsampling": x_subsampling,
                "y_subsampling": y_subsampling,
            }
        )
    image_width = x_size - x_origin
    image_height = y_size - y_origin
    tiles_x = math.ceil((x_size - tile_x_origin) / tile_width) - math.floor(
        (x_origin - tile_x_origin) / tile_width
    )
    tiles_y = math.ceil((y_size - tile_y_origin) / tile_height) - math.floor(
        (y_origin - tile_y_origin) / tile_height
    )
    return {
        "capabilities": capabilities,
        "reference_grid": {
            "x_size": x_size,
            "y_size": y_size,
            "x_origin": x_origin,
            "y_origin": y_origin,
        },
        "image_width": image_width,
        "image_height": image_height,
        "tile_width": tile_width,
        "tile_height": tile_height,
        "tile_x_origin": tile_x_origin,
        "tile_y_origin": tile_y_origin,
        "tiles_x": tiles_x,
        "tiles_y": tiles_y,
        "tile_count": tiles_x * tiles_y,
        "component_count": component_count,
        "components": components,
    }


def _parse_cod(payload: bytes) -> dict[str, Any]:
    if len(payload) < 10:
        raise ValueError("COD marker is truncated")
    coding_style = payload[0]
    progression_code = payload[1]
    quality_layers = struct.unpack(">H", payload[2:4])[0]
    decomposition_levels = payload[5]
    precinct_values = payload[10 : 10 + decomposition_levels + 1] if coding_style & 0x01 else b""
    precincts = [
        {
            "resolution": index,
            "width": 1 << (value & 0x0F),
            "height": 1 << ((value >> 4) & 0x0F),
        }
        for index, value in enumerate(precinct_values)
    ]
    return {
        "coding_style": coding_style,
        "sop_markers": bool(coding_style & 0x02),
        "eph_markers": bool(coding_style & 0x04),
        "progression_code": progression_code,
        "progression": PROGRESSION_ORDERS.get(progression_code, f"unknown_{progression_code}"),
        "quality_layers": quality_layers,
        "multiple_component_transform": payload[4],
        "decomposition_levels": decomposition_levels,
        "resolution_levels": decomposition_levels + 1,
        "code_block_width": 1 << (payload[6] + 2),
        "code_block_height": 1 << (payload[7] + 2),
        "code_block_style": payload[8],
        "wavelet_transform": "reversible_5_3" if payload[9] == 1 else "irreversible_9_7",
        "precincts_explicit": bool(coding_style & 0x01),
        "precincts": precincts,
    }


def _scan_tile_part_headers(
    stream: BinaryIO,
    first_sot: int,
    codestream_end: int,
) -> dict[str, Any]:
    tile_parts: list[dict[str, Any]] = []
    position = first_sot
    while position + 12 <= codestream_end:
        stream.seek(position)
        header = _read_exact(stream, 12)
        if header[:2] == b"\xff\xd9":
            break
        if header[:2] != b"\xff\x90":
            break
        segment_length = struct.unpack(">H", header[2:4])[0]
        tile_index = struct.unpack(">H", header[4:6])[0]
        tile_part_length = struct.unpack(">I", header[6:10])[0]
        tile_part_index = header[10]
        tile_part_count = header[11]
        if segment_length != 10:
            raise ValueError(f"Unexpected SOT segment length {segment_length}")

        marker_position = position + 12
        plt_segments = 0
        header_markers: list[str] = []
        sod_offset: int | None = None
        while marker_position + 2 <= codestream_end:
            stream.seek(marker_position)
            marker = _read_exact(stream, 2)
            if marker == b"\xff\x93":
                sod_offset = marker_position
                header_markers.append("SOD")
                break
            if marker[0] != 0xFF:
                raise ValueError(f"Invalid marker at byte {marker_position}")
            segment_size = struct.unpack(">H", _read_exact(stream, 2))[0]
            if segment_size < 2:
                raise ValueError(f"Invalid marker segment length at byte {marker_position}")
            marker_code = marker[1]
            marker_name = {0x58: "PLT", 0x5C: "QCD", 0x5D: "QCC", 0x53: "COC"}.get(
                marker_code, f"FF{marker_code:02X}"
            )
            header_markers.append(marker_name)
            if marker_code == 0x58:
                plt_segments += 1
            marker_position += 2 + segment_size

        tile_parts.append(
            {
                "tile_index": tile_index,
                "tile_part_index": tile_part_index,
                "declared_tile_part_count": tile_part_count,
                "offset": position,
                "length": tile_part_length,
                "sod_offset": sod_offset,
                "plt_segment_count": plt_segments,
                "header_markers": header_markers,
            }
        )
        if tile_part_length == 0:
            break
        next_position = position + tile_part_length
        if next_position <= position or next_position >= codestream_end:
            break
        position = next_position
    return {
        "tile_part_count_observed": len(tile_parts),
        "plt_present": any(item["plt_segment_count"] for item in tile_parts),
        "plt_segment_count_observed": sum(item["plt_segment_count"] for item in tile_parts),
        "tile_parts": tile_parts,
    }


def inspect_jp2_codestream(path: Path) -> dict[str, Any]:
    """Inspect marker headers without decoding image pixels or scanning packet data."""
    file_size = path.stat().st_size
    with path.open("rb") as stream:
        codestream_offset, codestream_length, boxes = _find_codestream(stream, file_size)
        codestream_end = codestream_offset + codestream_length
        stream.seek(codestream_offset)
        if _read_exact(stream, 2) != b"\xff\x4f":
            raise ValueError("JPEG 2000 SOC marker not found")
        position = codestream_offset + 2
        siz: dict[str, Any] | None = None
        cod: dict[str, Any] | None = None
        tlm_segments = 0
        main_header_markers: list[str] = ["SOC"]
        first_sot: int | None = None
        while position + 2 <= codestream_end:
            stream.seek(position)
            marker = _read_exact(stream, 2)
            if marker == b"\xff\x90":
                first_sot = position
                main_header_markers.append("SOT")
                break
            if marker[0] != 0xFF:
                raise ValueError(f"Invalid main-header marker at byte {position}")
            segment_length = struct.unpack(">H", _read_exact(stream, 2))[0]
            if segment_length < 2:
                raise ValueError(f"Invalid main-header segment length at byte {position}")
            payload = _read_exact(stream, segment_length - 2)
            marker_code = marker[1]
            marker_name = {
                0x51: "SIZ",
                0x52: "COD",
                0x53: "COC",
                0x55: "TLM",
                0x5C: "QCD",
                0x5D: "QCC",
                0x5E: "RGN",
                0x5F: "POC",
                0x63: "CRG",
                0x64: "COM",
            }.get(marker_code, f"FF{marker_code:02X}")
            main_header_markers.append(marker_name)
            if marker_code == 0x51:
                siz = _parse_siz(payload)
            elif marker_code == 0x52:
                cod = _parse_cod(payload)
            elif marker_code == 0x55:
                tlm_segments += 1
            position += 2 + segment_length
        if siz is None or cod is None or first_sot is None:
            raise ValueError("Required SIZ/COD/SOT marker was not found")
        tile_parts = _scan_tile_part_headers(stream, first_sot, codestream_end)
    return {
        "container": "raw_j2k" if codestream_offset == 0 else "jp2",
        "container_boxes": boxes,
        "codestream_offset": codestream_offset,
        "codestream_length": codestream_length,
        "main_header_bytes": first_sot - codestream_offset,
        "main_header_markers": main_header_markers,
        "tlm_present": tlm_segments > 0,
        "tlm_segment_count": tlm_segments,
        "siz": siz,
        "cod": cod,
        **tile_parts,
    }


def _windows_overlap(left: dict[str, int], right: dict[str, int], threshold: float = 0.25) -> bool:
    x0 = max(left["x"], right["x"])
    y0 = max(left["y"], right["y"])
    x1 = min(left["x"] + left["width"], right["x"] + right["width"])
    y1 = min(left["y"] + left["height"], right["y"] + right["height"])
    intersection = max(0, x1 - x0) * max(0, y1 - y0)
    area = min(left["width"] * left["height"], right["width"] * right["height"])
    return bool(area and intersection / area > threshold)


def _aligned_position(value: int, maximum: int, alignment: int) -> int:
    if alignment <= 1:
        return min(max(0, value), maximum)
    return min(max(0, (value // alignment) * alignment), maximum)


def _random_windows(
    width: int,
    height: int,
    size: int,
    seed: int,
    alignment: tuple[int, int],
) -> list[dict[str, Any]]:
    if width <= size + 2 or height <= size + 2:
        raise ValueError(f"Raster {width}x{height} is too small for internal {size}px windows")
    generator = random.Random(seed)
    alignment_x, alignment_y = alignment
    x_positions = list(range(alignment_x, width - size, alignment_x))
    y_positions = list(range(alignment_y, height - size, alignment_y))
    if not x_positions or not y_positions:
        raise ValueError("Raster has no internal block-aligned positions for the selected window")
    windows: list[dict[str, Any]] = []
    attempts = 0
    while len(windows) < RANDOM_WINDOW_COUNT and attempts < 100_000:
        attempts += 1
        candidate = {
            "x": generator.choice(x_positions),
            "y": generator.choice(y_positions),
            "width": size,
            "height": size,
        }
        if any(_windows_overlap(candidate, existing, threshold=0.80) for existing in windows):
            continue
        candidate.update({"id": f"random_{len(windows):03d}", "category": "random_internal"})
        windows.append(candidate)
    if len(windows) != RANDOM_WINDOW_COUNT:
        raise RuntimeError("Could not produce 60 deterministic internal windows")
    return windows


def _edge_windows(
    width: int,
    height: int,
    size: int,
    alignment: tuple[int, int],
) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    alignment_x, alignment_y = alignment
    positions_x = [
        _aligned_position(round(index * (width - size) / 6), width - size, alignment_x)
        for index in range(1, 6)
    ]
    positions_y = [
        _aligned_position(round(index * (height - size) / 6), height - size, alignment_y)
        for index in range(1, 6)
    ]
    candidates = [
        *(dict(x=x, y=0, width=size, height=size, edge="top") for x in positions_x),
        *(dict(x=width - size, y=y, width=size, height=size, edge="right") for y in positions_y),
        *(dict(x=x, y=height - size, width=size, height=size, edge="bottom") for x in positions_x),
        *(dict(x=0, y=y, width=size, height=size, edge="left") for y in positions_y),
    ]
    for index, candidate in enumerate(candidates):
        candidate.update({"id": f"edge_{index:03d}", "category": "scene_edge"})
        windows.append(candidate)
    if len(windows) != EDGE_WINDOW_COUNT:
        raise AssertionError("Edge workload must contain exactly 20 windows")
    return windows


def _high_contrast_windows(
    dataset: Any,
    size: int,
    alignment: tuple[int, int],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import numpy as np
    from rasterio.enums import Resampling

    preview_width = min(1024, dataset.width)
    preview_height = max(64, round(dataset.height * preview_width / dataset.width))
    started = time.perf_counter()
    preview = dataset.read(
        1,
        out_shape=(preview_height, preview_width),
        masked=True,
        resampling=Resampling.average,
    )
    values = np.asarray(preview.filled(np.nan), dtype=np.float64)
    valid = np.isfinite(values)
    if not valid.any():
        raise RuntimeError("Contrast preview contains no valid pixels")

    grid_x = 40
    grid_y = max(20, round(grid_x * preview_height / preview_width))
    candidates: list[tuple[float, int, int]] = []
    for grid_row in range(grid_y):
        center_y = round((grid_row + 0.5) * preview_height / grid_y)
        for grid_column in range(grid_x):
            center_x = round((grid_column + 0.5) * preview_width / grid_x)
            radius_x = max(2, preview_width // (grid_x * 2))
            radius_y = max(2, preview_height // (grid_y * 2))
            x0, x1 = max(0, center_x - radius_x), min(preview_width, center_x + radius_x + 1)
            y0, y1 = max(0, center_y - radius_y), min(preview_height, center_y + radius_y + 1)
            patch = values[y0:y1, x0:x1]
            finite = patch[np.isfinite(patch)]
            if finite.size < patch.size * 0.75:
                continue
            local_range = float(np.percentile(finite, 95) - np.percentile(finite, 5))
            local_std = float(np.std(finite))
            candidates.append((local_range + local_std, center_x, center_y))

    selected: list[dict[str, Any]] = []
    alignment_x, alignment_y = alignment
    for score, preview_x, preview_y in sorted(candidates, reverse=True):
        center_x = round((preview_x + 0.5) * dataset.width / preview_width)
        center_y = round((preview_y + 0.5) * dataset.height / preview_height)
        candidate = {
            "x": _aligned_position(center_x - size // 2, dataset.width - size, alignment_x),
            "y": _aligned_position(center_y - size // 2, dataset.height - size, alignment_y),
            "width": size,
            "height": size,
            "contrast_score": round(score, 6),
        }
        if any(_windows_overlap(candidate, existing) for existing in selected):
            continue
        candidate.update(
            {"id": f"contrast_{len(selected):03d}", "category": "high_contrast"}
        )
        selected.append(candidate)
        if len(selected) == HIGH_CONTRAST_WINDOW_COUNT:
            break
    if len(selected) != HIGH_CONTRAST_WINDOW_COUNT:
        raise RuntimeError("Could not select 20 non-overlapping high-contrast windows")
    return selected, {
        "method": "native_jp2_overview_average_grid_percentile_range_plus_std_v1",
        "preview_width": preview_width,
        "preview_height": preview_height,
        "candidate_count": len(candidates),
        "window_alignment": {"x": alignment_x, "y": alignment_y},
        "elapsed_seconds_observed": round(time.perf_counter() - started, 6),
        "timing_is_not_a_performance_result": True,
    }


def _pixel_checksum(array: Any) -> str:
    import numpy as np

    canonical = np.ascontiguousarray(array)
    if canonical.dtype.itemsize > 1:
        canonical = canonical.astype(canonical.dtype.newbyteorder("<"), copy=False)
    digest = hashlib.sha256()
    digest.update(str(canonical.dtype).encode("ascii"))
    digest.update(struct.pack(">II", canonical.shape[-2], canonical.shape[-1]))
    digest.update(canonical.tobytes(order="C"))
    return digest.hexdigest()


def _pixel_probes(array: Any) -> list[dict[str, int | float]]:
    probes: list[dict[str, int | float]] = []
    rows = [0, array.shape[0] // 3, (2 * array.shape[0]) // 3, array.shape[0] - 1]
    columns = [0, array.shape[1] // 3, (2 * array.shape[1]) // 3, array.shape[1] - 1]
    for row in rows:
        for column in columns:
            value = array[row, column]
            probes.append({"row": row, "column": column, "value": value.item()})
    return probes


def _reference_windows(dataset: Any, windows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import numpy as np
    from rasterio.windows import Window

    references: list[dict[str, Any]] = []
    started = time.perf_counter()
    for index, item in enumerate(windows, start=1):
        if index == 1 or index % 10 == 0 or index == len(windows):
            print(f"  A0 exact 1x references: {index}/{len(windows)}")
        read_started = time.perf_counter()
        array = dataset.read(
            1,
            window=Window(item["x"], item["y"], item["width"], item["height"]),
            masked=False,
        )
        if array.shape != (item["height"], item["width"]):
            raise RuntimeError(f"Unexpected array shape for {item['id']}: {array.shape}")
        references.append(
            {
                "window_id": item["id"],
                "sha256": _pixel_checksum(array),
                "dtype": str(array.dtype),
                "shape": list(array.shape),
                "minimum": int(np.min(array)),
                "maximum": int(np.max(array)),
                "mean": float(np.mean(array, dtype=np.float64)),
                "pixel_probes": _pixel_probes(array),
                "elapsed_seconds_observed": round(time.perf_counter() - read_started, 6),
            }
        )
    return references, {
        "strategy": "A0",
        "driver_required": "JP2OpenJPEG",
        "comparison": "exact_window_sha256_and_16_pixel_probes",
        "window_count": len(references),
        "elapsed_seconds_observed": round(time.perf_counter() - started, 6),
        "timing_is_not_a_performance_result": True,
    }


def _lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    n = 1 << zoom
    clamped_lat = max(-85.05112878, min(85.05112878, lat))
    x = int(math.floor((lon + 180.0) / 360.0 * n))
    latitude_radians = math.radians(clamped_lat)
    y = int(
        math.floor(
            (1.0 - math.asinh(math.tan(latitude_radians)) / math.pi) / 2.0 * n
        )
    )
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def _viewport_tiles(top_left_x: int, top_left_y: int, zoom: int, size: int = 4) -> list[dict[str, int]]:
    n = 1 << zoom
    return [
        {"z": zoom, "x": (top_left_x + column) % n, "y": max(0, min(n - 1, top_left_y + row))}
        for row in range(size)
        for column in range(size)
    ]


def _to_lonlat(dataset: Any, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    from rasterio.warp import transform as transform_coordinates

    if dataset.crs is None:
        raise RuntimeError("A georeferenced CRS is required for the Web Mercator workload")
    longitudes, latitudes = transform_coordinates(
        dataset.crs,
        "EPSG:4326",
        [point[0] for point in points],
        [point[1] for point in points],
    )
    return list(zip(longitudes, latitudes))


def _haversine_meters(left: tuple[float, float], right: tuple[float, float]) -> float:
    longitude_left, latitude_left = map(math.radians, left)
    longitude_right, latitude_right = map(math.radians, right)
    delta_longitude = longitude_right - longitude_left
    delta_latitude = latitude_right - latitude_left
    value = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(latitude_left)
        * math.cos(latitude_right)
        * math.sin(delta_longitude / 2) ** 2
    )
    return 2 * 6_378_137.0 * math.asin(min(1.0, math.sqrt(value)))


def _ground_sample_distance(dataset: Any) -> dict[str, float]:
    transform = dataset.transform
    center_column = dataset.width / 2
    center_row = dataset.height / 2
    points = [
        transform * (center_column, center_row),
        transform * (center_column + 1, center_row),
        transform * (center_column, center_row + 1),
    ]
    lonlat = _to_lonlat(dataset, points)
    x_distance = _haversine_meters(lonlat[0], lonlat[1])
    y_distance = _haversine_meters(lonlat[0], lonlat[2])
    return {
        "x_meters_per_source_pixel": abs(float(x_distance)),
        "y_meters_per_source_pixel": abs(float(y_distance)),
        "center_longitude": float(lonlat[0][0]),
        "center_latitude": float(lonlat[0][1]),
    }


def _webmercator_trace(dataset: Any, native_zoom: int = 19) -> dict[str, Any]:
    bounds = dataset.bounds
    corners = _to_lonlat(
        dataset,
        [
            (bounds.left, bounds.bottom),
            (bounds.left, bounds.top),
            (bounds.right, bounds.bottom),
            (bounds.right, bounds.top),
        ],
    )
    longitudes = [point[0] for point in corners]
    latitudes = [point[1] for point in corners]
    lon_min, lon_max = min(longitudes), max(longitudes)
    lat_min, lat_max = min(latitudes), max(latitudes)
    center_lon = (lon_min + lon_max) / 2
    center_lat = (lat_min + lat_max) / 2

    gsd = _ground_sample_distance(dataset)
    native_mpp = 156543.03392804097 * math.cos(math.radians(center_lat)) / (1 << native_zoom)
    closest_zoom = round(
        math.log2(
            156543.03392804097
            * math.cos(math.radians(center_lat))
            / gsd["x_meters_per_source_pixel"]
        )
    )

    # Fit the full source into a reproducible 1024x768 logical viewport.
    x_fraction = max(1e-12, (lon_max - lon_min) / 360.0)
    mercator_y = lambda lat: (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0
    y_fraction = max(1e-12, abs(mercator_y(lat_max) - mercator_y(lat_min)))
    wide_zoom = max(
        0,
        min(
            22,
            math.floor(
                min(
                    math.log2((1024 * 0.85) / (256 * x_fraction)),
                    math.log2((768 * 0.85) / (256 * y_fraction)),
                )
            ),
        ),
    )
    wide_center_x, wide_center_y = _lonlat_to_tile(center_lon, center_lat, wide_zoom)
    native_center_x, native_center_y = _lonlat_to_tile(center_lon, center_lat, native_zoom)
    base_x, base_y = native_center_x - 2, native_center_y - 2
    offsets = [(0, 0), (1, 0), (2, 0), (2, 1), (2, 2), (1, 2), (0, 0)]
    steps = []
    for index, (offset_x, offset_y) in enumerate(offsets):
        steps.append(
            {
                "step": index,
                "action": "initial" if index == 0 else ("return" if index == len(offsets) - 1 else "pan"),
                "top_left": {"z": native_zoom, "x": base_x + offset_x, "y": base_y + offset_y},
                "tiles": _viewport_tiles(base_x + offset_x, base_y + offset_y, native_zoom),
            }
        )
    return {
        "scheme": "WebMercator_XYZ",
        "tile_size": 256,
        "logical_viewport_pixels": [1024, 768],
        "geographic_bounds_wgs84": [lon_min, lat_min, lon_max, lat_max],
        "wide_view": {
            "zoom": wide_zoom,
            "center_tile": {"z": wide_zoom, "x": wide_center_x, "y": wide_center_y},
        },
        "native_view": {
            "zoom": native_zoom,
            "viewport_tiles": [4, 4],
            "steps": steps,
            "pan_count": 5,
            "returns_to_first_viewport": True,
        },
        "one_x_confirmation": {
            "requested_zoom": native_zoom,
            "closest_zoom_from_x_gsd": closest_zoom,
            "webmercator_ground_meters_per_pixel": native_mpp,
            **gsd,
            "output_to_source_pixel_ratio_x": native_mpp / gsd["x_meters_per_source_pixel"],
            "output_to_source_pixel_ratio_y": native_mpp / gsd["y_meters_per_source_pixel"],
            "confirmed": closest_zoom == native_zoom,
            "criterion": "requested zoom equals closest integer zoom derived from horizontal ground sample distance",
        },
    }


def _raster_metadata(dataset: Any) -> dict[str, Any]:
    return {
        "driver": dataset.driver,
        "width": dataset.width,
        "height": dataset.height,
        "band_count": dataset.count,
        "dtypes": list(dataset.dtypes),
        "nodata": dataset.nodata,
        "crs_wkt": dataset.crs.to_wkt() if dataset.crs else None,
        "transform": list(dataset.transform)[:6],
        "bounds": list(dataset.bounds),
        "block_shapes": [list(item) for item in dataset.block_shapes],
        "native_overviews": dataset.overviews(1),
        "color_interpretations": [item.name for item in dataset.colorinterp],
        "mask_flags": [[flag.name for flag in flags] for flags in dataset.mask_flag_enums],
        "image_structure": dataset.tags(ns="IMAGE_STRUCTURE"),
    }


def _environment(runtime_label: str, output_directory: Path) -> dict[str, Any]:
    import rasterio
    from osgeo import gdal

    try:
        import psutil

        memory = psutil.virtual_memory()
        memory_info = {"total_bytes": memory.total, "available_bytes": memory.available}
    except Exception as error:  # pragma: no cover - optional diagnostic only
        memory_info = {"error": str(error)}
    disk = shutil.disk_usage(output_directory)
    return {
        "captured_at": _utc_now(),
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "logical_cpu_count": os.cpu_count(),
        "python_executable": sys.executable,
        "python_version": sys.version,
        "runtime_label": runtime_label,
        "gdal_version": gdal.VersionInfo("--version"),
        "rasterio_version": rasterio.__version__,
        "gdal_data": os.environ.get("GDAL_DATA"),
        "gdal_driver_path": os.environ.get("GDAL_DRIVER_PATH"),
        "proj_data": os.environ.get("PROJ_DATA") or os.environ.get("PROJ_LIB"),
        "gdal_num_threads_env": os.environ.get("GDAL_NUM_THREADS"),
        "opj_num_threads_env": os.environ.get("OPJ_NUM_THREADS"),
        "gdal_cachemax_mib": DEFAULT_GDAL_CACHE_MIB,
        "memory": memory_info,
        "output_volume": {
            "path": str(output_directory),
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes_before": disk.free,
        },
    }


def _validate_source(path: Path) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    if resolved.suffix.casefold() not in {".jp2", ".j2k", ".j2c"}:
        raise ValueError(f"Not a JPEG 2000 source: {resolved}")
    return resolved


def _freeze_source(
    path: Path,
    *,
    source_id: str,
    role: str,
    seed: int,
    window_size: int,
    qualification: dict[str, Any],
) -> dict[str, Any]:
    import rasterio

    print(f"\nFreezing {source_id}: {path}")
    fingerprint = _source_fingerprint(path)
    codestream = inspect_jp2_codestream(path)
    with rasterio.Env(GDAL_CACHEMAX=DEFAULT_GDAL_CACHE_MIB, GDAL_NUM_THREADS="1"):
        with rasterio.open(path) as dataset:
            if dataset.driver != "JP2OpenJPEG":
                raise RuntimeError(
                    f"E0 reference requires JP2OpenJPEG, actually opened with {dataset.driver}"
                )
            metadata = _raster_metadata(dataset)
            block_height, block_width = dataset.block_shapes[0]
            alignment = (block_width, block_height)
            random_windows = _random_windows(
                dataset.width, dataset.height, window_size, seed, alignment
            )
            edge_windows = _edge_windows(
                dataset.width, dataset.height, window_size, alignment
            )
            contrast_windows, contrast_selection = _high_contrast_windows(
                dataset, window_size, alignment
            )
            windows = random_windows + edge_windows + contrast_windows
            references, reference_method = _reference_windows(dataset, windows)
            trace = _webmercator_trace(dataset)
    if len(windows) != 100 or len({item["id"] for item in windows}) != 100:
        raise AssertionError("Frozen workload must contain exactly 100 uniquely identified windows")
    if codestream["siz"]["image_width"] != metadata["width"] or codestream["siz"]["image_height"] != metadata["height"]:
        raise RuntimeError("Codestream SIZ dimensions disagree with GDAL metadata")
    return {
        "source_id": source_id,
        "role": role,
        "path": str(path),
        "qualification": qualification,
        "fingerprint": fingerprint,
        "raster": metadata,
        "codestream": codestream,
        "window_selection": {
            "seed": seed,
            "window_size": window_size,
            "alignment": {
                "reason": "one deterministic GDAL virtual block per internal window; unaligned access is covered by the Web Mercator trace",
                "x": alignment[0],
                "y": alignment[1],
            },
            "counts": {
                "random_internal": RANDOM_WINDOW_COUNT,
                "scene_edge": EDGE_WINDOW_COUNT,
                "high_contrast": HIGH_CONTRAST_WINDOW_COUNT,
                "total": len(windows),
            },
            "high_contrast": contrast_selection,
        },
        "windows_1x": windows,
        "a0_references": references,
        "reference_method": reference_method,
        "webmercator_trace": trace,
    }


def _deterministic_manifest_id(manifest: dict[str, Any]) -> str:
    stable = {
        "schema_name": manifest["schema_name"],
        "schema_version": manifest["schema_version"],
        "seed": manifest["seed"],
        "window_size": manifest["window_size"],
        "sources": [
            {
                "source_id": source["source_id"],
                "fingerprint": source["fingerprint"]["sha256"],
                "windows_1x": source["windows_1x"],
                "webmercator_trace": source["webmercator_trace"],
            }
            for source in manifest["sources"]
        ],
    }
    serialized = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    source_results: list[dict[str, Any]] = []
    if manifest.get("schema_name") != SCHEMA_NAME:
        errors.append("unexpected schema_name")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append("unexpected schema_version")
    if manifest.get("manifest_id") != _deterministic_manifest_id(manifest):
        errors.append("manifest_id does not match deterministic workload content")

    expected_categories = {
        "random_internal": RANDOM_WINDOW_COUNT,
        "scene_edge": EDGE_WINDOW_COUNT,
        "high_contrast": HIGH_CONTRAST_WINDOW_COUNT,
    }
    for source in manifest.get("sources", []):
        source_errors: list[str] = []
        source_id = str(source.get("source_id"))
        path = Path(source["path"])
        if not path.is_file():
            source_errors.append("source no longer exists")
            stat = None
        else:
            stat = path.stat()
            if stat.st_size != source["fingerprint"]["size_bytes"]:
                source_errors.append("source size changed after fingerprint")
            if stat.st_mtime_ns != source["fingerprint"]["mtime_ns_observed"]:
                source_errors.append("source mtime changed after fingerprint")

        windows = source.get("windows_1x", [])
        references = source.get("a0_references", [])
        window_ids = [item.get("id") for item in windows]
        reference_ids = [item.get("window_id") for item in references]
        if len(windows) != 100 or len(set(window_ids)) != 100:
            source_errors.append("workload does not contain 100 unique windows")
        category_counts = {
            category: sum(item.get("category") == category for item in windows)
            for category in expected_categories
        }
        if category_counts != expected_categories:
            source_errors.append(f"unexpected window categories: {category_counts}")
        if len(references) != 100 or set(reference_ids) != set(window_ids):
            source_errors.append("A0 references do not cover every window exactly")

        width = int(source["raster"]["width"])
        height = int(source["raster"]["height"])
        for item in windows:
            if (
                int(item["x"]) < 0
                or int(item["y"]) < 0
                or int(item["x"]) + int(item["width"]) > width
                or int(item["y"]) + int(item["height"]) > height
            ):
                source_errors.append(f"window outside raster bounds: {item.get('id')}")
                break
        for item in references:
            if (
                len(str(item.get("sha256", ""))) != 64
                or item.get("shape") != [manifest["window_size"], manifest["window_size"]]
                or len(item.get("pixel_probes", [])) != 16
            ):
                source_errors.append(f"invalid A0 reference: {item.get('window_id')}")
                break

        codestream = source["codestream"]
        if (
            codestream["siz"]["image_width"] != width
            or codestream["siz"]["image_height"] != height
        ):
            source_errors.append("SIZ dimensions disagree with GDAL metadata")
        trace = source["webmercator_trace"]["native_view"]
        steps = trace.get("steps", [])
        if (
            trace.get("pan_count") != 5
            or len(steps) != 7
            or not steps
            or steps[0].get("top_left") != steps[-1].get("top_left")
            or any(len(step.get("tiles", [])) != 16 for step in steps)
        ):
            source_errors.append("invalid 4x4 Web Mercator pan/return trace")
        if not source["webmercator_trace"]["one_x_confirmation"].get("confirmed"):
            source_errors.append("z19 was not confirmed as closest horizontal 1x zoom")

        source_results.append(
            {
                "source_id": source_id,
                "status": "passed" if not source_errors else "failed",
                "errors": source_errors,
                "source_size_and_mtime_unchanged": stat is not None and not any(
                    "source" in error for error in source_errors
                ),
                "window_count": len(windows),
                "reference_count": len(references),
                "category_counts": category_counts,
            }
        )
        errors.extend(f"{source_id}: {error}" for error in source_errors)
    if len(source_results) != manifest.get("source_count"):
        errors.append("source_count does not match sources")
    return {
        "schema_name": "geotile_jp2_fullres_manifest_verification",
        "schema_version": 1,
        "verified_at": _utc_now(),
        "manifest_id": manifest.get("manifest_id"),
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "sources": source_results,
    }


def _summary_markdown(manifest: dict[str, Any], environment: dict[str, Any]) -> str:
    lines = [
        "# E0 — zamrożony workload JP2 1×",
        "",
        f"- Manifest ID: `{manifest['manifest_id']}`",
        f"- Data: {manifest['generated_at']}",
        f"- Host: `{environment['host']}`",
        f"- Runtime: `{environment['runtime_label']}` / `{environment['gdal_version']}`",
        f"- Wolne miejsce przed testem: {environment['output_volume']['free_bytes_before'] / (1024**3):.2f} GiB",
        "",
        "## Źródła",
        "",
        "| ID | Rola | Rozmiar | SHA-256 | Okna | z19 = 1× |",
        "| --- | --- | ---: | --- | ---: | --- |",
    ]
    for source in manifest["sources"]:
        confirmed = source["webmercator_trace"]["one_x_confirmation"]["confirmed"]
        lines.append(
            f"| {source['source_id']} | {source['role']} | "
            f"{source['fingerprint']['size_bytes'] / (1024**3):.2f} GiB | "
            f"`{source['fingerprint']['sha256']}` | {len(source['windows_1x'])} | "
            f"{'tak' if confirmed else 'nie'} |"
        )
    lines.extend(
        [
            "",
            "Czasy odczytów zapisane w manifeście są wyłącznie diagnostyką przebiegu E0 i nie są",
            "baseline'em wydajności. Pomiary porównawcze zaczynają się od E2.",
            "",
        ]
    )
    return "\n".join(lines)


def freeze(args: argparse.Namespace) -> Path:
    primary = _validate_source(args.primary)
    secondary = _validate_source(args.secondary) if args.secondary else None
    if secondary == primary:
        raise ValueError("Primary and secondary source must differ")
    output_directory = args.output_dir.expanduser().resolve(strict=False)
    output_directory.mkdir(parents=True, exist_ok=True)
    environment = _environment(args.runtime_label, output_directory)
    _write_json_atomic(output_directory / "environment.json", environment)

    sources = [
        _freeze_source(
            primary,
            source_id="primary_arsenyev",
            role="critical_regression",
            seed=args.seed,
            window_size=args.window_size,
            qualification={
                "preferred_rgb_or_multiband": False,
                "alpha_or_mask": False,
                "note": "Critical production failure scene from the local delivery corpus.",
            },
        )
    ]
    if secondary:
        sources.append(
            _freeze_source(
                secondary,
                source_id="secondary_large_jp2",
                role="size_and_geography_control",
                seed=args.seed + 1,
                window_size=args.window_size,
                qualification={
                    "preferred_rgb_or_multiband": False,
                    "alpha_or_mask": False,
                    "meets_preferred_secondary_profile": False,
                    "note": (
                        "All five locally available JP2 sources are PAN. This is the "
                        "largest available secondary source; D1 remains conditional until an "
                        "RGB/alpha JP2 control is tested."
                    ),
                },
            )
        )

    manifest: dict[str, Any] = {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "stage": "E0",
        "status": "completed",
        "generated_at": _utc_now(),
        "seed": args.seed,
        "window_size": args.window_size,
        "source_count": len(sources),
        "sources": sources,
        "constraints": {
            "read_only_sources": True,
            "project_data_opened": False,
            "a0_driver": "JP2OpenJPEG",
            "cache_semantics": "new dataset handle; operating-system cache not flushed",
            "comparison_contract": "identical ordered windows, XYZ trace and exact pixel hashes",
        },
    }
    manifest["manifest_id"] = _deterministic_manifest_id(manifest)
    verification = validate_manifest(manifest)
    if verification["status"] != "passed":
        raise RuntimeError(f"E0 manifest validation failed: {verification['errors']}")
    manifest_path = output_directory / "benchmark_manifest.json"
    _write_json_atomic(manifest_path, manifest)
    _write_json_atomic(output_directory / "verification.json", verification)
    (output_directory / "summary.md").write_text(
        _summary_markdown(manifest, environment), encoding="utf-8"
    )
    print(f"\nE0 manifest: {manifest_path}")
    print(f"Manifest ID: {manifest['manifest_id']}")
    return manifest_path


def verify(args: argparse.Namespace) -> Path:
    manifest_path = args.manifest.expanduser().resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    verification = validate_manifest(manifest)
    destination = manifest_path.parent / "verification.json"
    _write_json_atomic(destination, verification)
    print(f"Verification: {verification['status']} ({destination})")
    if verification["status"] != "passed":
        raise RuntimeError("; ".join(verification["errors"]))
    return destination


# --- Shared W0-W8 runtime runner (E2, E3 and E5) ---------------------------


def _write_text_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def _write_csv_atomic(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def _duration_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    ordered = sorted(float(value) for value in values)

    def percentile(fraction: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        position = (len(ordered) - 1) * fraction
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return ordered[lower]
        weight = position - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    return {
        "count": len(ordered),
        "minimum_ms": round(ordered[0], 6),
        "mean_ms": round(statistics.fmean(ordered), 6),
        "p50_ms": round(percentile(0.50), 6),
        "p95_ms": round(percentile(0.95), 6),
        "maximum_ms": round(ordered[-1], 6),
        "stddev_ms": round(statistics.pstdev(ordered), 6),
    }


def _io_counter_dict(counter: Any) -> dict[str, int]:
    return {
        name: int(getattr(counter, name, 0))
        for name in (
            "read_count",
            "write_count",
            "read_bytes",
            "write_bytes",
            "other_count",
            "other_bytes",
        )
    }


@dataclass
class _RuntimeResourceTrace:
    """Sample RSS and process I/O for one workload.

    JPEG 2000 decoding happens inside GDAL in the current process, so process
    RSS covers the native codec allocations.  A child-process sum is retained
    for future strategies without changing the result schema.
    """

    label: str
    interval_seconds: float = 0.05
    samples: list[dict[str, Any]] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _process: Any = None
    _started: float = 0.0
    _rss_before: int = 0
    _cpu_before: float = 0.0
    _io_before: dict[str, int] = field(default_factory=dict)
    peak_rss_bytes: int = 0

    def __enter__(self) -> "_RuntimeResourceTrace":
        import psutil

        self._process = psutil.Process()
        self._started = time.perf_counter()
        self._rss_before = int(self._process.memory_info().rss)
        cpu = self._process.cpu_times()
        self._cpu_before = float(cpu.user + cpu.system)
        self._io_before = _io_counter_dict(self._process.io_counters())
        self._sample()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _sample(self) -> None:
        import psutil

        if self._process is None:
            return
        try:
            rss = int(self._process.memory_info().rss)
            for child in self._process.children(recursive=True):
                try:
                    rss += int(child.memory_info().rss)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            io = _io_counter_dict(self._process.io_counters())
            available = int(psutil.virtual_memory().available)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return
        self.peak_rss_bytes = max(self.peak_rss_bytes, rss)
        self.samples.append(
            {
                "workload_label": self.label,
                "elapsed_ms": round((time.perf_counter() - self._started) * 1000.0, 3),
                "rss_bytes": rss,
                "system_available_bytes": available,
                "read_bytes": io["read_bytes"],
                "write_bytes": io["write_bytes"],
            }
        )

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._sample()

    def __exit__(self, *_exc: Any) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._sample()

    def summary(self) -> dict[str, Any]:
        if self._process is None:
            raise RuntimeError("Resource trace was not started")
        cpu = self._process.cpu_times()
        io_after = _io_counter_dict(self._process.io_counters())
        rss_after = int(self._process.memory_info().rss)
        return {
            "wall_ms": round((time.perf_counter() - self._started) * 1000.0, 6),
            "cpu_ms": round((float(cpu.user + cpu.system) - self._cpu_before) * 1000.0, 6),
            "rss_before_bytes": self._rss_before,
            "rss_after_bytes": rss_after,
            "peak_rss_bytes": self.peak_rss_bytes,
            "peak_rss_delta_bytes": max(0, self.peak_rss_bytes - self._rss_before),
            "io_delta": {
                key: max(0, io_after[key] - self._io_before.get(key, 0))
                for key in io_after
            },
            "sample_count": len(self.samples),
        }


def _measure_runtime_workload(
    *,
    workload_id: str,
    variant: str,
    cache_scope: str,
    operation: Any,
    sample_sink: list[dict[str, Any]],
) -> dict[str, Any]:
    label = f"{workload_id}:{variant}:{cache_scope}"
    print(f"\n[{workload_id}] {variant} / {cache_scope}", flush=True)
    with _RuntimeResourceTrace(label) as trace:
        details = operation()
    resource = trace.summary()
    sample_sink.extend(trace.samples)
    return {
        "workload": workload_id,
        "variant": variant,
        "cache_scope": cache_scope,
        "resource": resource,
        "details": details,
    }


def _timed(callable_: Any) -> tuple[Any, float]:
    started = time.perf_counter()
    result = callable_()
    return result, (time.perf_counter() - started) * 1000.0


def _combine_checksums(values: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(str(value).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _open_explicit_gdal(path: Path, driver_name: str) -> Any:
    from osgeo import gdal

    dataset = gdal.OpenEx(
        str(path),
        gdal.OF_RASTER | gdal.OF_READONLY,
        allowed_drivers=[driver_name],
    )
    if dataset is None:
        raise RuntimeError(f"{driver_name} could not open {path}")
    actual = dataset.GetDriver().ShortName
    if actual != driver_name:
        dataset = None
        raise RuntimeError(f"Requested {driver_name}, GDAL selected {actual}")
    return dataset


def _gdal_raster_metadata(dataset: Any) -> dict[str, Any]:
    from osgeo import gdal

    spatial_reference = dataset.GetSpatialRef()
    bands: list[dict[str, Any]] = []
    for index in range(1, dataset.RasterCount + 1):
        band = dataset.GetRasterBand(index)
        bands.append(
            {
                "index": index,
                "dtype": gdal.GetDataTypeName(band.DataType),
                "color_interpretation": gdal.GetColorInterpretationName(
                    band.GetColorInterpretation()
                ),
                "block_size": list(band.GetBlockSize()),
                "overview_sizes": [
                    [band.GetOverview(item).XSize, band.GetOverview(item).YSize]
                    for item in range(band.GetOverviewCount())
                ],
                "nodata": band.GetNoDataValue(),
                "mask_flags": int(band.GetMaskFlags()),
            }
        )
    return {
        "driver": dataset.GetDriver().ShortName,
        "width": dataset.RasterXSize,
        "height": dataset.RasterYSize,
        "band_count": dataset.RasterCount,
        "geotransform_gdal": list(dataset.GetGeoTransform()),
        "crs_wkt": spatial_reference.ExportToWkt() if spatial_reference else "",
        "bands": bands,
    }


def _metadata_matches_manifest(metadata: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    from osgeo import osr

    expected = entry["raster"]
    affine = expected["transform"]
    expected_geotransform = [affine[2], affine[0], affine[1], affine[5], affine[3], affine[4]]
    actual_srs = osr.SpatialReference()
    expected_srs = osr.SpatialReference()
    actual_wkt = metadata.get("crs_wkt") or ""
    expected_wkt = expected.get("crs_wkt") or ""
    crs_equal = not actual_wkt and not expected_wkt
    if actual_wkt and expected_wkt:
        actual_srs.ImportFromWkt(actual_wkt)
        expected_srs.ImportFromWkt(expected_wkt)
        crs_equal = bool(actual_srs.IsSame(expected_srs))
    checks = {
        "width": metadata["width"] == expected["width"],
        "height": metadata["height"] == expected["height"],
        "band_count": metadata["band_count"] == expected["band_count"],
        "dtypes": [band["dtype"].casefold() for band in metadata["bands"]]
        == [str(value).casefold() for value in expected["dtypes"]],
        "geotransform": all(
            math.isclose(left, right, rel_tol=0.0, abs_tol=1e-12)
            for left, right in zip(metadata["geotransform_gdal"], expected_geotransform)
        ),
        "crs": crs_equal,
    }
    return {"checks": checks, "passed": all(checks.values())}


def _read_direct_window(dataset: Any, window: dict[str, Any], *, factor: int = 1) -> Any:
    band = dataset.GetRasterBand(1)
    width = int(window["width"])
    height = int(window["height"])
    if factor == 1:
        x_offset = int(window["x"])
        y_offset = int(window["y"])
        x_size = width
        y_size = height
    else:
        center_x = int(window["x"]) + width // 2
        center_y = int(window["y"]) + height // 2
        x_size = min(dataset.RasterXSize, width * factor)
        y_size = min(dataset.RasterYSize, height * factor)
        x_offset = min(max(0, center_x - x_size // 2), dataset.RasterXSize - x_size)
        y_offset = min(max(0, center_y - y_size // 2), dataset.RasterYSize - y_size)
    array = band.ReadAsArray(
        x_offset,
        y_offset,
        x_size,
        y_size,
        buf_xsize=width,
        buf_ysize=height,
    )
    if array is None:
        raise RuntimeError(f"RasterIO failed for factor {factor}: {window}")
    return array


def _tile_bounds_3857_runtime(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    span = 2.0 * WEB_MERCATOR_HALF_WORLD / (2**z)
    xmin = -WEB_MERCATOR_HALF_WORLD + x * span
    ymax = WEB_MERCATOR_HALF_WORLD - y * span
    return xmin, ymax - span, xmin + span, ymax


def _build_warped_vrt(dataset: Any) -> Any:
    from osgeo import gdal

    vrt = gdal.Warp(
        "",
        dataset,
        format="VRT",
        dstSRS="EPSG:3857",
        resampleAlg="bilinear",
        dstAlpha=True,
        multithread=False,
    )
    if vrt is None:
        raise RuntimeError("GDAL could not build the EPSG:3857 WarpedVRT")
    return vrt


def _read_warped_bounds(
    vrt: Any,
    bounds: tuple[float, float, float, float],
    output_width: int,
    output_height: int,
) -> Any:
    import numpy as np
    from osgeo import gdal, gdal_array

    xmin, ymin, xmax, ymax = bounds
    transform = vrt.GetGeoTransform()
    if transform[1] <= 0 or transform[5] >= 0 or transform[2] or transform[4]:
        raise RuntimeError(f"Unexpected WarpedVRT transform: {transform}")
    column_a = (xmin - transform[0]) / transform[1]
    column_b = (xmax - transform[0]) / transform[1]
    row_a = (ymax - transform[3]) / transform[5]
    row_b = (ymin - transform[3]) / transform[5]
    requested_x0, requested_x1 = min(column_a, column_b), max(column_a, column_b)
    requested_y0, requested_y1 = min(row_a, row_b), max(row_a, row_b)
    source_x0 = max(0, math.floor(requested_x0))
    source_y0 = max(0, math.floor(requested_y0))
    source_x1 = min(vrt.RasterXSize, math.ceil(requested_x1))
    source_y1 = min(vrt.RasterYSize, math.ceil(requested_y1))

    band_list = [1]
    if (
        vrt.RasterCount > 1
        and vrt.GetRasterBand(vrt.RasterCount).GetColorInterpretation() == gdal.GCI_AlphaBand
    ):
        band_list.append(vrt.RasterCount)
    dtype = np.dtype(gdal_array.GDALTypeCodeToNumericTypeCode(vrt.GetRasterBand(1).DataType))
    output = np.zeros((len(band_list), output_height, output_width), dtype=dtype)
    if source_x1 <= source_x0 or source_y1 <= source_y0:
        return output

    requested_width = max(1e-12, requested_x1 - requested_x0)
    requested_height = max(1e-12, requested_y1 - requested_y0)
    destination_x0 = max(
        0,
        min(output_width, round((source_x0 - requested_x0) / requested_width * output_width)),
    )
    destination_x1 = max(
        destination_x0,
        min(output_width, round((source_x1 - requested_x0) / requested_width * output_width)),
    )
    destination_y0 = max(
        0,
        min(output_height, round((source_y0 - requested_y0) / requested_height * output_height)),
    )
    destination_y1 = max(
        destination_y0,
        min(output_height, round((source_y1 - requested_y0) / requested_height * output_height)),
    )
    buffer_width = destination_x1 - destination_x0
    buffer_height = destination_y1 - destination_y0
    if buffer_width <= 0 or buffer_height <= 0:
        return output
    array = vrt.ReadAsArray(
        source_x0,
        source_y0,
        source_x1 - source_x0,
        source_y1 - source_y0,
        buf_xsize=buffer_width,
        buf_ysize=buffer_height,
        resample_alg=gdal.GRIORA_Bilinear,
        band_list=band_list,
    )
    if array is None:
        raise RuntimeError(f"WarpedVRT RasterIO failed for bounds {bounds}")
    array = np.asarray(array)
    if array.ndim == 2:
        array = array[np.newaxis, ...]
    output[:, destination_y0:destination_y1, destination_x0:destination_x1] = array
    return output


def _read_warped_tile(vrt: Any, tile: dict[str, int]) -> Any:
    return _read_warped_bounds(
        vrt,
        _tile_bounds_3857_runtime(tile["z"], tile["x"], tile["y"]),
        256,
        256,
    )


def _viewport_bounds(tiles: list[dict[str, int]]) -> tuple[float, float, float, float]:
    bounds = [_tile_bounds_3857_runtime(item["z"], item["x"], item["y"]) for item in tiles]
    return (
        min(item[0] for item in bounds),
        min(item[1] for item in bounds),
        max(item[2] for item in bounds),
        max(item[3] for item in bounds),
    )


def _read_viewport_tiles(vrt: Any, tiles: list[dict[str, int]]) -> str:
    return _combine_checksums(_pixel_checksum(_read_warped_tile(vrt, tile)) for tile in tiles)


def _read_viewport_metatile(vrt: Any, tiles: list[dict[str, int]]) -> str:
    array = _read_warped_bounds(vrt, _viewport_bounds(tiles), 1024, 1024)
    return _pixel_checksum(array)


def _read_tile_with_fresh_handle(asset: Path, driver: str, tile: dict[str, int]) -> str:
    source = _open_explicit_gdal(asset, driver)
    vrt = _build_warped_vrt(source)
    try:
        return _pixel_checksum(_read_warped_tile(vrt, tile))
    finally:
        vrt = None
        source = None


def _read_viewport_with_fresh_handles(
    asset: Path, driver: str, tiles: list[dict[str, int]]
) -> str:
    return _combine_checksums(_read_tile_with_fresh_handle(asset, driver, tile) for tile in tiles)


def _runtime_environment(
    *,
    runtime_label: str,
    strategy: str,
    driver: str,
    asset: Path,
    threads: int,
) -> dict[str, Any]:
    import psutil
    from osgeo import gdal

    plugin_name = f"gdal_{driver}.dll"
    plugin = Path(sys.executable).parent / "Library" / "lib" / "gdalplugins" / plugin_name
    return {
        "captured_at": _utc_now(),
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "logical_cpu_count": os.cpu_count(),
        "total_ram_bytes": int(psutil.virtual_memory().total),
        "python_executable": sys.executable,
        "python_version": sys.version,
        "runtime_label": runtime_label,
        "strategy": strategy,
        "requested_driver": driver,
        "gdal_version": gdal.VersionInfo("--version"),
        "gdal_release_name": gdal.VersionInfo("RELEASE_NAME"),
        "gdal_cachemax_bytes": int(gdal.GetCacheMax()),
        "threads": threads,
        "asset": str(asset),
        "gdal_data": os.environ.get("GDAL_DATA"),
        "gdal_driver_path": os.environ.get("GDAL_DRIVER_PATH"),
        "proj_data": os.environ.get("PROJ_DATA") or os.environ.get("PROJ_LIB"),
        "driver_plugin": (
            {
                "path": str(plugin),
                "size_bytes": plugin.stat().st_size,
                "sha256": _sha256_file(plugin),
            }
            if plugin.is_file()
            else None
        ),
    }


def _reference_map(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["window_id"]: item for item in entry["a0_references"]}


def run_runtime_arm(args: argparse.Namespace) -> Path:
    """Execute one source/runtime/driver arm in a fresh Python process."""
    from osgeo import gdal

    gdal.UseExceptions()
    gdal.SetCacheMax(int(args.gdal_cache_mib) * 1024 * 1024)
    gdal.SetConfigOption("GDAL_NUM_THREADS", str(args.threads))
    manifest_path = args.manifest.expanduser().resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    verification = validate_manifest(manifest)
    if verification["status"] != "passed":
        raise RuntimeError(f"Manifest verification failed: {verification['errors']}")
    try:
        entry = next(item for item in manifest["sources"] if item["source_id"] == args.source_id)
    except StopIteration as error:
        raise ValueError(f"Unknown source-id: {args.source_id}") from error
    source = Path(entry["path"]).resolve(strict=True)
    asset = args.asset.expanduser().resolve(strict=True) if args.asset else source
    output = args.output.expanduser().resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)

    source_before = source.stat()
    asset_before = asset.stat()
    source_sidecars_before = sorted(item.name for item in source.parent.glob(source.name + ".*"))
    references = _reference_map(entry)
    windows = entry["windows_1x"]
    focus_window = windows[0]
    trace_steps = entry["webmercator_trace"]["native_view"]["steps"]
    initial_tiles = trace_steps[0]["tiles"]
    single_tile = initial_tiles[5]
    measured_pan_steps = trace_steps[1:6]
    return_step = trace_steps[-1]
    samples: list[dict[str, Any]] = []
    workloads: list[dict[str, Any]] = []

    environment = _runtime_environment(
        runtime_label=args.runtime_label,
        strategy=args.strategy,
        driver=args.driver,
        asset=asset,
        threads=args.threads,
    )
    print(
        f"Runtime {args.strategy}: {environment['gdal_version']} / {args.driver} / "
        f"{args.source_id}",
        flush=True,
    )

    # W0: open and metadata, new handle for every repetition.
    def w0_operation() -> dict[str, Any]:
        warmup_ms: list[float] = []
        for _ in range(args.warmups):
            dataset, elapsed = _timed(lambda: _open_explicit_gdal(asset, args.driver))
            warmup_ms.append(elapsed)
            dataset = None
        durations: list[float] = []
        observed_drivers: set[str] = set()
        metadata: dict[str, Any] | None = None
        for _ in range(args.single_repeats):
            dataset, elapsed = _timed(lambda: _open_explicit_gdal(asset, args.driver))
            durations.append(elapsed)
            observed_drivers.add(dataset.GetDriver().ShortName)
            if metadata is None:
                metadata = _gdal_raster_metadata(dataset)
            dataset = None
        assert metadata is not None
        return {
            "warmup_ms": warmup_ms,
            "operation_ms": durations,
            "stats": _duration_stats(durations),
            "actual_drivers": sorted(observed_drivers),
            "metadata": metadata,
            "metadata_correctness": _metadata_matches_manifest(metadata, entry),
        }

    workloads.append(
        _measure_runtime_workload(
            workload_id="W0",
            variant="open_and_metadata",
            cache_scope="process_cold_new_handle",
            operation=w0_operation,
            sample_sink=samples,
        )
    )

    # W1: one exact 1x window on a persistent handle.
    def w1_operation() -> dict[str, Any]:
        dataset = _open_explicit_gdal(asset, args.driver)
        try:
            warmup_ms: list[float] = []
            warmup_hashes: list[str] = []
            for _ in range(args.warmups):
                array, elapsed = _timed(lambda: _read_direct_window(dataset, focus_window))
                warmup_ms.append(elapsed)
                warmup_hashes.append(_pixel_checksum(array))
            durations: list[float] = []
            hashes: list[str] = []
            for _ in range(args.single_repeats):
                array, elapsed = _timed(lambda: _read_direct_window(dataset, focus_window))
                durations.append(elapsed)
                hashes.append(_pixel_checksum(array))
            expected = references[focus_window["id"]]["sha256"]
            unique = sorted(set(hashes))
            return {
                "window_id": focus_window["id"],
                "source_factor": 1,
                "warmup_ms": warmup_ms,
                "operation_ms": durations,
                "stats": _duration_stats(durations),
                "expected_sha256": expected,
                "unique_sha256": unique,
                "correctness": {
                    "passed": unique == [expected] and all(value == expected for value in warmup_hashes),
                    "mismatch_count": sum(value != expected for value in hashes + warmup_hashes),
                },
                "signature": unique[0] if len(unique) == 1 else None,
            }
        finally:
            dataset = None

    workloads.append(
        _measure_runtime_workload(
            workload_id="W1",
            variant="single_1024x1024_exact_1x",
            cache_scope="handle_warm",
            operation=w1_operation,
            sample_sink=samples,
        )
    )

    # W2: all 100 distinct immutable windows and their E0 checksums.
    def w2_operation() -> dict[str, Any]:
        dataset = _open_explicit_gdal(asset, args.driver)
        try:
            for _ in range(args.warmups):
                _read_direct_window(dataset, focus_window)
            durations: list[float] = []
            hashes: list[str] = []
            mismatches: list[dict[str, str]] = []
            for index, window in enumerate(windows, start=1):
                array, elapsed = _timed(lambda item=window: _read_direct_window(dataset, item))
                digest = _pixel_checksum(array)
                durations.append(elapsed)
                hashes.append(digest)
                expected = references[window["id"]]["sha256"]
                if digest != expected:
                    mismatches.append(
                        {"window_id": window["id"], "expected": expected, "actual": digest}
                    )
                if index == 1 or index % 10 == 0 or index == len(windows):
                    print(f"  W2 windows: {index}/{len(windows)}", flush=True)
            return {
                "source_factor": 1,
                "window_count": len(windows),
                "operation_ms": durations,
                "stats": _duration_stats(durations),
                "signature": _combine_checksums(hashes),
                "correctness": {
                    "passed": not mismatches and len(hashes) == len(windows),
                    "mismatch_count": len(mismatches),
                    "mismatches": mismatches[:10],
                },
            }
        finally:
            dataset = None

    workloads.append(
        _measure_runtime_workload(
            workload_id="W2",
            variant="manifest_100_windows_exact_1x",
            cache_scope="handle_warm_spatially_distributed",
            operation=w2_operation,
            sample_sink=samples,
        )
    )

    # W3: a renderer-like Web Mercator tile with the source driver constrained
    # before the WarpedVRT is created.
    def w3_warm_operation() -> dict[str, Any]:
        dataset = _open_explicit_gdal(asset, args.driver)
        vrt = _build_warped_vrt(dataset)
        try:
            warmup_ms: list[float] = []
            for _ in range(args.warmups):
                _array, elapsed = _timed(lambda: _read_warped_tile(vrt, single_tile))
                warmup_ms.append(elapsed)
            durations: list[float] = []
            hashes: list[str] = []
            for _ in range(args.single_repeats):
                array, elapsed = _timed(lambda: _read_warped_tile(vrt, single_tile))
                durations.append(elapsed)
                hashes.append(_pixel_checksum(array))
            unique = sorted(set(hashes))
            return {
                "tile": single_tile,
                "warped_vrt": _gdal_raster_metadata(vrt),
                "warmup_ms": warmup_ms,
                "operation_ms": durations,
                "stats": _duration_stats(durations),
                "signature": unique[0] if len(unique) == 1 else None,
                "stable_pixels": len(unique) == 1,
            }
        finally:
            vrt = None
            dataset = None

    workloads.append(
        _measure_runtime_workload(
            workload_id="W3",
            variant="webmercator_tile_256",
            cache_scope="handle_warm",
            operation=w3_warm_operation,
            sample_sink=samples,
        )
    )

    workloads.append(
        _measure_runtime_workload(
            workload_id="W3",
            variant="webmercator_tile_256",
            cache_scope="renderer_fresh_handle_probe",
            operation=lambda: _single_cold_tile_probe(asset, args.driver, single_tile),
            sample_sink=samples,
        )
    )

    # W4: sixteen independent tile reads, persistent handle for repeatable
    # statistics and one app-like probe that reopens source+WarpedVRT per tile.
    def w4_warm_operation() -> dict[str, Any]:
        dataset = _open_explicit_gdal(asset, args.driver)
        vrt = _build_warped_vrt(dataset)
        try:
            warmup_ms: list[float] = []
            for _ in range(args.warmups):
                _digest, elapsed = _timed(lambda: _read_viewport_tiles(vrt, initial_tiles))
                warmup_ms.append(elapsed)
            durations: list[float] = []
            hashes: list[str] = []
            for index in range(args.trace_repeats):
                digest, elapsed = _timed(lambda: _read_viewport_tiles(vrt, initial_tiles))
                durations.append(elapsed)
                hashes.append(digest)
                print(f"  W4 trace: {index + 1}/{args.trace_repeats}", flush=True)
            unique = sorted(set(hashes))
            return {
                "tile_count": len(initial_tiles),
                "warmup_ms": warmup_ms,
                "operation_ms": durations,
                "stats": _duration_stats(durations),
                "signature": unique[0] if len(unique) == 1 else None,
                "stable_pixels": len(unique) == 1,
            }
        finally:
            vrt = None
            dataset = None

    workloads.append(
        _measure_runtime_workload(
            workload_id="W4",
            variant="viewport_4x4_independent_tiles",
            cache_scope="handle_warm",
            operation=w4_warm_operation,
            sample_sink=samples,
        )
    )

    workloads.append(
        _measure_runtime_workload(
            workload_id="W4",
            variant="viewport_4x4_independent_tiles",
            cache_scope="renderer_fresh_handle_probe",
            operation=lambda: _single_cold_viewport_probe(
                asset, args.driver, initial_tiles
            ),
            sample_sink=samples,
        )
    )

    # W5: one metatile covering exactly the W4 viewport.
    def w5_operation() -> dict[str, Any]:
        dataset = _open_explicit_gdal(asset, args.driver)
        vrt = _build_warped_vrt(dataset)
        try:
            warmup_ms: list[float] = []
            for _ in range(args.warmups):
                _digest, elapsed = _timed(lambda: _read_viewport_metatile(vrt, initial_tiles))
                warmup_ms.append(elapsed)
            durations: list[float] = []
            hashes: list[str] = []
            for index in range(args.trace_repeats):
                digest, elapsed = _timed(lambda: _read_viewport_metatile(vrt, initial_tiles))
                durations.append(elapsed)
                hashes.append(digest)
                print(f"  W5 metatile: {index + 1}/{args.trace_repeats}", flush=True)
            unique = sorted(set(hashes))
            return {
                "output_size": [1024, 1024],
                "warmup_ms": warmup_ms,
                "operation_ms": durations,
                "stats": _duration_stats(durations),
                "signature": unique[0] if len(unique) == 1 else None,
                "stable_pixels": len(unique) == 1,
            }
        finally:
            vrt = None
            dataset = None

    workloads.append(
        _measure_runtime_workload(
            workload_id="W5",
            variant="viewport_metatile_1024",
            cache_scope="handle_warm",
            operation=w5_operation,
            sample_sink=samples,
        )
    )

    # W6 and W7 share a navigation trace and therefore a resource envelope.
    navigation_record, return_record = _measure_navigation_trace(
        asset=asset,
        driver=args.driver,
        pan_steps=measured_pan_steps,
        return_step=return_step,
        warmups=args.warmups,
        repeats=args.trace_repeats,
        sample_sink=samples,
        fresh_handles=False,
    )
    workloads.extend([navigation_record, return_record])

    if not args.skip_navigation_cold_probe:
        navigation_record, return_record = _measure_navigation_trace(
            asset=asset,
            driver=args.driver,
            pan_steps=measured_pan_steps,
            return_step=return_step,
            warmups=0,
            repeats=1,
            sample_sink=samples,
            fresh_handles=True,
        )
        workloads.extend([navigation_record, return_record])

    # W8: source-native reduced levels. One record carries independent stats
    # for factors 2/4/8/16 and a deterministic signature for cross-arm parity.
    def w8_operation() -> dict[str, Any]:
        dataset = _open_explicit_gdal(asset, args.driver)
        try:
            factors: dict[str, Any] = {}
            all_hashes: list[str] = []
            for factor in (2, 4, 8, 16):
                warmup_ms: list[float] = []
                for _ in range(args.warmups):
                    _array, elapsed = _timed(
                        lambda value=factor: _read_direct_window(
                            dataset, focus_window, factor=value
                        )
                    )
                    warmup_ms.append(elapsed)
                durations: list[float] = []
                hashes: list[str] = []
                for _ in range(args.single_repeats):
                    array, elapsed = _timed(
                        lambda value=factor: _read_direct_window(
                            dataset, focus_window, factor=value
                        )
                    )
                    durations.append(elapsed)
                    hashes.append(_pixel_checksum(array))
                unique = sorted(set(hashes))
                all_hashes.extend(unique)
                factors[str(factor)] = {
                    "source_factor": factor,
                    "warmup_ms": warmup_ms,
                    "operation_ms": durations,
                    "stats": _duration_stats(durations),
                    "signature": unique[0] if len(unique) == 1 else None,
                    "stable_pixels": len(unique) == 1,
                }
                print(f"  W8 factor {factor} complete", flush=True)
            return {
                "factors": factors,
                "signature": _combine_checksums(all_hashes),
                "stable_pixels": all(item["stable_pixels"] for item in factors.values()),
            }
        finally:
            dataset = None

    workloads.append(
        _measure_runtime_workload(
            workload_id="W8",
            variant="native_reduced_levels_2_4_8_16",
            cache_scope="handle_warm",
            operation=w8_operation,
            sample_sink=samples,
        )
    )

    source_after = source.stat()
    asset_after = asset.stat()
    source_unchanged = {
        "size": source_before.st_size == source_after.st_size == entry["fingerprint"]["size_bytes"],
        "mtime": source_before.st_mtime_ns
        == source_after.st_mtime_ns
        == entry["fingerprint"]["mtime_ns_observed"],
        "sidecars_before": source_sidecars_before,
        "sidecars_after": sorted(item.name for item in source.parent.glob(source.name + ".*")),
    }
    asset_unchanged = {
        "size": asset_before.st_size == asset_after.st_size,
        "mtime": asset_before.st_mtime_ns == asset_after.st_mtime_ns,
    }
    correctness_checks = {
        "manifest_valid": verification["status"] == "passed",
        "explicit_driver_every_open": all(
            item["details"].get("actual_drivers", [args.driver]) == [args.driver]
            for item in workloads
        ),
        "metadata_matches_manifest": workloads[0]["details"]["metadata_correctness"]["passed"],
        "w1_reference_matches": next(
            item for item in workloads if item["workload"] == "W1"
        )["details"]["correctness"]["passed"],
        "w2_all_references_match": next(
            item for item in workloads if item["workload"] == "W2"
        )["details"]["correctness"]["passed"],
        "stable_runtime_pixels": all(
            item["details"].get("stable_pixels", True) for item in workloads
        ),
        "source_unchanged": source_unchanged["size"]
        and source_unchanged["mtime"]
        and source_unchanged["sidecars_before"] == source_unchanged["sidecars_after"],
        "asset_unchanged": all(asset_unchanged.values()),
    }
    record = {
        "schema_name": RUNTIME_SCHEMA_NAME,
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "stage": args.stage,
        "generated_at": _utc_now(),
        "manifest_path": str(manifest_path),
        "manifest_id": manifest["manifest_id"],
        "source_id": args.source_id,
        "source_path": str(source),
        "asset_path": str(asset),
        "strategy": args.strategy,
        "requested_driver": args.driver,
        "environment": environment,
        "configuration": {
            "gdal_cache_mib": args.gdal_cache_mib,
            "threads": args.threads,
            "warmups": args.warmups,
            "single_repeats": args.single_repeats,
            "trace_repeats": args.trace_repeats,
            "cache_semantics": {
                "process_cold_new_handle": "new GDAL dataset handle; OS cache not flushed",
                "handle_warm": "same source and WarpedVRT handles; three unreported warmups",
                "renderer_fresh_handle_probe": "one measured app-like sequential probe; source and WarpedVRT reopened per tile",
                "tile_cache_warm": "not measured: runner does not include GeoTile Label PNG cache",
            },
        },
        "workloads": workloads,
        "correctness": {
            "checks": correctness_checks,
            "passed": all(correctness_checks.values()),
            "source_unchanged": source_unchanged,
            "asset_unchanged": asset_unchanged,
        },
    }
    _write_json_atomic(output, record)
    resource_path = output.with_suffix(".resources.csv")
    _write_csv_atomic(
        resource_path,
        [
            "workload_label",
            "elapsed_ms",
            "rss_bytes",
            "system_available_bytes",
            "read_bytes",
            "write_bytes",
        ],
        samples,
    )
    print(f"\nArm result: {output}", flush=True)
    print(f"Correctness: {record['correctness']['passed']}", flush=True)
    return output


def _single_cold_tile_probe(asset: Path, driver: str, tile: dict[str, int]) -> dict[str, Any]:
    digest, elapsed = _timed(lambda: _read_tile_with_fresh_handle(asset, driver, tile))
    return {
        "tile": tile,
        "operation_ms": [elapsed],
        "stats": _duration_stats([elapsed]),
        "signature": digest,
        "actual_drivers": [driver],
    }


def _single_cold_viewport_probe(
    asset: Path, driver: str, tiles: list[dict[str, int]]
) -> dict[str, Any]:
    digest, elapsed = _timed(lambda: _read_viewport_with_fresh_handles(asset, driver, tiles))
    return {
        "tile_count": len(tiles),
        "operation_ms": [elapsed],
        "stats": _duration_stats([elapsed]),
        "signature": digest,
        "actual_drivers": [driver],
    }


def _measure_navigation_trace(
    *,
    asset: Path,
    driver: str,
    pan_steps: list[dict[str, Any]],
    return_step: dict[str, Any],
    warmups: int,
    repeats: int,
    sample_sink: list[dict[str, Any]],
    fresh_handles: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    cache_scope = "renderer_fresh_handle_probe" if fresh_handles else "handle_warm"
    label = f"W6_W7:navigation:{cache_scope}"
    print(f"\n[W6/W7] navigation / {cache_scope}", flush=True)

    dataset = None
    vrt = None
    if not fresh_handles:
        dataset = _open_explicit_gdal(asset, driver)
        vrt = _build_warped_vrt(dataset)

    def read_viewport(tiles: list[dict[str, int]]) -> str:
        if fresh_handles:
            return _read_viewport_with_fresh_handles(asset, driver, tiles)
        return _read_viewport_tiles(vrt, tiles)

    def one_trace() -> tuple[float, str, float, str, list[float]]:
        pan_durations: list[float] = []
        pan_hashes: list[str] = []
        for step in pan_steps:
            digest, elapsed = _timed(lambda item=step: read_viewport(item["tiles"]))
            pan_durations.append(elapsed)
            pan_hashes.append(digest)
        return_digest, return_elapsed = _timed(lambda: read_viewport(return_step["tiles"]))
        return (
            sum(pan_durations),
            _combine_checksums(pan_hashes),
            return_elapsed,
            return_digest,
            pan_durations,
        )

    try:
        with _RuntimeResourceTrace(label) as trace:
            warmup_pan_ms: list[float] = []
            warmup_return_ms: list[float] = []
            for _ in range(warmups):
                pan_ms, _pan_hash, return_ms, _return_hash, _steps = one_trace()
                warmup_pan_ms.append(pan_ms)
                warmup_return_ms.append(return_ms)
            pan_totals: list[float] = []
            pan_steps_ms: list[list[float]] = []
            pan_hashes: list[str] = []
            return_times: list[float] = []
            return_hashes: list[str] = []
            for index in range(repeats):
                pan_ms, pan_hash, return_ms, return_hash, step_ms = one_trace()
                pan_totals.append(pan_ms)
                pan_steps_ms.append(step_ms)
                pan_hashes.append(pan_hash)
                return_times.append(return_ms)
                return_hashes.append(return_hash)
                print(f"  navigation trace: {index + 1}/{repeats}", flush=True)
        resource = trace.summary()
        sample_sink.extend(trace.samples)
    finally:
        vrt = None
        dataset = None

    pan_unique = sorted(set(pan_hashes))
    return_unique = sorted(set(return_hashes))
    shared_group = hashlib.sha256(label.encode("utf-8")).hexdigest()[:12]
    common = {
        "resource": resource,
        "shared_resource_group": shared_group,
    }
    pan_record = {
        "workload": "W6",
        "variant": "five_adjacent_viewports_4x4_tiles",
        "cache_scope": cache_scope,
        **common,
        "details": {
            "viewport_count_per_trace": len(pan_steps),
            "warmup_ms": warmup_pan_ms,
            "operation_ms": pan_totals,
            "per_viewport_ms": pan_steps_ms,
            "stats": _duration_stats(pan_totals),
            "per_viewport_stats": _duration_stats(
                [value for trace_values in pan_steps_ms for value in trace_values]
            ),
            "signature": pan_unique[0] if len(pan_unique) == 1 else None,
            "stable_pixels": len(pan_unique) == 1,
            "actual_drivers": [driver],
        },
    }
    return_record = {
        "workload": "W7",
        "variant": "return_to_first_viewport_4x4_tiles",
        "cache_scope": cache_scope,
        **common,
        "details": {
            "warmup_ms": warmup_return_ms,
            "operation_ms": return_times,
            "stats": _duration_stats(return_times),
            "signature": return_unique[0] if len(return_unique) == 1 else None,
            "stable_pixels": len(return_unique) == 1,
            "actual_drivers": [driver],
        },
    }
    return pan_record, return_record


def _workload_key(workload: dict[str, Any]) -> tuple[str, str, str]:
    return workload["workload"], workload["variant"], workload["cache_scope"]


def _workload_signature(workload: dict[str, Any]) -> str | None:
    return workload.get("details", {}).get("signature")


def _flatten_runtime_rows(arms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for arm in arms:
        for workload in arm["workloads"]:
            details = workload["details"]
            stats = details.get("stats") or {}
            if workload["workload"] == "W8":
                factor_stats = [item["stats"] for item in details["factors"].values()]
                stats = {
                    "count": sum(item.get("count", 0) for item in factor_stats),
                    "p50_ms": statistics.fmean(item["p50_ms"] for item in factor_stats),
                    "p95_ms": max(item["p95_ms"] for item in factor_stats),
                    "maximum_ms": max(item["maximum_ms"] for item in factor_stats),
                }
            resource = workload["resource"]
            rows.append(
                {
                    "source_id": arm["source_id"],
                    "strategy": arm["strategy"],
                    "threads": int(arm["configuration"]["threads"]),
                    "gdal_version": arm["environment"]["gdal_release_name"],
                    "driver": arm["requested_driver"],
                    "workload": workload["workload"],
                    "variant": workload["variant"],
                    "cache_scope": workload["cache_scope"],
                    "repeat_count": stats.get("count"),
                    "p50_ms": stats.get("p50_ms"),
                    "p95_ms": stats.get("p95_ms"),
                    "maximum_ms": stats.get("maximum_ms"),
                    "wall_ms": resource["wall_ms"],
                    "cpu_ms": resource["cpu_ms"],
                    "peak_rss_bytes": resource["peak_rss_bytes"],
                    "peak_rss_delta_bytes": resource["peak_rss_delta_bytes"],
                    "read_bytes": resource["io_delta"]["read_bytes"],
                    "write_bytes": resource["io_delta"]["write_bytes"],
                    "signature": _workload_signature(workload),
                }
            )
    return rows


def _runtime_summary_lines(
    *,
    stage: str,
    manifest_id: str,
    arm_count: int,
    comparisons: list[dict[str, Any]],
    warp_parity: list[dict[str, Any]],
    correctness_checks: dict[str, bool],
    correctness_passed: bool,
    warped_outputs_exact: bool,
    warped_differences_characterized: bool,
) -> list[str]:
    lines = [
        f"# {stage} — runtime W0-W8",
        "",
        f"- Status: **{'DONE' if correctness_passed else 'FAILED'}**",
        f"- Manifest: `{manifest_id}`",
        f"- Arms: {arm_count}",
        f"- A0 problem reproduced: `{correctness_checks['a0_problem_reproduced']}`",
        f"- Direct pixels W1/W2/W8 exact: "
        f"`{correctness_checks['all_cross_arm_direct_pixels_match']}`",
        f"- Warped pixels W3–W7 exact: `{warped_outputs_exact}`",
        f"- Warped differences characterized: `{warped_differences_characterized}`",
        f"- Correctness gate: `{correctness_passed}`",
        "",
        "## A0 vs A1",
        "",
        "| Source | Workload | Cache | A0 p95 | A1 p95 | A1/A0 |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    for item in comparisons:
        if item["a0_p95_ms"] is None or item["a1_p95_ms"] is None:
            continue
        lines.append(
            f"| {item['source_id']} | {item['workload']} / {item['variant']} | "
            f"{item['cache_scope']} | {item['a0_p95_ms']:.2f} ms | "
            f"{item['a1_p95_ms']:.2f} ms | {item['a1_over_a0_p95']:.3f}× |"
        )

    if warp_parity:
        lines.extend(
            [
                "",
                "## WarpedVRT parity across GDAL versions",
                "",
                "Raw source pixels remain exact. The table characterizes only the "
                "bilinear WarpedVRT/RasterIO drift between GDAL versions.",
                "",
                "| Source | Array | NRMSE/span | PSNR | Correlation | Max abs | Alpha exact |",
                "| --- | --- | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for parity in warp_parity:
            source_id = parity["first"]["source_id"]
            alpha_indices = {
                band["index"]
                for band in parity["first"]["warped_vrt"]["bands"]
                if band["color_interpretation"].casefold() == "alpha"
            }
            for name, diagnostic in parity["comparisons"].items():
                data_bands = [
                    band for band in diagnostic["per_band"] if band["band"] not in alpha_indices
                ]
                alpha_bands = [
                    band for band in diagnostic["per_band"] if band["band"] in alpha_indices
                ]
                worst_nrmse = max(
                    band["normalized_rmse_by_signal_span"] for band in data_bands
                )
                worst_psnr = min(band["psnr_by_signal_span_db"] for band in data_bands)
                worst_correlation = min(band["correlation"] for band in data_bands)
                maximum = max(band["maximum_absolute_difference"] for band in data_bands)
                alpha_exact = all(band["different_pixels"] == 0 for band in alpha_bands)
                lines.append(
                    f"| {source_id} | {name.upper()} | {worst_nrmse:.4%} | "
                    f"{worst_psnr:.2f} dB | {worst_correlation:.6f} | {maximum} | "
                    f"{alpha_exact} |"
                )
        lines.extend(
            [
                "",
                "E2 uses these thresholds only to characterize a documented GDAL-version "
                "RasterIO/warp drift. They are not a decoder-parity tolerance for E3.",
            ]
        )

    lines.extend(
        [
            "",
            "`renderer_fresh_handle_probe` is one sequential, app-like probe. Repeated "
            "p50/p95 statistics come from `handle_warm`; the GeoTile Label PNG cache is "
            "not measured.",
            "",
        ]
    )
    return lines


def _runtime_workload(
    arm: dict[str, Any],
    workload: str,
    variant: str,
    cache_scope: str = "handle_warm",
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in arm["workloads"]
            if _workload_key(item) == (workload, variant, cache_scope)
        ),
        None,
    )


def _e3_performance(
    *,
    by_arm: dict[tuple[str, str, int], dict[str, Any]],
    source_ids: list[str],
    threads: list[int],
) -> dict[str, Any]:
    """Evaluate the latency and resource gates from section 11 of the plan."""
    candidates: list[dict[str, Any]] = []
    w4_key = ("W4", "viewport_4x4_independent_tiles")
    w6_key = ("W6", "five_adjacent_viewports_4x4_tiles")
    w7_key = ("W7", "return_to_first_viewport_4x4_tiles")
    for source_id in source_ids:
        for thread_count in threads:
            baseline = by_arm.get((source_id, "A1", thread_count))
            candidate = by_arm.get((source_id, "B1", thread_count))
            if not baseline or not candidate:
                continue
            a_w4 = _runtime_workload(baseline, *w4_key)
            b_w4 = _runtime_workload(candidate, *w4_key)
            a_w6 = _runtime_workload(baseline, *w6_key)
            b_w6 = _runtime_workload(candidate, *w6_key)
            a_w7 = _runtime_workload(baseline, *w7_key)
            b_w7 = _runtime_workload(candidate, *w7_key)
            if not all((a_w4, b_w4, a_w6, b_w6, a_w7, b_w7)):
                continue

            a_viewport = a_w4["details"]["stats"]["p95_ms"]
            b_viewport = b_w4["details"]["stats"]["p95_ms"]
            a_initial_viewport = a_w4["details"]["warmup_ms"][0]
            b_initial_viewport = b_w4["details"]["warmup_ms"][0]
            a_pan = a_w6["details"]["per_viewport_stats"]["p95_ms"]
            b_pan = b_w6["details"]["per_viewport_stats"]["p95_ms"]
            a_return = a_w7["details"]["stats"]["p95_ms"]
            b_return = b_w7["details"]["stats"]["p95_ms"]
            interactive = [
                item
                for item in candidate["workloads"]
                if item["workload"] in {"W3", "W4", "W5", "W6", "W7"}
                and item["cache_scope"] == "handle_warm"
            ]
            total_ram = int(candidate["environment"]["total_ram_bytes"])
            rss_limit = min(2 * 1024**3, int(total_ram * 0.15))
            peak_delta = max(item["resource"]["peak_rss_delta_bytes"] for item in interactive)
            retained = max(
                0,
                max(
                    item["resource"]["rss_after_bytes"]
                    - item["resource"]["rss_before_bytes"]
                    for item in interactive
                ),
            )
            retained_limit = min(256 * 1024**2, int(total_ram * 0.02))
            absolute = (
                b_initial_viewport <= 2000.0
                and b_viewport <= 2000.0
                and b_pan <= 1000.0
                and b_return <= 500.0
            )
            speedups = {
                "initial_viewport": (
                    a_initial_viewport / b_initial_viewport
                    if b_initial_viewport
                    else None
                ),
                "viewport": a_viewport / b_viewport if b_viewport else None,
                "adjacent_pan": a_pan / b_pan if b_pan else None,
                "warm_return": a_return / b_return if b_return else None,
            }
            conditional = all(
                value is not None and value >= 4.0 for value in speedups.values()
            )
            resource_passed = peak_delta <= rss_limit
            memory_stable = retained <= retained_limit
            passed = (absolute or conditional) and resource_passed and memory_stable
            candidates.append(
                {
                    "source_id": source_id,
                    "threads": thread_count,
                    "latency_ms": {
                        "initial_persistent_viewport": b_initial_viewport,
                        "viewport_4x4_p95": b_viewport,
                        "adjacent_viewport_p95": b_pan,
                        "warm_return_p95": b_return,
                    },
                    "a1_latency_ms": {
                        "initial_persistent_viewport": a_initial_viewport,
                        "viewport_4x4_p95": a_viewport,
                        "adjacent_viewport_p95": a_pan,
                        "warm_return_p95": a_return,
                    },
                    "speedup_over_a1": speedups,
                    "absolute_latency_gate": absolute,
                    "conditional_four_x_gate": conditional,
                    "peak_rss_delta_bytes": peak_delta,
                    "peak_rss_delta_limit_bytes": rss_limit,
                    "resource_gate": resource_passed,
                    "maximum_retained_rss_bytes": retained,
                    "retained_rss_limit_bytes": retained_limit,
                    "memory_returns_to_stable_level": memory_stable,
                    "passed": passed,
                }
            )

    passing_by_source = {
        source_id: [item for item in candidates if item["source_id"] == source_id and item["passed"]]
        for source_id in source_ids
    }
    common_threads = [
        thread_count
        for thread_count in threads
        if all(
            any(item["threads"] == thread_count for item in passing_by_source[source_id])
            for source_id in source_ids
        )
    ]
    recommended_threads = None
    if common_threads:
        recommended_threads = min(
            common_threads,
            key=lambda count: max(
                item["latency_ms"]["viewport_4x4_p95"]
                for item in candidates
                if item["threads"] == count
            ),
        )
    best_by_source = {}
    for source_id in source_ids:
        pool = passing_by_source[source_id] or [
            item for item in candidates if item["source_id"] == source_id
        ]
        if pool:
            best_by_source[source_id] = min(
                pool,
                key=lambda item: (
                    item["latency_ms"]["initial_persistent_viewport"],
                    item["latency_ms"]["viewport_4x4_p95"],
                ),
            )["threads"]
    return {
        "schema_name": "geotile_jp2_fullres_e3_performance",
        "schema_version": 1,
        "gate_definition": {
            "initial_persistent_viewport_ms": 2000,
            "viewport_4x4_p95_ms": 2000,
            "adjacent_viewport_p95_ms": 1000,
            "warm_return_p95_ms": 500,
            "conditional_minimum_speedup": 4.0,
            "peak_rss_delta": "min(2 GiB, 15% host RAM)",
            "retained_rss": "min(256 MiB, 2% host RAM)",
        },
        "candidates": candidates,
        "best_threads_by_source": best_by_source,
        "best_threads_by_source_is_recommendation": bool(recommended_threads),
        "recommended_threads": recommended_threads,
        "passed": bool(source_ids)
        and all(bool(passing_by_source[source_id]) for source_id in source_ids),
    }


def _summarize_runtime_e3(args: argparse.Namespace) -> Path:
    """Aggregate the same-GDAL A1/B1 decoder matrix and enforce E3-C/E3-P."""
    run_dir = args.run_dir.expanduser().resolve(strict=True)
    arm_paths = sorted((run_dir / "arms").glob("*.json"))
    arms = [json.loads(path.read_text(encoding="utf-8")) for path in arm_paths]
    if not arms:
        raise FileNotFoundError(f"No arm JSON files found in {run_dir / 'arms'}")
    manifests = {arm.get("manifest_id") for arm in arms}
    if len(manifests) != 1:
        raise RuntimeError(f"Arms use different manifests: {manifests}")
    manifest_id = next(iter(manifests))
    manifest_path = Path(arms[0]["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_ids = sorted(item["source_id"] for item in manifest["sources"])
    expected_threads = sorted(
        {int(value.strip()) for value in args.expected_threads.split(",") if value.strip()}
    )
    if not expected_threads:
        raise ValueError("E3 expected-threads must not be empty")

    by_arm: dict[tuple[str, str, int], dict[str, Any]] = {}
    duplicate_arms: list[tuple[str, str, int]] = []
    for arm in arms:
        key = (arm["source_id"], arm["strategy"], int(arm["configuration"]["threads"]))
        if key in by_arm:
            duplicate_arms.append(key)
        by_arm[key] = arm
    required = {
        (source_id, strategy, thread_count)
        for source_id in source_ids
        for strategy in ("A1", "B1")
        for thread_count in expected_threads
    }
    missing = sorted(required - set(by_arm))

    arm_checks: list[dict[str, Any]] = []
    for arm in arms:
        expected_driver = "JP2OpenJPEG" if arm["strategy"] == "A1" else "JP2Grok"
        arm_checks.append(
            {
                "source_id": arm["source_id"],
                "strategy": arm["strategy"],
                "threads": int(arm["configuration"]["threads"]),
                "requested_driver": arm["requested_driver"],
                "driver_contract": arm["requested_driver"] == expected_driver,
                "passed": arm["correctness"]["passed"]
                and arm["requested_driver"] == expected_driver,
                "checks": arm["correctness"]["checks"],
                "measurement_stage": arm.get("stage"),
                "provenance": arm.get("provenance"),
            }
        )

    cross_checks: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    same_gdal_pairs = True
    for source_id in source_ids:
        for thread_count in expected_threads:
            baseline = by_arm.get((source_id, "A1", thread_count))
            candidate = by_arm.get((source_id, "B1", thread_count))
            if not baseline or not candidate:
                continue
            same_gdal_pairs = same_gdal_pairs and (
                baseline["environment"]["gdal_release_name"]
                == candidate["environment"]["gdal_release_name"]
            )
            baseline_workloads = {_workload_key(item): item for item in baseline["workloads"]}
            candidate_workloads = {_workload_key(item): item for item in candidate["workloads"]}
            for key in sorted(set(baseline_workloads) & set(candidate_workloads)):
                first = baseline_workloads[key]
                second = candidate_workloads[key]
                signature_a = _workload_signature(first)
                signature_b = _workload_signature(second)
                if key[0] == "W0":
                    equal = (
                        first["details"]["metadata_correctness"]["passed"]
                        and second["details"]["metadata_correctness"]["passed"]
                    )
                else:
                    equal = bool(signature_a and signature_b and signature_a == signature_b)
                cross_checks.append(
                    {
                        "source_id": source_id,
                        "threads": thread_count,
                        "workload": key[0],
                        "variant": key[1],
                        "cache_scope": key[2],
                        "a1_signature": signature_a,
                        "b1_signature": signature_b,
                        "passed": equal,
                    }
                )
                stats_a = first["details"].get("stats") or {}
                stats_b = second["details"].get("stats") or {}
                p95_a = stats_a.get("p95_ms")
                p95_b = stats_b.get("p95_ms")
                comparisons.append(
                    {
                        "source_id": source_id,
                        "threads": thread_count,
                        "workload": key[0],
                        "variant": key[1],
                        "cache_scope": key[2],
                        "a1_p95_ms": p95_a,
                        "b1_p95_ms": p95_b,
                        "b1_over_a1_p95": (
                            round(p95_b / p95_a, 6)
                            if p95_a not in (None, 0) and p95_b is not None
                            else None
                        ),
                        "a1_peak_rss_bytes": first["resource"]["peak_rss_bytes"],
                        "b1_peak_rss_bytes": second["resource"]["peak_rss_bytes"],
                    }
                )

    parity: list[dict[str, Any]] = []
    for source_id in source_ids:
        path = run_dir / "warp-parity" / f"comparison_{source_id}.json"
        if path.exists():
            parity.append(json.loads(path.read_text(encoding="utf-8")))
    parity_sources = {
        item.get("first", {}).get("source_id")
        for item in parity
        if item.get("passed") and item.get("exact_pixels")
    }
    direct_checks = [
        item for item in cross_checks if item["workload"] in {"W0", "W1", "W2", "W8"}
    ]
    warped_checks = [
        item for item in cross_checks if item["workload"] in {"W3", "W4", "W5", "W6", "W7"}
    ]
    correctness_checks = {
        "all_required_arms_present": not missing and not duplicate_arms,
        "all_arm_checks_passed": all(item["passed"] for item in arm_checks),
        "same_gdal_for_each_decoder_pair": same_gdal_pairs,
        "all_cross_arm_direct_pixels_match": bool(direct_checks)
        and all(item["passed"] for item in direct_checks),
        "all_cross_arm_warped_pixels_match": bool(warped_checks)
        and all(item["passed"] for item in warped_checks),
        "boundary_mask_and_outside_parity": parity_sources == set(source_ids),
        "resource_metrics_present": all(
            item["resource"].get("peak_rss_bytes") is not None
            and item["resource"].get("cpu_ms") is not None
            and item["resource"].get("io_delta") is not None
            for arm in arms
            for item in arm["workloads"]
        ),
    }
    correctness = {
        "schema_name": "geotile_jp2_fullres_correctness",
        "schema_version": 2,
        "stage": "E3",
        "generated_at": _utc_now(),
        "checks": correctness_checks,
        "passed": all(correctness_checks.values()),
        "missing_arms": [list(item) for item in missing],
        "duplicate_arms": [list(item) for item in duplicate_arms],
        "arms": arm_checks,
        "cross_arm": cross_checks,
        "warp_parity": parity,
        "interpretation": (
            "E3 compares JP2OpenJPEG and JP2Grok on the same GDAL runtime. "
            "No numerical decoder tolerance is accepted: source, warped, alpha, "
            "mask, edge and outside-scene captures must be bit exact."
        ),
    }
    performance = _e3_performance(
        by_arm=by_arm,
        source_ids=source_ids,
        threads=expected_threads,
    )
    results = {
        "schema_name": "geotile_jp2_fullres_runtime_results",
        "schema_version": 2,
        "stage": "E3",
        "generated_at": _utc_now(),
        "manifest_id": manifest_id,
        "status": "accepted" if correctness["passed"] and performance["passed"] else "rejected",
        "correctness_gate_passed": correctness["passed"],
        "performance_gate_passed": performance["passed"],
        "arms": arms,
        "comparisons": comparisons,
        "warp_parity": parity,
        "performance": performance,
    }
    _write_json_atomic(run_dir / "results.json", results)
    _write_json_atomic(run_dir / "correctness.json", correctness)
    _write_json_atomic(run_dir / "performance.json", performance)
    _write_json_atomic(
        run_dir / "environment.json",
        {"stage": "E3", "arms": [arm["environment"] for arm in arms]},
    )

    rows = _flatten_runtime_rows(arms)
    result_fields = [
        "source_id", "strategy", "threads", "gdal_version", "driver", "workload",
        "variant", "cache_scope", "repeat_count", "p50_ms", "p95_ms", "maximum_ms",
        "wall_ms", "cpu_ms", "peak_rss_bytes", "peak_rss_delta_bytes", "read_bytes",
        "write_bytes", "signature",
    ]
    _write_csv_atomic(run_dir / "results.csv", result_fields, rows)

    combined_samples: list[dict[str, Any]] = []
    for arm_path, arm in zip(arm_paths, arms):
        resource_path = arm_path.with_suffix(".resources.csv")
        if not resource_path.exists():
            continue
        with resource_path.open("r", encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                combined_samples.append(
                    {
                        "source_id": arm["source_id"],
                        "strategy": arm["strategy"],
                        "threads": arm["configuration"]["threads"],
                        **row,
                    }
                )
    _write_csv_atomic(
        run_dir / "resource_samples.csv",
        [
            "source_id", "strategy", "threads", "workload_label", "elapsed_ms",
            "rss_bytes", "system_available_bytes", "read_bytes", "write_bytes",
        ],
        combined_samples,
    )

    lines = [
        "# E3 — JP2OpenJPEG vs JP2Grok",
        "",
        f"- Experiment status: **DONE**",
        f"- Candidate decision: **{results['status'].upper()}**",
        f"- Manifest: `{manifest_id}`",
        f"- Arms: {len(arms)}; threads: {', '.join(map(str, expected_threads))}",
        f"- E3-C correctness: `{correctness['passed']}`",
        f"- E3-P performance/resources: `{performance['passed']}`",
        f"- Recommended Grok threads: `{performance['recommended_threads']}`",
        "",
        "## Correctness checks",
        "",
    ]
    lines.extend(f"- {name}: `{value}`" for name, value in correctness_checks.items())
    lines.extend(
        [
            "",
            "## E3-P candidates",
            "",
            "| Source | Threads | Initial viewport | Steady viewport p95 | Adjacent p95 | Return p95 | Peak ΔRSS | Retained RSS | Gate |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for item in performance["candidates"]:
        latency = item["latency_ms"]
        lines.append(
            f"| {item['source_id']} | {item['threads']} | "
            f"{latency['initial_persistent_viewport']:.2f} ms | "
            f"{latency['viewport_4x4_p95']:.2f} ms | "
            f"{latency['adjacent_viewport_p95']:.2f} ms | "
            f"{latency['warm_return_p95']:.2f} ms | "
            f"{item['peak_rss_delta_bytes'] / 1024**3:.2f} GiB | "
            f"{item['maximum_retained_rss_bytes'] / 1024**2:.1f} MiB | "
            f"{item['passed']} |"
        )
    lines.extend(
        [
            "",
            "The latency gate includes the first persistent-handle viewport and the steady-state p95. "
            "This prevents fast repetitions after a whole-image decoder warm-up from hiding startup latency.",
            "`renderer_fresh_handle_probe` remains a separate, single app-like probe and is reported as "
            "diagnostic evidence; the GeoTile Label PNG cache is not measured.",
            "`best_threads_by_source` is only a diagnostic fastest configuration when no candidate passes; "
            "it is a production recommendation only when `best_threads_by_source_is_recommendation` is true.",
            "The 1-thread A1 arms may be reused byte-for-byte from E2; their provenance is recorded "
            "in `baseline_reuse.json` and they use the same lab runtime and runner configuration.",
            "",
        ]
    )
    _write_text_atomic(run_dir / "summary.md", "\n".join(lines))
    print(f"\nSummary: {run_dir / 'summary.md'}")
    print(f"E3-C: {correctness['passed']}; E3-P: {performance['passed']}")
    return run_dir / "results.json"


def summarize_runtime(args: argparse.Namespace) -> Path:
    if args.stage == "E3":
        return _summarize_runtime_e3(args)
    run_dir = args.run_dir.expanduser().resolve(strict=True)
    arm_paths = sorted((run_dir / "arms").glob("*.json"))
    arms = [json.loads(path.read_text(encoding="utf-8")) for path in arm_paths]
    if not arms:
        raise FileNotFoundError(f"No arm JSON files found in {run_dir / 'arms'}")
    manifests = {arm.get("manifest_id") for arm in arms}
    if len(manifests) != 1:
        raise RuntimeError(f"Arms use different manifests: {manifests}")

    by_pair = {(arm["source_id"], arm["strategy"]): arm for arm in arms}
    source_ids = sorted({arm["source_id"] for arm in arms})
    required_pairs = {(source_id, strategy) for source_id in source_ids for strategy in ("A0", "A1")}
    missing = sorted(required_pairs - set(by_pair)) if args.stage == "E2" else []
    cross_checks: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    if args.stage == "E2":
        for source_id in source_ids:
            if (source_id, "A0") not in by_pair or (source_id, "A1") not in by_pair:
                continue
            baseline = by_pair[(source_id, "A0")]
            candidate = by_pair[(source_id, "A1")]
            baseline_workloads = {_workload_key(item): item for item in baseline["workloads"]}
            candidate_workloads = {_workload_key(item): item for item in candidate["workloads"]}
            for key in sorted(set(baseline_workloads) & set(candidate_workloads)):
                first = baseline_workloads[key]
                second = candidate_workloads[key]
                signature_a = _workload_signature(first)
                signature_b = _workload_signature(second)
                if key[0] == "W0":
                    equal = (
                        first["details"]["metadata_correctness"]["passed"]
                        and second["details"]["metadata_correctness"]["passed"]
                    )
                else:
                    equal = bool(signature_a and signature_b and signature_a == signature_b)
                cross_checks.append(
                    {
                        "source_id": source_id,
                        "workload": key[0],
                        "variant": key[1],
                        "cache_scope": key[2],
                        "a0_signature": signature_a,
                        "a1_signature": signature_b,
                        "passed": equal,
                    }
                )
                stats_a = first["details"].get("stats") or {}
                stats_b = second["details"].get("stats") or {}
                p95_a = stats_a.get("p95_ms")
                p95_b = stats_b.get("p95_ms")
                comparisons.append(
                    {
                        "source_id": source_id,
                        "workload": key[0],
                        "variant": key[1],
                        "cache_scope": key[2],
                        "a0_p95_ms": p95_a,
                        "a1_p95_ms": p95_b,
                        "a1_over_a0_p95": (
                            round(p95_b / p95_a, 6)
                            if p95_a not in (None, 0) and p95_b is not None
                            else None
                        ),
                        "a0_peak_rss_bytes": first["resource"]["peak_rss_bytes"],
                        "a1_peak_rss_bytes": second["resource"]["peak_rss_bytes"],
                    }
                )

    arm_checks = [
        {
            "source_id": arm["source_id"],
            "strategy": arm["strategy"],
            "requested_driver": arm["requested_driver"],
            "passed": arm["correctness"]["passed"],
            "checks": arm["correctness"]["checks"],
        }
        for arm in arms
    ]
    warp_parity: list[dict[str, Any]] = []
    if args.stage == "E2":
        for source_id in source_ids:
            parity_path = run_dir / "warp-parity" / f"comparison_{source_id}.json"
            if parity_path.exists():
                warp_parity.append(json.loads(parity_path.read_text(encoding="utf-8")))
    a0_problem_evidence: list[dict[str, Any]] = []
    for arm in arms:
        if arm["strategy"] != "A0":
            continue
        by_key = {_workload_key(item): item for item in arm["workloads"]}
        w2 = next(item for item in arm["workloads"] if item["workload"] == "W2")
        w4_cold = by_key.get(
            (
                "W4",
                "viewport_4x4_independent_tiles",
                "renderer_fresh_handle_probe",
            )
        )
        w2_p95 = w2["details"]["stats"]["p95_ms"]
        w4_ms = w4_cold["details"]["stats"]["p95_ms"] if w4_cold else None
        reproduced = w2_p95 > 1000.0 or bool(w4_ms and w4_ms > 2000.0)
        a0_problem_evidence.append(
            {
                "source_id": arm["source_id"],
                "w2_p95_ms": w2_p95,
                "w4_renderer_fresh_handle_ms": w4_ms,
                "reproduced": reproduced,
            }
        )

    direct_cross_checks = [
        item for item in cross_checks if item["workload"] in {"W0", "W1", "W2", "W8"}
    ]
    warped_cross_checks = [
        item for item in cross_checks if item["workload"] in {"W3", "W4", "W5", "W6", "W7"}
    ]
    warped_outputs_exact = bool(warped_cross_checks) and all(
        item["passed"] for item in warped_cross_checks
    )
    parity_source_ids = {
        item.get("first", {}).get("source_id")
        for item in warp_parity
        if item.get("passed")
    }
    warped_differences_characterized = warped_outputs_exact or (
        parity_source_ids == set(source_ids)
        and all(item.get("passed") for item in warp_parity)
    )
    correctness_checks = {
        "all_required_arms_present": not missing,
        "all_arm_checks_passed": all(item["passed"] for item in arm_checks),
        "all_cross_arm_direct_pixels_match": bool(direct_cross_checks)
        and all(item["passed"] for item in direct_cross_checks),
        "warped_differences_characterized": warped_differences_characterized,
        "a0_problem_reproduced": bool(a0_problem_evidence)
        and all(item["reproduced"] for item in a0_problem_evidence),
        "resource_metrics_present": all(
            item["resource"].get("peak_rss_bytes") is not None
            and item["resource"].get("cpu_ms") is not None
            and item["resource"].get("io_delta") is not None
            for arm in arms
            for item in arm["workloads"]
        ),
    }
    correctness = {
        "schema_name": "geotile_jp2_fullres_correctness",
        "schema_version": 1,
        "stage": args.stage,
        "generated_at": _utc_now(),
        "checks": correctness_checks,
        "passed": all(correctness_checks.values()),
        "missing_arms": missing,
        "arms": arm_checks,
        "cross_arm": cross_checks,
        "observations": {
            "warped_outputs_exact": warped_outputs_exact,
            "warped_cross_check_count": len(warped_cross_checks),
            "warped_cross_check_failures": sum(
                not item["passed"] for item in warped_cross_checks
            ),
            "interpretation": (
                "E2 compares two GDAL versions using the same JP2OpenJPEG decoder. "
                "Warp drift is characterized separately and is not a decoder tolerance."
            ),
        },
        "warp_parity": warp_parity,
        "a0_problem_evidence": a0_problem_evidence,
    }
    results = {
        "schema_name": "geotile_jp2_fullres_runtime_results",
        "schema_version": 1,
        "stage": args.stage,
        "generated_at": _utc_now(),
        "manifest_id": next(iter(manifests)),
        "status": "passed" if correctness["passed"] else "failed",
        "arms": arms,
        "comparisons": comparisons,
        "warp_parity": warp_parity,
    }
    _write_json_atomic(run_dir / "results.json", results)
    _write_json_atomic(run_dir / "correctness.json", correctness)
    _write_json_atomic(
        run_dir / "environment.json",
        {"stage": args.stage, "arms": [arm["environment"] for arm in arms]},
    )

    rows = _flatten_runtime_rows(arms)
    result_fields = [
        "source_id",
        "strategy",
        "threads",
        "gdal_version",
        "driver",
        "workload",
        "variant",
        "cache_scope",
        "repeat_count",
        "p50_ms",
        "p95_ms",
        "maximum_ms",
        "wall_ms",
        "cpu_ms",
        "peak_rss_bytes",
        "peak_rss_delta_bytes",
        "read_bytes",
        "write_bytes",
        "signature",
    ]
    _write_csv_atomic(run_dir / "results.csv", result_fields, rows)

    combined_samples: list[dict[str, Any]] = []
    for arm_path, arm in zip(arm_paths, arms):
        resource_path = arm_path.with_suffix(".resources.csv")
        if not resource_path.exists():
            continue
        with resource_path.open("r", encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                combined_samples.append(
                    {
                        "source_id": arm["source_id"],
                        "strategy": arm["strategy"],
                        **row,
                    }
                )
    _write_csv_atomic(
        run_dir / "resource_samples.csv",
        [
            "source_id",
            "strategy",
            "workload_label",
            "elapsed_ms",
            "rss_bytes",
            "system_available_bytes",
            "read_bytes",
            "write_bytes",
        ],
        combined_samples,
    )

    summary_lines = [
        f"# {args.stage} — runtime W0-W8",
        "",
        f"- Status: **{'DONE' if correctness['passed'] else 'FAILED'}**",
        f"- Manifest: `{next(iter(manifests))}`",
        f"- Arms: {len(arms)}",
        f"- A0 problem reproduced: `{correctness_checks['a0_problem_reproduced']}`",
        f"- Correctness: `{correctness['passed']}`",
        "",
        "## A0 vs A1",
        "",
        "| Source | Workload | Cache | A0 p95 | A1 p95 | A1/A0 |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    for item in comparisons:
        if item["a0_p95_ms"] is None or item["a1_p95_ms"] is None:
            continue
        ratio = item["a1_over_a0_p95"]
        summary_lines.append(
            f"| {item['source_id']} | {item['workload']} / {item['variant']} | "
            f"{item['cache_scope']} | {item['a0_p95_ms']:.2f} ms | "
            f"{item['a1_p95_ms']:.2f} ms | {ratio:.3f}× |"
        )
    summary_lines.extend(
        [
            "",
            "`renderer_fresh_handle_probe` jest pojedynczą, sekwencyjną sondą ścieżki",
            "zbliżonej do obecnego endpointu aplikacji. Statystyki p50/p95 pochodzą z",
            "powtarzanych pomiarów `handle_warm`; cache PNG aplikacji nie jest mierzony.",
            "",
        ]
    )
    summary_lines = _runtime_summary_lines(
        stage=args.stage,
        manifest_id=next(iter(manifests)),
        arm_count=len(arms),
        comparisons=comparisons,
        warp_parity=warp_parity,
        correctness_checks=correctness_checks,
        correctness_passed=correctness["passed"],
        warped_outputs_exact=warped_outputs_exact,
        warped_differences_characterized=warped_differences_characterized,
    )
    _write_text_atomic(run_dir / "summary.md", "\n".join(summary_lines))
    print(f"\nSummary: {run_dir / 'summary.md'}")
    print(f"Correctness: {correctness['passed']}")
    if not correctness["passed"]:
        raise RuntimeError(f"{args.stage} gate failed: {correctness_checks}")
    return run_dir / "results.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze_parser = subparsers.add_parser("freeze", help="Freeze the deterministic E0 workload")
    freeze_parser.add_argument("--primary", type=Path, required=True)
    freeze_parser.add_argument("--secondary", type=Path)
    freeze_parser.add_argument("--output-dir", type=Path, required=True)
    freeze_parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    freeze_parser.add_argument("--window-size", type=int, default=DEFAULT_WINDOW_SIZE)
    freeze_parser.add_argument("--runtime-label", default="A0-production")
    freeze_parser.set_defaults(handler=freeze)
    verify_parser = subparsers.add_parser("verify", help="Validate an existing E0 manifest")
    verify_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser.set_defaults(handler=verify, window_size=DEFAULT_WINDOW_SIZE)

    run_parser = subparsers.add_parser(
        "run-arm",
        help="Run one explicit decoder/runtime arm of the W0-W8 benchmark",
    )
    run_parser.add_argument("--manifest", type=Path, required=True)
    run_parser.add_argument("--source-id", required=True)
    run_parser.add_argument(
        "--asset",
        type=Path,
        help="Optional alternate raster carrying the source pixels, for example a COG",
    )
    run_parser.add_argument("--stage", default="E2")
    run_parser.add_argument("--strategy", required=True)
    run_parser.add_argument("--driver", required=True)
    run_parser.add_argument("--runtime-label", required=True)
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--threads", type=int, default=1)
    run_parser.add_argument("--gdal-cache-mib", type=int, default=256)
    run_parser.add_argument("--warmups", type=int, default=3)
    run_parser.add_argument("--single-repeats", type=int, default=30)
    run_parser.add_argument("--trace-repeats", type=int, default=10)
    run_parser.add_argument(
        "--skip-navigation-cold-probe",
        action="store_true",
        help="Skip the expensive fresh-handle renderer probes (smoke tests only)",
    )
    run_parser.set_defaults(handler=run_runtime_arm)

    summarize_parser = subparsers.add_parser(
        "summarize",
        help="Aggregate arm JSON files and enforce the E2/E3/E5 benchmark gate",
    )
    summarize_parser.add_argument("--run-dir", type=Path, required=True)
    summarize_parser.add_argument("--stage", default="E2")
    summarize_parser.add_argument(
        "--expected-threads",
        default="1,2,4,8",
        help="Comma-separated E3 thread matrix; ignored by E2",
    )
    summarize_parser.set_defaults(handler=summarize_runtime)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(args, "window_size") and args.window_size < 64:
        raise ValueError("window-size must be at least 64 pixels")
    for name in ("threads", "gdal_cache_mib", "single_repeats", "trace_repeats"):
        if hasattr(args, name) and getattr(args, name) < 1:
            raise ValueError(f"{name.replace('_', '-')} must be positive")
    if hasattr(args, "warmups") and args.warmups < 0:
        raise ValueError("warmups must not be negative")
    args.handler(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
