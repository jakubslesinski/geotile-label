"""Build source-adjacent GDAL overview sidecars without blocking QGIS.

The script supports two execution modes:

1. QGIS desktop Python editor/console. Running the whole file opens a directory
   picker and starts an independent background worker. QGIS remains responsive.
2. QGIS Python environment for reproducible foreground runs::

       "C:\\Program Files\\QGIS 4.2.0\\bin\\python-qgis.bat" ^
         scripts\\qgis_batch_build_overviews.py ^
         --input "C:\\data\\scenes"

Only ``.tif``, ``.tiff`` and ``.jp2`` files are considered. Each raster is
handled by a separate ``gdaladdo`` process. The worker records incremental
JSON/CSV checkpoints, keeps a per-raster log, detects inactive children and
quarantines interrupted ``.ovr`` files instead of silently reusing them.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


SUPPORTED_SUFFIXES = {".tif", ".tiff", ".jp2"}
RESAMPLING_METHODS = (
    "average",
    "average_magphase",
    "cubic",
    "cubicspline",
    "gauss",
    "lanczos",
    "mode",
    "nearest",
)
FAILED_STATUSES = {"error", "timeout", "interrupted"}
DEFAULT_INACTIVITY_TIMEOUT = 15 * 60
DEFAULT_POLL_SECONDS = 2.0
DEFAULT_GDAL_CACHE_MB = 512


class InactivityTimeoutError(TimeoutError):
    """The child process stayed alive without observable work."""


class FileRuntimeTimeoutError(TimeoutError):
    """The child process exceeded the configured wall-clock limit."""


class ActiveBuildError(RuntimeError):
    """Another worker still owns the source-adjacent overview."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def _nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be zero or greater")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build validated source-adjacent .ovr files with isolated gdaladdo processes.",
    )
    parser.add_argument("--input", type=Path, help="Directory containing source rasters.")
    parser.add_argument(
        "--report", type=Path,
        help="JSON report path. A matching CSV report and logs are written next to it.",
    )
    parser.add_argument(
        "--recursive", action=argparse.BooleanOptionalAction, default=True,
        help="Search subdirectories (default: enabled).",
    )
    parser.add_argument(
        "--min-size", type=_positive_int, default=512,
        help="Stop after the coarsest power-of-two level above this size (default: 512).",
    )
    parser.add_argument(
        "--levels",
        help="Explicit comma/space-separated levels, e.g. '2 4 8 16'. Default: automatic.",
    )
    parser.add_argument(
        "--resampling", choices=RESAMPLING_METHODS, default="average",
        help="GDAL overview resampling method (default: average).",
    )
    parser.add_argument(
        "--compression", choices=("DEFLATE", "ZSTD", "LZW", "NONE"), default="DEFLATE",
        help="External GeoTIFF compression (default: DEFLATE).",
    )
    parser.add_argument(
        "--threads", type=_positive_int, default=1,
        help="GDAL threads per raster. Files are processed sequentially (default: 1).",
    )
    parser.add_argument(
        "--gdal-cache-mb", type=_positive_int, default=DEFAULT_GDAL_CACHE_MB,
        help=f"Bound GDAL block cache per process in MiB (default: {DEFAULT_GDAL_CACHE_MB}).",
    )
    parser.add_argument(
        "--inactivity-timeout", type=_nonnegative_float, default=DEFAULT_INACTIVITY_TIMEOUT,
        help=("Terminate a child after this many seconds without CPU, log or .ovr activity; "
              f"0 disables (default: {DEFAULT_INACTIVITY_TIMEOUT})."),
    )
    parser.add_argument(
        "--max-file-seconds", type=_nonnegative_float, default=0,
        help="Optional hard wall-clock limit per raster; 0 disables (default: disabled).",
    )
    parser.add_argument(
        "--poll-seconds", type=_positive_float, default=DEFAULT_POLL_SECONDS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--existing", choices=("skip", "refresh"), default="skip",
        help="Validate and skip, or rebuild an existing .ovr (default: skip).",
    )
    parser.add_argument(
        "--force-external-for-native", action="store_true",
        help="Request .ovr even when the source exposes native levels.",
    )
    parser.add_argument(
        "--max-files", type=_positive_int,
        help="Process at most this many rasters after deterministic path sorting.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Inventory, validate and report decisions without changing sidecars.",
    )
    parser.add_argument(
        "--stop-on-error", action="store_true",
        help="Stop the batch after the first failed raster.",
    )
    return parser.parse_args(argv)


