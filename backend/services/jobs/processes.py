"""Exact process identity and conservative whole-tree termination."""

from __future__ import annotations

import os
import time
from typing import Any, Literal

ProcessStatus = Literal["live", "absent", "recycled", "unknown"]


def _windows_creation_marker(pid: int) -> int | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        query_limited = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(query_limited, False, int(pid))
        if not handle:
            return None
        try:
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
            return (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def capture_process_identity(pid: int | None = None) -> dict[str, Any] | None:
    pid = int(pid or os.getpid())
    marker = _windows_creation_marker(pid)
    if marker is not None:
        return {"pid": pid, "marker_kind": "windows_filetime", "start_marker": marker}
    try:
        import psutil  # type: ignore

        created_ns = int(round(psutil.Process(pid).create_time() * 1_000_000_000))
        return {"pid": pid, "marker_kind": "psutil_create_time_ns", "start_marker": created_ns}
    except Exception:
        return None


def process_status(identity: dict[str, Any] | None) -> ProcessStatus:
    if not identity or not identity.get("pid") or identity.get("start_marker") is None:
        return "unknown"
    pid = int(identity["pid"])
    try:
        import psutil  # type: ignore

        if not psutil.pid_exists(pid):
            return "absent"
    except Exception:
        pass
    current = capture_process_identity(pid)
    if current is None:
        return "unknown"
    return "live" if current == identity else "recycled"


def terminate_process_tree(identity: dict[str, Any], timeout_s: float = 15.0) -> dict[str, Any]:
    """Terminate only a tree whose root still has the recorded creation marker."""

    status = process_status(identity)
    if status != "live":
        return {"status": status, "terminated": [], "preserved": [identity.get("pid")]}
    try:
        import psutil  # type: ignore
    except Exception as exc:
        return {
            "status": "unknown",
            "terminated": [],
            "preserved": [identity.get("pid")],
            "error": f"psutil unavailable: {exc}",
        }

    root = psutil.Process(int(identity["pid"]))
    descendants: list[tuple[int, Any, dict[str, Any]]] = []

    def collect(process: Any, depth: int) -> None:
        try:
            children = process.children(recursive=False)
        except Exception:
            children = []
        for child in children:
            child_identity = capture_process_identity(child.pid)
            if child_identity is None:
                continue
            descendants.append((depth + 1, child, child_identity))
            collect(child, depth + 1)

    collect(root, 0)
    root_identity = capture_process_identity(root.pid)
    if root_identity != identity:
        return {"status": "recycled", "terminated": [], "preserved": [root.pid]}
    descendants.sort(key=lambda item: item[0], reverse=True)
    targets = [(process, marker) for _, process, marker in descendants] + [(root, identity)]
    terminated: list[int] = []
    preserved: list[int] = []
    signalled: list[Any] = []
    for process, expected in targets:
        if process_status(expected) != "live":
            preserved.append(int(expected["pid"]))
            continue
        try:
            process.terminate()
            signalled.append(process)
        except psutil.NoSuchProcess:
            continue
        except Exception:
            preserved.append(process.pid)
    _gone, alive = psutil.wait_procs(signalled, timeout=max(0.1, float(timeout_s)))
    for process in alive:
        expected = next((item for item in targets if item[0].pid == process.pid), None)
        if expected is None or process_status(expected[1]) != "live":
            preserved.append(process.pid)
            continue
        try:
            process.kill()
        except Exception:
            preserved.append(process.pid)
    if alive:
        psutil.wait_procs(alive, timeout=3.0)
    for process, expected in targets:
        if process_status(expected) in {"absent", "recycled"}:
            terminated.append(process.pid)
        elif process.pid not in preserved:
            preserved.append(process.pid)
    return {
        "status": "terminated" if not preserved else "partial",
        "terminated": sorted(set(terminated)),
        "preserved": sorted(set(preserved)),
    }


def wait_until_not_live(identity: dict[str, Any], timeout_s: float = 5.0) -> ProcessStatus:
    deadline = time.monotonic() + timeout_s
    status = process_status(identity)
    while status == "live" and time.monotonic() < deadline:
        time.sleep(0.05)
        status = process_status(identity)
    return status

