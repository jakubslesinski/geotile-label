"""Pre-start checks for a training run.

Everything here is cheap and runs *before* a worker is spawned. The point is to fail
in the first second with a readable reason instead of forty minutes into an epoch —
or worse, to produce a run that silently trained on the wrong labels.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from services.training_resources import IMAGE_SUFFIXES, build_training_resource_recommendation

# Rough floor for a training run: dataset images are already on disk, this covers
# checkpoints, cached batches and ultralytics scratch space.
MIN_FREE_DISK_GB = 5.0


def _check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _device_checks(requested_device: str) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    try:
        import torch
    except Exception as exc:  # pragma: no cover - torch missing is its own failure
        checks.append(_check("torch", "error", f"PyTorch is unavailable: {exc}"))
        return checks

    checks.append(_check("torch", "ok", f"PyTorch {torch.__version__}"))

    wants_cuda = requested_device != "cpu"
    if not wants_cuda:
        checks.append(_check(
            "device",
            "warning",
            "Training on CPU. On a realistic dataset this takes hours to days.",
        ))
        return checks

    if not torch.cuda.is_available():
        checks.append(_check(
            "device",
            "error",
            "CUDA is not available. Install the GPU training pack "
            "(Settings -> GPU training pack) and restart the application.",
        ))
        return checks

    try:
        name = torch.cuda.get_device_name(0)
        free_bytes, total_bytes = torch.cuda.mem_get_info()
        free_gb = free_bytes / (1024 ** 3)
        total_gb = total_bytes / (1024 ** 3)
        checks.append(_check("device", "ok", f"{name}, {total_gb:.1f} GB VRAM"))
        # Free VRAM matters more than the total: a browser or another training run
        # can leave too little to start with, and the failure would look like a bug.
        if free_gb < 2.0:
            checks.append(_check(
                "vram",
                "error",
                f"Only {free_gb:.1f} GB VRAM free. Close other GPU applications.",
            ))
        elif free_gb < 4.0:
            checks.append(_check(
                "vram",
                "warning",
                f"{free_gb:.1f} GB VRAM free — use a small batch or a smaller model.",
            ))
        else:
            checks.append(_check("vram", "ok", f"{free_gb:.1f} GB VRAM free"))
    except Exception as exc:
        checks.append(_check("device", "warning", f"Could not read GPU details: {exc}"))
    return checks


def _dataset_checks(dataset_dir: Path, task: str, project_classes: list[str]) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []

    data_yaml = dataset_dir / ("data_obb.yaml" if task == "obb" else "data.yaml")
    if not data_yaml.is_file():
        checks.append(_check("data.yaml", "error", f"Missing {data_yaml.name} in {dataset_dir}"))
        return checks
    checks.append(_check("data.yaml", "ok", str(data_yaml)))

    names: list[str] = []
    try:
        import yaml

        parsed = yaml.safe_load(data_yaml.read_text(encoding="utf-8")) or {}
        raw_names = parsed.get("names")
        if isinstance(raw_names, dict):
            names = [str(value) for _, value in sorted(raw_names.items())]
        elif isinstance(raw_names, list):
            names = [str(value) for value in raw_names]
    except Exception as exc:
        checks.append(_check("data.yaml", "error", f"Cannot parse {data_yaml.name}: {exc}"))
        return checks

    if not names:
        checks.append(_check("classes", "error", "data.yaml declares no classes"))
    elif project_classes and names != project_classes:
        checks.append(_check(
            "classes",
            "error",
            f"Dataset classes {names} do not match project classes {project_classes}",
        ))
    else:
        checks.append(_check("classes", "ok", f"{len(names)} class(es): {', '.join(names)}"))

    label_dir_name = "labels_obb" if task == "obb" else "labels"
    for split in ("train", "val", "test"):
        images = dataset_dir / split / "images"
        labels = dataset_dir / split / label_dir_name
        if not images.is_dir():
            level = "error" if split in ("train", "val") else "warning"
            checks.append(_check(f"split:{split}", level, f"Missing {images}"))
            continue
        image_count = sum(
            1
            for path in images.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        )
        # Export provenance may add sidecars next to YOLO labels.  Only `.txt` files
        # are labels consumed by Ultralytics; counting every file doubled the number
        # shown by preflight on xView3.
        label_count = sum(
            1
            for path in labels.iterdir()
            if path.is_file() and path.suffix.lower() == ".txt"
        ) if labels.is_dir() else 0
        if image_count == 0:
            level = "error" if split in ("train", "val") else "warning"
            checks.append(_check(f"split:{split}", level, f"{images} contains no images"))
        else:
            checks.append(_check(
                f"split:{split}",
                "ok",
                f"{image_count} image(s), {label_count} label file(s)",
            ))

    if task == "obb":
        # ultralytics derives label paths from image paths by swapping /images/ for
        # /labels/, so oriented labels in labels_obb/ are not picked up as-is. The
        # worker stages an OBB-shaped copy; what has to be verified here is that
        # there is anything oriented to stage, because an empty staging would train
        # on nothing but background without ever raising.
        from services.training_dataset import obb_label_stats

        stats = obb_label_stats(dataset_dir)
        if stats["label_files"] == 0:
            checks.append(_check(
                "obb-labels",
                "error",
                "No labels_obb/ files in the dataset run. Regenerate the dataset run — a "
                "rotated-bbox project writes labels_obb/ automatically (older runs predate "
                "this and need regenerating).",
            ))
        elif stats["non_empty_label_files"] == 0:
            checks.append(_check(
                "obb-labels",
                "error",
                f"All {stats['label_files']} oriented label files are empty — there is "
                "nothing to learn from.",
            ))
        else:
            checks.append(_check(
                "obb-labels",
                "ok",
                f"{stats['non_empty_label_files']}/{stats['label_files']} oriented "
                "label files carry geometry; the worker stages them as labels/",
            ))

    return checks


def run_preflight(
    *,
    dataset_dir: Path,
    task: str,
    requested_device: str,
    project_classes: list[str],
    audit_readiness: str | None = None,
    imgsz: int = 640,
    batch: int = -1,
    advanced_options: dict[str, Any] | None = None,
    estimated_vram_gb: float | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    checks.extend(_device_checks(requested_device))
    checks.extend(_dataset_checks(Path(dataset_dir), task, project_classes))

    try:
        free_gb = shutil.disk_usage(dataset_dir).free / (1024 ** 3)
        level = "ok" if free_gb >= MIN_FREE_DISK_GB else "error"
        checks.append(_check("disk", level, f"{free_gb:.1f} GB free"))
    except Exception as exc:
        checks.append(_check("disk", "warning", f"Could not read free space: {exc}"))

    if audit_readiness == "not_ready":
        checks.append(_check(
            "dataset-audit",
            "warning",
            "The dataset audit reports errors. Training will run, but the result "
            "inherits those problems.",
        ))
    elif audit_readiness:
        checks.append(_check("dataset-audit", "ok", f"readiness: {audit_readiness}"))

    resource_recommendation: dict[str, Any] | None = None
    try:
        resource_recommendation = build_training_resource_recommendation(
            dataset_dir=Path(dataset_dir),
            requested_device=requested_device,
            imgsz=imgsz,
            current_batch=batch,
            advanced_options=advanced_options,
            estimated_vram_gb=estimated_vram_gb,
        )
        loader = resource_recommendation["loader_benchmark"]
        if loader.get("sample_count") and not loader.get("decoded_count"):
            checks.append(_check(
                "loader-benchmark",
                "error",
                "The bounded loader probe could not decode any sampled training image.",
            ))
        elif loader.get("failure_count"):
            checks.append(_check(
                "loader-benchmark",
                "warning",
                f"Decoded {loader.get('decoded_count', 0)}/{loader.get('sample_count', 0)} "
                "sampled images; inspect the reported failures before a final run.",
            ))
        else:
            checks.append(_check(
                "loader-benchmark",
                "ok",
                f"{loader.get('decoded_count', 0)} sampled image(s), "
                f"{loader.get('images_per_second') or 0:.1f} images/s, "
                f"{loader.get('read_mib_per_second') or 0:.1f} MiB/s read.",
            ))

        memory = resource_recommendation["memory_safety"]
        if (
            resource_recommendation["current_options"].get("cache") == "ram"
            and not memory.get("current_ram_cache_safe", memory.get("ram_cache_safe"))
        ):
            checks.append(_check(
                "cache-memory",
                "error",
                "cache=ram would violate the training memory headroom. Disable it or "
                "use the recommended configuration.",
            ))
        else:
            cache = resource_recommendation["recommended_options"].get("cache")
            checks.append(_check(
                "resource-config",
                "ok",
                f"Recommended: batch={resource_recommendation['recommended_options']['batch']}, "
                f"workers={resource_recommendation['recommended_options']['workers']}, "
                f"cache={str(cache).lower()}.",
            ))
    except Exception as exc:  # noqa: BLE001 - correctness checks still remain useful
        checks.append(_check(
            "loader-benchmark",
            "warning",
            f"Could not benchmark the input loader: {type(exc).__name__}: {exc}",
        ))

    errors = [item for item in checks if item["status"] == "error"]
    warnings = [item for item in checks if item["status"] == "warning"]
    return {
        "can_start": not errors,
        "checks": checks,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "resource_recommendation": resource_recommendation,
        # A trustworthy ETA needs the GPU, image size, batch and dataset size; a wrong
        # one is worse than none, so only a coarse band is offered up front and the
        # real estimate comes from measuring the first epoch.
        "duration_hint": "hours" if requested_device != "cpu" else "hours to days",
    }