def _pick_input_directory() -> Path | None:
    try:
        from qgis.PyQt.QtWidgets import QFileDialog
        selected = QFileDialog.getExistingDirectory(None, "Wybierz katalog ze scenami")
    except Exception:
        return None
    return Path(selected) if selected else None


def _iter_rasters(root: Path, recursive: bool) -> Iterable[Path]:
    iterator = root.rglob("*") if recursive else root.glob("*")
    return sorted(
        (path for path in iterator if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES),
        key=lambda path: str(path).casefold(),
    )


def _explicit_levels(raw: str | None) -> list[int] | None:
    if not raw:
        return None
    levels = sorted({int(token) for token in raw.replace(",", " ").split()})
    if not levels or any(level <= 1 for level in levels):
        raise ValueError("Overview levels must be integers greater than one")
    return levels


def _automatic_levels(width: int, height: int, min_size: int) -> list[int]:
    levels: list[int] = []
    factor = 2
    while max(width, height) / factor > min_size:
        levels.append(factor)
        factor *= 2
    return levels or [2]


def _overview_factors(dataset: Any, band_index: int = 1) -> list[int]:
    band = dataset.GetRasterBand(band_index)
    factors: list[int] = []
    for index in range(band.GetOverviewCount()):
        overview = band.GetOverview(index)
        if overview is not None and overview.XSize > 0 and overview.YSize > 0:
            factor_x = dataset.RasterXSize / overview.XSize
            factor_y = dataset.RasterYSize / overview.YSize
            factors.append(max(2, int(round((factor_x + factor_y) / 2))))
    return factors


def _dataset_info(path: Path) -> dict[str, Any]:
    from osgeo import gdal
    dataset = gdal.OpenEx(str(path), gdal.OF_RASTER | gdal.OF_READONLY)
    if dataset is None:
        raise RuntimeError(f"GDAL could not open raster: {path}")
    try:
        if dataset.RasterCount <= 0:
            raise RuntimeError(f"Raster has no bands: {path}")
        band = dataset.GetRasterBand(1)
        factors_by_band = [
            _overview_factors(dataset, band_index)
            for band_index in range(1, dataset.RasterCount + 1)
        ]
        return {
            "driver": dataset.GetDriver().ShortName,
            "width": dataset.RasterXSize,
            "height": dataset.RasterYSize,
            "bands": dataset.RasterCount,
            "data_type": gdal.GetDataTypeName(band.DataType),
            "overview_factors": factors_by_band[0],
            "overview_factors_by_band": factors_by_band,
        }
    finally:
        dataset = None


def _predictor(data_type: str) -> str:
    return "3" if data_type in {"Float32", "Float64", "CFloat32", "CFloat64"} else "2"


