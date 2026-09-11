"""Złożenie kanonicznego projektu GeoTile z DOTA — sterowanie writerami backendu.

DOTA to duże PNG **bez georeferencji** (do 20000×20000) + OBB w pikselach. Projekt jest
pixel-frame (SceneGeoModel none): georeferencing=NO_GEO, split=image_block_split. Rastry
REFERENCJONOWANE (project.scene_folder = root dota-v2), nie kopiowane. Kafelkowanie dużych
scen robi aplikacja przy budowie datasetu.

Wymaga środowiska backendu (PIL/rasterio). Import backendu leniwy; env musi być ustawione wcześniej.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

from dota.parse import collect_class_names, parse_dota_txt, to_canonical


def _deterministic_color(name: str) -> str:
    return f"#{hashlib.sha256(name.encode('utf-8')).hexdigest()[:6].upper()}"


def _scene_id(split: str, stem: str) -> str:
    return hashlib.sha256(f"dota:{split}/{stem}".encode("utf-8")).hexdigest()[:12]


def build_classes(annotation_dirs: list[Path]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    names = collect_class_names(annotation_dirs)
    classes = [
        {"id": i, "name": name, "color": _deterministic_color(name), "hotkey": (i + 1) if i < 9 else None}
        for i, name in enumerate(names)
    ]
    return classes, {name: i for i, name in enumerate(names)}


def build_project(
    dota_root: str | Path,
    project_location: str | Path,
    *,
    name: str = "DOTA",
    splits: list[str] | None = None,
    version: str = "version2.0",
    limit: int | None = None,
    author_email: str | None = None,
    backend_path: str | Path,
) -> dict[str, Any]:
    dota_root = Path(dota_root)
    splits = splits or ["train", "val"]

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

    # DOTA PNG bywają > limitu bomby dekompresji PIL — ufamy własnym plikom.
    from PIL import Image  # noqa: E402
    Image.MAX_IMAGE_PIXELS = None

    # 1) klasy z pełnego zbioru adnotacji train+val (stabilne id niezależnie od --limit)
    ann_dirs = [dota_root / split / "annotations" / version for split in splits]
    ann_dirs = [d for d in ann_dirs if d.is_dir()]
    classes, class_lookup = build_classes(ann_dirs)

    # 2) profil EO / NO_GEO / OBB (split=image_block_split wynika z NO_GEO)
    profile = default_project_profile(
        modality="EO",
        georeferencing="NO_GEO",
        annotation_mode="rotated_bbox",
    )
    if author_email:
        profile.labeling_author_email = author_email

    project_id = hashlib.sha256(f"dota-project:{name}".encode()).hexdigest()[:12]
    root = create_project_root(project_id, name, str(project_location))
    project = Project(
        id=project_id,
        name=name,
        scene_folder=str(dota_root),
        project_root=str(root),
        created_in_appdata=False,
        profile=profile,
        source_type="local_scenes",
    )
    save_json(project_id, "project", project.model_dump())
    save_json(project_id, "classes", classes)
    save_json(project_id, "tiling_config", {"tile_size": 1024, "buffer": 200})
    save_json(project_id, "dataset_config", {
        "train_ratio": 0.7, "val_ratio": 0.2, "test_ratio": 0.1,
        "min_box_fraction": 0.3, "negative_ratio": 0.1,
        "split_mode": profile.default_split_strategy, "split_seed": 42,
        "block_size_tiles": 5,
        "preprocessing_profile_id": profile.default_preprocessing_profile,
    })
    ensure_preprocessing_profiles(project_id)
    project_data = load_json(project_id, "project", default={})

    # 3) sceny + anotacje, po splitach (oryginalny podział DOTA zapisujemy w sidecarze)
    stats = {"scenes": 0, "annotations": 0, "missing_class": 0, "missing_label": 0}
    split_map: dict[str, dict[str, str]] = {}
    for split in splits:
        images_dir = dota_root / split / "images"
        ann_dir = dota_root / split / "annotations" / version
        if not images_dir.is_dir():
            continue
        pngs = sorted(images_dir.glob("*.png"))
        if limit is not None:  # --limit działa per split
            pngs = pngs[:limit]
        for png in pngs:
            stem = png.stem
            scene_id = _scene_id(split, stem)
            info = get_scene_info(png)
            scene = Scene(
                id=scene_id,
                filename=f"{split}/images/{png.name}",  # względny podpath (multi-split)
                display_name=f"{split}/{stem}",
                modality="EO",
                georeferencing="NO_GEO",
                raster_kind="direct",
                preparation_status="ready",
                status="pending",
                scene_info=info,
            ).model_dump()
            save_scene_json(project_id, scene_id, "scene", scene)
            write_scene_manifest(project_id, scene_id, project_data, scene, png)

            txt_path = ann_dir / f"{stem}.txt"
            annotations: list[dict[str, Any]] = []
            if txt_path.is_file():
                for i, obj in enumerate(parse_dota_txt(txt_path)):
                    class_id = class_lookup.get(obj["class_name"])
                    if class_id is None:
                        stats["missing_class"] += 1
                        continue
                    annotations.append(to_canonical(
                        obj, scene_id=scene_id, class_id=class_id,
                        index=i, stem=stem, annotator_email=author_email,
                    ))
            else:
                stats["missing_label"] += 1
            save_scene_json(project_id, scene_id, "annotations", annotations)

            scene_now = load_scene_json(project_id, scene_id, "scene", default={})
            scene_now["annotation_count"] = len(annotations)
            save_scene_json(project_id, scene_id, "scene", scene_now)

            split_map[scene_id] = {"split": split, "original_name": png.name}
            stats["scenes"] += 1
            stats["annotations"] += len(annotations)

    # 4) sidecar z oryginalnym podziałem DOTA (pod v13/v06 — split urzędowy),
    #    indeks scen i licznik
    save_json(project_id, "benchmark_import", {
        "benchmark": "DOTA", "version": version, "splits": splits,
        "scene_split": split_map,
    })
    rebuild_scenes_index(project_id)
    project_data = load_json(project_id, "project", default={})
    project_data["scene_count"] = stats["scenes"]
    save_json(project_id, "project", project_data)

    return {
        "project_id": project_id,
        "project_root": str(root),
        "class_count": len(classes),
        **stats,
    }
