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
import shutil
import sys
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="geotile-scene-identity-") as value:
    root = Path(value)
    os.environ["DATA_DIR"] = str(root / "data")
    sys.path.insert(0, "backend")

    from db.storage import load_scene_json, save_json, save_scene_json
    from services.scene_packages.identity import compute_scene_package_identity
    from services.scene_packages.working_view import working_grid_uid
    from services.scene_sources import save_scene_sources

    project_id = "identity-test"
    scene_id = "logical-scene"
    source_a = root / "source-a"
    source_a.mkdir()
    (source_a / "scene.tif").write_bytes(b"stable-raster-content")
    (source_a / "metadata.json").write_text('{"version": 1}', encoding="utf-8")

    save_json(project_id, "project", {
        "id": project_id,
        "name": "Identity test",
        "scene_folder": str(source_a),
        "scene_count": 1,
    })
    save_scene_sources(project_id, {"sources": [{
        "source_id": "src_stable",
        "provider": "generic",
        "root_path": str(source_a),
        "enabled": True,
        "added_at": "2026-01-01T00:00:00+00:00",
    }]})
    save_scene_json(project_id, scene_id, "scene", {
        "id": scene_id,
        "filename": "scene.tif",
        "preparation_status": "ready",
    })
    save_scene_json(project_id, scene_id, "scene_manifest", {
        "schema_name": "geotile_scene_manifest",
        "schema_version": 4,
        "scene_id": scene_id,
        "source_package": {
            "source_id": "src_stable",
            "provider_scene_id": "provider-1",
            "identity_asset_ids": ["raster"],
            "assets": [
                {"asset_id": "raster", "role": "primary_raster", "part_id": "R1C1", "relative_path": "scene.tif"},
                {"asset_id": "metadata", "role": "metadata", "relative_path": "metadata.json"},
            ],
        },
        "source_identity": {"status": "pending"},
        "working_view": {"preparation_status": "ready"},
    })

    first = compute_scene_package_identity(project_id, scene_id)
    assert first["status"] == "complete"
    assert first["identity_strength"] == "heuristic"
    assert first["source_scene_uid"] is None
    first_candidate_uid = first["source_scene_candidate_uid"]
    first_package = first["source_package_fingerprint"]

    (source_a / "metadata.json").write_text('{"version": 2, "extra": true}', encoding="utf-8")
    metadata_changed = compute_scene_package_identity(project_id, scene_id)
    assert metadata_changed["source_scene_candidate_uid"] == first_candidate_uid
    assert metadata_changed["source_package_fingerprint"] != first_package

    source_b = root / "source-b"
    shutil.copytree(source_a, source_b)
    save_scene_sources(project_id, {"sources": [{
        "source_id": "src_stable",
        "provider": "generic",
        "root_path": str(source_b),
        "enabled": True,
        "added_at": "2026-01-01T00:00:00+00:00",
    }]})
    relinked = compute_scene_package_identity(project_id, scene_id)
    assert relinked["source_scene_candidate_uid"] == first_candidate_uid
    assert relinked["status"] == "complete"

    exact = compute_scene_package_identity(project_id, scene_id, require_exact=True)
    assert exact["identity_strength"] == "exact"
    assert exact["source_scene_uid"].startswith("scene-sha256:")
    exact_uid = exact["source_scene_uid"]

    grid = {"width": 100, "height": 200, "crs": "EPSG:32634", "transform": [1, 0, 0, 0, -1, 0], "channels": 3}
    assert working_grid_uid(grid, "variant-a") == working_grid_uid(grid, "variant-a")
    assert working_grid_uid(grid, "variant-a") != working_grid_uid(grid, "variant-b")

    (source_b / "scene.tif").write_bytes(b"different-raster-content")
    changed = compute_scene_package_identity(project_id, scene_id, require_exact=True)
    assert changed["status"] == "changed"
    assert changed["source_scene_uid"] != exact_uid
    assert changed["change_detection_strength"] == "exact"
    scene = load_scene_json(project_id, scene_id, "scene", default={})
    assert scene["preparation_status"] == "source_changed"

print("Scene identity, relink and working-grid smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "Scene identity smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
