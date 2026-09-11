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

$EnvironmentRoot = Split-Path -Parent $PythonPath
$ProjData = Join-Path $EnvironmentRoot "Library\share\proj"
if (Test-Path $ProjData) {
    $env:PROJ_DATA = $ProjData
    $env:PROJ_LIB = $ProjData
}

$Script = @'
import csv
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="geotile-sidecars-") as temp_dir:
    root = Path(temp_dir)
    os.environ["DATA_DIR"] = str(root)
    sys.path.insert(0, "backend")

    from services.attribute_engine import compute_source_attributes, compute_tile_attributes
    from services.export_sidecars import generate_export_sidecars
    from services.dataset_package import build_dataset_package
    from routers.export import _write_dataset_zip

    project_id = "project-1"
    project_dir = root / "projects" / project_id
    scene_dir = project_dir / "scenes" / "scene-1"
    dataset_dir = project_dir / "dataset"
    images_dir = dataset_dir / "train" / "images"
    labels_dir = dataset_dir / "train" / "labels"
    images_dir.mkdir(parents=True)
    labels_dir.mkdir(parents=True)
    scene_dir.mkdir(parents=True)

    dataset_filename = "scene-1__1_1_scene.png"
    tile_filename = "1_1_scene.png"
    (images_dir / dataset_filename).write_bytes(b"png")
    (labels_dir / "scene-1__1_1_scene.txt").write_text("0 0.375 0.375 0.5 0.25\n", encoding="utf-8")

    project = {
        "id": project_id,
        "name": "Sidecar smoke",
        "profile": {
            "modality": "EO",
            "georeferencing": "GEO",
            "sensors": ["WorldView"],
            "annotation_mode": "bbox",
            "default_preprocessing_profile": "eo_rgb_percentile",
            "default_split_strategy": "spatial_block_split",
        },
    }
    manifest = {
        "schema_name": "geotile_scene_manifest",
        "schema_version": 2,
        "scene_id": "scene-1",
        "filename": "scene.tif",
        "provider": "WorldView",
        "sensor": "WorldView",
        "modality": "EO",
        "georeferencing": "GEO",
        "acquisition_datetime_utc": "2024-01-01T10:00:00+00:00",
        "image": {"width": 100, "height": 100},
        "geospatial": {
            "has_geo": True,
            "crs": "EPSG:3857",
            "transform": [1, 0, 1000, 0, -1, 2000],
        },
    }
    annotation = {
        "id": "annotation-1",
        "source_annotation_id": "annotation-1",
        "scene_id": "scene-1",
        "class_id": 0,
        "geometry_type": "bbox",
        "bbox": [10, 20, 50, 40],
        "is_negative": False,
        "annotation_source": "manual",
    }
    annotation["attributes"] = compute_source_attributes(annotation, manifest, "vehicle")
    tile = {
        "filename": tile_filename,
        "col": 1,
        "row": 1,
        "x0": 0,
        "y0": 0,
        "reviewed": True,
        "excluded": False,
    }
    link = compute_tile_attributes(annotation, tile, 80, 0.3)
    assert link is not None
    link["dataset_tile_filename"] = dataset_filename

    def write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    write_json(project_dir / "project.json", project)
    write_json(project_dir / "classes.json", [{"id": 0, "name": "vehicle", "color": "#ff0000"}])
    write_json(project_dir / "tiling_config.json", {"tile_size": 80, "buffer": 0})
    write_json(project_dir / "dataset_config.json", {
        "train_ratio": 1,
        "val_ratio": 0,
        "test_ratio": 0,
        "min_box_fraction": 0.3,
        "negative_ratio": 0,
        "split_mode": "spatial_block_split",
        "split_seed": 42,
    })
    write_json(project_dir / "tile_annotations.json", {dataset_filename: [[0, 10, 20, 50, 40]]})
    write_json(project_dir / "tile_annotation_links.json", {
        "schema_name": "geotile_tile_annotation_links",
        "schema_version": 1,
        "annotations": [link],
    })
    write_json(scene_dir / "scene.json", {"id": "scene-1", "filename": "scene.tif"})
    write_json(scene_dir / "scene_manifest.json", manifest)
    write_json(scene_dir / "tiles.json", [tile])
    write_json(scene_dir / "annotations.json", [annotation])

    result = generate_export_sidecars(project_id, dataset_dir, "yolo")
    manifest_result = json.loads((dataset_dir / "geotile_export_manifest.json").read_text(encoding="utf-8"))
    with (dataset_dir / "metadata" / "tile_metadata.csv").open(encoding="utf-8-sig") as handle:
        tile_rows = list(csv.DictReader(handle))
    with (dataset_dir / "metadata" / "annotation_links.csv").open(encoding="utf-8-sig") as handle:
        annotation_rows = list(csv.DictReader(handle))

    assert result["validation"]["status"] == "ok", result
    assert manifest_result["export_formats"] == ["yolo"]
    assert len(tile_rows) == 1 and tile_rows[0]["scene_id"] == "scene-1"
    assert len(annotation_rows) == 1
    assert annotation_rows[0]["source_annotation_id"] == "annotation-1"
    assert annotation_rows[0]["split"] == "train"
    import pyarrow.parquet as pq
    assert pq.read_table(dataset_dir / "metadata" / "tile_metadata.parquet").num_rows == 1
    assert pq.read_table(dataset_dir / "metadata" / "annotation_links.parquet").num_rows == 1
    wgs_schema = pq.read_schema(dataset_dir / "metadata" / "annotations_wgs84.geoparquet")
    assert b"geo" in (wgs_schema.metadata or {})

    write_json(dataset_dir / "dataset_selection.json", {
        "schema_name": "geotile_dataset_selection",
        "schema_version": 1,
        "catalog_id": "catalog-1",
        "used_tiles": [{"tile_id": "tile-1", "filename": dataset_filename, "split": "train"}],
    })

    package_dir = build_dataset_package(project_id, dataset_dir)
    assert (package_dir / "README_DATASET.md").is_file()
    assert (package_dir / "SHA256SUMS.txt").is_file()
    assert (package_dir / "yolo" / "data_obb.yaml").is_file()
    assert (package_dir / "yolo" / "labels_obb" / "train" / "scene-1__1_1_scene.txt").is_file()
    assert (package_dir / "coco" / "annotations" / "instances_train.json").is_file()
    assert (package_dir / "metadata" / "annotations_native.geoparquet").is_file()
    assert (package_dir / "metadata" / "dataset_selection.json").is_file()

    zip_path = root / "dataset.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        _write_dataset_zip(package_dir, archive)
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert "dataset_run_manifest.json" in names
    assert "README_DATASET.md" in names
    assert "SHA256SUMS.txt" in names
    assert "metadata/tile_metadata.csv" in names
    assert "metadata/annotation_links.csv" in names
    assert "metadata/dataset_selection.json" in names

print("Export sidecars smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
} finally {
    Pop-Location
}