def _source_snapshot(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _file_snapshot(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns
    except FileNotFoundError:
        return 0, 0


def _atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding=encoding)
    os.replace(temporary, path)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _write_reports(report_path: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    _atomic_write_json(report_path, payload)
    csv_path = report_path.with_suffix(".csv")
    columns = (
        "path", "suffix", "driver", "width", "height", "bands", "data_type", "status",
        "elapsed_seconds", "source_bytes", "overview_bytes_before", "overview_bytes_after",
        "overview_factors_before", "requested_levels", "overview_factors_after",
        "source_unchanged", "process_pid", "process_exit_code", "last_progress_at", "log_path",
        "quarantined_overview", "validation_valid", "error",
    )
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in payload["files"]:
        flat = dict(row)
        for key in ("overview_factors_before", "requested_levels", "overview_factors_after"):
            flat[key] = " ".join(str(value) for value in flat.get(key) or [])
        flat["validation_valid"] = (flat.get("validation") or {}).get("valid")
        writer.writerow(flat)
    _atomic_write_text(csv_path, buffer.getvalue(), encoding="utf-8-sig")
    return report_path, csv_path


def _default_report_path(root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return root / "_geotile_overview_reports" / f"qgis_overviews_{root.name}_{stamp}.json"


def _summarize(files: list[dict[str, Any]], elapsed_seconds: float) -> dict[str, Any]:
    built = [row for row in files if row.get("status") in {"built", "refreshed"}]
    source_bytes = sum(int(row.get("source_bytes") or 0) for row in built)
    overview_bytes = sum(int(row.get("overview_bytes_after") or 0) for row in built)
    status_counts: dict[str, int] = {}
    for row in files:
        status = str(row.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "raster_count": len(files),
        "status_counts": status_counts,
        "built_source_bytes": source_bytes,
        "built_overview_bytes": overview_bytes,
        "overview_to_source_ratio": (overview_bytes / source_bytes) if source_bytes else None,
        "elapsed_seconds": elapsed_seconds,
        "source_mib_per_second": (source_bytes / 2**20 / elapsed_seconds) if elapsed_seconds else None,
        "source_gib_per_hour": (source_bytes / 2**30 * 3600 / elapsed_seconds) if elapsed_seconds else None,
    }


def _environment_info() -> dict[str, Any]:
    from osgeo import gdal
    qgis_version: str | None = None
    try:
        from qgis.core import Qgis
        qgis_version = Qgis.QGIS_VERSION
    except Exception:
        pass
    return {
        "host": socket.gethostname(), "platform": platform.platform(), "python": sys.version,
        "qgis": qgis_version, "gdal": gdal.VersionInfo("--version"),
    }


def _configuration(args: argparse.Namespace, explicit_levels: list[int] | None) -> dict[str, Any]:
    return {
        "recursive": args.recursive, "min_size": args.min_size,
        "explicit_levels": explicit_levels, "resampling": args.resampling,
        "compression": args.compression, "threads_per_raster": args.threads,
        "gdal_cache_mb": args.gdal_cache_mb, "file_parallelism": 1,
        "external_format": "GTiff .ovr", "existing": args.existing,
        "force_external_for_native": args.force_external_for_native,
        "inactivity_timeout_seconds": args.inactivity_timeout,
        "max_file_seconds": args.max_file_seconds, "dry_run": args.dry_run,
    }


def _payload(
    *, root: Path, args: argparse.Namespace, explicit_levels: list[int] | None,
    started_at: str, batch_started: float, environment: dict[str, Any],
    rows: list[dict[str, Any]], state: str, finished_at: str | None = None,
) -> dict[str, Any]:
    elapsed = time.perf_counter() - batch_started
    current = next((row.get("path") for row in rows if row.get("status") == "running"), None)
    return {
        "schema_version": 2, "state": state, "worker_pid": os.getpid(), "started_at": started_at,
        "finished_at": finished_at, "updated_at": _utc_now(), "current_file": current,
        "input_directory": str(root), "configuration": _configuration(args, explicit_levels),
        "environment": environment, "summary": _summarize(rows, elapsed), "files": rows,
    }


def _sample_positions(width: int, height: int, sample_size: int = 32) -> list[tuple[int, int, int, int]]:
    window_width = max(1, min(sample_size, width))
    window_height = max(1, min(sample_size, height))
    xs = sorted({0, max(0, (width - window_width) // 2), max(0, width - window_width)})
    ys = sorted({0, max(0, (height - window_height) // 2), max(0, height - window_height)})
    return [(x, y, window_width, window_height) for y in ys for x in xs]


def _sample_band_signal(band: Any) -> dict[str, Any]:
    """Read deterministic windows and distinguish valid/all-zero/all-nodata content."""
    import numpy as np
    nodata = band.GetNoDataValue()
    reads = valid_pixels = nonzero_pixels = 0
    for xoff, yoff, width, height in _sample_positions(band.XSize, band.YSize):
        values = band.ReadAsArray(xoff, yoff, width, height)
        if values is None:
            raise RuntimeError(f"RasterIO returned no data for window {xoff},{yoff},{width},{height}")
        reads += 1
        valid = np.isfinite(values) if np.issubdtype(values.dtype, np.floating) else np.ones(values.shape, bool)
        if nodata is not None:
            valid &= ~np.isnan(values) if isinstance(nodata, float) and np.isnan(nodata) else values != nodata
        valid_pixels += int(np.count_nonzero(valid))
        if np.any(valid):
            nonzero_pixels += int(np.count_nonzero(values[valid]))
    return {
        "sample_reads": reads, "sample_valid_pixels": valid_pixels,
        "sample_nonzero_pixels": nonzero_pixels,
    }


def _overview_for_factor(dataset: Any, band_index: int, requested_factor: int) -> Any | None:
    band = dataset.GetRasterBand(band_index)
    for index in range(band.GetOverviewCount()):
        overview = band.GetOverview(index)
        if overview is None or overview.XSize <= 0 or overview.YSize <= 0:
            continue
        factor_x = dataset.RasterXSize / overview.XSize
        factor_y = dataset.RasterYSize / overview.YSize
        factor = max(2, int(round((factor_x + factor_y) / 2)))
        if factor == requested_factor:
            return overview
    return None


def _validate_overviews(path: Path, requested_levels: list[int], *, require_external: bool) -> dict[str, Any]:
    """Validate overview structure and readable content, not only IFD headers."""
    from osgeo import gdal
    sidecar = Path(str(path) + ".ovr")
    errors: list[str] = []
    bands_report: list[dict[str, Any]] = []
    factors_by_band: list[list[int]] = []
    if require_external and not sidecar.is_file():
        errors.append(f"Missing external sidecar: {sidecar}")
    try:
        dataset = gdal.OpenEx(str(path), gdal.OF_RASTER | gdal.OF_READONLY)
    except Exception as exc:
        return {
            "valid": False,
            "checked_at": _utc_now(),
            "sidecar_bytes": sidecar.stat().st_size if sidecar.is_file() else 0,
            "errors": [f"Could not reopen raster: {type(exc).__name__}: {exc}"],
        }
    if dataset is None:
        return {"valid": False, "errors": [f"GDAL could not reopen raster: {path}"]}
    try:
        factors_by_band = [
            _overview_factors(dataset, band_index)
            for band_index in range(1, dataset.RasterCount + 1)
        ]
        missing_by_band = {
            str(index): sorted(set(requested_levels) - set(factors))
            for index, factors in enumerate(factors_by_band, start=1)
            if not set(requested_levels).issubset(set(factors))
        }
        if missing_by_band:
            errors.append(f"Missing requested factors: {missing_by_band}")
        for band_index in range(1, dataset.RasterCount + 1):
            source_signal = _sample_band_signal(dataset.GetRasterBand(band_index))
            level_reports: list[dict[str, Any]] = []
            coarsest_factor = max(requested_levels)
            for factor in requested_levels:
                overview = _overview_for_factor(dataset, band_index, factor)
                if overview is None:
                    continue
                try:
                    signal = _sample_band_signal(overview)
                    # A full checksum of every level would read roughly one third
                    # of the source again. The coarsest level is inexpensive and
                    # was sufficient to expose the observed header-only sidecar;
                    # other levels are verified with distributed block reads.
                    checksum = int(overview.Checksum()) if factor == coarsest_factor else None
                    level_reports.append({"factor": factor, "checksum": checksum, **signal})
                    if source_signal["sample_valid_pixels"] > 0 and signal["sample_valid_pixels"] == 0:
                        errors.append(f"Band {band_index}, factor {factor}: no valid overview pixels")
                    if (source_signal["sample_nonzero_pixels"] > 0
                            and signal["sample_nonzero_pixels"] == 0
                            and factor == coarsest_factor and checksum == 0):
                        errors.append(f"Band {band_index}, factor {factor}: overview is unexpectedly empty")
                except Exception as exc:
                    errors.append(f"Band {band_index}, factor {factor}: {type(exc).__name__}: {exc}")
            bands_report.append({"band": band_index, "source_sample": source_signal, "levels": level_reports})
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        dataset = None
    return {
        "valid": not errors, "checked_at": _utc_now(),
        "sidecar_bytes": sidecar.stat().st_size if sidecar.is_file() else 0,
        "overview_factors_by_band": factors_by_band, "bands": bands_report, "errors": errors,
    }


def _marker_path(sidecar: Path) -> Path:
    return sidecar.with_name(sidecar.name + ".geotile-building.json")


def _read_marker(marker: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (FileNotFoundError, OSError, ValueError):
        return None


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _process_cpu_seconds(pid: int) -> float | None:
    try:
        import psutil
        times = psutil.Process(pid).cpu_times()
        return float(times.user + times.system)
    except (ImportError, OSError):
        pass
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        class FileTime(ctypes.Structure):
            _fields_ = (("low", wintypes.DWORD), ("high", wintypes.DWORD))
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = (
            wintypes.HANDLE, ctypes.POINTER(FileTime), ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime), ctypes.POINTER(FileTime),
        )
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            creation, exit_time, kernel, user = FileTime(), FileTime(), FileTime(), FileTime()
            if not kernel32.GetProcessTimes(
                handle, ctypes.byref(creation), ctypes.byref(exit_time),
                ctypes.byref(kernel), ctypes.byref(user),
            ):
                return None
            return (((kernel.high << 32) | kernel.low) + ((user.high << 32) | user.low)) / 10_000_000
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def _quarantine_sidecar(sidecar: Path) -> Path | None:
    if not sidecar.is_file():
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = sidecar.with_name(f"{sidecar.name}.partial-{stamp}")
    sequence = 1
    while destination.exists():
        destination = sidecar.with_name(f"{sidecar.name}.partial-{stamp}-{sequence}")
        sequence += 1
    os.replace(sidecar, destination)
    return destination


def _find_gdaladdo() -> Path:
    discovered = shutil.which("gdaladdo") or shutil.which("gdaladdo.exe")
    if discovered:
        return Path(discovered).resolve()
    candidates: list[Path] = []
    if os.environ.get("OSGEO4W_ROOT"):
        candidates.append(Path(os.environ["OSGEO4W_ROOT"]) / "bin" / "gdaladdo.exe")
    executable = Path(sys.executable).resolve()
    candidates.append(executable.parent / "gdaladdo.exe")
    if len(executable.parents) >= 3:
        candidates.append(executable.parents[2] / "bin" / "gdaladdo.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("gdaladdo executable was not found in the QGIS environment")


def _gdaladdo_command(
    executable: Path, path: Path, levels: list[int], *, resampling: str,
    compression: str, predictor: str, threads: int, cache_mb: int,
) -> list[str]:
    command = [
        str(executable), "-ro", "-r", resampling,
        "--config", "BIGTIFF_OVERVIEW", "IF_SAFER",
        "--config", "GDAL_NUM_THREADS", str(threads),
        "--config", "GDAL_CACHEMAX", str(cache_mb),
    ]
    if compression != "NONE":
        command.extend(("--config", "COMPRESS_OVERVIEW", compression))
        if compression in {"DEFLATE", "ZSTD", "LZW"}:
            command.extend(("--config", "PREDICTOR_OVERVIEW", predictor))
    command.extend((str(path), *(str(level) for level in levels)))
    return command


def _render_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)


def _terminate_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _tail_text(path: Path, max_lines: int = 30) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:])
    except OSError:
        return ""


def _run_gdaladdo(
    command: list[str], *, source: Path, sidecar: Path, marker: Path, log_path: Path,
    row: dict[str, Any], inactivity_timeout: float, max_file_seconds: float,
    poll_seconds: float, checkpoint: Callable[[], None],
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    marker_payload = {
        "schema_version": 1, "state": "starting", "source": str(source),
        "sidecar": str(sidecar), "worker_pid": os.getpid(), "process_pid": None,
        "started_at": _utc_now(), "command": command,
    }
    _atomic_write_json(marker, marker_payload)
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    with log_path.open("w", encoding="utf-8", errors="replace") as log_stream:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=log_stream, stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        row["process_pid"] = process.pid
        row["last_progress_at"] = _utc_now()
        marker_payload.update({
            "state": "running", "process_pid": process.pid,
            "last_progress_at": row["last_progress_at"],
        })
        _atomic_write_json(marker, marker_payload)
        checkpoint()
        started = last_activity = last_checkpoint = time.monotonic()
        last_sidecar = _file_snapshot(sidecar)
        last_log = _file_snapshot(log_path)
        last_cpu = _process_cpu_seconds(process.pid)
        try:
            while True:
                exit_code = process.poll()
                now = time.monotonic()
                current_sidecar = _file_snapshot(sidecar)
                current_log = _file_snapshot(log_path)
                current_cpu = _process_cpu_seconds(process.pid) if exit_code is None else last_cpu
                cpu_advanced = (current_cpu is not None and last_cpu is not None
                                and current_cpu > last_cpu + 0.01)
                if current_sidecar != last_sidecar or current_log != last_log or cpu_advanced:
                    last_activity = now
                    row["last_progress_at"] = _utc_now()
                    marker_payload["last_progress_at"] = row["last_progress_at"]
                    last_sidecar, last_log = current_sidecar, current_log
                if current_cpu is not None:
                    last_cpu = current_cpu
                    row["process_cpu_seconds"] = round(current_cpu, 3)
                row["overview_bytes_after"] = current_sidecar[0]
                if exit_code is not None:
                    row["process_exit_code"] = exit_code
                    break
                if max_file_seconds and now - started > max_file_seconds:
                    _terminate_process(process)
                    row["process_exit_code"] = process.returncode
                    raise FileRuntimeTimeoutError(
                        f"gdaladdo exceeded the {max_file_seconds:.0f} s per-file limit")
                if inactivity_timeout and now - last_activity > inactivity_timeout:
                    _terminate_process(process)
                    row["process_exit_code"] = process.returncode
                    raise InactivityTimeoutError(
                        f"gdaladdo showed no CPU, log or sidecar activity for {inactivity_timeout:.0f} s")
                if now - last_checkpoint >= 30:
                    _atomic_write_json(marker, marker_payload)
                    checkpoint()
                    last_checkpoint = now
                time.sleep(poll_seconds)
        except BaseException:
            _terminate_process(process)
            raise
    if process.returncode != 0:
        tail = _tail_text(log_path)
        detail = f"; log tail:\n{tail}" if tail else ""
        raise RuntimeError(f"gdaladdo exited with code {process.returncode}{detail}")
    return int(process.returncode)


def _safe_log_name(index: int, path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._")[:80] or "raster"
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:10]
    return f"{index:04d}_{stem}_{digest}.gdaladdo.log"


def run(args: argparse.Namespace) -> int:
    from osgeo import gdal
    gdal.UseExceptions()
    root = args.input.expanduser().resolve() if args.input else _pick_input_directory()
    if root is None:
        print("No input directory selected.")
        return 2
    if not root.is_dir():
        raise NotADirectoryError(root)
    rasters = list(_iter_rasters(root, args.recursive))
    if args.max_files is not None:
        rasters = rasters[: args.max_files]
    explicit_levels = _explicit_levels(args.levels)
    report_path = (args.report or _default_report_path(root)).expanduser().resolve()
    logs_directory = report_path.parent / f"{report_path.stem}_logs"
    started_at, batch_started = _utc_now(), time.perf_counter()
    environment = _environment_info()
    rows: list[dict[str, Any]] = []

    def checkpoint(*, state: str = "running", finished_at: str | None = None) -> None:
        _write_reports(report_path, _payload(
            root=root, args=args, explicit_levels=explicit_levels, started_at=started_at,
            batch_started=batch_started, environment=environment, rows=rows,
            state=state, finished_at=finished_at,
        ))

    checkpoint()
    gdaladdo = _find_gdaladdo() if not args.dry_run else None
    print(f"QGIS overview worker: {root}")
    print(f"Rasters selected: {len(rasters)}; report: {report_path}")
    interrupted = False
    for number, path in enumerate(rasters, start=1):
        file_started = time.perf_counter()
        sidecar = Path(str(path) + ".ovr")
        marker = _marker_path(sidecar)
        log_path = logs_directory / _safe_log_name(number, path)
        row: dict[str, Any] = {
            "path": str(path), "suffix": path.suffix.lower(), "status": "running",
            "elapsed_seconds": 0.0, "error": None, "overview_path": str(sidecar),
            "marker_path": str(marker), "log_path": str(log_path), "started_at": _utc_now(),
        }
        rows.append(row)
        checkpoint()
        build_started = False
        try:
            marker_present = marker.is_file()
            stale_marker = _read_marker(marker)
            if stale_marker:
                previous_pid = int(stale_marker.get("process_pid") or 0)
                if previous_pid and _pid_exists(previous_pid):
                    raise ActiveBuildError(
                        f"Another gdaladdo process (PID {previous_pid}) still owns {sidecar}")
            if marker_present:
                row["recovered_stale_marker"] = True
            source_before = _source_snapshot(path)
            before = _dataset_info(path)
            requested_levels = explicit_levels or _automatic_levels(
                before["width"], before["height"], args.min_size)
            row.update(before)
            row.update({
                "source_bytes": source_before[0],
                "overview_bytes_before": sidecar.stat().st_size if sidecar.is_file() else 0,
                "overview_factors_before": before["overview_factors"],
                "requested_levels": requested_levels,
            })
            if sidecar.is_file():
                existing_validation = _validate_overviews(path, requested_levels, require_external=True)
                row["existing_validation"] = existing_validation
                if existing_validation["valid"] and args.existing == "skip":
                    row["validation"] = existing_validation
                    row["status"] = (
                        "would_skip_valid_external" if args.dry_run else "skipped_valid_external")
                    if marker_present and not args.dry_run:
                        marker.unlink(missing_ok=True)
                elif args.dry_run:
                    row["status"] = (
                        "would_refresh" if existing_validation["valid"]
                        else "would_rebuild_invalid_external")
                else:
                    quarantined = _quarantine_sidecar(sidecar)
                    if quarantined:
                        row["quarantined_overview"] = str(quarantined)
                    marker.unlink(missing_ok=True)
            if row["status"] == "running" and not sidecar.is_file():
                without_external = _dataset_info(path)
                native_complete = all(
                    set(requested_levels).issubset(set(factors))
                    for factors in without_external["overview_factors_by_band"])
                if native_complete and not args.force_external_for_native:
                    validation = _validate_overviews(path, requested_levels, require_external=False)
                    row["validation"] = validation
                    if not validation["valid"]:
                        raise RuntimeError("Native overview validation failed: " + "; ".join(validation["errors"]))
                    row["status"] = (
                        "would_skip_valid_native" if args.dry_run else "skipped_valid_native")
                    if marker_present and not args.dry_run:
                        marker.unlink(missing_ok=True)
                elif args.dry_run:
                    row["status"] = "would_build"
                else:
                    action = "refreshed" if row.get("overview_bytes_before") else "built"
                    command = _gdaladdo_command(
                        gdaladdo, path, requested_levels, resampling=args.resampling,
                        compression=args.compression, predictor=_predictor(before["data_type"]),
                        threads=args.threads, cache_mb=args.gdal_cache_mb,
                    )
                    row["command"], row["command_text"] = command, _render_command(command)
                    build_started = True
                    _run_gdaladdo(
                        command, source=path, sidecar=sidecar, marker=marker, log_path=log_path,
                        row=row, inactivity_timeout=args.inactivity_timeout,
                        max_file_seconds=args.max_file_seconds, poll_seconds=args.poll_seconds,
                        checkpoint=checkpoint,
                    )
                    if sidecar.is_file():
                        validation = _validate_overviews(path, requested_levels, require_external=True)
                        row["status"] = action
                    else:
                        validation = _validate_overviews(path, requested_levels, require_external=False)
                        row["status"] = "native_overviews_retained"
                    row["validation"] = validation
                    if not validation["valid"]:
                        raise RuntimeError("Overview validation failed: " + "; ".join(validation["errors"]))
                    marker.unlink(missing_ok=True)
            after = _dataset_info(path)
            source_after = _source_snapshot(path)
            row.update({
                "overview_bytes_after": sidecar.stat().st_size if sidecar.is_file() else 0,
                "overview_factors_after": after["overview_factors"],
                "source_unchanged": source_before == source_after,
            })
            if source_before != source_after:
                raise RuntimeError("Source raster size or mtime changed during overview generation")
        except ActiveBuildError as exc:
            row["status"], row["error"] = "error", f"{type(exc).__name__}: {exc}"
        except (InactivityTimeoutError, FileRuntimeTimeoutError) as exc:
            row["status"], row["error"] = "timeout", f"{type(exc).__name__}: {exc}"
            if build_started:
                quarantined = _quarantine_sidecar(sidecar)
                if quarantined:
                    row["quarantined_overview"] = str(quarantined)
                marker.unlink(missing_ok=True)
        except KeyboardInterrupt:
            row["status"], row["error"] = "interrupted", "KeyboardInterrupt: batch interrupted by user"
            if build_started:
                quarantined = _quarantine_sidecar(sidecar)
                if quarantined:
                    row["quarantined_overview"] = str(quarantined)
                marker.unlink(missing_ok=True)
            interrupted = True
        except Exception as exc:
            row["status"] = "error"
            row["error"] = f"{type(exc).__name__}: {exc}"
            row["traceback"] = traceback.format_exc()
            if build_started:
                quarantined = _quarantine_sidecar(sidecar)
                if quarantined:
                    row["quarantined_overview"] = str(quarantined)
                marker.unlink(missing_ok=True)
        finally:
            row["elapsed_seconds"] = round(time.perf_counter() - file_started, 6)
            row["finished_at"] = _utc_now()
            checkpoint()
            print(f"[{number}/{len(rasters)}] {row['status']}: {path.name} ({row['elapsed_seconds']:.2f} s)")
        if interrupted or (row["status"] in FAILED_STATUSES and args.stop_on_error):
            break
    state = (
        "interrupted"
        if interrupted
        else "completed_with_errors"
        if any(row["status"] in FAILED_STATUSES for row in rows)
        else "completed"
    )
    final_payload = _payload(
        root=root, args=args, explicit_levels=explicit_levels, started_at=started_at,
        batch_started=batch_started, environment=environment, rows=rows,
        state=state, finished_at=_utc_now(),
    )
    json_path, csv_path = _write_reports(report_path, final_payload)
    print(json.dumps(final_payload["summary"], ensure_ascii=False, indent=2))
    print(f"JSON report: {json_path}")
    print(f"CSV report:  {csv_path}")
    if interrupted:
        return 130
    return 1 if any(row["status"] in FAILED_STATUSES for row in rows) else 0


def main(argv: list[str] | None = None) -> int:
    return run(_parse_args(list(sys.argv[1:] if argv is None else argv)))


def _script_path() -> Path:
    compiled_filename = Path(_script_path.__code__.co_filename)
    if compiled_filename.is_file():
        return compiled_filename.resolve()
    explicit = globals().get("__file__")
    if explicit:
        return Path(str(explicit)).resolve()
    return compiled_filename.resolve()


def _find_qgis_python() -> Path:
    candidates: list[Path] = []
    try:
        from qgis.core import QgsApplication
        prefix = Path(QgsApplication.prefixPath()).resolve()
        if len(prefix.parents) >= 2:
            candidates.append(prefix.parents[1] / "bin" / "python.exe")
    except Exception:
        pass
    if os.environ.get("OSGEO4W_ROOT"):
        candidates.append(Path(os.environ["OSGEO4W_ROOT"]) / "bin" / "python.exe")
    discovered = shutil.which("python.exe")
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("QGIS Python executable was not found")


def _launch_background_worker(argv: list[str], report_path: Path) -> int:
    python_executable, script = _find_qgis_python(), _script_path()
    worker_log = report_path.with_suffix(".worker.log")
    worker_log.parent.mkdir(parents=True, exist_ok=True)
    command = [str(python_executable), "-u", str(script), *argv]
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    with worker_log.open("w", encoding="utf-8", errors="replace") as stream:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            cwd=str(script.parent.parent), creationflags=creationflags, close_fds=True,
        )
    print("Overview build started in a separate process.")
    print(f"Worker PID: {process.pid}")
    print(f"Progress report: {report_path}")
    print(f"Worker log: {worker_log}")
    print("QGIS stays usable. Do not start a second batch for the same files.")
    return process.pid


def run_from_qgis_console(
    input_directory: str | Path | None = None, *, report_path: str | Path | None = None,
    recursive: bool = True, min_size: int = 512, levels: str | Iterable[int] | None = None,
    resampling: str = "average", compression: str = "DEFLATE", threads: int = 1,
    gdal_cache_mb: int = DEFAULT_GDAL_CACHE_MB,
    inactivity_timeout: float = DEFAULT_INACTIVITY_TIMEOUT, max_file_seconds: float = 0,
    existing: str = "skip", force_external_for_native: bool = False,
    max_files: int | None = None, dry_run: bool = False, stop_on_error: bool = False,
    background: bool = True,
) -> int:
    """Launch a responsive background worker from QGIS, or run in foreground."""
    selected = Path(input_directory) if input_directory is not None else _pick_input_directory()
    if selected is None:
        print("No input directory selected.")
        return 2
    selected = selected.expanduser().resolve()
    if not selected.is_dir():
        raise NotADirectoryError(selected)
    resolved_report = (
        Path(report_path).expanduser().resolve() if report_path is not None
        else _default_report_path(selected)
    )
    argv: list[str] = ["--input", str(selected), "--report", str(resolved_report)]
    argv.append("--recursive" if recursive else "--no-recursive")
    argv.extend(("--min-size", str(min_size)))
    if levels is not None:
        rendered = levels if isinstance(levels, str) else " ".join(str(level) for level in levels)
        argv.extend(("--levels", rendered))
    argv.extend(("--resampling", resampling.lower(), "--compression", compression.upper()))
    argv.extend(("--threads", str(threads), "--gdal-cache-mb", str(gdal_cache_mb)))
    argv.extend(("--inactivity-timeout", str(inactivity_timeout)))
    argv.extend(("--max-file-seconds", str(max_file_seconds), "--existing", existing.lower()))
    if force_external_for_native:
        argv.append("--force-external-for-native")
    if max_files is not None:
        argv.extend(("--max-files", str(max_files)))
    if dry_run:
        argv.append("--dry-run")
    if stop_on_error:
        argv.append("--stop-on-error")
    if background and _inside_qgis_desktop():
        return _launch_background_worker(argv, resolved_report)
    return main(argv)


def _inside_qgis_desktop() -> bool:
    try:
        from qgis.utils import iface
        return iface is not None
    except Exception:
        return False


if __name__ in {"__main__", "__console__"}:
    if _inside_qgis_desktop():
        run_from_qgis_console(background=True)
    elif __name__ == "__main__":
        raise SystemExit(main())
