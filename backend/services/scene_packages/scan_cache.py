"""Versioned, rebuildable source inventory cache for durable scene scans (P1.2).

The cache lives in application data, never in a provider delivery.  Freshness is
proved by a deterministic deep file snapshot; the root directory mtime is recorded
for diagnostics but is never the sole cache key.
"""

from __future__ import annotations

import copy
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from db import storage
from services.jobs.store import read_json, write_json_atomic
from services.scene_packages import archives
from services.scene_packages.base import canonical_hash
from services.scene_packages.contracts import CONTRACT_VERSION, graph_v2_enabled
from services.scene_packages.resolvers import get_resolver, resolve_package_candidate
from services.scene_sources import canonical_source_root, plain_path


SCAN_CACHE_SCHEMA_NAME = "geotile_scene_package_scan_cache"
SCAN_CACHE_SCHEMA_VERSION = 2
SCAN_CACHE_LOGIC_VERSION = 2
SNAPSHOT_SCHEMA_VERSION = 1
CACHE_GENERATION = SCAN_CACHE_SCHEMA_VERSION

ProgressCallback = Callable[[str, int, int | None, dict[str, Any]], None]
CancelCheck = Callable[[], bool]


class SceneScanCancelled(RuntimeError):
    pass


class SourceChangedDuringScan(RuntimeError):
    code = "source_changed_during_scan"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_cancel(should_cancel: CancelCheck | None) -> None:
    if should_cancel is not None and should_cancel():
        raise SceneScanCancelled("Scene source scan cancelled")


def _emit(
    progress: ProgressCallback | None,
    stage: str,
    current: int,
    total: int | None = None,
    **detail: Any,
) -> None:
    if progress is not None:
        progress(stage, current, total, detail)


def snapshot_source_tree(
    root: Path,
    *,
    should_cancel: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
    stage: str = "snapshot",
) -> dict[str, Any]:
    """Stat every regular file without opening its contents."""

    resolved = root.resolve(strict=True)
    stack = [resolved]
    files: list[dict[str, Any]] = []
    directories = 0
    while stack:
        _check_cancel(should_cancel)
        directory = stack.pop()
        directories += 1
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name.casefold())
        except OSError as exc:
            raise RuntimeError(f"Could not scan source directory {directory}: {exc}") from exc
        child_dirs: list[Path] = []
        for entry in entries:
            _check_cancel(should_cancel)
            try:
                if entry.is_dir(follow_symlinks=False):
                    child_dirs.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise RuntimeError(f"Could not stat source entry {entry.path}: {exc}") from exc
            files.append({
                "relative_path": Path(entry.path).relative_to(resolved).as_posix(),
                "size": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            })
        # Reverse because the stack is LIFO; traversal remains deterministic.
        stack.extend(reversed(child_dirs))
        _emit(progress, stage, directories, None, files=len(files), directory=str(directory))

    files.sort(key=lambda item: str(item["relative_path"]).casefold())
    fingerprint = canonical_hash({
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "files": files,
    })
    try:
        root_mtime_ns = resolved.stat().st_mtime_ns
    except OSError:
        root_mtime_ns = None
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "root_path": str(resolved),
        "root_mtime_ns": root_mtime_ns,
        "file_count": len(files),
        "directory_count": directories,
        "files": files,
        "fingerprint": fingerprint,
    }


def _cache_descriptor(root: Path, provider: str) -> dict[str, Any]:
    resolver = get_resolver(provider)
    return {
        "provider": provider,
        "resolver_version": int(resolver.version),
        "contract_version": CONTRACT_VERSION,
        "scan_logic_version": SCAN_CACHE_LOGIC_VERSION,
        "graph_v2": bool(graph_v2_enabled()),
        # Klucz cache'u liczy sie na sciezce BEZ prefiksu rozszerzonego: inaczej ten sam
        # katalog dawalby dwa rozne deskryptory i kazdy skan zaczynalby od zera.
        "canonical_root": canonical_source_root(plain_path(root)),
    }


