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
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="geotile-scope-") as temp_dir:
    root = Path(temp_dir)
    os.environ["DATA_DIR"] = str(root)
    os.environ["MODELS_ROOT"] = str(root / "models")
    sys.path.insert(0, "backend")

    from db.storage import save_json, save_scene_json
    from services.annotation_package import save_annotation_package
    from services.annotation_import import (
        load_annotation_package,
        preview_annotation_import,
    )

    OWNER = "analyst@example.com"

    def make_project(pid, uid, email=OWNER, annotations=None):
        save_json(pid, "project", {
            "id": pid, "name": pid, "scene_folder": str(root / "src"),
            "profile": {"modality": "EO", "georeferencing": "NO_GEO", "annotation_mode": "bbox",
                        "labeling_author_email": email},
        })
        save_json(pid, "classes", [{"id": 0, "name": "vehicle", "color": "#ff0000"}])
        anns = annotations if annotations is not None else [{
            "id": "a1", "source_annotation_id": "a1", "scene_id": "s1", "class_id": 0,
            "geometry_type": "bbox", "bbox": [10, 10, 50, 40], "is_negative": False,
            "annotation_source": "manual", "annotator_email": email,
        }]
        save_scene_json(pid, "s1", "scene", {"id": "s1", "filename": "lot.tif",
                                             "annotation_count": len(anns)})
        save_scene_json(pid, "s1", "scene_manifest", {
            "source_scene_uid": uid, "source_file_sha256": "a" * 64, "source_file_size": 1234,
            "filename": "lot.tif", "sensor": "test-sensor",
            "acquisition_datetime_utc": "2026-01-01T00:00:00Z",
            "working_view": {"working_grid_uid": "grid-A"},
            "image": {"width": 800, "height": 600, "file_size": 1234},
            "geospatial": {"has_geo": False}, "metadata_status": "ok",
        })
        save_scene_json(pid, "s1", "annotations", anns)

    def read_manifest(zip_path):
        with zipfile.ZipFile(zip_path) as archive:
            return json.loads(archive.read("annotation_package_manifest.json").decode("utf-8"))

    def downgrade_to_v1(src_zip, dst_zip):
        """Rewrite a v2 package as a v1 one (no scope) with valid checksums."""
        with zipfile.ZipFile(src_zip) as archive:
            payload = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(payload["annotation_package_manifest.json"].decode("utf-8"))
        manifest["schema_version"] = 1
        manifest.pop("scope", None)
        manifest.pop("superseded_source_annotation_ids", None)
        payload["annotation_package_manifest.json"] = json.dumps(
            manifest, indent=2, ensure_ascii=False).encode("utf-8")
        lines = [
            f"{hashlib.sha256(data).hexdigest()}  {name}"
            for name, data in sorted(payload.items()) if name != "SHA256SUMS.txt"
        ]
        payload["SHA256SUMS.txt"] = ("\n".join(lines) + "\n").encode("utf-8")
        with zipfile.ZipFile(dst_zip, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(payload.items()):
                archive.writestr(name, data)

    # === Gate A: v2 carries an explicit scope ==============================
    make_project("analyst", "uid-scene-1")
    pkg1 = root / "out" / "v1st.zip"
    result1 = save_annotation_package("analyst", str(pkg1))
    m1 = read_manifest(pkg1)
    assert m1["schema_version"] == 2, m1["schema_version"]
    scope = m1["scope"]
    assert scope["owner_email"] == OWNER, scope
    assert scope["scene_uids"] == ["uid-scene-1"], scope
    assert scope["authoritative_as_of"], scope
    assert scope["supersedes_package_id"] is None, scope  # first export chains to nothing
    assert m1["superseded_source_annotation_ids"] == [], m1

    # === Gate B: second export auto-chains onto the first ==================
    pkg2 = root / "out" / "v2nd.zip"
    result2 = save_annotation_package("analyst", str(pkg2))
    m2 = read_manifest(pkg2)
    assert m2["scope"]["supersedes_package_id"] == m1["package_id"], (m2["scope"], m1["package_id"])
    assert result2["supersedes_package_id"] == m1["package_id"], result2

    # Chained pair selected together is NOT a conflict.
    make_project("manager", "uid-scene-1", email="manager@example.com", annotations=[])
    chained = preview_annotation_import("manager", [str(pkg1), str(pkg2)])
    conflict_errors = [e for e in chained["errors"] if "supersedes" in e]
    assert not conflict_errors, chained["errors"]

    # === Gate C: v1 still imports, flagged additive-only ===================
    legacy = root / "out" / "legacy_v1.zip"
    downgrade_to_v1(pkg1, legacy)
    loaded = load_annotation_package(str(legacy))
    assert loaded["manifest"]["schema_version"] == 1
    assert loaded["scope"]["additive_only"] is True, loaded["scope"]
    assert loaded["scope"]["owner_email"] == OWNER, loaded["scope"]
    # Scene uids are synthesized from the manifest when no scope was declared.
    assert loaded["scope"]["scene_uids"] == ["uid-scene-1"], loaded["scope"]
    assert loaded["scope"]["supersedes_package_id"] is None

    v1_preview = preview_annotation_import("manager", [str(legacy)])
    entry = v1_preview["packages"][0]
    assert entry["schema_version"] == 1 and entry["additive_only"] is True, entry
    assert v1_preview["new_annotation_count"] == 1, v1_preview["new_annotation_count"]

    # === Gate D: same owner, same scene, no chain -> hard error ============
    # A second project (same owner, same scene uid) has its own history, so its
    # package chains to nothing and cannot be ordered against the first.
    make_project("analyst-other", "uid-scene-1")
    pkg_other = root / "out" / "other.zip"
    save_annotation_package("analyst-other", str(pkg_other))

    clash = preview_annotation_import("manager", [str(pkg1), str(pkg_other)])
    clash_errors = [e for e in clash["errors"] if "supersedes" in e]
    assert clash_errors, clash["errors"]
    assert "neither supersedes the other" in clash_errors[0], clash_errors
    assert OWNER in clash_errors[0], clash_errors
    assert clash["can_apply"] is False, clash["can_apply"]

    # === Gate E: selecting the same package twice is a duplicate, not a clash ==
    twice = preview_annotation_import("manager", [str(pkg1), str(pkg1)])
    assert not [e for e in twice["errors"] if "supersedes" in e], twice["errors"]
    assert any("selected more than once" in w for w in twice["warnings"]), twice["warnings"]

print("Annotation package scope (T4) smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "Package scope smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
