"""Laboratory probe for JP2Grok's documented GDALDatasetAdviseRead path.

This is not production code.  GDAL 3.13.3's Python wrapper in the isolated E1
environment raises a SWIG TypeError for Dataset.AdviseRead, so the probe calls
the public GDAL C API through ctypes and records that fact explicitly.
"""

from __future__ import annotations

import argparse
import ctypes
import json
from pathlib import Path
import sys
import time

try:
    from .benchmark_jp2_fullres_strategies import (
        _RuntimeResourceTrace,
        _open_explicit_gdal,
        _pixel_checksum,
        _read_direct_window,
        _reference_map,
        _runtime_environment,
        _utc_now,
        _write_json_atomic,
        validate_manifest,
    )
except ImportError:
    from benchmark_jp2_fullres_strategies import (
        _RuntimeResourceTrace,
        _open_explicit_gdal,
        _pixel_checksum,
        _read_direct_window,
        _reference_map,
        _runtime_environment,
        _utc_now,
        _write_json_atomic,
        validate_manifest,
    )


def _gdal_library() -> Path:
    candidates = [
        Path(sys.prefix) / "Library" / "bin" / "gdal.dll",
        Path(sys.prefix) / "bin" / "gdal.dll",
    ]
    return next((path for path in candidates if path.is_file()), candidates[0])


def _c_api_advise(dataset, window: dict[str, int]) -> tuple[int, float, str | None]:
    from osgeo import gdal

    python_binding_error = None
    try:
        dataset.AdviseRead(
            int(window["x"]),
            int(window["y"]),
            int(window["width"]),
            int(window["height"]),
            int(window["width"]),
            int(window["height"]),
            gdal.GDT_UInt16,
            [1],
            [],
        )
    except TypeError as error:
        python_binding_error = f"{type(error).__name__}: {error}"

    library = ctypes.CDLL(str(_gdal_library()))
    advise = library.GDALDatasetAdviseRead
    advise.restype = ctypes.c_int
    advise.argtypes = (
        [ctypes.c_void_p]
        + [ctypes.c_int] * 8
        + [ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_char_p)]
    )
    band_map = (ctypes.c_int * 1)(1)
    started = time.perf_counter()
    status = advise(
        ctypes.c_void_p(int(dataset.this)),
        int(window["x"]),
        int(window["y"]),
        int(window["width"]),
        int(window["height"]),
        int(window["width"]),
        int(window["height"]),
        int(dataset.GetRasterBand(1).DataType),
        1,
        band_map,
        None,
    )
    return status, (time.perf_counter() - started) * 1000.0, python_binding_error


def run(args: argparse.Namespace) -> Path:
    from osgeo import gdal

    gdal.UseExceptions()
    gdal.SetCacheMax(args.gdal_cache_mib * 1024 * 1024)
    gdal.SetConfigOption("GDAL_NUM_THREADS", str(args.threads))
    manifest_path = args.manifest.resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if validate_manifest(manifest)["status"] != "passed":
        raise RuntimeError("Invalid E0 manifest")
    entry = next(item for item in manifest["sources"] if item["source_id"] == args.source_id)
    source = Path(entry["path"]).resolve(strict=True)
    window = entry["windows_1x"][0]
    expected = _reference_map(entry)[window["id"]]["sha256"]
    dataset = _open_explicit_gdal(source, "JP2Grok")
    block_size = list(dataset.GetRasterBand(1).GetBlockSize())
    advise_status = None
    advise_ms = None
    python_binding_error = None
    with _RuntimeResourceTrace(f"grok_advise_{args.mode}") as trace:
        if args.mode == "advise":
            advise_status, advise_ms, python_binding_error = _c_api_advise(dataset, window)
        started = time.perf_counter()
        array = _read_direct_window(dataset, window)
        read_ms = (time.perf_counter() - started) * 1000.0
        digest = _pixel_checksum(array)
    resource = trace.summary()
    dataset = None
    record = {
        "schema_name": "geotile_jp2_grok_advise_probe",
        "schema_version": 1,
        "generated_at": _utc_now(),
        "manifest_id": manifest["manifest_id"],
        "source_id": args.source_id,
        "source_path": str(source),
        "mode": args.mode,
        "threads": args.threads,
        "gdal_cache_mib": args.gdal_cache_mib,
        "runtime": _runtime_environment(
            runtime_label="E3-JP2Grok-AdviseRead-probe",
            strategy="B1-advise-probe",
            driver="JP2Grok",
            asset=source,
            threads=args.threads,
        ),
        "window": window,
        "driver_block_size": block_size,
        "python_binding_error": python_binding_error,
        "c_api_advise_status": advise_status,
        "advise_ms": advise_ms,
        "read_ms": read_ms,
        "expected_sha256": expected,
        "actual_sha256": digest,
        "pixels_exact": digest == expected,
        "resource": resource,
    }
    output = args.output.resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(output, record)
    print(json.dumps(record, indent=2))
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--mode", choices=("control", "advise"), required=True)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--gdal-cache-mib", type=int, default=256)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.threads < 1 or args.gdal_cache_mib < 1:
        raise ValueError("threads and gdal-cache-mib must be positive")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
