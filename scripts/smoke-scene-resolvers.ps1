param(
    [string]$PythonPath = "",
    [switch]$RequireJp2
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) {
    $PackedPython = Join-Path $RepoRoot ".desktop-build\backend-env\python.exe"
    $PythonPath = if (Test-Path $PackedPython) { $PackedPython } else { "python" }
}

$Script = @'
import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, "backend")
from services.scene_packages import scan_source
from services.metadata_parser import parse_scene_metadata

if os.environ.get("GEOTILE_REQUIRE_JP2") == "1":
    try:
        from osgeo import gdal
        import rasterio
        gdal.UseExceptions()
        with rasterio.Env() as environment:
            drivers = environment.drivers()
        assert "JP2OpenJPEG" in drivers, "GDAL runtime does not provide the JP2OpenJPEG driver"
        with tempfile.TemporaryDirectory(prefix="geotile-jp2-") as jp2_temp:
            jp2_path = Path(jp2_temp) / "driver-test.jp2"
            source = gdal.GetDriverByName("MEM").Create("", 8, 8, 1, gdal.GDT_Byte)
            source.SetGeoTransform((500000, 1, 0, 10000, 0, -1))
            output = gdal.GetDriverByName("JP2OpenJPEG").CreateCopy(str(jp2_path), source)
            assert output is not None, "JP2OpenJPEG could not create a test raster"
            output = None
            source = None
            with rasterio.open(jp2_path) as dataset:
                assert dataset.width == 8 and dataset.height == 8 and dataset.count == 1
    except Exception as exc:
        raise AssertionError(f"JP2 runtime preflight failed: {exc}") from exc

def touch(root, relative, text=""):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

with tempfile.TemporaryDirectory(prefix="geotile-resolvers-") as value:
    root = Path(value)
    cases = []

    generic = root / "generic"
    touch(generic, "image.tif")
    cases.append((generic, "generic", "ready", "IMAGE"))

    iceye = root / "iceye"
    touch(iceye, "scene/ICEYE_X31_GRD_VV_20250101.tif")
    touch(iceye, "scene/ICEYE_X31_SLC_VV_20250101.h5")
    cases.append((iceye, "iceye", "ready", "GRD"))

    capella = root / "capella"
    touch(capella, "scene/CAPELLA_C10_GEC_HH_20240101.tif")
    touch(capella, "scene/CAPELLA_C10_GEC_HH_20240101.json", "{}")
    cases.append((capella, "capella", "ready", "GEC"))

    umbra = root / "umbra"
    touch(umbra, "scene/2024-01-01_UMBRA_GEC_VV.tif")
    touch(umbra, "scene/stac.json", "{}")
    cases.append((umbra, "umbra", "ready", "GEC"))

    blacksky = root / "blacksky"
    touch(blacksky, "scene/BSG-113_browse.png")
    touch(blacksky, "scene/BSG-113_ortho-mask.tif")
    touch(blacksky, "scene/BSG-113_ortho-pan.tif")
    touch(blacksky, "scene/BSG-113_ortho.tif")
    touch(blacksky, "scene/BSG-113_metadata.json", "{}")
    cases.append((blacksky, "blacksky", "ready", "ORTHO_RGB"))

    pneo = root / "pneo"
    touch(pneo, "delivery/VOL_PNEO.XML", "<PNEO/>")
    touch(pneo, "delivery/IMG_01_PNEO4_PMS-FS/IMG_PNEO4_PMS-FS_ORT_RGB_R1C1.JP2")
    touch(pneo, "delivery/IMG_01_PNEO4_PMS-FS/IMG_PNEO4_PMS-FS_ORT_NED_R1C1.JP2")
    cases.append((pneo, "pleiades_neo", "ready", "PMS-FS_RGB_ORTHO"))

    worldview = root / "worldview"
    touch(worldview, "delivery/DeliveryMetadata.xml", "<ISD><SATID>WV02</SATID></ISD>")
    touch(worldview, "delivery/product_MUL/scene-M2AS_R1C1.tif")
    touch(worldview, "delivery/product_PAN/scene-P1BS_R1C1.tif")
    touch(worldview, "delivery/product_MUL/scene.imd", "BAND_C = 1;\nBAND_B = 1;\nBAND_G = 1;\nBAND_R = 1;\n")
    cases.append((worldview, "worldview", "prepare_required", "MUL+PAN"))

    for source, provider, status, product in cases:
        packages, diagnostics = scan_source(source, provider)
        assert not [item for item in diagnostics if item.get("level") == "error"], (provider, diagnostics)
        assert len(packages) == 1, (provider, len(packages))
        selection = packages[0]["selection"]
        assert selection["status"] == status, (provider, selection)
        assert selection["product_type"] == product, (provider, selection)
        if provider == "pleiades_neo":
            assets = {item["asset_id"]: item for item in packages[0]["assets"]}
            selected_names = [assets[item]["package_relative_path"] for item in selection["asset_ids"]]
            assert selected_names and all("_RGB_" in name for name in selected_names), selected_names
        if provider == "blacksky":
            assets = {item["asset_id"]: item for item in packages[0]["assets"]}
            selected_names = [assets[item]["package_relative_path"] for item in selection["asset_ids"]]
            assert selected_names == ["BSG-113_ortho.tif"], selected_names
            assert selection["rgb_bands"] == [1, 2, 3], selection

    umbra_stac = root / "umbra-stac.json"
    touch(root, "umbra-stac.json", '''{
      "type": "Feature",
      "id": "umbra-smoke",
      "properties": {
        "constellation": "umbra",
        "platform": "Umbra-10",
        "start_datetime": "2025-06-22T23:57:53Z",
        "sar:instrument_mode": "SPOTLIGHT",
        "sar:product_type": "GEC",
        "sar:polarizations": ["HH"]
      }
    }''')
    metadata = parse_scene_metadata(None, [str(umbra_stac)])
    assert metadata["parser_name"] == "umbra_stac_v2", metadata
    assert metadata["sar"]["product_type"] == "GEC", metadata