def invalidate_legacy_scan_caches() -> dict[str, Any]:
    """Remove incompatible inventory JSON once per application-data directory.

    Cache files are rebuildable.  Cleanup is deliberately limited to the exact cache
    directory and plain JSON files; source deliveries and project documents are never
    candidates.  A marker is written only after a failure-free pass, so locked files are
    retried on the next startup.
    """
    root = storage.DATA_DIR / "cache" / "scene-package-inventories"
    marker = root / f".generation-{CACHE_GENERATION}.complete.json"
    if marker.is_file():
        return {"generation": CACHE_GENERATION, "status": "already_complete", "removed": 0}

    root.mkdir(parents=True, exist_ok=True)
    removed = 0
    reclaimed_bytes = 0
    failures: list[dict[str, str]] = []
    for path in root.glob("*.json"):
        if path == marker:
            continue
        payload = read_json(path, default={}) or {}
        descriptor = payload.get("descriptor") or {}
        compatible = (
            payload.get("schema_name") == SCAN_CACHE_SCHEMA_NAME
            and payload.get("schema_version") == SCAN_CACHE_SCHEMA_VERSION
            and descriptor.get("scan_logic_version") == SCAN_CACHE_LOGIC_VERSION
            and descriptor.get("contract_version") == CONTRACT_VERSION
        )
        if compatible:
            continue
        try:
            size = path.stat().st_size
            path.unlink()
            removed += 1
            reclaimed_bytes += size
        except OSError as exc:
            failures.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})

    result = {
        "generation": CACHE_GENERATION,
        "status": "complete" if not failures else "retry_required",
        "removed": removed,
        "reclaimed_bytes": reclaimed_bytes,
        "failures": failures,
        "completed_at": _utc_now() if not failures else None,
    }
    if not failures:
        write_json_atomic(marker, result)
    return result


def scan_cache_path(root: Path, provider: str) -> Path:
    descriptor = _cache_descriptor(root, provider)
    key = hashlib.sha256(
        canonical_hash(descriptor).encode("ascii")
    ).hexdigest()[:32]
    target = storage.DATA_DIR / "cache" / "scene-package-inventories" / f"{key}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _load_cache(root: Path, provider: str) -> tuple[Path, dict[str, Any]]:
    path = scan_cache_path(root, provider)
    payload = read_json(path, default={}) or {}
    if (
        payload.get("schema_name") != SCAN_CACHE_SCHEMA_NAME
        or payload.get("schema_version") != SCAN_CACHE_SCHEMA_VERSION
        or payload.get("descriptor") != _cache_descriptor(root, provider)
    ):
        return path, {}
    return path, payload


def _candidate_records(snapshot: dict[str, Any], relative_root: str) -> list[dict[str, Any]]:
    relative = str(relative_root or ".").replace("\\", "/").strip("/")
    records = snapshot.get("files") or []
    if relative in {"", "."}:
        return list(records)
    # Generic scenes use a file itself as candidate root.
    exact = [item for item in records if str(item.get("relative_path")) == relative]
    if exact:
        return exact
    prefix = relative + "/"
    return [
        item for item in records
        if str(item.get("relative_path") or "").startswith(prefix)
    ]


def _candidate_fingerprint(snapshot: dict[str, Any], relative_root: str) -> str:
    return canonical_hash({
        "relative_root": relative_root,
        "files": _candidate_records(snapshot, relative_root),
    })


