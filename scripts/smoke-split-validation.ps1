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
$env:GEOTILE_ENABLE_YOLO = "0"

$Script = @'
import random
import sys
import tempfile
from pathlib import Path
from PIL import Image

sys.path.insert(0, "backend")

from models.dataset_config import DatasetConfig
from models.tiling_config import TileInfo
from services.dataset_builder import _assign_splits
from services.split_validation import validate_dataset_split


def tile(scene, col, row, x0=None, y0=None):
    return TileInfo(
        filename=f"{scene}__{col}_{row}.png",
        col=col,
        row=row,
        x0=(col - 1) * 100 if x0 is None else x0,
        y0=(row - 1) * 100 if y0 is None else y0,
    )


random_tiles = [tile("scene-a", index + 1, 1) for index in range(20)]
random_config = DatasetConfig(
    train_ratio=0.6,
    val_ratio=0.2,
    test_ratio=0.2,
    split_mode="random_tile",
    split_seed=42,
)
first = _assign_splits(random_tiles, {}, random_config, random.Random(42))
second = _assign_splits(random_tiles, {}, random_config, random.Random(42))
assert first == second

scene_tiles = [tile("scene-a", i + 1, 1) for i in range(4)] + [
    tile("scene-b", i + 1, 1) for i in range(4)
]
scene_config = DatasetConfig(
    train_ratio=0.5,
    val_ratio=0.5,
    test_ratio=0,
    split_mode="scene_split",
    split_seed=7,
)
scene_assignments = _assign_splits(scene_tiles, {}, scene_config, random.Random(7))
for scene_id in ("scene-a", "scene-b"):
    assert len({scene_assignments[item.filename] for item in scene_tiles if item.filename.startswith(scene_id)}) == 1

block_tiles = [tile("scene-a", 1, 1), tile("scene-a", 3, 1), tile("scene-a", 5, 1)]
block_config = DatasetConfig(
    train_ratio=0.5,
    val_ratio=0.5,
    test_ratio=0,
    split_mode="image_block_split",
    split_seed=3,
    block_size_tiles=2,
)
shared_links = [
    {"dataset_tile_filename": block_tiles[0].filename, "source_annotation_id": "source-1"},
    {"dataset_tile_filename": block_tiles[1].filename, "source_annotation_id": "source-1"},
]
block_assignments = _assign_splits(
    block_tiles,
    {},
    block_config,
    random.Random(3),
    tile_annotation_links=shared_links,
)
assert block_assignments[block_tiles[0].filename] == block_assignments[block_tiles[1].filename]

geo_tiles = [
    tile("scene-a", 1, 1, 0, 0),
    tile("scene-b", 1, 1, 0, 0),
    tile("scene-a", 4, 1, 300, 0),
    tile("scene-b", 7, 1, 600, 0),
]
geo_manifest = {
    "georeferencing": "GEO",
    "geospatial": {
        "has_geo": True,
        "crs": "EPSG:3857",
        "transform": [1, 0, 0, 0, -1, 1000],
    },
}
geo_config = DatasetConfig(
    train_ratio=0.5,
    val_ratio=0.25,
    test_ratio=0.25,
    split_mode="spatial_block_split",
    split_seed=11,
    block_size_tiles=1,
)
geo_assignments = _assign_splits(
    geo_tiles,
    {},
    geo_config,
    random.Random(11),
    scene_manifests={"scene-a": geo_manifest, "scene-b": geo_manifest},
    tile_size=100,
)
assert geo_assignments[geo_tiles[0].filename] == geo_assignments[geo_tiles[1].filename]

with tempfile.TemporaryDirectory(prefix="geotile-split-validation-") as temp_dir:
    dataset_dir = Path(temp_dir)
    (dataset_dir / "train" / "images").mkdir(parents=True)
    (dataset_dir / "val" / "images").mkdir(parents=True)
    (dataset_dir / "test" / "images").mkdir(parents=True)
    duplicate = Image.new("L", (32, 32), 20)
    duplicate.putpixel((8, 8), 240)
    duplicate.save(dataset_dir / "train" / "images" / "scene-a__1_1.png")

    no_geo_report = validate_dataset_split(
        dataset_dir,
        DatasetConfig(split_mode="spatial_block_split"),
        {"georeferencing": "NO_GEO"},
        {"scene-a": {"georeferencing": "NO_GEO", "geospatial": {"has_geo": False}}},
        [],
    )
    codes = {item["code"] for item in no_geo_report["issues"]}
    assert "spatial_split_without_georeferencing" in codes
    assert "empty_splits" in codes

    duplicate.save(dataset_dir / "val" / "images" / "scene-a__3_1.png")
    leakage_links = [
        {"dataset_tile_filename": "scene-a__1_1.png", "source_annotation_id": "shared"},
        {"dataset_tile_filename": "scene-a__3_1.png", "source_annotation_id": "shared"},
    ]
    safe_report = validate_dataset_split(
        dataset_dir,
        DatasetConfig(split_mode="image_block_split", test_ratio=0),
        {"georeferencing": "NO_GEO"},
        {},
        leakage_links,
    )
    assert safe_report["status"] == "error"
    assert safe_report["cross_split_source_annotation_count"] == 1
    assert safe_report["near_duplicate_summary"]["cross_split_pair_count"] == 1
    assert safe_report["checks"]["near_duplicate_tiles"] == "complete"
    assert any(item["code"] == "near_duplicate_tiles" for item in safe_report["issues"])

    random_report = validate_dataset_split(
        dataset_dir,
        DatasetConfig(split_mode="random_tile", test_ratio=0),
        {"georeferencing": "NO_GEO"},
        {},
        leakage_links,
    )
    assert random_report["status"] == "error"
    assert random_report["near_duplicate_summary"]["cross_split_pair_count"] == 1

print("Split strategies and validation smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "Split validation smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
