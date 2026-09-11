param(
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

if (-not $PythonPath) {
    $PackedPython = Join-Path $RepoRoot ".desktop-build\backend-env\python.exe"
    $PythonPath = if (Test-Path $PackedPython) { $PackedPython } else { "C:\ProgramData\anaconda3\python.exe" }
}

$Script = @'
import json
import os
import sys
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="geotile-migration-") as temp_dir:
    root = Path(temp_dir)
    os.environ["DATA_DIR"] = str(root / "data")
    sys.path.insert(0, "backend")

    from db.storage import load_json, load_scene_json
    from services.project_diagnostics import build_project_diagnostics

    project_id = "legacy-013"
    project_root = root / "data" / "projects" / project_id
    scene_root = project_root / "scenes" / "scene-stable-id"
    scene_root.mkdir(parents=True)

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    write(project_root / "project.json", {
        "id": project_id,
        "name": "Legacy 0.1.3 project",
        "schema_version": 1,
        "app_version": "0.1.3",
        "scene_folder": "C:/missing-scenes",
        "scene_count": 1,
    })
    write(scene_root / "scene.json", {
        "id": "scene-stable-id",
        "filename": "legacy-scene.tif",
        "status": "tiled",
    })
    write(scene_root / "scene_manifest.json", {
        "schema_name": "geotile_scene_manifest",
        "schema_version": 1,
        "scene_id": "different-scene-id",
        "filename": "legacy-scene.tif",
        "source_identity_status": "pending",
    })
    write(scene_root / "annotations.json", [{
        "id": "stable-annotation-id",
        "class_id": 3,
        "bbox": [1, 2, 10, 12],
    }])
    legacy_cache = scene_root / "tiles" / "images" / "tile.png"
    legacy_cache.parent.mkdir(parents=True)
    legacy_cache.write_bytes(b"legacy")
    preview_cache = project_root / "tile_catalogs" / "catalog" / "preview_cache" / "preview.png"
    preview_cache.parent.mkdir(parents=True)
    preview_cache.write_bytes(b"preview")
    geo_cache = project_root / "scenes" / "scene-stable-id" / "geo_tile_cache" / "0.png"
    geo_cache.parent.mkdir(parents=True)
    geo_cache.write_bytes(b"geo")
    write(project_root / "annotation_imports" / "import-1" / "import_report.json", {
        "import_id": "import-1",
        "status": "imported",
        "imported_annotation_count": 1,
        "collisions": [{"source_annotation_id": "x", "bbox": [1, 2, 3, 4], "geometry": {"type": "Polygon"}}],
    })

    migrated = load_json(project_id, "project")
    assert migrated["schema_version"] == 2
    assert migrated["migration"]["migrated_from_schema_version"] == 1
    annotations = load_scene_json(project_id, "scene-stable-id", "annotations", default=[])
    assert annotations[0]["id"] == "stable-annotation-id"
    assert annotations[0]["source_annotation_id"] == "stable-annotation-id"
    assert annotations[0]["scene_id"] == "scene-stable-id"
    assert annotations[0]["annotation_source"] == "manual"

    backups = list((project_root / ".migration_backups").glob("schema-1-to-2-*"))
    assert len(backups) == 1
    backup_annotation = json.loads((backups[0] / "scenes" / "scene-stable-id" / "annotations.json").read_text())
    assert "source_annotation_id" not in backup_annotation[0]
    load_json(project_id, "project")
    load_scene_json(project_id, "scene-stable-id", "annotations", default=[])
    assert len(list((project_root / ".migration_backups").glob("schema-1-to-2-*"))) == 1

    diagnostics = build_project_diagnostics()
    project_diagnostic = next(item for item in diagnostics["projects"] if item["project_id"] == project_id)
    assert diagnostics["supported_schema_versions"]["project"] == 2
    assert diagnostics["privacy"]["full_geometries_included"] is False
    assert project_diagnostic["identity_issue_count"] >= 2
    assert project_diagnostic["tile_storage"]["legacy"]["file_count"] == 1
    assert project_diagnostic["tile_storage"]["preview_cache"]["file_count"] == 1
    assert project_diagnostic["tile_storage"]["geo_tile_cache"]["file_count"] == 1
    latest_import = json.dumps(project_diagnostic["latest_annotation_import"], sort_keys=True)
    assert '"bbox"' not in latest_import
    assert '"geometry"' not in latest_import

print("Project migration and diagnostics smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "Project migration smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
