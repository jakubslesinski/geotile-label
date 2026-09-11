"""Capture and compare GDAL WarpedVRT pixels across isolated runtimes.

This diagnostic complements E2/E3.  Raw 1x decoder parity is enforced by the
main W0-W8 runner; this script characterizes numerical differences introduced
by different GDAL warp/resampling implementations on the exact same map tiles.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable

import numpy as np

try:
    from .benchmark_jp2_fullres_strategies import (
        _build_warped_vrt,
        _gdal_raster_metadata,
        _open_explicit_gdal,
        _read_warped_bounds,
        _read_warped_tile,
        _viewport_bounds,
        _write_json_atomic,
        validate_manifest,
    )
except ImportError:  # Direct script execution.
    from benchmark_jp2_fullres_strategies import (
        _build_warped_vrt,
        _gdal_raster_metadata,
        _open_explicit_gdal,
        _read_warped_bounds,
        _read_warped_tile,
        _viewport_bounds,
        _write_json_atomic,
        validate_manifest,
    )


def _array_checksum(array: np.ndarray) -> str:
    canonical = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(str(canonical.dtype).encode("ascii"))
    digest.update(json.dumps(list(canonical.shape), separators=(",", ":")).encode("ascii"))
    digest.update(canonical.tobytes(order="C"))
    return digest.hexdigest()


def _boundary_tiles(vrt: Any, zoom: int) -> tuple[list[dict[str, int]], list[dict[str, int]]]:
    """Return deterministic tiles touching and surrounding the warped footprint.

    The main W3-W5 trace is deliberately internal to the scene.  E3 additionally
    needs the four footprint corners and four requests straddling/outside the
    footprint so decoder differences cannot hide in alpha/nodata handling.
    """
    transform = vrt.GetGeoTransform()
    xmin = transform[0]
    ymax = transform[3]
    xmax = xmin + transform[1] * vrt.RasterXSize
    ymin = ymax + transform[5] * vrt.RasterYSize
    span = 2.0 * 20037508.342789244 / (2**zoom)
    epsilon = span * 1e-9
    left = math.floor((xmin + 20037508.342789244) / span)
    right = math.floor((xmax + 20037508.342789244 - epsilon) / span)
    top = math.floor((20037508.342789244 - ymax) / span)
    bottom = math.floor((20037508.342789244 - ymin - epsilon) / span)
    middle_x = (left + right) // 2
    middle_y = (top + bottom) // 2
    edges = [
        {"z": zoom, "x": left, "y": top},
        {"z": zoom, "x": right, "y": top},
        {"z": zoom, "x": left, "y": bottom},
        {"z": zoom, "x": right, "y": bottom},
    ]
    outside = [
        {"z": zoom, "x": left - 1, "y": middle_y},
        {"z": zoom, "x": right + 1, "y": middle_y},
        {"z": zoom, "x": middle_x, "y": top - 1},
        {"z": zoom, "x": middle_x, "y": bottom + 1},
    ]
    return edges, outside


def _read_source_edge_masks(dataset: Any, windows: list[dict[str, Any]]) -> np.ndarray:
    mask = dataset.GetRasterBand(1).GetMaskBand()
    arrays = []
    for window in windows:
        array = mask.ReadAsArray(
            int(window["x"]),
            int(window["y"]),
            int(window["width"]),
            int(window["height"]),
        )
        if array is None:
            raise RuntimeError("Could not read a source edge mask")
        arrays.append(np.asarray(array))
    return np.stack(arrays, axis=0)


def capture(args: argparse.Namespace) -> Path:
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
    output = args.output.expanduser().resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    source_before = source.stat()
    steps = entry["webmercator_trace"]["native_view"]["steps"]
    viewport_tiles = steps[0]["tiles"]
    single_tile = viewport_tiles[5]
    edge_candidates = [
        window for window in entry["windows_1x"] if window.get("category") == "scene_edge"
    ]
    edge_windows = [
        next((window for window in edge_candidates if window.get("edge") == side), None)
        for side in ("top", "right", "bottom", "left")
    ]
    edge_windows = [window for window in edge_windows if window is not None]
    if len(edge_windows) != 4:
        raise RuntimeError("E3 boundary capture requires four frozen source edge windows")

    dataset = _open_explicit_gdal(source, args.driver)
    vrt = _build_warped_vrt(dataset)
    try:
        w3 = np.asarray(_read_warped_tile(vrt, single_tile))
        w4 = np.stack(
            [np.asarray(_read_warped_tile(vrt, tile)) for tile in viewport_tiles],
            axis=0,
        )
        w5 = np.asarray(
            _read_warped_bounds(vrt, _viewport_bounds(viewport_tiles), 1024, 1024)
        )
        edge_tiles, outside_tiles = _boundary_tiles(vrt, int(single_tile["z"]))
        warped_edges = np.stack(
            [np.asarray(_read_warped_tile(vrt, tile)) for tile in edge_tiles], axis=0
        )
        warped_outside = np.stack(
            [np.asarray(_read_warped_tile(vrt, tile)) for tile in outside_tiles], axis=0
        )
        source_edge_masks = _read_source_edge_masks(dataset, edge_windows)
        vrt_metadata = _gdal_raster_metadata(vrt)
        source_metadata = _gdal_raster_metadata(dataset)
        actual_driver = dataset.GetDriver().ShortName
    finally:
        vrt = None
        dataset = None

    temporary = output.with_suffix(output.suffix + ".partial")
    with temporary.open("wb") as stream:
        np.savez_compressed(
            stream,
            w3=w3,
            w4=w4,
            w5=w5,
            warped_edges=warped_edges,
            warped_outside=warped_outside,
            source_edge_masks=source_edge_masks,
        )
    os.replace(temporary, output)
    source_after = source.stat()
    metadata = {
        "schema_name": "geotile_jp2_warp_capture",
        "schema_version": 2,
        "manifest_id": manifest["manifest_id"],
        "source_id": args.source_id,
        "runtime_label": args.runtime_label,
        "gdal_version": gdal.VersionInfo("--version"),
        "requested_driver": args.driver,
        "actual_driver": actual_driver,
        "threads": args.threads,
        "gdal_cache_mib": args.gdal_cache_mib,
        "single_tile": single_tile,
        "viewport_tiles": viewport_tiles,
        "edge_tiles": edge_tiles,
        "outside_tiles": outside_tiles,
        "source_edge_window_ids": [window["id"] for window in edge_windows],
        "source_raster": source_metadata,
        "warped_vrt": vrt_metadata,
        "arrays": {
            name: {
                "shape": list(array.shape),
                "dtype": str(array.dtype),
                "sha256": _array_checksum(array),
            }
            for name, array in (
                ("w3", w3),
                ("w4", w4),
                ("w5", w5),
                ("warped_edges", warped_edges),
                ("warped_outside", warped_outside),
                ("source_edge_masks", source_edge_masks),
            )
        },
        "source_unchanged": (
            source_before.st_size == source_after.st_size
            and source_before.st_mtime_ns == source_after.st_mtime_ns
        ),
        "capture_path": str(output),
    }
    _write_json_atomic(output.with_suffix(".json"), metadata)
    print(output)
    return output


def _array_difference(first: np.ndarray, second: np.ndarray) -> dict[str, Any]:
    if first.shape != second.shape:
        return {
            "passed": False,
            "shape_equal": False,
            "first_shape": list(first.shape),
            "second_shape": list(second.shape),
        }
    difference = np.abs(first.astype(np.int64) - second.astype(np.int64))
    changed = difference != 0
    changed_count = int(np.count_nonzero(changed))
    total = int(difference.size)
    percentiles = np.percentile(difference, [50, 95, 99, 99.9, 100])
    per_band: list[dict[str, Any]] = []
    band_axis = 1 if first.ndim == 4 else 0
    band_count = first.shape[band_axis]
    for band_index in range(band_count):
        first_band = np.take(first, band_index, axis=band_axis).astype(np.float64)
        second_band = np.take(second, band_index, axis=band_axis).astype(np.float64)
        signed_difference = first_band - second_band
        band_difference = np.abs(signed_difference)
        band_changed = int(np.count_nonzero(band_difference))
        signal_min = min(float(first_band.min()), float(second_band.min()))
        signal_max = max(float(first_band.max()), float(second_band.max()))
        signal_span = signal_max - signal_min
        rmse = float(np.sqrt(np.mean(signed_difference * signed_difference)))
        if float(first_band.std()) == 0.0 or float(second_band.std()) == 0.0:
            correlation = 1.0 if np.array_equal(first_band, second_band) else None
        else:
            correlation = float(
                np.corrcoef(first_band.reshape(-1), second_band.reshape(-1))[0, 1]
            )
        per_band.append(
            {
                "band": band_index + 1,
                "different_pixels": band_changed,
                "different_fraction": band_changed / int(band_difference.size),
                "maximum_absolute_difference": int(band_difference.max(initial=0)),
                "mean_absolute_difference": float(band_difference.mean()),
                "signal_min": signal_min,
                "signal_max": signal_max,
                "signal_span": signal_span,
                "rmse": rmse,
                "normalized_rmse_by_signal_span": (
                    rmse / signal_span if signal_span else 0.0 if rmse == 0.0 else None
                ),
                "psnr_by_signal_span_db": (
                    20.0 * float(np.log10(signal_span / rmse))
                    if signal_span and rmse
                    else None
                ),
                "correlation": correlation,
            }
        )
    return {
        "passed": True,
        "shape_equal": True,
        "dtype_equal": first.dtype == second.dtype,
        "first_dtype": str(first.dtype),
        "second_dtype": str(second.dtype),
        "pixel_values": total,
        "different_values": changed_count,
        "different_fraction": changed_count / total,
        "maximum_absolute_difference": int(difference.max(initial=0)),
        "mean_absolute_difference": float(difference.mean()),
        "absolute_difference_percentiles": {
            "p50": float(percentiles[0]),
            "p95": float(percentiles[1]),
            "p99": float(percentiles[2]),
            "p99_9": float(percentiles[3]),
            "maximum": float(percentiles[4]),
        },
        "per_band": per_band,
    }


def _semantic_bands(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Strip driver-specific physical block layout from correctness metadata."""
    keys = (
        "index",
        "dtype",
        "color_interpretation",
        "overview_sizes",
        "nodata",
        "mask_flags",
    )
    return [{key: band.get(key) for key in keys} for band in metadata.get("bands", [])]


