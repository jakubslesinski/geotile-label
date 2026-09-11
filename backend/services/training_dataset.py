"""Resolve training datasets in the layout Ultralytics actually reads.

Dataset runs keep two label sets side by side::

    <split>/images
    <split>/labels        axis-aligned  (detect)
    <split>/labels_obb    oriented      (obb)

Ultralytics derives the label directory from the image path by replacing
``/images/`` with ``/labels/``. An OBB model therefore needs a parallel tree whose
``labels/`` contains the oriented labels. The legacy path builds that tree inside
every training run. P2.3 adds an optional, rebuildable project cache so identical
runs share both that tree and the ``*.cache`` files subsequently created by
Ultralytics.

The cache is deliberately outside ``dataset_runs/``. It contains hardlinks where
the filesystem permits them and is never a source of truth: deleting it cannot
delete or mutate the published dataset run.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from db.storage import project_dir
from services.export_yolo import copy_or_link
from services.jobs.store import interprocess_lock, read_json, utc_now, write_json_atomic

SPLITS = ("train", "val", "test")
TRAINING_DATASET_CACHE_SCHEMA_VERSION = 1
TRAINING_DATASET_EXPORTER_VERSION = "1"
TRAINING_DATASET_CACHE_FLAG = "GEOTILE_TRAINING_DATASET_CACHE"
_TRUE_VALUES = frozenset(("1", "true", "yes", "on"))
_CACHE_KEY_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class TrainingDatasetError(ValueError):
    pass


def training_dataset_cache_enabled() -> bool:
    """Return the P2.3 rollout flag; the compatibility path remains the default."""

    return str(os.environ.get(TRAINING_DATASET_CACHE_FLAG) or "").strip().lower() in _TRUE_VALUES


def training_dataset_cache_root(project_id: str) -> Path:
    path = project_dir(project_id) / "artifacts" / "training_datasets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_class_names(dataset_dir: Path) -> dict[int, str]:
    # Prefer the geometry-specific file. Older runs may only have data.yaml.
    for candidate in ("data_obb.yaml", "data.yaml"):
        path = dataset_dir / candidate
        if not path.is_file():
            continue
        parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        names = parsed.get("names")
        if isinstance(names, dict):
            return {int(key): str(value) for key, value in names.items()}
        if isinstance(names, list):
            return {index: str(value) for index, value in enumerate(names)}
    raise TrainingDatasetError(f"No usable data.yaml in {dataset_dir}")


def obb_label_stats(dataset_dir: Path) -> dict[str, Any]:
    """How many oriented label files exist and how many carry any geometry."""

    files = 0
    non_empty = 0
    for split in SPLITS:
        labels_dir = Path(dataset_dir) / split / "labels_obb"
        if not labels_dir.is_dir():
            continue
        for path in labels_dir.iterdir():
            if not path.is_file() or path.suffix.lower() != ".txt":
                continue
            files += 1
            try:
                if path.read_text(encoding="utf-8").strip():
                    non_empty += 1
            except OSError:
                continue
    return {"label_files": files, "non_empty_label_files": non_empty}


def _new_stage_stats() -> dict[str, Any]:
    return {
        "payload_file_count": 0,
        "logical_size_bytes": 0,
        "allocated_size_estimate_bytes": 0,
        "storage": {"hardlinked": 0, "copied": 0, "generated": 0},
        "dependencies": [],
    }


def _dependency_bucket(
    buckets: dict[tuple[str, str], dict[str, Any]],
    *,
    kind: str,
    relative_root: str,
) -> dict[str, Any]:
    key = (kind, relative_root)
    if key not in buckets:
        buckets[key] = {
            "kind": kind,
            "relative_root": relative_root,
            "file_count": 0,
            "size_bytes": 0,
        }
    return buckets[key]


def _link_or_copy_with_stats(source: Path, destination: Path, stats: dict[str, Any]) -> None:
    copy_or_link(source, destination)
    size = int(destination.stat().st_size)
    try:
        linked = os.path.samefile(source, destination)
    except OSError:
        linked = False
    method = "hardlinked" if linked else "copied"
    stats["storage"][method] += 1
    stats["payload_file_count"] += 1
    stats["logical_size_bytes"] += size
    if not linked:
        stats["allocated_size_estimate_bytes"] += size


def _materialize_obb_dataset(
    dataset_dir: Path,
    target_dir: Path,
    *,
    published_root: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    source = Path(dataset_dir)
    target = Path(target_dir)
    class_names = _read_class_names(source)
    stats = _new_stage_stats()
    dependencies: dict[tuple[str, str], dict[str, Any]] = {}

    staged_any = False
    for split in SPLITS:
        images_dir = source / split / "images"
        oriented_dir = source / split / "labels_obb"
        if not images_dir.is_dir():
            continue

        target_images = target / split / "images"
        target_labels = target / split / "labels"
        target_images.mkdir(parents=True, exist_ok=True)
        target_labels.mkdir(parents=True, exist_ok=True)
        images_dependency = _dependency_bucket(
            dependencies,
            kind="image_tree",
            relative_root=f"{split}/images",
        )
        labels_dependency = _dependency_bucket(
            dependencies,
            kind="obb_label_tree",
            relative_root=f"{split}/labels_obb",
        )

        for image_path in sorted(path for path in images_dir.iterdir() if path.is_file()):
            image_size = int(image_path.stat().st_size)
            images_dependency["file_count"] += 1
            images_dependency["size_bytes"] += image_size
            _link_or_copy_with_stats(image_path, target_images / image_path.name, stats)

            oriented = oriented_dir / f"{image_path.stem}.txt"
            destination = target_labels / f"{image_path.stem}.txt"
            if oriented.is_file():
                label_size = int(oriented.stat().st_size)
                labels_dependency["file_count"] += 1
                labels_dependency["size_bytes"] += label_size
                _link_or_copy_with_stats(oriented, destination, stats)
            else:
                # A missing label means a background tile. This file is generated,
                # not recorded as a source dependency.
                destination.write_text("", encoding="utf-8")
                stats["storage"]["generated"] += 1
                stats["payload_file_count"] += 1
            staged_any = True

    if not staged_any:
        raise TrainingDatasetError(f"No images found to stage under {source}")

    data = {
        # Ultralytics resolves a relative path through settings.datasets_dir/CWD,
        # not relative to the yaml file, so cached training inputs use an absolute path.
        # During an atomic cache build files live in ``*.partial`` and are renamed
        # afterwards. Write the final published root into YAML, never the temporary one.
        "path": (published_root or target).resolve().as_posix(),
        "train": "train/images",
        "val": "val/images",
        "test": "test/images",
        "names": {key: value for key, value in sorted(class_names.items())},
    }
    data_yaml = target / "data.yaml"
    with data_yaml.open("w", encoding="utf-8", newline="\n") as handle:
        yaml.safe_dump(data, handle, default_flow_style=False, sort_keys=False, allow_unicode=True)
    yaml_size = int(data_yaml.stat().st_size)
    stats["storage"]["generated"] += 1
    stats["payload_file_count"] += 1
    stats["logical_size_bytes"] += yaml_size
    stats["allocated_size_estimate_bytes"] += yaml_size
    stats["dependencies"] = sorted(
        dependencies.values(),
        key=lambda item: (item["relative_root"], item["kind"]),
    )
    return data_yaml, stats


def stage_obb_dataset(dataset_dir: str | Path, target_dir: str | Path) -> Path:
    """Materialise an OBB tree without persistent reuse (legacy compatibility path)."""

    data_yaml, _stats = _materialize_obb_dataset(Path(dataset_dir), Path(target_dir))
    return data_yaml


def _source_descriptors(dataset_dir: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for name in ("dataset_run_manifest.json", "data_obb.yaml", "data.yaml"):
        path = dataset_dir / name
        if not path.is_file():
            continue
        result.append({
            "kind": "source_file",
            "relative_path": name,
            "size_bytes": int(path.stat().st_size),
            "sha256": _sha256_file(path),
        })
    return result


def training_dataset_cache_key(
    *,
    dataset_run_id: str,
    task: str,
    preprocessing_hash: str | None,
    dataset_input_hash: str | None,
    source_fingerprint: str,
    exporter_version: str = TRAINING_DATASET_EXPORTER_VERSION,
) -> str:
    """Fingerprint the immutable inputs and the staging implementation version."""

    return _canonical_hash({
        "dataset_run_id": dataset_run_id,
        "task": task,
        "preprocessing_hash": preprocessing_hash,
        "dataset_input_hash": dataset_input_hash,
        "source_fingerprint": source_fingerprint,
        "exporter_version": exporter_version,
    })[:32]


def _valid_cache_manifest(
    cache_dir: Path,
    manifest: dict[str, Any],
    expected: dict[str, Any],
) -> bool:
    return bool(
        manifest.get("schema_name") == "geotile_training_dataset_cache"
        and manifest.get("schema_version") == TRAINING_DATASET_CACHE_SCHEMA_VERSION
        and all(manifest.get(key) == value for key, value in expected.items())
        and (cache_dir / "data.yaml").is_file()
        and int(manifest.get("payload_file_count") or 0) > 0
        and isinstance(manifest.get("dependencies"), list)
    )


def resolve_obb_training_dataset(
    *,
    project_id: str,
    dataset_run_id: str,
    dataset_dir: str | Path,
    dataset_input_hash: str | None = None,
    preprocessing_hash: str | None = None,
    fallback_target: str | Path | None = None,
    cache_enabled: bool | None = None,
) -> dict[str, Any]:
    """Resolve OBB inputs, reusing an atomic project cache when the flag is enabled."""

    source = Path(dataset_dir)
    enabled = training_dataset_cache_enabled() if cache_enabled is None else cache_enabled
    if not enabled:
        if fallback_target is None:
            raise TrainingDatasetError("fallback_target is required when the cache is disabled")
        data_yaml = stage_obb_dataset(source, Path(fallback_target))
        return {
            "data_yaml": str(data_yaml),
            "cache_enabled": False,
            "cache_hit": False,
            "cache_key": None,
            "cache_manifest": None,
        }

    descriptors = _source_descriptors(source)
    if not descriptors:
        raise TrainingDatasetError(f"Dataset run has no cacheable manifest/yaml under {source}")
    source_fingerprint = _canonical_hash(descriptors)
    cache_key = training_dataset_cache_key(
        dataset_run_id=dataset_run_id,
        task="obb",
        preprocessing_hash=preprocessing_hash,
        dataset_input_hash=dataset_input_hash,
        source_fingerprint=source_fingerprint,
    )
    root = training_dataset_cache_root(project_id)
    cache_dir = root / cache_key
    manifest_path = cache_dir / "cache_manifest.json"
    expected = {
        "cache_key": cache_key,
        "dataset_run_id": dataset_run_id,
        "task": "obb",
        "preprocessing_hash": preprocessing_hash,
        "dataset_input_hash": dataset_input_hash,
        "source_fingerprint": source_fingerprint,
        "exporter_version": TRAINING_DATASET_EXPORTER_VERSION,
    }
    cache_hit = False

    with interprocess_lock(root / f".{cache_key}.lock", timeout_s=300.0) as acquired:
        if not acquired:
            raise TimeoutError(f"Timed out waiting for training dataset cache {cache_key}")
        cached = read_json(manifest_path, default={}) or {}
        cache_hit = _valid_cache_manifest(cache_dir, cached, expected)
        if cache_hit:
            cached["last_accessed_at"] = utc_now()
            cached["access_count"] = int(cached.get("access_count") or 0) + 1
            write_json_atomic(manifest_path, cached)
        else:
            # Keep the temporary component short enough for non-long-path-aware
            # Windows runtimes; atomic JSON writes add another UUID component below it.
            partial = root / f".{cache_key[:8]}.{uuid.uuid4().hex[:8]}.partial"
            try:
                data_yaml, stats = _materialize_obb_dataset(
                    source,
                    partial,
                    published_root=cache_dir,
                )
                built_at = utc_now()
                manifest = {
                    "schema_name": "geotile_training_dataset_cache",
                    "schema_version": TRAINING_DATASET_CACHE_SCHEMA_VERSION,
                    **expected,
                    "created_at": built_at,
                    "last_accessed_at": built_at,
                    "access_count": 1,
                    "data_yaml": data_yaml.relative_to(partial).as_posix(),
                    "source_dataset_dir": str(source.resolve()),
                    "source_descriptors": descriptors,
                    **stats,
                }
                write_json_atomic(partial / "cache_manifest.json", manifest)
                if cache_dir.exists():
                    shutil.rmtree(cache_dir)
                os.replace(partial, cache_dir)
                cached = manifest
            finally:
                shutil.rmtree(partial, ignore_errors=True)

    return {
        "data_yaml": str(cache_dir / "data.yaml"),
        "cache_enabled": True,
        "cache_hit": cache_hit,
        "cache_key": cache_key,
        "cache_manifest": str(manifest_path),
        "logical_size_bytes": int(cached.get("logical_size_bytes") or 0),
        "allocated_size_estimate_bytes": int(cached.get("allocated_size_estimate_bytes") or 0),
    }


def delete_training_dataset_cache(project_id: str, cache_key: str | None = None) -> dict[str, Any]:
    """Delete one or all rebuildable caches, never anything below ``dataset_runs``."""

    root = training_dataset_cache_root(project_id)
    if cache_key is not None and not _CACHE_KEY_PATTERN.fullmatch(cache_key):
        raise TrainingDatasetError(f"Invalid training dataset cache key: {cache_key}")
    candidates = [root / cache_key] if cache_key else [
        path for path in root.iterdir() if path.is_dir() and _CACHE_KEY_PATTERN.fullmatch(path.name)
    ]
    removed: list[str] = []
    logical_size_bytes = 0
    allocated_size_estimate_bytes = 0
    for candidate in candidates:
        key = candidate.name
        with interprocess_lock(root / f".{key}.lock", timeout_s=60.0) as acquired:
            if not acquired or not candidate.is_dir():
                continue
            manifest = read_json(candidate / "cache_manifest.json", default={}) or {}
            logical_size_bytes += int(manifest.get("logical_size_bytes") or 0)
            allocated_size_estimate_bytes += int(
                manifest.get("allocated_size_estimate_bytes") or 0
            )
            shutil.rmtree(candidate)
            removed.append(key)
    return {
        "removed": removed,
        "removed_count": len(removed),
        "logical_size_bytes": logical_size_bytes,
        "allocated_size_estimate_bytes": allocated_size_estimate_bytes,
    }


def cleanup_training_dataset_cache(
    project_id: str,
    *,
    retention_days: int = 30,
    max_total_bytes: int | None = None,
) -> dict[str, Any]:
    """Remove stale cache entries, then oldest entries until the optional LRU budget fits."""

    root = training_dataset_cache_root(project_id)
    now = datetime.now(timezone.utc)
    entries: list[dict[str, Any]] = []
    for path in root.iterdir():
        if not path.is_dir() or not _CACHE_KEY_PATTERN.fullmatch(path.name):
            continue
        manifest = read_json(path / "cache_manifest.json", default={}) or {}
        raw_accessed = manifest.get("last_accessed_at") or manifest.get("created_at")
        try:
            accessed = datetime.fromisoformat(str(raw_accessed))
            if accessed.tzinfo is None:
                accessed = accessed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            accessed = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        entries.append({
            "key": path.name,
            "accessed": accessed,
            "logical_size_bytes": int(manifest.get("logical_size_bytes") or 0),
            "allocated_size_estimate_bytes": int(
                manifest.get("allocated_size_estimate_bytes")
                if "allocated_size_estimate_bytes" in manifest
                else manifest.get("logical_size_bytes") or 0
            ),
        })

    cutoff_seconds = max(1, int(retention_days)) * 86400
    remove_keys = {
        entry["key"] for entry in entries if (now - entry["accessed"]).total_seconds() > cutoff_seconds
    }
    remaining = [entry for entry in entries if entry["key"] not in remove_keys]
    if max_total_bytes is not None:
        budget = max(0, int(max_total_bytes))
        current = sum(entry["allocated_size_estimate_bytes"] for entry in remaining)
        for entry in sorted(remaining, key=lambda item: item["accessed"]):
            if current <= budget:
                break
            remove_keys.add(entry["key"])
            current -= entry["allocated_size_estimate_bytes"]

    result = {
        "removed": [],
        "removed_count": 0,
        "logical_size_bytes": 0,
        "allocated_size_estimate_bytes": 0,
    }
    for key in sorted(remove_keys):
        deleted = delete_training_dataset_cache(project_id, key)
        result["removed"].extend(deleted["removed"])
        result["removed_count"] += deleted["removed_count"]
        result["logical_size_bytes"] += deleted["logical_size_bytes"]
        result["allocated_size_estimate_bytes"] += deleted[
            "allocated_size_estimate_bytes"
        ]
    result.update({
        "retention_days": retention_days,
        "max_total_bytes": max_total_bytes,
        "entry_count_before": len(entries),
    })
    return result
