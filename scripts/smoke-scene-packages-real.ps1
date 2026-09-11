param(
    [string]$Config = "test-scene-packages.local.json",
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ConfigPath = if ([IO.Path]::IsPathRooted($Config)) { $Config } else { Join-Path $RepoRoot $Config }
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Real scene package config was not found: $ConfigPath"
}
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

sys.path.insert(0, "backend")

from osgeo import gdal
from services.scene_loader import generate_thumbnail, get_scene_info
from services.scene_packages import scan_source
from services.scene_packages.working_view import _validate_mosaic_parts

gdal.UseExceptions()
config_path = Path(os.environ["GEOTILE_REAL_SCENE_CONFIG"])
config = json.loads(config_path.read_text(encoding="utf-8-sig"))
sources = config.get("sources") or []
assert sources, "The real scene package config does not contain sources"

results = []
with tempfile.TemporaryDirectory(prefix="geotile-real-scenes-") as temp_value:
    temp = Path(temp_value)
    for index, source in enumerate(sources):
        provider = source["provider"]
        root = Path(source["root_path"])
        assert root.is_dir(), f"Source folder does not exist: {root}"
        packages, diagnostics = scan_source(root, provider)
        errors = [item for item in diagnostics if item.get("level") == "error"]
        assert not errors, (provider, errors)
        expected_packages = int(source.get("expected_packages", 1))
        assert len(packages) == expected_packages, (provider, root, len(packages), expected_packages)

        for package_index, package in enumerate(packages):
            selection = package["selection"]
            expected_status = source.get("expected_status")
            expected_product = source.get("expected_product")
            if expected_status:
                assert selection["status"] == expected_status, (provider, selection)
            if expected_product:
                assert selection["product_type"] == expected_product, (provider, selection)

            assets = {item["asset_id"]: item for item in package["assets"]}
            selected_paths = [
                root / assets[asset_id]["relative_path"]
                for asset_id in selection.get("asset_ids") or []
            ]
            assert selected_paths, (provider, "Resolver did not select raster assets")
            assert all(path.is_file() for path in selected_paths), (provider, selected_paths)

            result = {
                "provider": provider,
                "package": package.get("package_root_relative"),
                "status": selection["status"],
                "product": selection["product_type"],
                "selected_assets": len(selected_paths),
                "rasters": [],
            }

            group_fields = [
                field for field in ("multispectral_asset_ids", "panchromatic_asset_ids")
                if selection.get(field)
            ]
            if group_fields:
                groups = [
                    (field, [root / assets[asset_id]["relative_path"] for asset_id in selection[field]])
                    for field in group_fields
                ]
            else:
                groups = [("working", selected_paths)]

            for group_index, (group_name, paths) in enumerate(groups):
                if len(paths) > 1:
                    _validate_mosaic_parts(paths)
                    raster_path = temp / f"{index}-{package_index}-{group_index}.vrt"
                    dataset = gdal.BuildVRT(str(raster_path), [str(path) for path in paths])
                    assert dataset is not None, (provider, group_name, "GDAL BuildVRT failed")
                    dataset.FlushCache()
                    dataset = None
                else:
                    raster_path = paths[0]

                info = get_scene_info(raster_path)
                assert info.width > 0 and info.height > 0 and info.channels > 0
                assert info.has_geo, (provider, raster_path, "Expected georeferenced sample")
                raster_result = {
                    "group": group_name,
                    "parts": len(paths),
                    "width": info.width,
                    "height": info.height,
                    "channels": info.channels,
                    "dtype": info.dtype,
                    "has_geo": info.has_geo,
                }
                if group_name != "panchromatic_asset_ids":
                    thumbnail = temp / f"{index}-{package_index}-{group_index}.png"
                    generate_thumbnail(raster_path, thumbnail)
                    assert thumbnail.stat().st_size > 1000, (provider, "Thumbnail is empty")
                    raster_result["thumbnail_bytes"] = thumbnail.stat().st_size
                result["rasters"].append(raster_result)
            results.append(result)

print(json.dumps(results, indent=2, ensure_ascii=False))
print("Real scene package smoke test passed.")
'@

Push-Location $RepoRoot
$PreviousConfig = $env:GEOTILE_REAL_SCENE_CONFIG
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
    $env:GEOTILE_REAL_SCENE_CONFIG = (Resolve-Path -LiteralPath $ConfigPath).Path
    $Script | & $PythonPath -
    if ($LASTEXITCODE -ne 0) {
        throw "Real scene package smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    $env:GEOTILE_REAL_SCENE_CONFIG = $PreviousConfig
    $env:GDAL_DRIVER_PATH = $PreviousGdalDriverPath
    $env:GDAL_DATA = $PreviousGdalData
    $env:PROJ_DATA = $PreviousProjData
    $env:PROJ_LIB = $PreviousProjLib
    $env:PATH = $PreviousPath
    Pop-Location
}
