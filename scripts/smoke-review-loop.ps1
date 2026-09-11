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
import zipfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="geotile-review-") as temp_dir:
    root = Path(temp_dir)
    os.environ["DATA_DIR"] = str(root)
    os.environ["MODELS_ROOT"] = str(root / "models")
    sys.path.insert(0, "backend")

    from db.storage import load_scene_json, save_json, save_scene_json
    from services.annotation_package import save_annotation_package
    from services.annotation_import import apply_annotation_import, preview_annotation_import
    from services.review_package import (
        ReviewPackageError,
        apply_review_import,
        preview_review_import,
        preview_review_package,
        save_review_package,
        set_scene_review,
    )

    UID = "uid-review"
    ANALYST = "analyst@example.com"
    MANAGER = "manager@example.com"

    def ann(aid, bbox, owner=ANALYST):
        return {
            "id": aid, "source_annotation_id": aid, "scene_id": "s1", "class_id": 0,
            "geometry_type": "bbox", "bbox": bbox, "is_negative": False,
            "annotation_source": "manual", "annotator_email": owner,
            "created_at": "2026-01-01T10:00:00+00:00", "updated_at": "2026-01-01T10:00:00+00:00",
        }

    def make_project(pid, email, annotations, role):
        save_json(pid, "project", {
            "id": pid, "name": pid, "scene_folder": str(root / "src"),
            "profile": {"modality": "EO", "georeferencing": "NO_GEO", "annotation_mode": "bbox",
                        "labeling_author_email": email, "project_role": role},
        })
        save_json(pid, "classes", [{"id": 0, "name": "vehicle", "color": "#ff0000"}])
        save_scene_json(pid, "s1", "scene", {"id": "s1", "filename": "lot.tif",
                                             "annotation_count": len(annotations)})
        save_scene_json(pid, "s1", "scene_manifest", {
            "source_scene_uid": UID, "source_file_sha256": "a" * 64, "source_file_size": 1234,
            "filename": "lot.tif", "sensor": "test-sensor",
            "acquisition_datetime_utc": "2026-01-01T00:00:00Z",
            "working_view": {"working_grid_uid": "grid-A"},
            "image": {"width": 800, "height": 600, "file_size": 1234},
            "geospatial": {"has_geo": False}, "metadata_status": "ok",
        })
        save_scene_json(pid, "s1", "annotations", annotations)

    def scene(pid):
        return load_scene_json(pid, "s1", "scene", default={})

    def annotations(pid):
        return load_scene_json(pid, "s1", "annotations", default=[])

    # === Round 1: analyst ships work, manager merges it ====================
    make_project("analyst", ANALYST, [ann("aaa1", [10, 10, 50, 40]), ann("bbb2", [60, 60, 90, 90])],
                 role="labeling")
    make_project("manager", MANAGER, [], role="review")
    pkg = root / "out" / "work.zip"
    save_annotation_package("analyst", str(pkg))
    p = preview_annotation_import("manager", [str(pkg)])
    apply_annotation_import("manager", p["preview_id"])
    assert len(annotations("manager")) == 2

    # Scenes written before the review fields existed carry none of them; they must
    # simply read as "not reviewed" and stay out of any review package.
    legacy_scene = scene("manager")
    assert "review_status" not in legacy_scene, legacy_scene
    empty = preview_review_package("manager", ANALYST)
    assert empty["can_export"] is False and empty["scene_count"] == 0, empty

    # === Manager records a scene-level verdict with two pins ===============
    updated = set_scene_review(
        "manager", "s1",
        status="needs_fix",
        comment="Boxes on the north row are too loose.",
        pins=[{"source_annotation_id": "aaa1", "comment": "too loose"},
              {"source_annotation_id": "bbb2", "comment": "missing front"}],
    )
    assert updated["review_status"] == "needs_fix", updated
    assert updated["review_round"] == 1, updated
    assert updated["reviewed_by"] == MANAGER, updated
    assert len(updated["review_pins"]) == 2, updated

    # A pin must point at an annotation that exists in the scene.
    try:
        set_scene_review("manager", "s1", status="accepted",
                         pins=[{"source_annotation_id": "nope"}])
        raise AssertionError("expected unknown pin to be rejected")
    except ReviewPackageError as exc:
        assert "not in this scene" in str(exc), exc
    # The rejected call must not have changed the verdict.
    assert scene("manager")["review_status"] == "needs_fix", scene("manager")

    # === Manager exports the review package ================================
    rp = preview_review_package("manager", ANALYST)
    assert rp["can_export"] is True, rp["errors"]
    assert rp["scene_count"] == 1 and rp["pin_count"] == 2, rp
    review_zip = root / "out" / "review.zip"
    saved = save_review_package("manager", str(review_zip), ANALYST)
    assert saved["scene_count"] == 1, saved

    with zipfile.ZipFile(review_zip) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("review_package_manifest.json").decode("utf-8"))
    assert {"review_package_manifest.json", "scenes_index.json", "pins.json",
            "README_REVIEW.md", "SHA256SUMS.txt"} <= names, names
    # Verdicts only: no geometry, no annotations travel back to the analyst.
    assert not any("annotation" in n for n in names), names
    assert manifest["scope"]["owner_email"] == ANALYST, manifest["scope"]

    # === Analyst imports the review: verdict lands, geometry untouched =====
    before = annotations("analyst")
    ri = preview_review_import("analyst", [str(review_zip)])
    assert ri["update_count"] == 1 and ri["can_apply"] is True, ri
    report = apply_review_import("analyst", [str(review_zip)])
    assert report["applied_scene_count"] == 1, report

    flagged = scene("analyst")
    assert flagged["review_status"] == "needs_fix", flagged
    assert flagged["review_comment"] == "Boxes on the north row are too loose.", flagged
    assert flagged["reviewed_by"] == MANAGER, flagged
    assert flagged["review_round"] == 1, flagged
    assert {p["source_annotation_id"] for p in flagged["review_pins"]} == {"aaa1", "bbb2"}, flagged
    assert annotations("analyst") == before, "importing a review must not change annotations"

    # === Re-importing the same (now stale) package is a harmless no-op =====
    again = apply_review_import("analyst", [str(review_zip)])
    assert again["status"] == "no_changes", again
    assert again["stale_verdict_count"] == 1, again
    assert scene("analyst")["review_round"] == 1, scene("analyst")

    # === Analyst fixes the flagged scene; round 2 closes the loop ==========
    save_scene_json("analyst", "s1", "annotations", [ann("aaa1", [11, 11, 51, 41])])
    pkg2 = root / "out" / "work_v2.zip"
    save_annotation_package("analyst", str(pkg2))
    p2 = preview_annotation_import("manager", [str(pkg2)])
    apply_annotation_import("manager", p2["preview_id"], accepted_scene_ids=["s1"])
    final = {a["source_annotation_id"]: a["bbox"] for a in annotations("manager")}
    assert final == {"aaa1": [11, 11, 51, 41]}, final

    # Manager accepts the corrected work — a newer round supersedes the old verdict.
    accepted = set_scene_review("manager", "s1", status="accepted", comment="Good now.")
    assert accepted["review_round"] == 2, accepted
    review_zip2 = root / "out" / "review2.zip"
    save_review_package("manager", str(review_zip2), ANALYST)
    apply_review_import("analyst", [str(review_zip2)])
    assert scene("analyst")["review_status"] == "accepted", scene("analyst")
    assert scene("analyst")["review_round"] == 2, scene("analyst")

    # An older package can no longer roll the verdict back.
    apply_review_import("analyst", [str(review_zip)])
    assert scene("analyst")["review_status"] == "accepted", scene("analyst")

print("Review loop (T6) smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "Review loop smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
