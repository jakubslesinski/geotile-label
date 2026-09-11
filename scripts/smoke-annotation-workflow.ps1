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
import hashlib
import os
import sys
import tempfile
import zipfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="geotile-annotation-workflow-") as temp_dir:
    root = Path(temp_dir)
    os.environ["DATA_DIR"] = str(root)
    sys.path.insert(0, "backend")

    from services.annotation_import import apply_annotation_import, preview_annotation_import
    from services.annotation_package import preview_annotation_package, save_annotation_package
    from services.annotation_summary import compute_project_annotation_summary

    def write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def project(project_id, name, author, classes, scene_id, annotations):
        project_dir = root / "projects" / project_id
        scene_dir = project_dir / "scenes" / scene_id
        write_json(project_dir / "project.json", {
            "id": project_id,
            "name": name,
            "profile": {
                "modality": "EO",
                "allowed_modalities": ["EO"],
                "georeferencing": "GEO",
                "allowed_georeferencing": ["GEO"],
                "allow_mixed_scenes": False,
                "sensors": ["WorldView"],
                "annotation_mode": "rotated_bbox",
                "labeling_author_email": author,
                "default_preprocessing_profile": "eo_rgb_percentile",
                "default_split_strategy": "spatial_block_split",
            },
        })
        write_json(project_dir / "classes.json", classes)
        write_json(scene_dir / "scene.json", {"id": scene_id, "filename": "same_scene.tif"})
        source_digest = hashlib.sha256(b"shared-scene").hexdigest()
        write_json(scene_dir / "scene_manifest.json", {
            "schema_name": "geotile_scene_manifest",
            "schema_version": 3,
            "scene_id": scene_id,
            "source_scene_uid": f"sha256:{source_digest}",
            "source_identity_status": "complete",
            "source_identity_method": "sha256",
            "source_identity_strength": "exact",
            "source_file_sha256": source_digest,
            "source_file_size": 123456,
            "filename": "same_scene.tif",
            "provider": "Maxar",
            "sensor": "WorldView",
            "modality": "EO",
            "georeferencing": "GEO",
            "metadata_status": "ok",
            "acquisition_datetime_utc": "2025-01-02T03:04:05+00:00",
            "image": {"width": 100, "height": 100, "file_size": 123456},
            "geospatial": {
                "has_geo": True,
                "crs": "EPSG:3857",
                "transform": [1, 0, 1000, 0, -1, 2000],
            },
        })
        write_json(scene_dir / "annotations.json", annotations)
        return project_dir

    analyst_annotations = [
        {
            "id": "vehicle-1",
            "source_annotation_id": "vehicle-1",
            "scene_id": "analyst-scene",
            "class_id": 0,
            "geometry_type": "bbox",
            "bbox": [10, 10, 30, 30],
            "annotation_source": "manual",
            "annotator_email": "analyst@example.com",
            "created_at": "2025-01-02T04:00:00+00:00",
            "updated_at": "2025-01-02T04:00:00+00:00",
        },
        {
            "id": "aircraft-1",
            "source_annotation_id": "aircraft-1",
            "scene_id": "analyst-scene",
            "class_id": 1,
            "geometry_type": "rotated_bbox",
            "bbox": [40, 40, 70, 70],
            "polygon_scene_px": [[45, 40], [70, 50], [65, 70], [40, 60]],
            "orientation_angle_deg": 21.8,
            "annotation_source": "manual",
            "annotator_email": "analyst@example.com",
        },
    ]
    analyst_dir = project(
        "analyst", "Analyst project", "analyst@example.com",
        [{"id": 0, "name": "vehicle", "color": "#ff0000"}, {"id": 1, "name": "aircraft", "color": "#00ff00"}],
        "analyst-scene", analyst_annotations,
    )
    manager_dir = project(
        "manager", "Manager project", "manager@example.com",
        [{"id": 7, "name": " Vehicle ", "color": "#0000ff"}],
        "manager-scene", [],
    )

    package_preview = preview_annotation_package("analyst")
    assert package_preview["can_export"] and package_preview["annotation_count"] == 2
    assert package_preview["suggested_filename"].startswith(
        "GeoTileAnnotations_Analyst_project_analyst_example_com_"
    )
    assert package_preview["suggested_filename"].endswith(
        f"_{package_preview['package_id'][:8]}.zip"
    )
    package_path = root / package_preview["suggested_filename"]
    package_result = save_annotation_package(
        "analyst", package_path, package_preview["package_id"]
    )
    assert package_result["annotation_count"] == 2
    assert package_result["package_id"] == package_preview["package_id"]
    with zipfile.ZipFile(package_path) as archive:
        names = set(archive.namelist())
        assert "annotation_package_manifest.json" in names
        assert "SHA256SUMS.txt" in names
        assert "annotations_wgs84.geoparquet" in names
        assert not any("tiles" in name or "dataset" in name for name in names)
        package_manifest = json.loads(archive.read("annotation_package_manifest.json"))
        assert package_manifest["package_id"] == package_preview["package_id"]
        assert package_manifest["archive_filename"] == package_path.name

    preview = preview_annotation_import("manager", [str(package_path)])
    assert preview["errors"] == []
    assert preview["matched_scene_count"] == 1
    assert preview["scene_matches"][0]["method"] == "source_identity"
    assert preview["new_annotation_count"] == 1
    assert preview["blocked_annotation_count"] == 1
    assert preview["missing_classes"] == ["aircraft"]

    report = apply_annotation_import(
        "manager", preview["preview_id"], create_missing_classes=True
    )
    assert report["imported_annotation_count"] == 2
    manager_annotations = json.loads(
        (manager_dir / "scenes" / "manager-scene" / "annotations.json").read_text(encoding="utf-8")
    )
    assert len(manager_annotations) == 2
    assert {item["scene_id"] for item in manager_annotations} == {"manager-scene"}
    assert {item["annotator_email"] for item in manager_annotations} == {"analyst@example.com"}
    assert any(item["geometry_type"] == "rotated_bbox" for item in manager_annotations)
    assert {item["import_id"] for item in manager_annotations} == {report["import_id"]}
    assert all(item["source_package_id"] for item in manager_annotations)
    manager_classes = json.loads((manager_dir / "classes.json").read_text(encoding="utf-8"))
    assert {item["name"].strip().casefold() for item in manager_classes} == {"vehicle", "aircraft"}
    import_root = manager_dir / "annotation_imports" / report["import_id"]
    assert (import_root / "import_manifest.json").is_file()
    assert (import_root / "import_report.json").is_file()
    assert (import_root / "source_package_manifest.json").is_file()
    assert any((import_root / "snapshots").iterdir())

    summary = compute_project_annotation_summary("manager")
    assert summary["scene_count"] == 1
    assert summary["scenes_with_annotations"] == 1
    assert summary["annotation_count"] == 2
    assert summary["missing_author_count"] == 0
    assert summary["per_author"] == [{"annotator_email": "analyst@example.com", "annotation_count": 2}]
    assert summary["import_count"] == 1
    assert summary["import_totals"]["imported_annotation_count"] == 2
    assert summary["last_import_report"]["import_id"] == report["import_id"]
    assert summary["per_scene"][0]["import_ids"] == [report["import_id"]]
    assert len(summary["per_scene"][0]["package_ids"]) == 1

    duplicate_preview = preview_annotation_import("manager", [str(package_path)])
    assert duplicate_preview["new_annotation_count"] == 0
    assert duplicate_preview["duplicate_annotation_count"] == 2

    malicious_path = root / "malicious.zip"
    with zipfile.ZipFile(malicious_path, "w") as archive:
        archive.writestr("../escape.json", "{}")
    malicious_preview = preview_annotation_import("manager", [str(malicious_path)])
    assert malicious_preview["valid_package_count"] == 0
    assert malicious_preview["errors"]

    tampered_path = root / "tampered.zip"
    with zipfile.ZipFile(package_path) as source, zipfile.ZipFile(tampered_path, "w") as target:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "classes.json":
                payload = b"[]"
            target.writestr(info, payload)
    tampered_preview = preview_annotation_import("manager", [str(tampered_path)])
    assert tampered_preview["valid_package_count"] == 0
    assert any("Checksum mismatch" in error for error in tampered_preview["errors"])

print("Annotation package and import workflow smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
} finally {
    Pop-Location
}
