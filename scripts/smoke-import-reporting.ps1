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

$env:GEOTILE_ENABLE_YOLO = "0"

$Script = @'
import os
import sys
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="geotile-import-report-") as temp_dir:
    root = Path(temp_dir)
    os.environ["DATA_DIR"] = str(root)
    os.environ["MODELS_ROOT"] = str(root / "models")
    sys.path.insert(0, "backend")

    from db.storage import save_json, save_scene_json
    from services.annotation_package import save_annotation_package
    from services.annotation_import import (
        apply_annotation_import,
        preview_annotation_import,
    )

    UID = "scene-uid-report"

    def make_project(pid, email, grid, annotations):
        save_json(pid, "project", {
            "id": pid, "name": pid, "scene_folder": str(root / "src"),
            "profile": {"modality": "EO", "georeferencing": "NO_GEO", "annotation_mode": "bbox",
                        "labeling_author_email": email},
        })
        save_json(pid, "classes", [{"id": 0, "name": "vehicle", "color": "#ff0000"}])
        save_scene_json(pid, "s1", "scene", {"id": "s1", "filename": "lot.tif",
                                             "annotation_count": len(annotations)})
        save_scene_json(pid, "s1", "scene_manifest", {
            "source_scene_uid": UID, "source_file_sha256": "a" * 64, "source_file_size": 1234,
            "filename": "lot.tif", "sensor": "test-sensor",
            "acquisition_datetime_utc": "2026-01-01T00:00:00Z",
            "working_view": {"working_grid_uid": grid},
            "image": {"width": 800, "height": 600, "file_size": 1234},
            "geospatial": {"has_geo": False}, "metadata_status": "ok",
        })
        save_scene_json(pid, "s1", "annotations", annotations)

    def ann(aid, bbox):
        return {
            "id": aid, "source_annotation_id": aid, "scene_id": "s1", "class_id": 0,
            "geometry_type": "bbox", "bbox": bbox, "is_negative": False,
            "annotation_source": "manual", "annotator_email": "analyst@example.com",
            "created_at": "2026-01-01T10:00:00Z", "updated_at": "2026-01-01T10:00:00Z",
        }

    # === Gate A: "changed", not "collision" ================================
    # Manager already holds aaa1; analyst ships aaa1 with different geometry.
    make_project("analyst", "analyst@example.com", "grid-A", [ann("aaa1", [11, 11, 51, 41])])
    make_project("manager", "manager@example.com", "grid-A", [ann("aaa1", [10, 10, 50, 40])])

    pkg = root / "out" / "changed.zip"
    save_annotation_package("analyst", str(pkg))
    p = preview_annotation_import("manager", [str(pkg)])

    assert "changed_annotation_count" in p, sorted(p)
    assert "collision_count" not in p, "old key must be gone"
    assert p["changed_annotation_count"] == 1, p["changed_annotation_count"]
    assert p["new_annotation_count"] == 0 and p["duplicate_annotation_count"] == 0, p
    # Since T5 a scoped (v2) package updates a changed annotation instead of dropping
    # it, so this preview now has an effect. The "NOT updated" wording survives only
    # for additive v1 packages (covered by smoke-scoped-replacement).
    assert p["can_apply"] is True, p["can_apply"]
    joined = " ".join(p["warnings"]).lower()
    assert "will be updated" in joined, p["warnings"]
    assert "collision" not in joined, ("the word 'collision' must be gone", p["warnings"])

    r = apply_annotation_import("manager", p["preview_id"])
    assert r["changed_annotation_count"] == 1 and "collision_count" not in r, sorted(r)

    # === Gate B: incompatible working view is NOT "ambiguous" ==============
    # Same source scene, different working grid on the manager side.
    make_project("analyst2", "analyst@example.com", "grid-A", [ann("bbb1", [10, 10, 50, 40])])
    make_project("manager2", "manager@example.com", "grid-B", [])

    pkg2 = root / "out" / "grid.zip"
    save_annotation_package("analyst2", str(pkg2))
    g = preview_annotation_import("manager2", [str(pkg2)])

    assert g["incompatible_scene_count"] == 1, g["incompatible_scene_count"]
    assert g["ambiguous_scene_count"] == 0, g["ambiguous_scene_count"]
    assert g["missing_scene_count"] == 0, g["missing_scene_count"]
    assert g["blocked_annotation_count"] == 1, g["blocked_annotation_count"]
    assert g["can_apply"] is False, g["can_apply"]
    gjoined = " ".join(g["warnings"]).lower()
    assert "working view" in gjoined or "working grid" in gjoined, g["warnings"]
    assert "matched more than one" not in gjoined, ("must not mislabel as ambiguity", g["warnings"])
    entry = g["incompatible_scenes"][0]
    assert entry["method"] == "working_grid_uid" and entry["confidence"] == "incompatible", entry

print("Import reporting (T1) smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "Import reporting smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