def compare(args: argparse.Namespace) -> Path:
    first_path = args.first.expanduser().resolve(strict=True)
    second_path = args.second.expanduser().resolve(strict=True)
    output = args.output.expanduser().resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    first_metadata = json.loads(first_path.with_suffix(".json").read_text(encoding="utf-8"))
    second_metadata = json.loads(second_path.with_suffix(".json").read_text(encoding="utf-8"))
    with np.load(first_path, allow_pickle=False) as first, np.load(
        second_path, allow_pickle=False
    ) as second:
        names = sorted(set(first.files) | set(second.files))
        comparisons = {
            name: _array_difference(first[name], second[name])
            if name in first.files and name in second.files
            else {"passed": False, "missing": True}
            for name in names
        }
    same_driver = first_metadata["requested_driver"] == second_metadata["requested_driver"]
    decoder_pair = {
        first_metadata["requested_driver"], second_metadata["requested_driver"]
    } == {"JP2OpenJPEG", "JP2Grok"}
    same_gdal = first_metadata["gdal_version"] == second_metadata["gdal_version"]
    first_source = first_metadata.get("source_raster", {})
    second_source = second_metadata.get("source_raster", {})
    first_warped = first_metadata["warped_vrt"]
    second_warped = second_metadata["warped_vrt"]
    checks = {
        "same_manifest": first_metadata["manifest_id"] == second_metadata["manifest_id"],
        "same_source": first_metadata["source_id"] == second_metadata["source_id"],
        "driver_contract": same_driver if args.stage == "E2" else decoder_pair,
        "same_gdal_runtime": True if args.stage == "E2" else same_gdal,
        "source_metadata_equal": (
            first_source.get("width") == second_source.get("width")
            and first_source.get("height") == second_source.get("height")
            and first_source.get("band_count") == second_source.get("band_count")
            and first_source.get("geotransform_gdal")
            == second_source.get("geotransform_gdal")
            and first_source.get("crs_wkt") == second_source.get("crs_wkt")
            and _semantic_bands(first_source) == _semantic_bands(second_source)
        ),
        "same_grid": (
            first_warped["width"] == second_warped["width"]
            and first_warped["height"] == second_warped["height"]
            and first_warped["geotransform_gdal"] == second_warped["geotransform_gdal"]
            and first_warped.get("crs_wkt") == second_warped.get("crs_wkt")
            and first_warped.get("bands") == second_warped.get("bands")
        ),
        "all_shapes_equal": all(item.get("shape_equal", False) for item in comparisons.values()),
        "sources_unchanged": (
            first_metadata["source_unchanged"] and second_metadata["source_unchanged"]
        ),
    }
    alpha_bands = {
        band["index"]
        for band in first_metadata["warped_vrt"]["bands"]
        if band["color_interpretation"].casefold() == "alpha"
    }
    alpha_exact = True
    data_within_e2_tolerance = True
    exact_pixels = True
    for comparison in comparisons.values():
        exact_pixels = exact_pixels and comparison.get("different_values") == 0
        for band in comparison.get("per_band", []):
            if band["band"] in alpha_bands:
                alpha_exact = alpha_exact and band["different_pixels"] == 0
                continue
            data_within_e2_tolerance = data_within_e2_tolerance and (
                band["normalized_rmse_by_signal_span"] is not None
                and band["normalized_rmse_by_signal_span"] <= 0.005
                and band["psnr_by_signal_span_db"] is not None
                and band["psnr_by_signal_span_db"] >= 45.0
                and band["correlation"] is not None
                and band["correlation"] >= 0.999
            )
    checks["alpha_exact"] = alpha_exact
    if args.stage == "E2":
        checks["data_within_e2_characterization_tolerance"] = data_within_e2_tolerance
    else:
        checks["all_decoder_outputs_exact"] = exact_pixels
    result = {
        "schema_name": "geotile_jp2_warp_parity",
        "schema_version": 2,
        "stage": args.stage,
        "first": first_metadata,
        "second": second_metadata,
        "checks": checks,
        "passed": all(checks.values()),
        "exact_pixels": exact_pixels,
        "tolerance": (
            {
                "purpose": "Characterize GDAL-version warp drift in E2; not a decoder parity tolerance",
                "maximum_normalized_rmse_by_signal_span": 0.005,
                "minimum_psnr_by_signal_span_db": 45.0,
                "minimum_correlation": 0.999,
                "alpha_must_be_exact": True,
            }
            if args.stage == "E2"
            else {
                "purpose": "E3 compares decoders on one GDAL runtime; all captured values must be bit exact",
                "maximum_absolute_difference": 0,
                "alpha_and_masks_must_be_exact": True,
            }
        ),
        "comparisons": comparisons,
        "observations": {
            "source_block_layout_equal": [
                band.get("block_size") for band in first_source.get("bands", [])
            ]
            == [band.get("block_size") for band in second_source.get("bands", [])],
            "first_source_block_layout": [
                band.get("block_size") for band in first_source.get("bands", [])
            ],
            "second_source_block_layout": [
                band.get("block_size") for band in second_source.get("bands", [])
            ],
            "interpretation": (
                "Physical block layout is driver-specific and is recorded as a performance "
                "characteristic; it is not raster semantic correctness."
            ),
        },
    }
    _write_json_atomic(output, result)
    print(json.dumps({"passed": result["passed"], "comparisons": comparisons}, indent=2))
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture_parser = commands.add_parser("capture")
    capture_parser.add_argument("--manifest", type=Path, required=True)
    capture_parser.add_argument("--source-id", required=True)
    capture_parser.add_argument("--driver", default="JP2OpenJPEG")
    capture_parser.add_argument("--runtime-label", required=True)
    capture_parser.add_argument("--output", type=Path, required=True)
    capture_parser.add_argument("--threads", type=int, default=1)
    capture_parser.add_argument("--gdal-cache-mib", type=int, default=256)
    capture_parser.set_defaults(handler=capture)
    compare_parser = commands.add_parser("compare")
    compare_parser.add_argument("--first", type=Path, required=True)
    compare_parser.add_argument("--second", type=Path, required=True)
    compare_parser.add_argument("--output", type=Path, required=True)
    compare_parser.add_argument("--stage", choices=("E2", "E3"), default="E2")
    compare_parser.set_defaults(handler=compare)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(args, "threads") and args.threads < 1:
        raise ValueError("threads must be positive")
    if hasattr(args, "gdal_cache_mib") and args.gdal_cache_mib < 1:
        raise ValueError("gdal-cache-mib must be positive")
    args.handler(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