def scan_source_cached(
    root: Path,
    provider: str,
    *,
    should_cancel: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Scan one provider root with stable-snapshot publication semantics."""

    resolved = root.resolve(strict=True)
    resolver = get_resolver(provider)
    diagnostics = resolver.validate_source(resolved)
    before = snapshot_source_tree(
        resolved,
        should_cancel=should_cancel,
        progress=progress,
        stage="snapshot_before",
    )
    cache_path, cached = _load_cache(resolved, provider)
    cached_candidates = cached.get("candidates") or {}
    cache_hits = 0
    cache_misses = 0

    if cached and cached.get("source_fingerprint") == before["fingerprint"]:
        candidate_order = [
            key for key in (cached.get("candidate_order") or sorted(cached_candidates))
            if key in cached_candidates
        ]
        packages = [
            copy.deepcopy(package)
            for key in candidate_order
            for package in (cached_candidates[key].get("packages") or [])
        ]
        # Compatibility with the first schema-v1 implementation written during
        # development, before candidate payloads became the single source of truth.
        if not packages:
            packages = copy.deepcopy(cached.get("packages") or [])
        cache_hits = len(cached_candidates) or len(packages)
        _emit(progress, "cache_hit", cache_hits, cache_hits, packages=len(packages))
        candidate_payload = copy.deepcopy(cached_candidates)
        for index, key in enumerate(candidate_order, start=1):
            _emit(
                progress,
                "package",
                index,
                len(candidate_payload),
                relative_root=key,
                cache_hit=True,
                package_count=len(candidate_payload[key].get("packages") or []),
            )
        cache_status = "hit"
    else:
        candidates = resolver.discover_packages(resolved)
        _emit(progress, "discovery", 0, len(candidates), candidates=len(candidates))
        packages = []
        candidate_payload: dict[str, Any] = {}
        candidate_order: list[str] = []
        seen_roots: set[str] = set()
        for index, candidate in enumerate(candidates, start=1):
            _check_cancel(should_cancel)
            key = str(candidate.relative_root or ".").replace("\\", "/")
            canonical_candidate = str(candidate.root.resolve(strict=False)).casefold()
            if canonical_candidate in seen_roots:
                continue
            seen_roots.add(canonical_candidate)
            fingerprint = _candidate_fingerprint(before, key)
            cached_candidate = cached_candidates.get(key) or {}
            # Klasyfikacja archiwum (P1.3a) zalezy od plikow LEZACYCH OBOK, a nie od samego
            # archiwum: usuniecie rozpakowanej kopii nie zmienia ani bajta w ZIP-ie, wiec
            # odcisk kandydata nie dowodzi swiezosci wyniku. Pelne trafienie cache wyzej jest
            # bezpieczne (nie zmienilo sie NIC w zrodle); tutaj indeksujemy ponownie —
            # to okolo 19 ms na archiwum wobec 0,2 s pelnego skanu zrodla.
            if archives.is_archive(key):
                cached_candidate = {}
            if cached_candidate.get("fingerprint") == fingerprint:
                resolved_packages = copy.deepcopy(cached_candidate.get("packages") or [])
                cache_hits += 1
                hit = True
            else:
                resolved_packages = resolve_package_candidate(resolver, resolved, candidate)
                cache_misses += 1
                hit = False
            packages.extend(resolved_packages)
            candidate_payload[key] = {
                "fingerprint": fingerprint,
                "packages": copy.deepcopy(resolved_packages),
            }
            candidate_order.append(key)
            _emit(
                progress,
                "package",
                index,
                len(candidates),
                relative_root=key,
                cache_hit=hit,
                package_count=len(resolved_packages),
            )
        cache_status = "partial" if cache_hits else "miss"

    _check_cancel(should_cancel)
    after = snapshot_source_tree(
        resolved,
        should_cancel=should_cancel,
        progress=progress,
        stage="snapshot_after",
    )
    if before["fingerprint"] != after["fingerprint"]:
        raise SourceChangedDuringScan(
            "source_changed_during_scan: source contents changed between stable snapshots"
        )

    payload = {
        "schema_name": SCAN_CACHE_SCHEMA_NAME,
        "schema_version": SCAN_CACHE_SCHEMA_VERSION,
        "descriptor": _cache_descriptor(resolved, provider),
        "source_fingerprint": after["fingerprint"],
        "source_snapshot": after,
        "package_count": len(packages),
        "candidates": candidate_payload,
        "candidate_order": candidate_order,
        "diagnostics": diagnostics,
        "updated_at": _utc_now(),
    }
    write_json_atomic(cache_path, payload)
    return {
        "packages": packages,
        "diagnostics": diagnostics,
        "snapshot_before": before,
        "snapshot_after": after,
        "cache": {
            "status": cache_status,
            "hits": cache_hits,
            "misses": cache_misses,
            "path": str(cache_path),
            "schema_version": SCAN_CACHE_SCHEMA_VERSION,
        },
    }
