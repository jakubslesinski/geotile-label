"""Shared, low-overhead performance reports for benchmarks and long-running jobs.

The module is deliberately dependency-light.  ``psutil`` is used when available, but
the base desktop build can still record process memory on Windows/Linux without it.
Reports are JSON artifacts with a versioned schema and are always written atomically.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import threading
import time
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

from db.storage import APP_VERSION, _atomic_write_bytes


PERFORMANCE_SCHEMA_NAME = "geotile_performance_report"
PERFORMANCE_SCHEMA_VERSION = 1
CacheState = Literal["cold", "warm", "unspecified"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _package_versions() -> dict[str, str | None]:
    packages = (
        "numpy",
        "Pillow",
        "rasterio",
        "pyarrow",
        "torch",
        "torchvision",
        "ultralytics",
        "usearch",
    )
    versions: dict[str, str | None] = {}
    for package in packages:
        try:
            versions[package.lower()] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package.lower()] = None
    # Do not import Rasterio merely to collect metadata: loading GDAL changes process
    # state, can emit environment warnings, and would contaminate a cold benchmark.
    rasterio_module = sys.modules.get("rasterio")
    versions["gdal"] = (
        getattr(rasterio_module, "__gdal_version__", None)
        if rasterio_module is not None
        else None
    )
    return versions


def _current_rss_bytes() -> int | None:
    try:
        import psutil  # type: ignore

        return int(psutil.Process().memory_info().rss)
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ok = ctypes.windll.psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb
            )
            return int(counters.WorkingSetSize) if ok else None
        except Exception:
            return None

    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        with open("/proc/self/statm", "r", encoding="ascii") as handle:
            resident_pages = int(handle.read().split()[1])
        return resident_pages * page_size
    except Exception:
        return None


def _peak_rss_bytes() -> int | None:
    try:
        import psutil  # type: ignore

        info = psutil.Process().memory_info()
        peak = getattr(info, "peak_wset", None)
        if peak is not None:
            return int(peak)
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ok = ctypes.windll.psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb
            )
            return int(counters.PeakWorkingSetSize) if ok else None
        except Exception:
            return None

    try:
        import resource

        peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        # Linux reports KiB; macOS reports bytes.
        return peak if sys.platform == "darwin" else peak * 1024
    except Exception:
        return _current_rss_bytes()


def _total_memory_bytes() -> int | None:
    try:
        import psutil  # type: ignore

        return int(psutil.virtual_memory().total)
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(status)
            return int(status.ullTotalPhys) if ctypes.windll.kernel32.GlobalMemoryStatusEx(
                ctypes.byref(status)
            ) else None
        except Exception:
            return None
    try:
        return int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
    except Exception:
        return None


def _mb(value: int | None) -> float | None:
    return None if value is None else round(value / (1024 * 1024), 3)


def environment_snapshot(
    *, storage_profile: str | None = None, collect_gpu: bool = False
) -> dict[str, Any]:
    """Stable environment metadata collected outside timed benchmark stages."""
    snapshot: dict[str, Any] = {
        "platform": platform.platform(),
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "python": platform.python_version(),
        "python_executable": str(Path(sys.executable).resolve()),
        "cpu_logical": os.cpu_count(),
        "memory_total_mb": _mb(_total_memory_bytes()),
        "storage_profile": storage_profile,
        "packages": _package_versions(),
    }
    if collect_gpu:
        gpu: dict[str, Any] = {"available": False}
        try:
            import torch

            gpu["available"] = bool(torch.cuda.is_available())
            gpu["torch_cuda"] = torch.version.cuda
            if gpu["available"]:
                gpu["device_count"] = int(torch.cuda.device_count())
                gpu["device_name"] = torch.cuda.get_device_name(0)
                free_bytes, total_bytes = torch.cuda.mem_get_info(0)
                gpu["vram_free_mb"] = _mb(int(free_bytes))
                gpu["vram_total_mb"] = _mb(int(total_bytes))
        except Exception as exc:
            gpu["error"] = f"{type(exc).__name__}: {exc}"
        snapshot["gpu"] = gpu
    return snapshot


class StageTimer(AbstractContextManager["StageTimer"]):
    """One timed stage; repeated names are aggregated by ``PerformanceRecorder``."""

    def __init__(
        self,
        recorder: "PerformanceRecorder",
        name: str,
        metadata_value: dict[str, Any] | None = None,
    ) -> None:
        if not name or not name.strip():
            raise ValueError("Stage name cannot be empty")
        self.recorder = recorder
        self.name = name
        self.metadata = dict(metadata_value or {})
        self.metrics: dict[str, Any] = {}
        self._wall_started = 0.0
        self._cpu_started = 0.0
        self._rss_started: int | None = None

    def __enter__(self) -> "StageTimer":
        self._rss_started = _current_rss_bytes()
        self._cpu_started = time.process_time()
        self._wall_started = time.perf_counter()
        return self

    def record(self, **values: Any) -> None:
        self.metrics.update(values)

    def __exit__(self, exc_type, exc, _traceback) -> bool:
        wall_seconds = time.perf_counter() - self._wall_started
        cpu_seconds = time.process_time() - self._cpu_started
        rss_ended = _current_rss_bytes()
        error = None if exc is None else f"{type(exc).__name__}: {exc}"
        self.recorder._record_stage(
            self.name,
            wall_seconds=wall_seconds,
            cpu_seconds=cpu_seconds,
            rss_started=self._rss_started,
            rss_ended=rss_ended,
            peak_rss=_peak_rss_bytes(),
            metadata_value=self.metadata,
            metrics=self.metrics,
            error=error,
        )
        return False


class PerformanceRecorder(AbstractContextManager["PerformanceRecorder"]):
    """Collect a versioned performance report and optionally persist it on exit."""

    def __init__(
        self,
        operation: str,
        *,
        output_path: str | Path | None = None,
        cache_state: CacheState = "unspecified",
        project_id: str | None = None,
        run_id: str | None = None,
        storage_profile: str | None = None,
        collect_gpu: bool = False,
        inputs: dict[str, Any] | None = None,
        metadata_value: dict[str, Any] | None = None,
    ) -> None:
        if not operation or not operation.strip():
            raise ValueError("Operation cannot be empty")
        if cache_state not in ("cold", "warm", "unspecified"):
            raise ValueError(f"Unsupported cache state: {cache_state}")
        self.output_path = Path(output_path) if output_path is not None else None
        self._lock = threading.Lock()
        self._wall_started = time.perf_counter()
        self._cpu_started = time.process_time()
        self._finished = False
        self.report: dict[str, Any] = {
            "schema_name": PERFORMANCE_SCHEMA_NAME,
            "schema_version": PERFORMANCE_SCHEMA_VERSION,
            "app_version": APP_VERSION,
            "operation": operation,
            "project_id": project_id,
            "run_id": run_id,
            "cache_state": cache_state,
            "started_at": utc_now(),
            "completed_at": None,
            "status": "running",
            "error": None,
            "process": {"pid": os.getpid()},
            "environment": environment_snapshot(
                storage_profile=storage_profile, collect_gpu=collect_gpu
            ),
            "inputs": dict(inputs or {}),
            "metadata": dict(metadata_value or {}),
            "stages": {},
            "metrics": {},
            "totals": {},
        }

    def __enter__(self) -> "PerformanceRecorder":
        return self

    def __exit__(self, exc_type, exc, _traceback) -> bool:
        if exc is None:
            self.finish("completed")
        else:
            self.finish("failed", f"{type(exc).__name__}: {exc}")
        if self.output_path is not None:
            self.write(self.output_path)
        return False

    def stage(
        self, name: str, *, metadata_value: dict[str, Any] | None = None
    ) -> StageTimer:
        return StageTimer(self, name, metadata_value)

    def update_inputs(self, **values: Any) -> None:
        self.report["inputs"].update(values)

    def set_metric(self, name: str, value: Any) -> None:
        self.report["metrics"][name] = value

    def add_metric(self, name: str, value: float | int) -> None:
        current = self.report["metrics"].get(name, 0)
        self.report["metrics"][name] = current + value

    def finish(self, status: Literal["completed", "failed"], error: str | None = None) -> None:
        if self._finished:
            return
        self._finished = True
        wall_seconds = time.perf_counter() - self._wall_started
        cpu_seconds = time.process_time() - self._cpu_started
        self.report["completed_at"] = utc_now()
        self.report["status"] = status
        self.report["error"] = error
        self.report["totals"] = {
            "wall_seconds": round(wall_seconds, 6),
            "cpu_seconds": round(cpu_seconds, 6),
            "current_rss_mb": _mb(_current_rss_bytes()),
            "peak_rss_mb": _mb(_peak_rss_bytes()),
        }

    def write(self, path: str | Path | None = None) -> Path:
        destination = Path(path) if path is not None else self.output_path
        if destination is None:
            raise ValueError("No performance report output path configured")
        if not self._finished:
            self.finish("completed")
        payload = json.dumps(
            self.report, indent=2, ensure_ascii=False, default=str
        ).encode("utf-8")
        _atomic_write_bytes(destination, payload)
        return destination

    def _record_stage(
        self,
        name: str,
        *,
        wall_seconds: float,
        cpu_seconds: float,
        rss_started: int | None,
        rss_ended: int | None,
        peak_rss: int | None,
        metadata_value: dict[str, Any],
        metrics: dict[str, Any],
        error: str | None,
    ) -> None:
        with self._lock:
            stages: dict[str, dict[str, Any]] = self.report["stages"]
            stage = stages.setdefault(
                name,
                {
                    "calls": 0,
                    "wall_seconds": 0.0,
                    "cpu_seconds": 0.0,
                    "rss_delta_mb": 0.0,
                    "peak_rss_mb": None,
                    "metadata": {},
                    "metrics": {},
                    "errors": [],
                },
            )
            stage["calls"] += 1
            stage["wall_seconds"] = round(stage["wall_seconds"] + wall_seconds, 6)
            stage["cpu_seconds"] = round(stage["cpu_seconds"] + cpu_seconds, 6)
            if rss_started is not None and rss_ended is not None:
                delta_mb = (rss_ended - rss_started) / (1024 * 1024)
                stage["rss_delta_mb"] = round(stage["rss_delta_mb"] + delta_mb, 3)
            peak_mb = _mb(peak_rss)
            if peak_mb is not None:
                current_peak = stage.get("peak_rss_mb")
                stage["peak_rss_mb"] = peak_mb if current_peak is None else max(
                    current_peak, peak_mb
                )
            stage["metadata"].update(metadata_value)
            for metric_name, metric_value in metrics.items():
                old_value = stage["metrics"].get(metric_name)
                if isinstance(old_value, (int, float)) and isinstance(metric_value, (int, float)):
                    stage["metrics"][metric_name] = old_value + metric_value
                else:
                    stage["metrics"][metric_name] = metric_value
            if error is not None:
                stage["errors"].append(error)


def write_performance_report(path: str | Path, report: dict[str, Any]) -> Path:
    """Atomically write an externally assembled report using the same contract."""
    destination = Path(path)
    payload = json.dumps(report, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    _atomic_write_bytes(destination, payload)
    return destination
