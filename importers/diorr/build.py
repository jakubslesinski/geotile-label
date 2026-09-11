"""Złożenie kanonicznego projektu GeoTile z DIOR-R — sterowanie writerami backendu.

DIOR-R: JPEG 800×800 **bez geo** + OBB (VOC-XML robndbox). Projekt pixel-frame (NO_GEO,
image_block_split). Obrazy train/val w JPEGImages-trainval, test w JPEGImages-test; adnotacje
OBB w jednym katalogu dla wszystkich id. Rastry referencjonowane (scene_folder = root DIOR-R).

Wymaga środowiska backendu (PIL). Import backendu leniwy; env wcześniej.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

from diorr.parse import collect_class_names, parse_diorr_xml, to_canonical

# split → podkatalog z obrazami
_SPLIT_IMAGE_DIR = {
    "train": "JPEGImages-trainval",
    "val": "JPEGImages-trainval",
    "trainval": "JPEGImages-trainval",
    "test": "JPEGImages-test",
}


def _deterministic_color(name: str) -> str:
    return f"#{hashlib.sha256(name.encode('utf-8')).hexdigest()[:6].upper()}"


def _scene_id(split: str, image_id: str) -> str:
    return hashlib.sha256(f"diorr:{split}/{image_id}".encode("utf-8")).hexdigest()[:12]


def _read_ids(imagesets_dir: Path, split: str) -> list[str]:
    f = imagesets_dir / f"{split}.txt"
    if not f.is_file():
        return []
    return [line.strip() for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]


def _build_classes_from_file(classes_file: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    names = [line.strip() for line in classes_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    classes = [
        {"id": i, "name": n, "color": _deterministic_color(n), "hotkey": (i + 1) if i < 9 else None}
        for i, n in enumerate(names)
    ]
    return classes, {n: i for i, n in enumerate(names)}


def build_project(
    diorr_root: str | Path,
    project_location: str | Path,
    *,
    name: str = "DIOR-R",
    splits: list[str] | None = None,
    classes_file: str | Path | None = None,
    limit: int | None = None,
    author_email: str | None = None,
    backend_path: str | Path,
) -> dict[str, Any]:
    diorr_root = Path(diorr_root)
    splits = splits or ["train", "val"]
    ann_dir = diorr_root / "Annotations" / "Oriented Bounding Boxes"
    imagesets_dir = diorr_root / "ImageSets" / "Main"
    if not ann_dir.is_dir() or not imagesets_dir.is_dir():
        raise FileNotFoundError(f"Brak Annotations/'Oriented Bounding Boxes' lub ImageSets/Main w {diorr_root}")

    sys.path.insert(0, str(backend_path))
    from db.storage import (  # noqa: E402
        create_project_root,
        load_json,
        load_scene_json,
        save_json,
        save_scene_json,
    )
    from models.project import Project, default_project_profile  # noqa: E402
    from models.scene import Scene  # noqa: E402
    from services.preprocessing_profiles import ensure_preprocessing_profiles  # noqa: E402
    from services.scene_loader import get_scene_info  # noqa: E402
    from services.scene_manifest import rebuild_scenes_index, write_scene_manifest  # noqa: E402

    # id-y per split (kolejność stała → deterministyczne --limit)
    ids_by_split = {split: _read_ids(imagesets_dir, split) for split in splits}

    # klasy: z classes.txt (kanoniczna kolejność DIOR) lub zebrane z XML importowanych id
    if classes_file and Path(classes_file).is_file():
        classes, class_lookup = _build_classes_from_file(Path(classes_file))
    else:
        all_ids = [i for split in splits for i in ids_by_split[split]]
        names = collect_class_names(ann_dir, all_ids)
        classes = [
            {"id": i, "name": n, "color": _deterministic_color(n), "hotkey": (i + 1) if i < 9 else None}
            for i, n in enumerate(names)
        ]
        class_lookup = {n: i for i, n in enumerate(names)}

    profile = default_project_profile(
        modality="EO",
        georeferencing="NO_GEO",
        annotation_mode="rotated_bbox",
    )
    if author_email:
        profile.labeling_author_email = author_email

    project_id = hashlib.sha256(f"diorr-project:{name}".encode()).hexdigest()[:12]
    root = create_project_root(project_id, name, str(project_location))
    project = Project(
        id=project_id,
        name=name,
        scene_folder=str(diorr_root),
        project_root=str(root),
        created_in_appdata=False,
        profile=profile,
        source_type="local_scenes",
    )
    save_json(project_id, "project", project.model_dump())
    save_json(project_id, "classes", classes)
    save_json(project_id, "tiling_config", {"tile_size": 800, "buffer": 0})
    save_json(project_id, "dataset_config", {
        "train_ratio": 0.7, "val_ratio": 0.2, "test_ratio": 0.1,
        "min_box_fraction": 0.3, "negative_ratio": 0.1,
        "split_mode": profile.default_split_strategy, "split_seed": 42,
        "block_size_tiles": 5,
        "preprocessing_profile_id": profile.default_preprocessing_profile,
    })
    ensure_preprocessing_profiles(project_id)
    project_data = load_json(project_id, "project", default={})

    stats = {"scenes": 0, "annotations": 0, "missing_class": 0, "missing_image": 0, "missing_xml": 0}
    split_map: dict[str, dict[str, str]] = {}
    for split in splits:
        image_subdir = _SPLIT_IMAGE_DIR.get(split)
        if image_subdir is None:
            continue
        ids = ids_by_split[split]
        if limit is not None:
            ids = ids[:limit]
        for image_id in ids:
            img_rel = f"{image_subdir}/{image_id}.jpg"
            img_path = diorr_root / img_rel
            if not img_path.is_file():
                stats["missing_image"] += 1
                continue
            scene_id = _scene_id(split, image_id)
            info = get_scene_info(img_path)
            scene = Scene(
                id=scene_id,
                filename=img_rel,
                display_name=f"{split}/{image_id}",
                modality="EO",
                georeferencing="NO_GEO",
                raster_kind="direct",
                preparation_status="ready",
                status="pending",
                scene_info=info,
            ).model_dump()
            save_scene_json(project_id, scene_id, "scene", scene)
            write_scene_manifest(project_id, scene_id, project_data, scene, img_path)

            xml_path = ann_dir / f"{image_id}.xml"
            annotations: list[dict[str, Any]] = []
            if xml_path.is_file():
                for i, obj in enumerate(parse_diorr_xml(xml_path)):
                    class_id = class_lookup.get(obj["class_name"])
                    if class_id is None:
                        stats["missing_class"] += 1
                        continue
                    annotations.append(to_canonical(
                        obj, scene_id=scene_id, class_id=class_id,
                        index=i, stem=image_id, annotator_email=author_email,
                    ))
            else:
                stats["missing_xml"] += 1
            save_scene_json(project_id, scene_id, "annotations", annotations)

            scene_now = load_scene_json(project_id, scene_id, "scene", default={})
            scene_now["annotation_count"] = len(annotations)
            save_scene_json(project_id, scene_id, "scene", scene_now)

            split_map[scene_id] = {"split": split, "image_id": image_id}
            stats["scenes"] += 1
            stats["annotations"] += len(annotations)

    save_json(project_id, "benchmark_import", {
        "benchmark": "DIOR-R", "splits": splits, "scene_split": split_map,
    })
    rebuild_scenes_index(project_id)
    project_data = load_json(project_id, "project", default={})
    project_data["scene_count"] = stats["scenes"]
    save_json(project_id, "project", project_data)

    return {"project_id": project_id, "project_root": str(root), "class_count": len(classes), **stats}
