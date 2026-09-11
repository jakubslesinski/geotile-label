param(
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

if (-not $PythonPath) {
    $PackedPython = Join-Path $RepoRoot ".desktop-build\backend-env\python.exe"
    if (Test-Path $PackedPython) {
        $PythonPath = $PackedPython
    } else {
        $PythonPath = "C:\ProgramData\anaconda3\python.exe"
    }
}

$ResolvedPython = (Get-Command $PythonPath -ErrorAction Stop).Source
$EnvironmentRoot = Split-Path -Parent $ResolvedPython
$PreviousGdalDriverPath = $env:GDAL_DRIVER_PATH
$PreviousGdalData = $env:GDAL_DATA
$PreviousProjData = $env:PROJ_DATA
$PreviousProjLib = $env:PROJ_LIB
$PreviousPath = $env:PATH
$PreviousYolo = $env:GEOTILE_ENABLE_YOLO
$GdalPluginDir = Join-Path $EnvironmentRoot "Library\lib\gdalplugins"
$GdalDataDir = Join-Path $EnvironmentRoot "Library\share\gdal"
$ProjData = Join-Path $EnvironmentRoot "Library\share\proj"
$RuntimeBin = Join-Path $EnvironmentRoot "Library\bin"
if (Test-Path $GdalPluginDir) {
    $env:GDAL_DRIVER_PATH = $GdalPluginDir
}
if (Test-Path $GdalDataDir) {
    $env:GDAL_DATA = $GdalDataDir
}
if (Test-Path $ProjData) {
    $env:PROJ_DATA = $ProjData
    $env:PROJ_LIB = $ProjData
}
if (Test-Path $RuntimeBin) {
    $env:PATH = "$RuntimeBin;$EnvironmentRoot;$PreviousPath"
}
$env:GEOTILE_ENABLE_YOLO = "0"

$Script = @'
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

with tempfile.TemporaryDirectory(prefix="geotile-tile-catalog-") as temp_dir:
    root = Path(temp_dir)
    data_dir = root / "data"
    scenes_dir = root / "source-scenes"
    scenes_dir.mkdir(parents=True)
    os.environ["DATA_DIR"] = str(data_dir)
    sys.path.insert(0, "backend")

    from db.storage import project_dir, save_json, save_scene_json
    from models.dataset_config import DatasetConfig
    from models.tiling_config import TileInfo
    from routers.projects import ProjectImportBackup, download_project_backup, import_project_backup
    from services.dataset_builder import build_dataset
    from services.preprocessing_profiles import ensure_preprocessing_profiles, resolve_preprocessing_profile
    from services.tile_catalog import (
        build_tile_catalog,
        clear_legacy_tile_cache,
        ensure_tile_catalog,
        get_active_catalog_manifest,
        get_catalog_filter_options,
        get_catalog_links,
        get_catalog_tiles,
        legacy_cache_info,
        render_tile_preview,
        update_review_state,
    )

    scene_path = scenes_dir / "scene.png"
    pixels = np.zeros((64, 64, 3), dtype=np.uint8)
    pixels[:, :, 0] = np.arange(64, dtype=np.uint8)[None, :] * 4
    pixels[:, :, 1] = np.arange(64, dtype=np.uint8)[:, None] * 4
    pixels[:, :, 2] = 96
    Image.fromarray(pixels).save(scene_path)

    project_id = "catalog-project"
    scene_id = "scene-1"
    profile = {
        "modality": "EO",
        "allowed_modalities": ["EO"],
        "georeferencing": "GEO",
        "allowed_georeferencing": ["GEO"],
        "allow_mixed_scenes": False,
        "sensors": [],
        "annotation_mode": "bbox",
        "default_preprocessing_profile": "eo_rgb_percentile",
        "default_split_strategy": "spatial_block_split",
    }
    save_json(project_id, "project", {
        "id": project_id,
        "name": "Tile catalog smoke",
        "scene_folder": str(scenes_dir),
        "profile": profile,
    })
    save_json(project_id, "classes", [{"id": 0, "name": "vehicle", "color": "#ff0000"}])
    save_json(project_id, "tiling_config", {"tile_size": 32, "buffer": 0})
    save_json(project_id, "dataset_config", DatasetConfig(
        train_ratio=1,
        val_ratio=0,
        test_ratio=0,
        negative_ratio=0,
        min_box_fraction=0.3,
        split_mode="random_tile",
        preprocessing_profile_id="eo_rgb_percentile",
    ).model_dump())
    save_scene_json(project_id, scene_id, "scene", {
        "id": scene_id,
        "filename": scene_path.name,
        "status": "pending",
        "scene_info": {
            "width": 64,
            "height": 64,
            "channels": 3,
            "dtype": "uint8",
            "has_geo": False,
        },
    })
    save_scene_json(project_id, scene_id, "scene_manifest", {
        "schema_name": "geotile_scene_manifest",
        "schema_version": 3,
        "scene_id": scene_id,
        "source_scene_uid": "sha256:catalog-smoke-scene",
        "source_file_sha256": "catalog-smoke-scene",
        "modality": "EO",
        "georeferencing": "GEO",
        "image": {"width": 64, "height": 64, "channels": 3, "dtype": "uint8"},
        "geospatial": {
            "has_geo": True,
            "crs": "EPSG:3857",
            "transform": [1, 0, 0, 0, -1, 64],
            "bounds_wgs84": [0, 0, 0.001, 0.001],
        },
    })
    save_scene_json(project_id, scene_id, "annotations", [{
        "id": "annotation-1",
        "source_annotation_id": "annotation-1",
        "scene_id": scene_id,
        "class_id": 0,
        "geometry_type": "bbox",
        "bbox": [8, 8, 24, 24],
        "is_negative": False,
        "annotation_source": "manual",
        "annotator_email": "analyst@example.com",
    }])
    ensure_preprocessing_profiles(project_id)

    manifest = build_tile_catalog(project_id)
    assert manifest["materialization"] == "metadata_only"
    assert manifest["schema_version"] == 3
    assert manifest["tile_count"] == 4
    catalog_root = project_dir(project_id) / "tile_catalogs" / manifest["catalog_id"]
    required = {
        "tile_catalog_manifest.json",
        "tiles.parquet",
        "tile_annotation_links.parquet",
        "review_state.json",
        "catalog_statistics.json",
    }
    assert required.issubset({path.name for path in catalog_root.iterdir()})
    assert not (project_dir(project_id) / "scenes" / scene_id / "tiles" / "images").exists()

    tiles = get_catalog_tiles(project_id, scene_id)
    links = get_catalog_links(project_id, scene_id)
    assert len(tiles) == 4
    assert links
    assert tiles[0]["geometry_wgs84"]
    assert links[0]["annotator_email"] == "analyst@example.com"
    options = get_catalog_filter_options(project_id)
    assert options["catalog_id"] == manifest["catalog_id"]
    assert options["authors"][0]["email"] == "analyst@example.com"
    assert all(0 <= value <= 1 for value in links[0]["bbox_yolo_norm"])
    update_review_state(project_id, scene_id, [3], reviewed=True)
    assert get_catalog_tiles(project_id, scene_id)[3]["reviewed"] is True

    first_catalog_id = manifest["catalog_id"]
    config = DatasetConfig(**json.loads((project_dir(project_id) / "dataset_config.json").read_text()))
    config.split_seed = 999
    config.split_mode = "scene_split"
    save_json(project_id, "dataset_config", config.model_dump())
    assert ensure_tile_catalog(project_id)["catalog_id"] == first_catalog_id

    preview = render_tile_preview(project_id, tiles[0]["tile_id"])
    assert preview.is_file()
    assert len(list((catalog_root / "preview_cache").glob("*.png"))) == 1

    annotations = json.loads((project_dir(project_id) / "scenes" / scene_id / "annotations.json").read_text())
    annotations[0]["bbox"] = [10, 10, 26, 26]
    save_scene_json(project_id, scene_id, "annotations", annotations)
    rebuilt = ensure_tile_catalog(project_id)
    assert rebuilt["catalog_id"] != first_catalog_id

    active_tiles = get_catalog_tiles(project_id, scene_id)
    active_links = get_catalog_links(project_id, scene_id)
    annotations_by_tile = {}
    for link in active_links:
        if link.get("exportable_yolo"):
            annotations_by_tile.setdefault(link["tile_filename"], []).append(
                [link["class_id"], *link["bbox_yolo_norm"]]
            )
    dataset_tiles = []
    dataset_annotations = {}
    for tile in active_tiles:
        prefixed = f"{scene_id}__{tile['filename']}"
        dataset_annotations[prefixed] = annotations_by_tile.get(tile["filename"], [])
        dataset_tiles.append(TileInfo(**{**tile, "filename": prefixed}))
    preprocessing = resolve_preprocessing_profile(project_id, "eo_rgb_percentile", profile)
    run_dir = root / "dataset-run"
    generator = build_dataset(
        tiles=dataset_tiles,
        tile_annotations=dataset_annotations,
        tile_source_map={},
        dataset_dir=run_dir,
        config=DatasetConfig(
            train_ratio=1,
            val_ratio=0,
            test_ratio=0,
            negative_ratio=0,
            split_mode="random_tile",
            preprocessing_profile_id=preprocessing.profile_id,
        ),
        class_names={0: "vehicle"},
        scene_source_map={scene_id: str(scene_path)},
        preprocessing_profile=preprocessing.model_dump(),
        tile_size=32,
    )
    try:
        while True:
            next(generator)
    except StopIteration as result:
        stats = result.value
    assert stats.positive_tiles >= 1
    assert list((run_dir / "train" / "images").glob("*.png"))
    assert not (project_dir(project_id) / "scenes" / scene_id / "tiles" / "images").exists()

    async def save_backup(path):
        # P0.3 zmienilo backup ze strumienia trzymanego w pamieci na archiwum budowane na
        # dysku i serwowane przez FileResponse. `body_iterator` nalezy do StreamingResponse
        # i na FileResponse nie istnieje — czytamy plik spod `response.path`, a nastepnie
        # uruchamiamy to samo zadanie tla, ktore w produkcji sprzata plik po transmisji.
        response = await download_project_backup(project_id)
        path.write_bytes(Path(response.path).read_bytes())
        if response.background is not None:
            await response.background()
        assert not Path(response.path).exists(), "tymczasowe archiwum nie zostalo posprzatane"

    backup_path = root / "catalog-project-backup.zip"
    asyncio.run(save_backup(backup_path))
    # P0.3 zmienilo ten endpoint z `async def` na synchroniczny handler `def` (FastAPI
    # uruchamia go we wlasnej puli watkow), wiec nie zwraca juz korutyny.
    imported = import_project_backup(ProjectImportBackup(
        backup_file=str(backup_path),
        scene_folder=str(scenes_dir),
        name="Imported catalog project",
    ))
    assert imported["tile_catalog_restored"] is True
    imported_manifest = get_active_catalog_manifest(imported["project_id"], auto_migrate=False)
    assert imported_manifest and imported_manifest["catalog_id"] == rebuilt["catalog_id"]
    imported_scene_manifest = json.loads(
        (project_dir(imported["project_id"]) / "scenes" / scene_id / "scene_manifest.json").read_text()
    )
    assert imported_scene_manifest["source_scene_uid"] == "sha256:catalog-smoke-scene"
    assert imported_scene_manifest["scene_id"] == scene_id

    legacy_id = "legacy-project"
    legacy_scene_id = "legacy-scene"
    save_json(legacy_id, "project", {
        "id": legacy_id,
        "name": "Legacy catalog smoke",
        "scene_folder": str(scenes_dir),
        "profile": profile,
    })
    save_json(legacy_id, "classes", [])
    save_json(legacy_id, "tiling_config", {"tile_size": 32, "buffer": 0})
    save_scene_json(legacy_id, legacy_scene_id, "scene", {
        "id": legacy_scene_id,
        "filename": scene_path.name,
        "status": "tiled",
    })
    save_scene_json(legacy_id, legacy_scene_id, "scene_manifest", {
        "source_scene_uid": "sha256:legacy-smoke-scene",
        "image": {"width": 64, "height": 64},
        "geospatial": {"has_geo": False},
    })
    save_scene_json(legacy_id, legacy_scene_id, "annotations", [])
    save_scene_json(legacy_id, legacy_scene_id, "tiles", [{
        "filename": "1_1_scene.png",
        "col": 1,
        "row": 1,
        "x0": 0,
        "y0": 0,
        "reviewed": True,
        "excluded": False,
    }])
    legacy_images = project_dir(legacy_id) / "scenes" / legacy_scene_id / "tiles" / "images"
    legacy_images.mkdir(parents=True)
    (legacy_images / "1_1_scene.png").write_bytes(b"legacy-cache")
    migrated = get_active_catalog_manifest(legacy_id, auto_migrate=True)
    assert migrated and migrated["migrated_from_legacy"] is True
    assert get_catalog_tiles(legacy_id, legacy_scene_id)[0]["reviewed"] is True
    assert legacy_cache_info(legacy_id)["file_count"] == 1
    removed = clear_legacy_tile_cache(legacy_id)
    assert removed["removed_files"] == 1
    assert legacy_cache_info(legacy_id)["file_count"] == 0
    assert len(get_catalog_tiles(legacy_id, legacy_scene_id)) == 1

print("Metadata-only tile catalog smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "Metadata-only tile catalog smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    $env:GDAL_DRIVER_PATH = $PreviousGdalDriverPath
    $env:GDAL_DATA = $PreviousGdalData
    $env:PROJ_DATA = $PreviousProjData
    $env:PROJ_LIB = $PreviousProjLib
    $env:PATH = $PreviousPath
    $env:GEOTILE_ENABLE_YOLO = $PreviousYolo
    Pop-Location
}
