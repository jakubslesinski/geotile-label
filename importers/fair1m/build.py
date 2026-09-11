"""Złożenie kanonicznego projektu GeoTile z FAIR1M — sterowanie writerami backendu.

Nie duplikujemy schematu: scenę/manifest/anotacje zapisujemy przez funkcje aplikacji
(`save_json`, `write_scene_manifest`, `rebuild_scenes_index`), więc wynik jest tym, co
aplikacja realnie otwiera. Rastry są REFERENCJONOWANE (project.scene_folder), nie kopiowane.

Wymaga środowiska backendu (rasterio/GDAL/PROJ). Import backendu jest leniwy — zmienne
środowiskowe (DATA_DIR, SCENES_ROOT, GDAL_DATA, PROJ_LIB) muszą być ustawione PRZED wywołaniem.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

from .parse import collect_class_names, parse_fair1m_xml, to_canonical


def _deterministic_color(name: str) -> str:
    h = hashlib.sha256(name.encode("utf-8")).hexdigest()
    return f"#{h[:6].upper()}"


def _scene_id(stem: str) -> str:
    return hashlib.sha256(f"fair1m:{stem}".encode("utf-8")).hexdigest()[:12]


def build_classes(label_dir: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    names = collect_class_names(label_dir)
    classes = [
        {"id": i, "name": name, "color": _deterministic_color(name), "hotkey": (i + 1) if i < 9 else None}
        for i, name in enumerate(names)
    ]
    lookup = {name: i for i, name in enumerate(names)}
    return classes, lookup


def build_project(
    fair1m_root: str | Path,
    project_location: str | Path,
    *,
    name: str = "FAIR1M",
    limit: int | None = None,
    author_email: str | None = None,
    backend_path: str | Path,
) -> dict[str, Any]:
    fair1m_root = Path(fair1m_root)
    images_dir = fair1m_root / "data" / "images"
    label_dir = fair1m_root / "data" / "labelXmls"
    if not images_dir.is_dir() or not label_dir.is_dir():
        raise FileNotFoundError(f"Nie znaleziono data/images oraz data/labelXmls w {fair1m_root}")

    sys.path.insert(0, str(backend_path))
    from db.storage import (  # noqa: E402
        create_project_root,
        load_json,
        save_json,
        save_scene_json,
    )
    from models.project import Project, default_project_profile  # noqa: E402
    from models.scene import Scene  # noqa: E402
    from services.preprocessing_profiles import ensure_preprocessing_profiles  # noqa: E402
    from services.scene_loader import get_scene_info  # noqa: E402
    from services.scene_manifest import rebuild_scenes_index, write_scene_manifest  # noqa: E402

    # 1) klasy z pełnego zbioru XML → stabilne id niezależnie od --limit
    classes, class_lookup = build_classes(label_dir)

    # 2) profil EO/GEO/OBB (spatial_block_split wynika z georeferencing=GEO)
    profile = default_project_profile(
        modality="EO",
        georeferencing="GEO",
        annotation_mode="rotated_bbox",
    )
    if author_email:
        profile.labeling_author_email = author_email

    project_id = hashlib.sha256(f"fair1m-project:{name}".encode()).hexdigest()[:12]
    root = create_project_root(project_id, name, str(project_location))
    project = Project(
        id=project_id,
        name=name,
        scene_folder=str(images_dir),
        project_root=str(root),
        created_in_appdata=False,
        profile=profile,
        source_type="local_scenes",
    )
    save_json(project_id, "project", project.model_dump())
    save_json(project_id, "classes", classes)
    save_json(project_id, "tiling_config", {"tile_size": 640, "buffer": 0})
    save_json(project_id, "dataset_config", {
        "train_ratio": 0.7, "val_ratio": 0.2, "test_ratio": 0.1,
        "min_box_fraction": 0.3, "negative_ratio": 0.1,
        "split_mode": profile.default_split_strategy, "split_seed": 42,
        "block_size_tiles": 5,
        "preprocessing_profile_id": profile.default_preprocessing_profile,
    })
    ensure_preprocessing_profiles(project_id)
    project_data = load_json(project_id, "project", default={})

    # 3) sceny + anotacje
    tif_paths = sorted(images_dir.glob("*.tif"))
    if limit is not None:
        tif_paths = tif_paths[:limit]

    stats = {
        "scenes": 0, "scenes_no_geo": 0, "annotations": 0,
        "skipped_non_quad": 0, "missing_class": 0, "missing_label_xml": 0,
    }
    for tif_path in tif_paths:
        stem = tif_path.stem
        scene_id = _scene_id(stem)
        info = get_scene_info(tif_path)
        if not info.has_geo:
            stats["scenes_no_geo"] += 1

        scene = Scene(
            id=scene_id,
            filename=tif_path.name,
            display_name=stem,
            modality="EO",
            georeferencing="GEO" if info.has_geo else "NO_GEO",
            raster_kind="direct",
            preparation_status="ready",
            status="pending",
            scene_info=info,
        ).model_dump()
        save_scene_json(project_id, scene_id, "scene", scene)
        write_scene_manifest(project_id, scene_id, project_data, scene, tif_path)

        # anotacje z odpowiadającego XML
        xml_path = label_dir / f"{stem}.xml"
        annotations: list[dict[str, Any]] = []
        if xml_path.is_file():
            raw = parse_fair1m_xml(xml_path)
            for i, obj in enumerate(raw):
                class_id = class_lookup.get(obj["class_name"])
                if class_id is None:
                    stats["missing_class"] += 1
                    continue
                annotations.append(to_canonical(
                    obj, scene_id=scene_id, class_id=class_id,
                    index=i, stem=stem, annotator_email=author_email,
                ))
        else:
            stats["missing_label_xml"] += 1
        save_scene_json(project_id, scene_id, "annotations", annotations)

        # zaktualizuj licznik na scenie
        from db.storage import load_scene_json  # noqa: E402
        scene_now = load_scene_json(project_id, scene_id, "scene", default={})
        scene_now["annotation_count"] = len(annotations)
        save_scene_json(project_id, scene_id, "scene", scene_now)

        stats["scenes"] += 1
        stats["annotations"] += len(annotations)

    # 4) indeks + licznik scen
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
