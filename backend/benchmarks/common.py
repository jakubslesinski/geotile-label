"""Common CLI and generator helpers for performance benchmarks."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

# Match the backend process defaults before NumPy/BLAS is imported by a benchmark.
# Apart from preventing oversubscription, this makes baselines comparable between runs.
for _thread_variable in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_thread_variable, "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("MKL_THREADING_LAYER", "SEQUENTIAL")

# A packed Conda runtime launched directly (without activation/Tauri) does not expose
# Library/bin to the Windows DLL loader. NumPy may import successfully and then crash on
# the first BLAS call because MKL dependencies are missing. Keep directory handles alive
# for the lifetime of the benchmark process.
_DLL_DIRECTORY_HANDLES: list[Any] = []
if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
    runtime_root = Path(sys.executable).resolve().parent
    dll_candidates = (runtime_root / "Library" / "bin", runtime_root / "DLLs")
    existing_dll_dirs = [candidate for candidate in dll_candidates if candidate.is_dir()]
    if existing_dll_dirs:
        os.environ["PATH"] = os.pathsep.join(
            [*(str(candidate) for candidate in existing_dll_dirs), str(runtime_root), os.environ.get("PATH", "")]
        )
    for candidate in existing_dll_dirs:
        if candidate.is_dir():
            try:
                _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(candidate)))
            except OSError:
                pass


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def add_common_arguments(
    parser: argparse.ArgumentParser,
    *,
    project_required: bool = False,
    collect_gpu: bool = False,
) -> None:
    if project_required:
        parser.add_argument("--project-id", required=True)
    else:
        parser.add_argument("--project-id")
    parser.add_argument("--data-dir", help="Override DATA_DIR before importing backend storage")
    parser.add_argument("--output", help="Output performance JSON path")
    parser.add_argument(
        "--cache-state",
        choices=("cold", "warm", "unspecified"),
        default="unspecified",
        help="Declared cache state; cold does not flush the operating-system cache",
    )
    parser.add_argument("--storage-profile", default="unspecified")
    if collect_gpu:
        parser.add_argument(
            "--collect-gpu-info", action=argparse.BooleanOptionalAction, default=True
        )


def configure_environment(args: argparse.Namespace) -> None:
    if getattr(args, "data_dir", None):
        os.environ["DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())
    # Prevent lazy schema normalization from writing project JSON while benchmarking.
    os.environ["GEOTILE_BENCHMARK_READ_ONLY"] = "1"


def default_output_path(operation: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path.cwd() / "benchmark-results" / f"{operation}_{timestamp}.json"


def output_path(args: argparse.Namespace, operation: str) -> Path:
    value = getattr(args, "output", None)
    return Path(value).expanduser().resolve() if value else default_output_path(operation)


def consume_generator(generator: Generator[dict, None, Any]) -> tuple[Any, int]:
    progress_events = 0
    while True:
        try:
            next(generator)
            progress_events += 1
        except StopIteration as stopped:
            return stopped.value, progress_events
