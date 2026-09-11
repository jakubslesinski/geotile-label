param(
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

if (-not $PythonPath) {
    $CondaPython = "C:\ProgramData\anaconda3\python.exe"
    if (Test-Path $CondaPython) {
        $PythonPath = $CondaPython
    } else {
        $PythonPath = "python"
    }
}

$Script = @'
import sys
from pathlib import Path

repo_root = Path.cwd()
sys.path.insert(0, str(repo_root / "backend"))

from services.metadata_parser import parse_scene_metadata
from services.scene_manifest import find_metadata_sidecars

samples = [
    ("UMBRA", Path("data/SAR_sample_data/2024-06-05-20-40-46_UMBRA-04_GEC.tif")),
    ("Capella", Path("data/SAR_sample_data/CAPELLA_C10_SS_GEO_HH_20240407192953_20240407193008.tif")),
    ("ICEYE", Path("data/SAR_sample_data/ICEYE_X38_GRD_SLEDP_4018254_20240404T121830.tif")),
    ("BlackSky", Path("data/EO_sample_data/BSG-112-20241013-070515-285144018-ortho/BSG-112-20241013-070515-285144018_ortho.tif")),
]

pleiades_root = Path("data/EO_sample_data/59181581-ecb4-4d70-baef-8aeede23b17e_NONE_STD_A")
if pleiades_root.exists():
    pleiades = next(pleiades_root.rglob("*.TIF"), None)
    if pleiades:
        samples.append(("Pleiades Neo", pleiades))

worldview_root = Path("data/EO_sample_data/EPWAv2_014670314010_0")
if worldview_root.exists():
    worldview = next(worldview_root.rglob("*M2AS_R1C1*.TIF"), None)
    if worldview:
        samples.append(("WorldView", worldview))

checked = 0
for expected, scene_path in samples:
    if not scene_path.exists():
        print(f"SKIP {expected}: sample scene missing")
        continue
    sidecars = find_metadata_sidecars(scene_path)
    parsed = parse_scene_metadata(scene_path, sidecars, scene_path.name)
    provider = parsed.get("provider")
    print(f"{scene_path.name}: {provider} via {[Path(item).name for item in sidecars]}")
    if provider != expected:
        raise SystemExit(f"Expected provider {expected}, got {provider} for {scene_path}")
    checked += 1

if checked == 0:
    print("No local metadata samples found; parser smoke test skipped.")
else:
    print(f"Metadata parser smoke test passed for {checked} sample scenes.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -
} finally {
    Pop-Location
}