print("Scene package resolver smoke test passed.")
'@

Push-Location $RepoRoot
$PreviousRequireJp2 = $env:GEOTILE_REQUIRE_JP2
$PreviousGdalDriverPath = $env:GDAL_DRIVER_PATH
$PreviousGdalData = $env:GDAL_DATA
$PreviousProjData = $env:PROJ_DATA
$PreviousProjLib = $env:PROJ_LIB
$PreviousPath = $env:PATH
try {
    $env:GEOTILE_REQUIRE_JP2 = if ($RequireJp2) { "1" } else { "0" }
    if ($RequireJp2) {
        $ResolvedPython = (Get-Command $PythonPath -ErrorAction Stop).Source
        $GdalPluginDir = Join-Path (Split-Path -Parent $ResolvedPython) "Library\lib\gdalplugins"
        $RuntimeRoot = Split-Path -Parent $ResolvedPython
        $RuntimeBin = Join-Path $RuntimeRoot "Library\bin"
        $GdalDataDir = Join-Path $RuntimeRoot "Library\share\gdal"
        $ProjDataDir = Join-Path $RuntimeRoot "Library\share\proj"
        if (Test-Path $GdalPluginDir) {
            $env:GDAL_DRIVER_PATH = $GdalPluginDir
        }
        if (Test-Path $GdalDataDir) {
            $env:GDAL_DATA = $GdalDataDir
        }
        if (Test-Path $ProjDataDir) {
            $env:PROJ_DATA = $ProjDataDir
            $env:PROJ_LIB = $ProjDataDir
        }
        $env:PATH = "$RuntimeBin;$RuntimeRoot;$PreviousPath"
    }
    $Script | & $PythonPath -
    if ($LASTEXITCODE -ne 0) {
        throw "Scene package resolver smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    $env:GEOTILE_REQUIRE_JP2 = $PreviousRequireJp2
    $env:GDAL_DRIVER_PATH = $PreviousGdalDriverPath
    $env:GDAL_DATA = $PreviousGdalData
    $env:PROJ_DATA = $PreviousProjData
    $env:PROJ_LIB = $PreviousProjLib
    $env:PATH = $PreviousPath
    Pop-Location
}
