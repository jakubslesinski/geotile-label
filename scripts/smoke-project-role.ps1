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
import json
import os
import sys
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="geotile-role-") as temp_dir:
    root = Path(temp_dir)
    os.environ["DATA_DIR"] = str(root)
    os.environ["MODELS_ROOT"] = str(root / "models")
    sys.path.insert(0, "backend")

    from db.storage import load_json, save_json
    from fastapi import HTTPException
    from models.annotation_package import AnnotationImportPreviewRequest
    from routers.annotation_workflow import (
        _project_role,
        _require_role,
        create_annotation_import_preview,
        get_annotation_package_preview,
    )

    # === Gate A: free migration — pre-T2 project.json has no project_role ===
    pid = "legacy-proj"
    legacy_dir = root / "projects" / pid
    legacy_dir.mkdir(parents=True)
    # Written raw (not via save_json) so normalization does not add the field first.
    (legacy_dir / "project.json").write_text(json.dumps({
        "id": pid, "name": "Legacy", "schema_version": 2,
        "scene_folder": str(root / "src"),
        "profile": {
            "modality": "EO", "georeferencing": "NO_GEO", "annotation_mode": "bbox",
            "labeling_author_email": "a@example.com",
        },
    }), encoding="utf-8")

    loaded = load_json(pid, "project")
    assert loaded["profile"]["project_role"] == "labeling", loaded["profile"]
    # Migration is persisted, not just in-memory.
    on_disk = json.loads((legacy_dir / "project.json").read_text(encoding="utf-8"))
    assert on_disk["profile"]["project_role"] == "labeling", on_disk["profile"]
    assert _project_role(pid) == "labeling"

    # === Gate B: role enforcement ==========================================
    def make(pid, role):
        save_json(pid, "project", {
            "id": pid, "name": pid, "scene_folder": str(root / "src"),
            "profile": {"modality": "EO", "georeferencing": "NO_GEO",
                        "annotation_mode": "bbox", "labeling_author_email": "a@example.com",
                        "project_role": role},
        })

    make("lab", "labeling")
    make("rev", "review")
    assert _project_role("lab") == "labeling" and _project_role("rev") == "review"

    # Matching role passes the gate (no raise).
    _require_role("lab", "labeling", "x")
    _require_role("rev", "review", "x")

    # Review project cannot export a package.
    try:
        get_annotation_package_preview("rev")
        raise AssertionError("review project should not export a package")
    except HTTPException as exc:
        assert exc.status_code == 409, exc.status_code
        assert "labeling" in exc.detail and "review" in exc.detail, exc.detail

    # Labeling project cannot import packages (409 fires before touching paths).
    try:
        create_annotation_import_preview("lab", AnnotationImportPreviewRequest(package_paths=["/nope.zip"]))
        raise AssertionError("labeling project should not import packages")
    except HTTPException as exc:
        assert exc.status_code == 409, exc.status_code
        assert "review" in exc.detail and "labeling" in exc.detail, exc.detail

    # === Gate C: the review channel is gated the other way round ===========
    from models.annotation_package import ReviewImportRequest, SceneReviewRequest
    from routers.annotation_workflow import (
        create_review_import_preview,
        get_review_package_preview,
        put_scene_review,
    )

    # Only a review project may pass a verdict or ship a review package.
    for call, label in (
        (lambda: put_scene_review("lab", "s1", SceneReviewRequest(review_status="accepted")), "verdict"),
        (lambda: get_review_package_preview("lab", "a@example.com"), "review package export"),
    ):
        try:
            call()
            raise AssertionError(f"labeling project should not allow {label}")
        except HTTPException as exc:
            assert exc.status_code == 409, (label, exc.status_code)
            assert "review" in exc.detail, (label, exc.detail)

    # ...and only a labeling project may take a review package back in.
    try:
        create_review_import_preview("rev", ReviewImportRequest(package_paths=["/nope.zip"]))
        raise AssertionError("review project should not import review packages")
    except HTTPException as exc:
        assert exc.status_code == 409, exc.status_code
        assert "labeling" in exc.detail, exc.detail

print("Project role (T2) smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "Project role smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
