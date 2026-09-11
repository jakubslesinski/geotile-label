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
import json
import os
import struct
import sys
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="geotile-source-export-") as temp_dir:
    root = Path(temp_dir)
    os.environ["DATA_DIR"] = str(root)
    sys.path.insert(0, "backend")

    import pyarrow.parquet as pq
    from services.source_annotation_export import export_source_annotations_geoparquet

    project_id = "project-source-export"
    project_dir = root / "projects" / project_id

    def write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    write_json(project_dir / "project.json", {
        "id": project_id,
        "name": "Source export smoke",
        "profile": {
            "modality": "EO",
            "allowed_modalities": ["EO"],
            "georeferencing": "GEO",
            "allowed_georeferencing": ["GEO"],
            "allow_mixed_scenes": False,
            "sensors": ["WorldView"],
            "annotation_mode": "rotated_bbox",
            "labeling_author_email": "analyst@example.com",
            "default_preprocessing_profile": "eo_rgb_percentile",
            "default_split_strategy": "spatial_block_split",
        },
    })
    write_json(project_dir / "classes.json", [
        {"id": 0, "name": "vehicle"},
        {"id": 1, "name": "vehicle_obb"},
    ])

    geo_manifest = {
        "schema_name": "geotile_scene_manifest",
        "schema_version": 3,
        "scene_id": "geo-scene",
        "source_scene_uid": "sha256:geo",
        "source_identity_status": "complete",
        "source_file_sha256": "geo",
        "filename": "geo.tif",
        "provider": "Maxar",
        "sensor": "WorldView",
        "modality": "EO",
        "georeferencing": "GEO",
        "acquisition_datetime_utc": "2025-01-02T03:04:05+00:00",
        "image": {"width": 100, "height": 100},
        "geospatial": {
            "has_geo": True,
            "crs": "EPSG:3857",
            "transform": [1, 0, 1000, 0, -1, 2000],
        },
    }
    no_geo_manifest = {
        "schema_name": "geotile_scene_manifest",
        "schema_version": 3,
        "scene_id": "no-geo-scene",
        "source_scene_uid": "sha256:no-geo",
        "source_identity_status": "complete",
        "filename": "no_geo.png",
        "sensor": "Other",
        "modality": "EO",
        "georeferencing": "NO_GEO",
        "image": {"width": 100, "height": 100},
        "geospatial": {"has_geo": False},
    }
    geo_annotations = [
        {
            "id": "aabb-1",
            "source_annotation_id": "aabb-1",
            "scene_id": "geo-scene",
            "class_id": 0,
            "geometry_type": "bbox",
            "bbox": [10, 10, 30, 30],
            "annotation_source": "manual",
            "annotator_email": "analyst@example.com",
            "created_at": "2025-01-02T04:00:00+00:00",
            "updated_at": "2025-01-02T04:00:00+00:00",
        },
        {
            "id": "obb-1",
            "source_annotation_id": "obb-1",
            "scene_id": "geo-scene",
            "class_id": 1,
            "geometry_type": "rotated_bbox",
            "bbox": [5, 10, 30, 30],
            "polygon_scene_px": [[10, 10], [30, 20], [25, 30], [5, 20]],
            "orientation_angle_deg": 26.565,
            "annotation_source": "manual",
            "annotator_email": "analyst@example.com",
            "created_at": "2025-01-02T04:01:00+00:00",
            "updated_at": "2025-01-02T04:01:00+00:00",
        },
    ]
    no_geo_annotations = [{
        "id": "no-geo-1",
        "source_annotation_id": "no-geo-1",
        "scene_id": "no-geo-scene",
        "class_id": 0,
        "geometry_type": "bbox",
        "bbox": [1, 2, 5, 8],
        "annotation_source": "manual",
        "annotator_email": "analyst@example.com",
    }]

    for scene_id, filename, manifest, annotations in (
        ("geo-scene", "geo.tif", geo_manifest, geo_annotations),
        ("no-geo-scene", "no_geo.png", no_geo_manifest, no_geo_annotations),
    ):
        scene_dir = project_dir / "scenes" / scene_id
        write_json(scene_dir / "scene.json", {"id": scene_id, "filename": filename})
        write_json(scene_dir / "scene_manifest.json", manifest)
        write_json(scene_dir / "annotations.json", annotations)

    selected_path = root / "exports" / "annotations_wgs84.geoparquet"
    summary = export_source_annotations_geoparquet(project_id, selected_path)
    native_path = selected_path.with_name("annotations_native.geoparquet")
    summary_path = selected_path.with_name("annotation_summary.json")

    assert selected_path.is_file()
    assert native_path.is_file()
    assert summary_path.is_file()
    assert summary["source_annotation_count"] == 3
    assert summary["wgs84_annotation_count"] == 2
    assert summary["native_annotation_count"] == 2
    assert summary["omitted_from_wgs84_count"] == 1

    wgs_table = pq.read_table(selected_path)
    native_table = pq.read_table(native_path)
    assert wgs_table.num_rows == 2
    assert native_table.num_rows == 2
    assert b"geo" in (wgs_table.schema.metadata or {})
    assert "source_scene_uid" in wgs_table.column_names
    assert "annotator_email" in wgs_table.column_names
    assert "native_crs" in native_table.column_names

    native_rows = native_table.to_pylist()
    obb_geometry = next(row["geometry"] for row in native_rows if row["source_annotation_id"] == "obb-1")
    point_count = struct.unpack_from("<I", obb_geometry, 9)[0]
    points = [struct.unpack_from("<dd", obb_geometry, 13 + index * 16) for index in range(point_count)]
    assert len({round(point[0], 6) for point in points}) > 2
    assert len({round(point[1], 6) for point in points}) > 2

print("Source annotation GeoParquet smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
} finally {
    Pop-Location
}
