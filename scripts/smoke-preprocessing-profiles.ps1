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
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

with tempfile.TemporaryDirectory(prefix="geotile-preprocessing-") as temp_dir:
    root = Path(temp_dir)
    os.environ["DATA_DIR"] = str(root)
    sys.path.insert(0, "backend")

    from db.storage import save_json
    from models.dataset_config import DatasetConfig
    from models.preprocessing import PreprocessingProfile
    from models.tiling_config import TileInfo
    from services.dataset_builder import build_dataset
    from services.preprocessing_profiles import (
        apply_preprocessing_profile,
        default_preprocessing_profiles,
        ensure_preprocessing_profiles,
        upsert_preprocessing_profile,
        write_preprocessed_tile,
    )

    project_id = "project-1"
    save_json(project_id, "project", {
        "id": project_id,
        "name": "Preprocessing smoke",
        "profile": {
            "modality": "SAR",
            "georeferencing": "GEO",
            "default_preprocessing_profile": "sar_log_percentile",
        },
    })
    profiles_file = ensure_preprocessing_profiles(project_id)
    assert len(profiles_file["profiles"]) >= 4
    defaults = {item.profile_id: item for item in default_preprocessing_profiles()}
    sar_profile = defaults["sar_log_percentile"]
    assert len(sar_profile.profile_hash) == 64

    unchanged = upsert_preprocessing_profile(project_id, sar_profile)
    assert unchanged.profile_hash == sar_profile.profile_hash
    changed = upsert_preprocessing_profile(
        project_id,
        PreprocessingProfile(**{
            **unchanged.model_dump(),
            "gamma": 1.15,
        }),
    )
    assert changed.profile_hash != unchanged.profile_hash
    assert changed.profile_version == unchanged.profile_version + 1

    gradient = np.linspace(0, 65535, 64 * 64, dtype=np.uint16).reshape(64, 64, 1)
    valid = np.ones((64, 64), dtype=bool)
    valid[-8:, :] = False
    processed = apply_preprocessing_profile(gradient, sar_profile, valid)
    assert processed.dtype == np.uint8
    assert processed.shape == (64, 64, 3)
    assert len(np.unique(processed[:-8, :, 0])) > 32
    assert np.all(processed[-8:, :] == 0)

    import rasterio
    from rasterio.transform import from_origin

    scene_path = root / "sar_uint16.tif"
    with rasterio.open(
        scene_path,
        "w",
        driver="GTiff",
        width=64,
        height=64,
        count=1,
        dtype="uint16",
        crs="EPSG:3857",
        transform=from_origin(0, 64, 1, 1),
        nodata=0,
    ) as dataset:
        dataset.write(gradient[:, :, 0], 1)

    direct_output = root / "direct.png"
    write_preprocessed_tile(scene_path, 0, 0, 80, sar_profile, direct_output)
    repeated_output = root / "direct-repeated.png"
    write_preprocessed_tile(scene_path, 0, 0, 80, sar_profile, repeated_output)
    assert direct_output.read_bytes() == repeated_output.read_bytes()
    from PIL import Image
    direct = np.asarray(Image.open(direct_output))
    assert direct.shape == (80, 80, 3)
    assert np.all(direct[64:, :] == 0)

    run_dir = root / "dataset_run"
    tile = TileInfo(filename="scene-1__1_1_scene.png", col=1, row=1, x0=0, y0=0)
    generator = build_dataset(
        tiles=[tile],
        tile_annotations={tile.filename: [[0, 0.5, 0.5, 0.25, 0.25]]},
        tile_source_map={},
        dataset_dir=run_dir,
        config=DatasetConfig(
            train_ratio=1,
            val_ratio=0,
            test_ratio=0,
            negative_ratio=0,
            preprocessing_profile_id=sar_profile.profile_id,
        ),
        class_names={0: "vehicle"},
        scene_source_map={"scene-1": str(scene_path)},
        preprocessing_profile=sar_profile.model_dump(),
        tile_size=80,
    )
    try:
        while True:
            next(generator)
    except StopIteration as result:
        stats = result.value
    output = run_dir / "train" / "images" / tile.filename
    assert output.exists()
    assert stats.total_tiles == 1
    generated = np.asarray(Image.open(output))
    assert len(np.unique(generated[:64, :64, 0])) > 32

print("Preprocessing profiles smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "Preprocessing profiles smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
