"""Model registry: what was trained, on what, and which model is in use.

Two things this exists for:

* **Promotion with history.** Setting a project model today silently overwrites
  ``prediction_config.model_path``. Here every promotion is recorded, so the previous
  model can be named and restored rather than reconstructed from memory.
* **Lineage.** From a model it must be possible to answer "what was this trained on"
  all the way down to who drew the annotations. The chain is
  model → training run → dataset run → tile catalog → source annotations, and the
  catalog's link table already carries ``annotator_email`` per annotation, so the
  answer is exact rather than an approximation from scene membership.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.storage import _atomic_write_bytes, load_json, project_dir, save_json
from services.dataset_runs import dataset_identity_summary, get_dataset_run_manifest, read_publication
from services.training_runs import read_json, training_run_dir

MODEL_REGISTRY_SCHEMA_VERSION = 1
REGISTRY_NAME = "model_registry.json"


class ModelRegistryError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def registry_path(project_id: str) -> Path:
    return project_dir(project_id) / REGISTRY_NAME


def _empty_registry(project_id: str) -> dict[str, Any]:
    return {
        "schema_name": "geotile_model_registry",
        "schema_version": MODEL_REGISTRY_SCHEMA_VERSION,
        "project_id": project_id,
        "current_model_id": None,
        "models": [],
        "promotions": [],
    }


def read_registry(project_id: str) -> dict[str, Any]:
    value = read_json(registry_path(project_id), default=None)
    if not isinstance(value, dict):
        return _empty_registry(project_id)
    for key, default in _empty_registry(project_id).items():
        value.setdefault(key, default)
    return value


def _write_registry(project_id: str, registry: dict[str, Any]) -> None:
    payload = json.dumps(registry, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    _atomic_write_bytes(registry_path(project_id), payload)


def _annotation_mode(project_id: str) -> str | None:
    return ((load_json(project_id, "project", default={}) or {}).get("profile") or {}).get("annotation_mode")


def register_model(project_id: str, training_run_id: str) -> dict[str, Any]:
    """Add a finished training run to the registry.

    Only a run with a manifest qualifies: the manifest is what marks a run as
    actually finished, so anything without one has untrustworthy results.
    """
    run_dir = training_run_dir(project_id, training_run_id)
    manifest = read_json(run_dir / "training_manifest.json", default=None)
    if not isinstance(manifest, dict):
        raise ModelRegistryError(
            "This training run has no manifest — it did not finish, so it cannot be registered."
        )
    checkpoint = run_dir / "weights" / "best.pt"
    if not checkpoint.is_file():
        raise ModelRegistryError(f"Checkpoint is missing: {checkpoint}")

    registry = read_registry(project_id)
    existing = next(
        (item for item in registry["models"] if item.get("training_run_id") == training_run_id),
        None,
    )
    if existing:
        return existing

    dataset_run_id = manifest.get("dataset_run_id")
    publication = read_publication(project_id, dataset_run_id) if dataset_run_id else {}
    metrics = read_json(run_dir / "metrics.json", default={}) or {}
    evaluation = read_json(run_dir / "test_evaluation.json", default=None)

    entry = {
        "model_id": training_run_id,
        "training_run_id": training_run_id,
        "dataset_run_id": dataset_run_id,
        "dataset_label": publication.get("label"),
        "task": manifest.get("task"),
        "base_model": manifest.get("base_model"),
        "checkpoint_path": str(checkpoint),
        "attempt": manifest.get("attempt"),
        "config_fingerprint": manifest.get("config_fingerprint"),
        "validation_metrics": metrics.get("summary") if isinstance(metrics, dict) else None,
        "test_metrics": (evaluation or {}).get("metrics"),
        "registered_at": utc_now(),
        "status": "archived",
    }
    registry["models"].append(entry)
    _write_registry(project_id, registry)
    return entry


def promote_model(project_id: str, model_id: str) -> dict[str, Any]:
    """Make a model the project model, recording what it replaced."""
    registry = read_registry(project_id)
    entry = next((item for item in registry["models"] if item.get("model_id") == model_id), None)
    if entry is None:
        raise ModelRegistryError(f"Model '{model_id}' is not in the registry")
    if not Path(entry["checkpoint_path"]).is_file():
        raise ModelRegistryError(f"Checkpoint no longer exists: {entry['checkpoint_path']}")

    # A detection model in a rotated-geometry project (or the reverse) would predict
    # the wrong geometry type entirely — refuse rather than let it be discovered later.
    mode = _annotation_mode(project_id)
    required = {"bbox": "detect", "rotated_bbox": "obb"}.get(str(mode or ""))
    if required and entry.get("task") != required:
        raise ModelRegistryError(
            f"Model '{model_id}' is a '{entry.get('task')}' model, but this project's "
            f"'{mode}' geometry requires a '{required}' model"
        )

    previous_id = registry.get("current_model_id")
    previous = next((item for item in registry["models"] if item.get("model_id") == previous_id), None)
    config = load_json(project_id, "prediction_config", default={}) or {}

    registry["promotions"].append({
        "promoted_at": utc_now(),
        "model_id": model_id,
        "replaced_model_id": previous_id,
        # The raw previous path is kept as well, so a model promoted before the
        # registry existed can still be restored.
        "replaced_model_path": config.get("model_path") or None,
    })
    for item in registry["models"]:
        item["status"] = "current" if item.get("model_id") == model_id else "archived"
    registry["current_model_id"] = model_id
    _write_registry(project_id, registry)

    config["model_path"] = entry["checkpoint_path"]
    save_json(project_id, "prediction_config", config)

    return {
        "model_id": model_id,
        "checkpoint_path": entry["checkpoint_path"],
        "replaced_model_id": previous_id,
        "replaced_model_path": (previous or {}).get("checkpoint_path") or config.get("model_path"),
    }


def remove_model(project_id: str, training_run_id: str) -> dict[str, Any]:
    """Drop a model entry when its training run is deleted; demote it if it was current.

    Also clears the prediction model_path when it pointed at the removed model, so the
    project never references checkpoint files that are about to disappear.
    """
    registry = read_registry(project_id)
    models = registry.get("models", [])
    removed = [item for item in models if item.get("training_run_id") == training_run_id]
    if not removed:
        return {"removed": 0, "current_model_id": registry.get("current_model_id"), "was_current": False}
    registry["models"] = [item for item in models if item.get("training_run_id") != training_run_id]
    was_current = registry.get("current_model_id") == training_run_id
    if was_current:
        registry["current_model_id"] = None
    _write_registry(project_id, registry)
    if was_current:
        config = load_json(project_id, "prediction_config", default={}) or {}
        if config.get("model_path"):
            config["model_path"] = None
            save_json(project_id, "prediction_config", config)
    return {
        "removed": len(removed),
        "current_model_id": registry.get("current_model_id"),
        "was_current": was_current,
    }


def list_models(project_id: str) -> dict[str, Any]:
    registry = read_registry(project_id)
    return {
        "schema_name": registry["schema_name"],
        "current_model_id": registry.get("current_model_id"),
        "model_count": len(registry["models"]),
        "models": sorted(
            registry["models"],
            key=lambda item: str(item.get("registered_at") or ""),
            reverse=True,
        ),
        "promotions": list(reversed(registry.get("promotions", []))),
    }


def model_lineage(project_id: str, model_id: str) -> dict[str, Any]:
    """Walk model → training run → dataset run → catalog → annotation authors."""
    registry = read_registry(project_id)
    entry = next((item for item in registry["models"] if item.get("model_id") == model_id), None)
    if entry is None:
        raise ModelRegistryError(f"Model '{model_id}' is not in the registry")

    run_dir = training_run_dir(project_id, entry["training_run_id"])
    manifest = read_json(run_dir / "training_manifest.json", default={}) or {}
    dataset_run_id = entry.get("dataset_run_id")
    dataset_manifest = get_dataset_run_manifest(project_id, dataset_run_id) if dataset_run_id else None

    authors: dict[str, int] = {}
    sources: dict[str, int] = {}
    classes: dict[str, int] = {}
    annotation_ids: set[str] = set()
    scenes: set[str] = set()
    catalog_id = None
    truncated = False

    selection = read_json(
        project_dir(project_id) / "dataset_runs" / str(dataset_run_id) / "dataset_selection.json",
        default=None,
    ) if dataset_run_id else None

    if isinstance(selection, dict):
        catalog_id = selection.get("catalog_id")
        used_tile_ids = {
            str(tile.get("tile_id")) for tile in selection.get("used_tiles", []) if tile.get("tile_id")
        }
        scenes = {
            str(tile.get("scene_id")) for tile in selection.get("used_tiles", []) if tile.get("scene_id")
        }
        links_path = (
            project_dir(project_id) / "tile_catalogs" / str(catalog_id) / "tile_annotation_links.parquet"
        )
        if catalog_id and links_path.is_file():
            try:
                from services.tile_catalog import read_parquet_rows

                for link in read_parquet_rows(links_path):
                    if str(link.get("catalog_tile_id")) not in used_tile_ids:
                        continue
                    annotation_id = str(link.get("source_annotation_id") or "")
                    if annotation_id:
                        annotation_ids.add(annotation_id)
                    author = str(link.get("annotator_email") or "unknown")
                    authors[author] = authors.get(author, 0) + 1
                    source = str(link.get("annotation_source") or "manual")
                    sources[source] = sources.get(source, 0) + 1
                    class_name = str(link.get("class_name") or link.get("class_id") or "?")
                    classes[class_name] = classes.get(class_name, 0) + 1
            except Exception:
                # Lineage is reporting, not a gate — a missing or unreadable catalog
                # must degrade to the dataset level rather than fail the request.
                truncated = True
        else:
            truncated = True
    else:
        truncated = True

    return {
        "model_id": model_id,
        "task": entry.get("task"),
        "status": entry.get("status"),
        "training_run": {
            "training_run_id": entry.get("training_run_id"),
            "base_model": manifest.get("base_model"),
            "base_model_sha256": manifest.get("base_model_sha256"),
            "attempt": manifest.get("attempt"),
            "seed": manifest.get("seed"),
            "device": manifest.get("device"),
            "torch": manifest.get("torch"),
            "ultralytics": manifest.get("ultralytics"),
            "completed_at": manifest.get("completed_at"),
        },
        "dataset_run": {
            "dataset_run_id": dataset_run_id,
            "label": entry.get("dataset_label"),
            "input_hash": (dataset_manifest or {}).get("input_hash"),
            "split_mode": ((dataset_manifest or {}).get("dataset_config") or {}).get("split_mode"),
            "split_seed": ((dataset_manifest or {}).get("dataset_config") or {}).get("split_seed"),
            "exists": dataset_manifest is not None,
            "source_identity": manifest.get("dataset_source_identity") or dataset_identity_summary(dataset_manifest),
        },
        "tile_catalog_id": catalog_id,
        "scene_count": len(scenes),
        "annotation_count": len(annotation_ids),
        "authors": dict(sorted(authors.items(), key=lambda item: -item[1])),
        "annotation_sources": dict(sorted(sources.items())),
        "classes": dict(sorted(classes.items(), key=lambda item: -item[1])),
        # True when the chain could not be walked to the annotation level; the answer
        # is then dataset-deep only, and says so instead of implying completeness.
        "truncated": truncated,
    }
