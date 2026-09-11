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
import sys

sys.path.insert(0, "backend")

from services.attribute_engine import compute_source_attributes, compute_tile_attributes

bbox = {
    "id": "bbox-1",
    "source_annotation_id": "bbox-1",
    "scene_id": "scene-1",
    "class_id": 0,
    "geometry_type": "bbox",
    "bbox": [10, 20, 50, 40],
    "annotation_source": "manual",
}
no_geo_manifest = {
    "scene_id": "scene-1",
    "schema_version": 2,
    "modality": "EO",
    "geospatial": {"has_geo": False},
}
bbox_attributes = compute_source_attributes(bbox, no_geo_manifest, "vehicle")
assert bbox_attributes["attribute_status"] == "computed"
assert bbox_attributes["orientation"]["orientation_px_deg"] is None
assert bbox_attributes["geospatial"]["reason"] == "scene_not_georeferenced"

obb = {
    **bbox,
    "id": "obb-1",
    "source_annotation_id": "obb-1",
    "geometry_type": "rotated_bbox",
    "rotated_bbox": {"cx": 30, "cy": 30, "width": 40, "height": 20, "angle_deg": 0},
    "polygon_scene_px": [[10, 20], [50, 20], [50, 40], [10, 40]],
    "front_vector_scene_px": [1, 0],
    "orientation_angle_deg": 0,
}
geo_manifest = {
    **no_geo_manifest,
    "geospatial": {
        "has_geo": True,
        "crs": "EPSG:3857",
        "transform": [1, 0, 1000, 0, -1, 2000],
    },
}
geo_attributes = compute_source_attributes(obb, geo_manifest, "vehicle")
assert geo_attributes["attribute_status"] == "computed", geo_attributes
assert geo_attributes["geospatial"]["available"] is True
assert geo_attributes["geospatial"]["area_m2"] > 0
assert geo_attributes["orientation"]["orientation_px_deg"] == 0

tile_attributes = compute_tile_attributes(
    obb,
    {"filename": "tile.png", "x0": 30, "y0": 0},
    tile_size=40,
    min_box_fraction=0.3,
)
assert tile_attributes is not None
assert tile_attributes["is_clipped"] is True
assert abs(tile_attributes["visible_fraction"] - 0.5) < 1e-9
assert tile_attributes["exportable_yolo"] is True

print("Attribute engine smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
} finally {
    Pop-Location
}
