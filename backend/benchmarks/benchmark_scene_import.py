"""Read-only P1.7 benchmark for scene metadata and display-overview construction.

The benchmark never writes next to a source raster.  Overview VRT/OVR artifacts are
built in a dedicated temporary directory and removed unless ``--keep-artifacts`` is
requested.  Multiple profiles should use balanced, disjoint groups to reduce the
influence of the operating-system cache::

    --group-count 4 --group-index 0
    --group-count 4 --group-index 1

Run this script with the packed GeoTile Label runtime when benchmarking overviews,
because the ordinary development interpreter may not include ``osgeo.gdal``.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import math
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterable

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.common import add_common_arguments, configure_environment, output_path


OPERATION = "scene_import_p1_7"
DEFAULT_EXTENSIONS = (".tif", ".tiff", ".jp2")


def discover_scene_paths(
    explicit: Iterable[str | Path],
    root: str | Path | None,
    *,
    extensions: Iterable[str] = DEFAULT_EXTENSIONS,
) -> list[Path]:
    """Return deterministic, unique source rasters without opening them."""
    accepted = {
        value.casefold() if str(value).startswith(".") else f".{str(value).casefold()}"
        for value in extensions
    }
    candidates: list[Path] = []
    for value in explicit:
        path = Path(value).expanduser().resolve(strict=False)
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.suffix.casefold() not in accepted:
            raise ValueError(f"Unsupported scene extension: {path}")
        candidates.append(path)
    if root:
        directory = Path(root).expanduser().resolve(strict=False)
        if not directory.is_dir():
            raise NotADirectoryError(directory)
        candidates.extend(
            path.resolve(strict=False)
            for path in directory.rglob("*")
            if path.is_file()
            and path.suffix.casefold() in accepted
            and not any(part.startswith(".") for part in path.relative_to(directory).parts)
        )
    return sorted(set(candidates), key=lambda path: str(path).casefold())


def balanced_scene_groups(paths: Iterable[Path], group_count: int) -> list[list[Path]]:
    """Greedy size-balanced, deterministic partitions for disjoint cold-ish runs."""
    if group_count < 1:
        raise ValueError("group_count must be at least 1")
    groups: list[list[Path]] = [[] for _ in range(group_count)]
    totals = [0] * group_count
    ranked = sorted(
        paths,
        key=lambda path: (-path.stat().st_size, str(path).casefold()),
    )
    for path in ranked:
        target = min(range(group_count), key=lambda index: (totals[index], index))
        groups[target].append(path)
        totals[target] += path.stat().st_size
    for group in groups:
        group.sort(key=lambda path: str(path).casefold())
    return groups


def overview_factors(
    width: int,
    height: int,
    *,
    first_factor: int = 2,
    min_overview_size: int = 512,
) -> list[int]:
    if first_factor < 2 or first_factor & (first_factor - 1):
        raise ValueError("first_factor must be a power of two >= 2")
    if min_overview_size < 1:
        raise ValueError("min_overview_size must be positive")
    factors: list[int] = []
    factor = first_factor
    while max(width, height) / factor > min_overview_size:
        factors.append(factor)
        factor *= 2
    return factors or [first_factor]


def _io_snapshot() -> dict[str, int | None]:
    try:
        import psutil  # type: ignore

        counters = psutil.Process().io_counters()
        return {
            "read_bytes": int(counters.read_bytes),
            "write_bytes": int(counters.write_bytes),
        }
    except Exception:
        return {"read_bytes": None, "write_bytes": None}


def _peak_rss_bytes() -> int | None:
    try:
        import psutil  # type: ignore

        info = psutil.Process().memory_info()
        peak = getattr(info, "peak_wset", None)
        return int(peak if peak is not None else info.rss)
    except Exception:
        return None


def _delta(after: int | None, before: int | None) -> int | None:
    return None if after is None or before is None else max(0, after - before)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


class ProcessTreeResourceSampler:
    """Low-overhead aggregate RSS/CPU sampler for benchmark resource gates."""

    def __init__(self, interval_seconds: float = 0.5) -> None:
        if interval_seconds <= 0:
            raise ValueError("resource sample interval must be positive")
        self.interval_seconds = float(interval_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._samples: list[dict[str, float | int]] = []
        self._error: str | None = None
        self._psutil = None
        self._root = None
        self._last_sample_at: float | None = None
        self._last_cpu_by_pid: dict[int, float] = {}
        self._observed_cpu_seconds = 0.0
        self._initial_swap_used_bytes: int | None = None

    def __enter__(self) -> "ProcessTreeResourceSampler":
        try:
            import psutil  # type: ignore

            self._psutil = psutil
            self._root = psutil.Process(os.getpid())
            root_cpu = self._root.cpu_times()
            self._last_cpu_by_pid[self._root.pid] = float(root_cpu.user + root_cpu.system)
            self._last_sample_at = time.perf_counter()
            self._initial_swap_used_bytes = int(psutil.swap_memory().used)
            psutil.cpu_percent(interval=None)
            self._sample()
            self._thread = threading.Thread(
                target=self._run,
                name="scene-import-resource-sampler",
                daemon=True,
            )
            self._thread.start()
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self.interval_seconds * 4))
        if self._psutil is not None and self._root is not None:
            self._sample()
        return False

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._sample()

    def _sample(self) -> None:
        psutil = self._psutil
        root = self._root
        if psutil is None or root is None:
            return
        try:
            processes = [root, *root.children(recursive=True)]
            rss_bytes = 0
            pagefile_bytes = 0
            cpu_by_pid: dict[int, float] = {}
            live_processes = 0
            for process in processes:
                try:
                    memory = process.memory_info()
                    cpu = process.cpu_times()
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                live_processes += 1
                rss_bytes += int(memory.rss)
                pagefile_bytes += int(getattr(memory, "pagefile", 0) or 0)
                cpu_by_pid[int(process.pid)] = float(cpu.user + cpu.system)

            now = time.perf_counter()
            elapsed = max(1e-9, now - (self._last_sample_at or now))
            cpu_delta = 0.0
            for pid, current in cpu_by_pid.items():
                previous = self._last_cpu_by_pid.get(pid, 0.0)
                cpu_delta += max(0.0, current - previous)
            self._observed_cpu_seconds += cpu_delta
            self._last_cpu_by_pid.update(cpu_by_pid)
            self._last_sample_at = now

            virtual_memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
            self._samples.append(
                {
                    "aggregate_rss_bytes": rss_bytes,
                    "aggregate_pagefile_bytes": pagefile_bytes,
                    "available_memory_bytes": int(virtual_memory.available),
                    "system_memory_percent": float(virtual_memory.percent),
                    "system_swap_used_bytes": int(swap.used),
                    "system_cpu_percent": float(psutil.cpu_percent(interval=None)),
                    "tree_equivalent_cores": cpu_delta / elapsed,
                    "process_count": live_processes,
                    "child_count": max(0, live_processes - 1),
                }
            )
        except Exception as exc:
            if self._error is None:
                self._error = f"{type(exc).__name__}: {exc}"

    def summary(self) -> dict[str, Any]:
        if not self._samples:
            return {
                "status": "unavailable",
                "sample_interval_seconds": self.interval_seconds,
                "sample_count": 0,
                "error": self._error,
            }

        def values(name: str) -> list[float]:
            return [float(sample[name]) for sample in self._samples]

        def rounded(value: float | None, digits: int = 6) -> float | None:
            return None if value is None else round(value, digits)

        rss = values("aggregate_rss_bytes")
        pagefile = values("aggregate_pagefile_bytes")
        available = values("available_memory_bytes")
        system_memory = values("system_memory_percent")
        system_cpu = values("system_cpu_percent")
        equivalent_cores = values("tree_equivalent_cores")
        child_counts = values("child_count")
        swap_used = values("system_swap_used_bytes")
        initial_swap = self._initial_swap_used_bytes or 0
        return {
            "status": "completed" if self._error is None else "completed_with_warning",
            "sample_interval_seconds": self.interval_seconds,
            "sample_count": len(self._samples),
            "error": self._error,
            "aggregate_rss_peak_bytes": int(max(rss)),
            "aggregate_rss_p50_bytes": int(_percentile(rss, 0.50) or 0),
            "aggregate_rss_p95_bytes": int(_percentile(rss, 0.95) or 0),
            "aggregate_pagefile_peak_bytes": int(max(pagefile)),
            "available_memory_min_bytes": int(min(available)),
            "system_memory_percent_peak": rounded(max(system_memory), 3),
            "system_cpu_percent_p50": rounded(_percentile(system_cpu, 0.50), 3),
            "system_cpu_percent_p95": rounded(_percentile(system_cpu, 0.95), 3),
            "system_cpu_percent_peak": rounded(max(system_cpu), 3),
            "tree_equivalent_cores_p50": rounded(_percentile(equivalent_cores, 0.50), 3),
            "tree_equivalent_cores_p95": rounded(_percentile(equivalent_cores, 0.95), 3),
            "tree_equivalent_cores_peak": rounded(max(equivalent_cores), 3),
            "tree_cpu_seconds_observed": round(self._observed_cpu_seconds, 6),
            "max_child_processes": int(max(child_counts)),
            "system_swap_used_peak_bytes": int(max(swap_used)),
            "system_swap_used_growth_bytes": int(max(0, max(swap_used) - initial_swap)),
        }


def _overview_creation_options(payload: dict[str, Any]) -> list[str]:
    """Return explicit GDAL 3.x overview options for controlled E1 experiments."""

    compression = str(payload["compression"]).upper()
    options = [
        "LOCATION=EXTERNAL",
        f"COMPRESS={compression}",
        "BIGTIFF=YES",
        f"NUM_THREADS={payload['gdal_threads']}",
    ]
    predictor = payload.get("predictor")
    if predictor is not None and compression in {"DEFLATE", "LZW", "ZSTD"}:
        options.append(f"PREDICTOR={int(predictor)}")
    block_size = payload.get("block_size")
    if block_size is not None:
        block_size = int(block_size)
        if block_size < 64 or block_size > 4096 or block_size & (block_size - 1):
            raise ValueError("block_size must be a power of two within [64, 4096]")
        options.append(f"BLOCKSIZE={block_size}")
    zstd_level = payload.get("zstd_level")
    if zstd_level is not None:
        zstd_level = int(zstd_level)
        if zstd_level < 1 or zstd_level > 22:
            raise ValueError("zstd_level must be within [1, 22]")
        if compression not in {"ZSTD", "LERC_ZSTD"}:
            raise ValueError("zstd_level requires ZSTD or LERC_ZSTD compression")
        options.append(f"ZSTD_LEVEL={zstd_level}")
    max_z_error = payload.get("max_z_error")
    if max_z_error is not None:
        if compression not in {"LERC", "LERC_DEFLATE", "LERC_ZSTD"}:
            raise ValueError("max_z_error requires a LERC compression profile")
        options.append(f"MAX_Z_ERROR={float(max_z_error):g}")
    return options


def _configure_packed_gdal_environment() -> dict[str, str]:
    """Make direct packed-Python benchmark runs match the Tauri launcher."""

    runtime = Path(sys.executable).resolve().parent
    candidates = {
        "GDAL_DRIVER_PATH": runtime / "Library" / "lib" / "gdalplugins",
        "GDAL_DATA": runtime / "Library" / "share" / "gdal",
        "PROJ_LIB": runtime / "Library" / "share" / "proj",
    }
    configured: dict[str, str] = {}
    for name, path in candidates.items():
        if path.is_dir():
            os.environ.setdefault(name, str(path))
            configured[name] = str(path)
    library_bin = runtime / "Library" / "bin"
    if library_bin.is_dir():
        os.environ["PATH"] = f"{library_bin}{os.pathsep}{runtime}{os.pathsep}{os.environ.get('PATH', '')}"
    return configured


def _partition_native_overviews(
    paths: list[Path],
) -> tuple[list[Path], list[dict[str, Any]]]:
    """Match production: sources with native pyramids do not get project sidecars."""

    import rasterio

    pending: list[Path] = []
    native: list[dict[str, Any]] = []
    for path in paths:
        before = path.stat()
        io_before = _io_snapshot()
        wall_started = time.perf_counter()
        cpu_started = time.process_time()
        with rasterio.open(path) as src:
            factors = list(src.overviews(1)) if src.count else []
            if not factors:
                pending.append(path)
                continue
            width, height = int(src.width), int(src.height)
            bands = int(src.count)
            dtype = str(src.dtypes[0]) if src.count else None
            driver = str(src.driver)
        after = path.stat()
        if after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
            raise RuntimeError(f"Source raster changed during native-overview check: {path}")
        io_after = _io_snapshot()
        native.append(
            {
                "source": str(path),
                "source_bytes": int(before.st_size),
                "width": width,
                "height": height,
                "bands": bands,
                "dtype": dtype,
                "driver": driver,
                "factors": factors,
                "overview_bytes": 0,
                "wall_seconds": round(time.perf_counter() - wall_started, 6),
                "cpu_seconds": round(time.process_time() - cpu_started, 6),
                "read_bytes": _delta(io_after["read_bytes"], io_before["read_bytes"]),
                "write_bytes": _delta(io_after["write_bytes"], io_before["write_bytes"]),
                "peak_rss_bytes": _peak_rss_bytes(),
                "source_unchanged": True,
                "status": "native",
            }
        )
    return pending, native


def _build_overview_worker(payload: dict[str, Any]) -> dict[str, Any]:
    """Process-isolated GDAL overview build; safe for concurrent profile runs."""
    source = Path(payload["source"])
    target_dir = Path(payload["target_dir"])
    target_dir.mkdir(parents=True, exist_ok=False)
    vrt = target_dir / "display_overview.vrt"
    source_before = source.stat()
    io_before = _io_snapshot()
    wall_started = time.perf_counter()
    cpu_started = time.process_time()

    try:
        from osgeo import gdal

        gdal.UseExceptions()
        dataset = gdal.BuildVRT(str(vrt), [str(source)])
        if dataset is None:
            raise RuntimeError("GDAL BuildVRT returned no dataset")
        width, height = int(dataset.RasterXSize), int(dataset.RasterYSize)
        bands = int(dataset.RasterCount)
        dtype = gdal.GetDataTypeName(dataset.GetRasterBand(1).DataType) if bands else None
        factors = overview_factors(
            width,
            height,
            first_factor=int(payload["first_factor"]),
            min_overview_size=int(payload["min_overview_size"]),
        )
        dataset = None

        option_mode = str(payload.get("creation_option_mode") or "legacy")
        explicit_options: list[str] = []
        if option_mode == "explicit":
            explicit_options = _overview_creation_options(payload)
            handle = gdal.Open(str(vrt), gdal.GA_Update)
            if handle is None:
                raise RuntimeError("GDAL could not reopen benchmark VRT")
            result = handle.BuildOverviews(
                str(payload["resampling"]),
                factors,
                options=explicit_options,
            )
            handle = None
            if result not in {None, 0}:
                raise RuntimeError(f"GDAL BuildOverviews failed with code {result}")
        else:
            legacy_options = {
                "COMPRESS_OVERVIEW": str(payload["compression"]),
                "BIGTIFF_OVERVIEW": "YES",
                "GDAL_NUM_THREADS": str(payload["gdal_threads"]),
            }
            predictor = payload.get("predictor")
            if predictor is not None:
                legacy_options["PREDICTOR_OVERVIEW"] = str(predictor)
            for key, value in legacy_options.items():
                gdal.SetConfigOption(key, value)
            try:
                handle = gdal.Open(str(vrt), gdal.GA_Update)
                if handle is None:
                    raise RuntimeError("GDAL could not reopen benchmark VRT")
                result = handle.BuildOverviews(str(payload["resampling"]), factors)
                handle = None
                if result not in {None, 0}:
                    raise RuntimeError(f"GDAL BuildOverviews failed with code {result}")
            finally:
                for key in legacy_options:
                    gdal.SetConfigOption(key, None)

        ovr = vrt.with_name(vrt.name + ".ovr")
        if not ovr.is_file():
            raise RuntimeError("GDAL did not create the external overview sidecar")
        source_after = source.stat()
        if (
            source_after.st_size != source_before.st_size
            or source_after.st_mtime_ns != source_before.st_mtime_ns
        ):
            raise RuntimeError("Source raster changed during read-only benchmark")
        io_after = _io_snapshot()
        build_wall_seconds = time.perf_counter() - wall_started

        read_started = time.perf_counter()
        display = gdal.Open(str(vrt), gdal.GA_ReadOnly)
        if display is None:
            raise RuntimeError("GDAL could not open completed benchmark VRT")
        for band_index in range(1, min(3, int(display.RasterCount)) + 1):
            pixels = display.GetRasterBand(band_index).ReadAsArray(
                0,
                0,
                display.RasterXSize,
                display.RasterYSize,
                buf_xsize=256,
                buf_ysize=256,
                resample_alg=gdal.GRIORA_Bilinear,
            )
            if pixels is None:
                raise RuntimeError(f"GDAL returned no low-zoom pixels for band {band_index}")
        display = None
        overview_low_zoom_seconds = time.perf_counter() - read_started
        return {
            "source": str(source),
            "source_bytes": int(source_before.st_size),
            "width": width,
            "height": height,
            "bands": bands,
            "dtype": dtype,
            "factors": factors,
            "overview_bytes": int(ovr.stat().st_size),
            "wall_seconds": round(build_wall_seconds, 6),
            "overview_low_zoom_seconds": round(overview_low_zoom_seconds, 6),
            "cpu_seconds": round(time.process_time() - cpu_started, 6),
            "read_bytes": _delta(io_after["read_bytes"], io_before["read_bytes"]),
            "write_bytes": _delta(io_after["write_bytes"], io_before["write_bytes"]),
            "peak_rss_bytes": _peak_rss_bytes(),
            "gdal_version": gdal.VersionInfo(),
            "creation_option_mode": option_mode,
            "creation_options": explicit_options,
            "source_unchanged": True,
        }
    except Exception as exc:
        return {
            "source": str(source),
            "source_bytes": int(source_before.st_size),
            "wall_seconds": round(time.perf_counter() - wall_started, 6),
            "cpu_seconds": round(time.process_time() - cpu_started, 6),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _metadata_baseline(paths: list[Path]) -> list[dict[str, Any]]:
    from services.scene_loader import get_scene_info

    results: list[dict[str, Any]] = []
    for path in paths:
        before = path.stat()
        io_before = _io_snapshot()
        started = time.perf_counter()
        cpu_started = time.process_time()
        try:
            info = get_scene_info(path).model_dump()
            error = None
        except Exception as exc:
            info = {}
            error = f"{type(exc).__name__}: {exc}"
        io_after = _io_snapshot()
        after = path.stat()
        unchanged = before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns
        results.append(
            {
                "source": str(path),
                "source_bytes": int(before.st_size),
                "wall_seconds": round(time.perf_counter() - started, 6),
                "cpu_seconds": round(time.process_time() - cpu_started, 6),
                "read_bytes": _delta(io_after["read_bytes"], io_before["read_bytes"]),
                "write_bytes": _delta(io_after["write_bytes"], io_before["write_bytes"]),
                "source_unchanged": unchanged,
                "scene_info": info,
                "error": error,
            }
        )
    return results


def _target_name(index: int, source: Path) -> str:
    digest = hashlib.sha256(str(source).casefold().encode("utf-8")).hexdigest()[:12]
    return f"{index:04d}_{digest}"


def _run_overviews(
    paths: list[Path],
    work_dir: Path,
    *,
    workers: int,
    compression: str,
    gdal_threads: str,
    predictor: int | None,
    first_factor: int,
    min_overview_size: int,
    resampling: str,
    creation_option_mode: str,
    zstd_level: int | None,
    block_size: int | None,
    max_z_error: float | None,
) -> list[dict[str, Any]]:
    payloads = [
        {
            "source": str(path),
            "target_dir": str(work_dir / _target_name(index, path)),
            "compression": compression,
            "gdal_threads": gdal_threads,
            "predictor": predictor,
            "first_factor": first_factor,
            "min_overview_size": min_overview_size,
            "resampling": resampling,
            "creation_option_mode": creation_option_mode,
            "zstd_level": zstd_level,
            "block_size": block_size,
            "max_z_error": max_z_error,
        }
        for index, path in enumerate(paths)
    ]
    results: list[dict[str, Any]] = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(_build_overview_worker, payload) for payload in payloads]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda item: str(item.get("source") or "").casefold())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument("--phase", choices=("metadata", "overview"), required=True)
    parser.add_argument("--scene-root")
    parser.add_argument("--scene-path", action="append", default=[])
    parser.add_argument("--extensions", nargs="+", default=list(DEFAULT_EXTENSIONS))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--group-count", type=int, default=1)
    parser.add_argument("--group-index", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--compression",
        choices=("DEFLATE", "ZSTD", "LZW", "LERC", "LERC_DEFLATE", "LERC_ZSTD"),
        default="DEFLATE",
    )
    parser.add_argument("--gdal-threads", default="1", help="GDAL_NUM_THREADS value")
    parser.add_argument("--predictor", type=int)
    parser.add_argument("--zstd-level", type=int)
    parser.add_argument("--block-size", type=int)
    parser.add_argument("--max-z-error", type=float)
    parser.add_argument(
        "--creation-option-mode",
        choices=("legacy", "explicit"),
        default="legacy",
        help="Use existing config variables or explicit GDAL BuildOverviews options",
    )
    parser.add_argument("--resource-sample-interval", type=float, default=0.5)
    parser.add_argument("--first-factor", type=int, default=2)
    parser.add_argument("--min-overview-size", type=int, default=512)
    parser.add_argument("--resampling", default="AVERAGE")
    parser.add_argument(
        "--skip-native-overviews",
        action="store_true",
        help="Match production and skip external VRT/OVR for sources with native pyramids",
    )
    parser.add_argument("--work-dir", help="Parent directory for generated VRT/OVR artifacts")
    parser.add_argument("--keep-artifacts", action="store_true")
    return parser


def _sum_metric(results: list[dict[str, Any]], name: str) -> int:
    return sum(int(item.get(name) or 0) for item in results)


def run(args: argparse.Namespace) -> Path:
    packed_gdal_environment = _configure_packed_gdal_environment()
    configure_environment(args)
    from services.performance_metrics import PerformanceRecorder

    discovered = discover_scene_paths(args.scene_path, args.scene_root, extensions=args.extensions)
    groups = balanced_scene_groups(discovered, args.group_count)
    if args.group_index < 0 or args.group_index >= len(groups):
        raise ValueError("group_index must be within [0, group_count)")
    paths = groups[args.group_index]
    if args.limit > 0:
        paths = paths[: args.limit]
    if not paths:
        raise RuntimeError("No scene rasters selected for benchmark")
    if args.workers < 1:
        raise ValueError("workers must be at least 1")
    if args.resource_sample_interval <= 0:
        raise ValueError("resource_sample_interval must be positive")
    if args.creation_option_mode == "legacy" and any(
        value is not None for value in (args.zstd_level, args.block_size, args.max_z_error)
    ):
        raise ValueError(
            "zstd_level, block_size and max_z_error require --creation-option-mode explicit"
        )
    if args.creation_option_mode == "explicit":
        _overview_creation_options(
            {
                "compression": args.compression,
                "gdal_threads": args.gdal_threads,
                "predictor": args.predictor,
                "zstd_level": args.zstd_level,
                "block_size": args.block_size,
                "max_z_error": args.max_z_error,
            }
        )

    destination = output_path(args, OPERATION)
    profile = {
        "phase": args.phase,
        "workers": args.workers,
        "compression": args.compression,
        "gdal_threads": args.gdal_threads,
        "predictor": args.predictor,
        "zstd_level": args.zstd_level,
        "block_size": args.block_size,
        "max_z_error": args.max_z_error,
        "creation_option_mode": args.creation_option_mode,
        "first_factor": args.first_factor,
        "min_overview_size": args.min_overview_size,
        "resampling": args.resampling,
        "skip_native_overviews": bool(args.skip_native_overviews),
        "resource_sample_interval": args.resource_sample_interval,
    }
    inputs = {
        "scene_count": len(paths),
        "scene_bytes": sum(path.stat().st_size for path in paths),
        "scene_paths": [str(path) for path in paths],
        "discovered_scene_count": len(discovered),
        "group_count": args.group_count,
        "group_index": args.group_index,
        "profile": profile,
    }

    base_work = (
        Path(args.work_dir).expanduser().resolve(strict=False)
        if args.work_dir
        else destination.parent.resolve(strict=False)
    )
    base_work.mkdir(parents=True, exist_ok=True)
    temp_context: tempfile.TemporaryDirectory[str] | None = None
    if args.keep_artifacts:
        work_dir = Path(tempfile.mkdtemp(prefix="geotile_p1_7_keep_", dir=base_work))
    else:
        temp_context = tempfile.TemporaryDirectory(prefix="geotile_p1_7_", dir=base_work)
        work_dir = Path(temp_context.name)

    results: list[dict[str, Any]] = []
    resource_usage: dict[str, Any] | None = None
    try:
        with PerformanceRecorder(
            OPERATION,
            output_path=destination,
            cache_state=args.cache_state,
            storage_profile=args.storage_profile,
            inputs=inputs,
            metadata_value={
                "read_only_sources": True,
                "work_dir": str(work_dir) if args.keep_artifacts else "temporary",
                "cache_semantics": "cold does not flush the operating-system cache",
                "packed_gdal_environment": packed_gdal_environment,
            },
        ) as recorder:
            if args.phase == "metadata":
                with recorder.stage("metadata_current") as timer:
                    results = _metadata_baseline(paths)
                    timer.record(scenes=len(results), failed=sum(bool(item.get("error")) for item in results))
            else:
                with recorder.stage("overview_build") as timer:
                    with ProcessTreeResourceSampler(args.resource_sample_interval) as resources:
                        build_paths = paths
                        native_results: list[dict[str, Any]] = []
                        if args.skip_native_overviews:
                            build_paths, native_results = _partition_native_overviews(paths)
                        results = native_results + _run_overviews(
                            build_paths,
                            work_dir,
                            workers=args.workers,
                            compression=args.compression,
                            gdal_threads=args.gdal_threads,
                            predictor=args.predictor,
                            first_factor=args.first_factor,
                            min_overview_size=args.min_overview_size,
                            resampling=args.resampling,
                            creation_option_mode=args.creation_option_mode,
                            zstd_level=args.zstd_level,
                            block_size=args.block_size,
                            max_z_error=args.max_z_error,
                        )
                    resource_usage = resources.summary()
                    results = sorted(results, key=lambda item: str(item.get("source") or "").casefold())
                    timer.record(
                        scenes=len(results),
                        built=len(build_paths),
                        native=len(native_results),
                        failed=sum(bool(item.get("error")) for item in results),
                        aggregate_rss_peak_bytes=(resource_usage or {}).get(
                            "aggregate_rss_peak_bytes"
                        ),
                    )

            failed = [item for item in results if item.get("error")]
            changed = [item for item in results if item.get("source_unchanged") is False]
            wall = float(recorder.report["stages"][
                "metadata_current" if args.phase == "metadata" else "overview_build"
            ]["wall_seconds"])
            recorder.set_metric("scene_results", results)
            recorder.set_metric("failed_scenes", len(failed))
            recorder.set_metric("changed_sources", len(changed))
            recorder.set_metric("source_bytes", _sum_metric(results, "source_bytes"))
            recorder.set_metric("read_bytes", _sum_metric(results, "read_bytes"))
            recorder.set_metric("write_bytes", _sum_metric(results, "write_bytes"))
            recorder.set_metric("overview_bytes", _sum_metric(results, "overview_bytes"))
            recorder.set_metric(
                "native_overview_sources",
                sum(item.get("status") == "native" for item in results),
            )
            if args.phase == "metadata":
                recorder.set_metric(
                    "raster_open_count",
                    sum(
                        int((item.get("scene_info") or {}).get("characterization_open_count") or 0)
                        for item in results
                    ),
                )
                recorder.set_metric(
                    "sample_read_count",
                    sum(
                        int(
                            (((item.get("scene_info") or {}).get("display_stats") or {}).get("read_count"))
                            or 0
                        )
                        for item in results
                    ),
                )
                recorder.set_metric(
                    "deferred_display_profiles",
                    sum(
                        ((item.get("scene_info") or {}).get("display_stats") or {}).get("status")
                        == "deferred"
                        for item in results
                    ),
                )
            recorder.set_metric("child_cpu_seconds", round(sum(float(item.get("cpu_seconds") or 0) for item in results), 6))
            recorder.set_metric("throughput_source_mib_s", round(inputs["scene_bytes"] / (1024 * 1024) / max(wall, 1e-9), 3))
            recorder.set_metric("artifacts_retained", bool(args.keep_artifacts))
            if resource_usage is not None:
                recorder.set_metric("resource_usage", resource_usage)
                recorder.set_metric(
                    "equivalent_cores_from_child_cpu",
                    round(
                        sum(float(item.get("cpu_seconds") or 0) for item in results)
                        / max(wall, 1e-9),
                        6,
                    ),
                )
            if args.keep_artifacts:
                recorder.set_metric("artifact_directory", str(work_dir))
            if changed:
                raise RuntimeError("Benchmark detected a changed source raster")
            if failed:
                raise RuntimeError(f"{len(failed)} scene benchmark operations failed")
    finally:
        if temp_context is not None:
            temp_context.cleanup()
    return destination


def main() -> None:
    args = build_parser().parse_args()
    print(run(args))


if __name__ == "__main__":
    main()
