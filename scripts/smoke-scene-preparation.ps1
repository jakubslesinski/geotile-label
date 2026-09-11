param(
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) {
    $PackedPython = Join-Path $RepoRoot ".desktop-build\backend-env\python.exe"
    $PythonPath = if (Test-Path $PackedPython) { $PackedPython } else { "python" }
}

$Script = @'
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

with tempfile.TemporaryDirectory(prefix="geotile-preparation-") as temp_value:
    temp = Path(temp_value)
    os.environ["DATA_DIR"] = str(temp / "data")
    sys.path.insert(0, "backend")

    import rasterio
    from osgeo import gdal
    from rasterio.transform import from_origin
    from db.storage import create_project_root, save_json, save_scene_json
    from services.scene_packages import working_view
    from services.scene_packages.fullres_cog_builder import (
        build_fullres_cog,
        cleanup_build_intermediates,
        validate_fullres_candidate,
    )
    from services.scene_packages.working_view import prepare_pansharpened_cog
    from services.scene_sources import save_scene_sources

    source_root = temp / "source"
    source_root.mkdir()
    multispectral = source_root / "worldview_mul.tif"
    panchromatic = source_root / "worldview_pan.tif"

    ms_profile = {
        "driver": "GTiff",
        # Wynik musi byc wiekszy od BLOCKSIZE=512. Dla rastra 256x256 poprawny COG
        # miesci sie w jednym bloku i GDAL celowo nie tworzy zadnego overview, przez co
        # test sprawdzal zalozenie o rozmiarze zamiast dzialania pipeline'u.
        "width": 256,
        "height": 256,
        "count": 8,
        "dtype": "uint16",
        "crs": "EPSG:32631",
        "transform": from_origin(500000, 10000, 4, 4),
    }
    with rasterio.open(multispectral, "w", **ms_profile) as dataset:
        for band in range(1, 9):
            dataset.write(np.full((256, 256), band * 100, dtype=np.uint16), band)

    pan_profile = {
        **ms_profile,
        "width": 1024,
        "height": 1024,
        "count": 1,
        "transform": from_origin(500000, 10000, 1, 1),
    }
    with rasterio.open(panchromatic, "w", **pan_profile) as dataset:
        dataset.write(np.full((1024, 1024), 900, dtype=np.uint16), 1)

    project_id = "preparation-smoke"
    scene_id = "worldview-smoke"
    source_id = "src_smoke"
    create_project_root(project_id, "Preparation smoke")
    save_json(project_id, "project", {"id": project_id, "name": "Preparation smoke", "scene_folder": str(source_root)})
    save_scene_sources(project_id, {"sources": [{
        "source_id": source_id,
        "provider": "worldview",
        "root_path": str(source_root),
        "enabled": True,
        "added_at": "2026-01-01T00:00:00+00:00",
    }]})
    assets = [
        {"asset_id": "asset_ms", "relative_path": multispectral.name},
        {"asset_id": "asset_pan", "relative_path": panchromatic.name},
    ]
    save_scene_json(project_id, scene_id, "scene_manifest", {
        "source_package": {
            "source_id": source_id,
            "assets": assets,
            "identity_asset_ids": ["asset_ms", "asset_pan"],
            "selection": {
                "multispectral_asset_ids": ["asset_ms"],
                "panchromatic_asset_ids": ["asset_pan"],
            },
        },
        "working_view": {"locked": False},
    })

    result = prepare_pansharpened_cog(project_id, scene_id, [5, 3, 2])
    output = Path(result["path"])
    assert output.is_file() and output.stat().st_size > 0
    with rasterio.open(output) as dataset:
        assert (dataset.width, dataset.height, dataset.count) == (1024, 1024, 3)
        assert dataset.dtypes == ("uint16", "uint16", "uint16")
        assert dataset.crs is not None
        assert dataset.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
        assert dataset.overviews(1), (
            f"COG {dataset.width}x{dataset.height} with blocks {dataset.block_shapes} "
            "has no overview levels"
        )

    # Exercise the real packaged JP2OpenJPEG plugin. The production large-JP2
    # policy is forced for this compact fixture so release preflight covers the
    # native-resolution copy without allocating a multi-gigapixel test raster.
    jp2_driver = gdal.GetDriverByName("JP2OpenJPEG")
    assert jp2_driver is not None, "Packaged runtime has no JP2OpenJPEG driver"
    native_jp2 = source_root / "native_gray_alpha.jp2"
    memory = gdal.GetDriverByName("MEM").Create("", 1024, 768, 2, gdal.GDT_UInt16)
    memory.SetGeoTransform((500000, 1, 0, 10000, 0, -1))
    with rasterio.open(panchromatic) as reference:
        memory.SetProjection(reference.crs.to_wkt())
    memory.GetRasterBand(1).Fill(1200)
    memory.GetRasterBand(1).SetColorInterpretation(gdal.GCI_GrayIndex)
    memory.GetRasterBand(2).Fill(65535)
    memory.GetRasterBand(2).SetColorInterpretation(gdal.GCI_AlphaBand)
    encoded = jp2_driver.CreateCopy(
        str(native_jp2),
        memory,
        options=["REVERSIBLE=YES", "QUALITY=100", "RESOLUTIONS=5"],
    )
    assert encoded is not None, "JP2OpenJPEG could not create the preparation fixture"
    encoded = None
    memory = None
    source_before = (native_jp2.stat().st_size, native_jp2.stat().st_mtime_ns)
    opened = gdal.Open(str(native_jp2))
    source_factors = [
        round(opened.RasterXSize / opened.GetRasterBand(1).GetOverview(index).XSize)
        for index in range(opened.GetRasterBand(1).GetOverviewCount())
    ]
    opened = None
    assert source_factors and source_factors[0] == 2

    original_inspect = working_view.inspect_source_overviews
    original_ready = working_view.source_overviews_are_display_ready
    try:
        working_view.inspect_source_overviews = lambda _path: {
            "type": "native_multiresolution",
            "driver": "JP2OpenJPEG",
            "width": 1024,
            "height": 768,
            "usable": True,
            "factors": source_factors,
            "read_error": None,
        }
        working_view.source_overviews_are_display_ready = lambda _state: False
        jp2_result = working_view.build_direct_overviews(
            project_id,
            "jp2-smoke",
            native_jp2,
            "variant-jp2-smoke",
            profile={
                "compression": "ZSTD",
                "predictor": "auto",
                "jp2_gdal_threads": 2,
                "jp2_base_factor": 2,
            },
        )
    finally:
        working_view.inspect_source_overviews = original_inspect
        working_view.source_overviews_are_display_ready = original_ready
    assert jp2_result is not None and jp2_result.is_file()
    assert jp2_result.with_name(jp2_result.name + ".ovr").is_file()
    assert (native_jp2.stat().st_size, native_jp2.stat().st_mtime_ns) == source_before
    prepared = gdal.Open(str(jp2_result))
    assert prepared.RasterCount == 2
    assert prepared.GetRasterBand(1).GetOverviewCount() > 0
    assert round(
        prepared.RasterXSize / prepared.GetRasterBand(1).GetOverview(0).XSize
    ) == 2
    assert prepared.GetRasterBand(2).GetColorInterpretation() == gdal.GCI_AlphaBand
    prepared = None
    overview_profile = json.loads(
        (jp2_result.parent / working_view.DIRECT_OVERVIEW_PROFILE_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert overview_profile["schema_version"] == 3
    assert overview_profile["strategy"] == "jp2_native_level_copy_v1"

    # Exercise the complete COG v2 construction and validator used after the
    # first preview viewport. Two bands are intentional: this catches the former
    # strip-append layout that produced horizontal gaps in Gray+Alpha scenes.
    fullres_target = temp / "fullres-v2"
    fullres_profile = {
        "width": 1024,
        "height": 768,
        "band_count": 2,
        "itemsize": 2,
        "gdal_dtype": "UInt16",
        "crs_wkt": "EPSG:32631",
        "transform": [1, 0, 500000, 0, -1, 10000],
        "color_interpretation": ["gray", "alpha"],
        "nodata_values": [None, None],
    }
    fullres = build_fullres_cog(
        source=native_jp2,
        target_dir=fullres_target,
        scene_profile=fullres_profile,
        source_bytes=native_jp2.stat().st_size,
    )
    validation = validate_fullres_candidate(fullres, scene_profile=fullres_profile)
    assert validation["status"] == "valid" and validation["bands"] == 2
    assert fullres.cog_path.name == "fullres.candidate.tif"

    # Candidate validation must be substantive, not just a structural COG check.
    # Corrupting the still-private RAW reference has to make the same candidate fail.
    with fullres.raw_path.open("r+b") as raw_handle:
        original = raw_handle.read(2)
        raw_handle.seek(0)
        raw_handle.write(bytes((original[0] ^ 0xFF, original[1])))
    rejected = False
    try:
        validate_fullres_candidate(fullres, scene_profile=fullres_profile)
    except RuntimeError as exc:
        rejected = "validation_failed" in str(exc)
    assert rejected, "Pixel-corrupted RAW reference did not invalidate the candidate"
    cleanup_build_intermediates(fullres, remove_candidate=True)

print("Scene preparation, JP2 preview and fullres COG v2 smoke test passed.")
'@

Push-Location $RepoRoot
$PreviousGdalDriverPath = $env:GDAL_DRIVER_PATH
$PreviousGdalData = $env:GDAL_DATA
$PreviousProjData = $env:PROJ_DATA
$PreviousProjLib = $env:PROJ_LIB
$PreviousPath = $env:PATH
try {
    $ResolvedPython = (Get-Command $PythonPath -ErrorAction Stop).Source
    $RuntimeRoot = Split-Path -Parent $ResolvedPython
    $RuntimeBin = Join-Path $RuntimeRoot "Library\bin"
    $GdalPluginDir = Join-Path $RuntimeRoot "Library\lib\gdalplugins"
    $GdalDataDir = Join-Path $RuntimeRoot "Library\share\gdal"
    $ProjDataDir = Join-Path $RuntimeRoot "Library\share\proj"
    if (Test-Path $GdalPluginDir) { $env:GDAL_DRIVER_PATH = $GdalPluginDir }
    if (Test-Path $GdalDataDir) { $env:GDAL_DATA = $GdalDataDir }
    if (Test-Path $ProjDataDir) {
        $env:PROJ_DATA = $ProjDataDir
        $env:PROJ_LIB = $ProjDataDir
    }
    if (Test-Path $RuntimeBin) { $env:PATH = "$RuntimeBin;$RuntimeRoot;$PreviousPath" }
    $Script | & $PythonPath -
    if ($LASTEXITCODE -ne 0) {
        throw "Scene preparation smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    $env:GDAL_DRIVER_PATH = $PreviousGdalDriverPath
    $env:GDAL_DATA = $PreviousGdalData
    $env:PROJ_DATA = $PreviousProjData
    $env:PROJ_LIB = $PreviousProjLib
    $env:PATH = $PreviousPath
    Pop-Location
}
