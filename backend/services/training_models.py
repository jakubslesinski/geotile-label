"""Offline registry of base training weights, backed by a curated allowlist.

Training starts from base weights. `ultralytics` **downloads** them from the network
on first use, which breaks the application's offline invariant — so the weights must
sit locally in ``MODELS_ROOT/base/``, and an architecture without a file is
**unavailable with a stated reason**, never fetched.

The registry is an **allowlist**: only known architectures are recognised, by file
name. Dropping an arbitrary `.pt` into the directory does not make it a base model.
That narrowing is deliberate — `torch.load` on an unknown file executes pickle code.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

MODELS_ROOT = Path(
    os.environ.get(
        "MODELS_ROOT",
        str(Path(__file__).resolve().parent.parent.parent / "data" / "models"),
    )
)

TrainingTask = Literal["detect", "obb"]

# Curated catalog. File names match what ultralytics uses, so weights prepared by the
# developer script land here without renaming.
#
# Two kinds of entry:
#   * ``pretrained=True``  -> a ``.pt`` checkpoint that must sit in MODELS_ROOT/base/;
#   * ``pretrained=False`` -> a ``.yaml`` architecture config bundled inside the
#     ultralytics package, trained from random initialisation. No file to prepare and
#     nothing to download, so such an entry is always usable.
#
# Scratch entries exist because some architectures ship a config but **no published
# weights** — YOLO12-OBB is the case in point. Offering it as scratch-only is honest;
# listing it as pretrained would produce an entry that resolves and then fails to load.
#
# ``params_m`` is measured from the bundled config at nc=80, not copied from marketing
# tables, so the numbers stay consistent across families.
BASE_MODEL_CATALOG: tuple[dict[str, Any], ...] = (
    # YOLO11 — the stable baseline.
    {"file": "yolo11n.pt", "name": "YOLO11n", "task": "detect", "params_m": 2.6, "pretrained": True, "min_ultralytics": "8.3"},
    {"file": "yolo11s.pt", "name": "YOLO11s", "task": "detect", "params_m": 9.4, "pretrained": True, "min_ultralytics": "8.3"},
    {"file": "yolo11m.pt", "name": "YOLO11m", "task": "detect", "params_m": 20.1, "pretrained": True, "min_ultralytics": "8.3"},
    {"file": "yolo11n-obb.pt", "name": "YOLO11n-OBB", "task": "obb", "params_m": 2.7, "pretrained": True, "min_ultralytics": "8.3"},
    {"file": "yolo11s-obb.pt", "name": "YOLO11s-OBB", "task": "obb", "params_m": 9.7, "pretrained": True, "min_ultralytics": "8.3"},
    {"file": "yolo11m-obb.pt", "name": "YOLO11m-OBB", "task": "obb", "params_m": 20.9, "pretrained": True, "min_ultralytics": "8.3"},
    # YOLOv8 — kept for comparison against earlier results.
    {"file": "yolov8n.pt", "name": "YOLOv8n", "task": "detect", "params_m": 3.2, "pretrained": True, "min_ultralytics": "8.0"},
    {"file": "yolov8s.pt", "name": "YOLOv8s", "task": "detect", "params_m": 11.2, "pretrained": True, "min_ultralytics": "8.0"},
    {"file": "yolov8m.pt", "name": "YOLOv8m", "task": "detect", "params_m": 25.9, "pretrained": True, "min_ultralytics": "8.0"},
    {"file": "yolov8n-obb.pt", "name": "YOLOv8n-OBB", "task": "obb", "params_m": 3.2, "pretrained": True, "min_ultralytics": "8.0"},
    {"file": "yolov8s-obb.pt", "name": "YOLOv8s-OBB", "task": "obb", "params_m": 11.5, "pretrained": True, "min_ultralytics": "8.0"},
    {"file": "yolov8m-obb.pt", "name": "YOLOv8m-OBB", "task": "obb", "params_m": 26.5, "pretrained": True, "min_ultralytics": "8.0"},
    # YOLOv10 — NMS-free, detection only; upstream publishes no OBB variant.
    {"file": "yolov10n.pt", "name": "YOLOv10n", "task": "detect", "params_m": 2.8, "pretrained": True, "min_ultralytics": "8.2"},
    {"file": "yolov10s.pt", "name": "YOLOv10s", "task": "detect", "params_m": 8.1, "pretrained": True, "min_ultralytics": "8.2"},
    {"file": "yolov10m.pt", "name": "YOLOv10m", "task": "detect", "params_m": 16.6, "pretrained": True, "min_ultralytics": "8.2"},
    # YOLO12 — detection has published weights; OBB has a config but none, so it is
    # offered from scratch only.
    {"file": "yolo12n.pt", "name": "YOLO12n", "task": "detect", "params_m": 2.6, "pretrained": True, "min_ultralytics": "8.3"},
    {"file": "yolo12s.pt", "name": "YOLO12s", "task": "detect", "params_m": 9.3, "pretrained": True, "min_ultralytics": "8.3"},
    {"file": "yolo12m.pt", "name": "YOLO12m", "task": "detect", "params_m": 20.2, "pretrained": True, "min_ultralytics": "8.3"},
    {"file": "yolo12n-obb.yaml", "name": "YOLO12n-OBB", "task": "obb", "params_m": 2.7, "pretrained": False, "min_ultralytics": "8.3"},
    {"file": "yolo12s-obb.yaml", "name": "YOLO12s-OBB", "task": "obb", "params_m": 9.6, "pretrained": False, "min_ultralytics": "8.3"},
    {"file": "yolo12m-obb.yaml", "name": "YOLO12m-OBB", "task": "obb", "params_m": 21.0, "pretrained": False, "min_ultralytics": "8.3"},
    # YOLO26 — newest family, both tasks.
    {"file": "yolo26n.pt", "name": "YOLO26n", "task": "detect", "params_m": 2.6, "pretrained": True, "min_ultralytics": "8.4"},
    {"file": "yolo26s.pt", "name": "YOLO26s", "task": "detect", "params_m": 10.0, "pretrained": True, "min_ultralytics": "8.4"},
    {"file": "yolo26m.pt", "name": "YOLO26m", "task": "detect", "params_m": 21.9, "pretrained": True, "min_ultralytics": "8.4"},
    {"file": "yolo26n-obb.pt", "name": "YOLO26n-OBB", "task": "obb", "params_m": 2.7, "pretrained": True, "min_ultralytics": "8.4"},
    {"file": "yolo26s-obb.pt", "name": "YOLO26s-OBB", "task": "obb", "params_m": 10.6, "pretrained": True, "min_ultralytics": "8.4"},
    {"file": "yolo26m-obb.pt", "name": "YOLO26m-OBB", "task": "obb", "params_m": 23.6, "pretrained": True, "min_ultralytics": "8.4"},
)

# Ultralytics weights ship under AGPL-3.0 (or an Ultralytics commercial licence).
# Recorded explicitly because a model trained from them inherits the licence
# obligations, and that has to be known before shipping a product.
BASE_MODEL_LICENSE = "AGPL-3.0 (Ultralytics); commercial use requires an Ultralytics Enterprise licence"

# Scratch training uses no pretrained weights, but the training code is still
# ultralytics — AGPL-3.0 applies to the pipeline regardless of how the model started.
BASE_MODEL_LICENSE_SCRATCH = (
    "No pretrained weights; trained with Ultralytics code under AGPL-3.0, "
    "so commercial use still requires an Ultralytics Enterprise licence"
)

ULTRALYTICS_MIN_VERSION = "8.3"

# Project geometry mode -> required model task.
ANNOTATION_MODE_TASKS: dict[str, TrainingTask] = {
    "bbox": "detect",
    "rotated_bbox": "obb",
}

_HASH_CACHE_NAME = "base_models_index.json"


class TrainingModelError(ValueError):
    pass


def base_models_dir() -> Path:
    return MODELS_ROOT / "base"


def _read_hash_cache(directory: Path) -> dict[str, Any]:
    path = directory / _HASH_CACHE_NAME
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_hash_cache(directory: Path, cache: dict[str, Any]) -> None:
    try:
        (directory / _HASH_CACHE_NAME).write_text(
            json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8"
        )
    except OSError:
        # The cache is a convenience, not a source of truth — a failed write must not
        # break listing.
        pass


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_cached(path: Path, cache: dict[str, Any]) -> tuple[str, bool]:
    """Return (sha256, cache_changed). The key covers size and mtime."""
    stat = path.stat()
    entry = cache.get(path.name)
    if (
        isinstance(entry, dict)
        and entry.get("size") == stat.st_size
        and entry.get("mtime") == int(stat.st_mtime)
        and isinstance(entry.get("sha256"), str)
    ):
        return entry["sha256"], False
    digest = _file_sha256(path)
    cache[path.name] = {
        "size": stat.st_size,
        "mtime": int(stat.st_mtime),
        "sha256": digest,
    }
    return digest, True


def list_base_models(annotation_mode: str | None = None) -> dict[str, Any]:
    """Catalog of architectures together with local weight availability."""
    directory = base_models_dir()
    directory.mkdir(parents=True, exist_ok=True)
    cache = _read_hash_cache(directory)
    cache_changed = False
    required_task = ANNOTATION_MODE_TASKS.get(str(annotation_mode or ""))

    models: list[dict[str, Any]] = []
    for entry in BASE_MODEL_CATALOG:
        pretrained = bool(entry.get("pretrained", True))
        path = directory / str(entry["file"])
        # A scratch entry is an architecture config bundled with ultralytics, so there
        # is nothing local to find and nothing to hash.
        present = path.is_file() if pretrained else False
        sha256: str | None = None
        size: int | None = None
        if present:
            size = path.stat().st_size
            sha256, changed = _sha256_cached(path, cache)
            cache_changed = cache_changed or changed

        reasons: list[str] = []
        if pretrained and not present:
            reasons.append(f"Weights are missing locally: {path}")
        if required_task and entry["task"] != required_task:
            reasons.append(
                f"A '{entry['task']}' model does not match the project geometry "
                f"('{annotation_mode}' requires '{required_task}')"
            )

        if pretrained:
            resolved_path = str(path) if present else None
        else:
            # ultralytics resolves a bare config name from its bundled cfg/models.
            resolved_path = str(entry["file"])

        models.append({
            **entry,
            "pretrained": pretrained,
            "path": resolved_path,
            "present": present,
            "size": size,
            "sha256": sha256,
            "license": BASE_MODEL_LICENSE if pretrained else BASE_MODEL_LICENSE_SCRATCH,
            "ultralytics_min_version": str(
                entry.get("min_ultralytics", ULTRALYTICS_MIN_VERSION)
            ),
            "available": not reasons,
            "reason": "; ".join(reasons) if reasons else None,
            "recommended": (
                entry["name"] in {"YOLO11n", "YOLO11s", "YOLO11n-OBB", "YOLO11s-OBB"}
                and not reasons
                and pretrained
            ),
            "estimated_vram_gb": (
                4 if float(entry["params_m"]) < 5
                else 6 if float(entry["params_m"]) < 15
                else 10
            ),
        })

    if cache_changed:
        _write_hash_cache(directory, cache)

    return {
        "schema_name": "geotile_training_base_models",
        "schema_version": 1,
        "directory": str(directory),
        "annotation_mode": annotation_mode,
        "required_task": required_task,
        "available_count": sum(1 for item in models if item["available"]),
        "models": models,
    }


def required_task_for(annotation_mode: str | None) -> TrainingTask:
    """Model task required by the project geometry mode."""
    task = ANNOTATION_MODE_TASKS.get(str(annotation_mode or ""))
    if task is None:
        raise TrainingModelError(
            f"Training is not supported for geometry mode '{annotation_mode}'"
        )
    return task


def resolve_base_model(file_name: str, annotation_mode: str | None = None) -> dict[str, Any]:
    """Resolve a catalog entry into a ready-to-use base model.

    Refuses **before** training starts, instead of letting ultralytics reach for the
    network or failing halfway through the first epoch.
    """
    name = Path(str(file_name)).name
    entry = next((item for item in BASE_MODEL_CATALOG if item["file"] == name), None)
    if entry is None:
        known = ", ".join(item["file"] for item in BASE_MODEL_CATALOG)
        raise TrainingModelError(
            f"'{name}' is not a known base architecture. Available: {known}"
        )

    if annotation_mode is not None:
        required = required_task_for(annotation_mode)
        if entry["task"] != required:
            raise TrainingModelError(
                f"Model '{name}' is a '{entry['task']}' model, but a project with "
                f"'{annotation_mode}' geometry requires a '{required}' model"
            )

    min_version = str(entry.get("min_ultralytics", ULTRALYTICS_MIN_VERSION))

    if not bool(entry.get("pretrained", True)):
        # Scratch: the config ships inside ultralytics, so there is no local file to
        # check and no checkpoint to hash. The bare name is what the worker passes on.
        return {
            **entry,
            "pretrained": False,
            "path": name,
            "sha256": None,
            "size": None,
            "license": BASE_MODEL_LICENSE_SCRATCH,
            "ultralytics_min_version": min_version,
        }

    directory = base_models_dir()
    path = directory / name
    if not path.is_file():
        raise TrainingModelError(
            f"Weights '{name}' are missing in {directory}. Prepare them with "
            f"scripts/fetch-base-models.ps1 and copy them into that directory — the "
            f"application never downloads weights by itself."
        )

    cache = _read_hash_cache(directory)
    sha256, changed = _sha256_cached(path, cache)
    if changed:
        _write_hash_cache(directory, cache)

    return {
        **entry,
        "pretrained": True,
        "path": str(path),
        "sha256": sha256,
        "size": path.stat().st_size,
        "license": BASE_MODEL_LICENSE,
        "ultralytics_min_version": min_version,
    }
