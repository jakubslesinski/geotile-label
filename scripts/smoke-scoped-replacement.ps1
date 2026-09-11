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

with tempfile.TemporaryDirectory(prefix="geotile-replace-") as temp_dir:
    root = Path(temp_dir)
    os.environ["DATA_DIR"] = str(root)
    os.environ["MODELS_ROOT"] = str(root / "models")
    sys.path.insert(0, "backend")

    from db.storage import load_scene_json, save_json, save_scene_json
    from services.annotation_package import save_annotation_package
    from services.annotation_import import apply_annotation_import, preview_annotation_import

    UID = "uid-replace"
    ANALYST = "analyst@example.com"
    OTHER = "analyst2@example.com"
    MANAGER = "manager@example.com"

    def ann(aid, bbox, owner=ANALYST, class_id=0):
        return {
            "id": aid, "source_annotation_id": aid, "scene_id": "s1", "class_id": class_id,
            "geometry_type": "bbox", "bbox": bbox, "is_negative": False,
            "annotation_source": "manual", "annotator_email": owner,
            "created_at": "2026-01-01T10:00:00+00:00", "updated_at": "2026-01-01T10:00:00+00:00",
        }

    def make_project(pid, email, annotations, classes=None):
        save_json(pid, "project", {
            "id": pid, "name": pid, "scene_folder": str(root / "src"),
            "profile": {"modality": "EO", "georeferencing": "NO_GEO", "annotation_mode": "bbox",
                        "labeling_author_email": email},
        })
        save_json(pid, "classes", classes or [{"id": 0, "name": "vehicle", "color": "#ff0000"}])
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

    def boxes(pid, owner=None):
        rows = load_scene_json(pid, "s1", "annotations", default=[])
        return {
            a["source_annotation_id"]: a["bbox"]
            for a in rows
            if owner is None or a.get("annotator_email") == owner
        }

    def owners(pid):
        return sorted({a.get("annotator_email") for a in load_scene_json(pid, "s1", "annotations", default=[])})

    def downgrade_to_v1(src_zip, dst_zip):
        with zipfile.ZipFile(src_zip) as archive:
            payload = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(payload["annotation_package_manifest.json"].decode("utf-8"))
        manifest["schema_version"] = 1
        manifest.pop("scope", None)
        manifest.pop("superseded_source_annotation_ids", None)
        payload["annotation_package_manifest.json"] = json.dumps(
            manifest, indent=2, ensure_ascii=False).encode("utf-8")
        lines = [f"{hashlib.sha256(data).hexdigest()}  {name}"
                 for name, data in sorted(payload.items()) if name != "SHA256SUMS.txt"]
        payload["SHA256SUMS.txt"] = ("\n".join(lines) + "\n").encode("utf-8")
        with zipfile.ZipFile(dst_zip, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(payload.items()):
                archive.writestr(name, data)

    # =====================================================================
    # Round 1: analyst ships two boxes; manager also holds work of its own.
    # =====================================================================
    make_project("analyst", ANALYST, [ann("aaa1", [10, 10, 50, 40]), ann("bbb2", [100, 100, 140, 130])])
    make_project("manager", MANAGER, [
        ann("ccc3", [200, 200, 240, 230], owner=OTHER),    # another analyst's work
        ann("ddd4", [300, 300, 340, 330], owner=MANAGER),  # the manager's own work
    ])

    pkg_v1 = root / "out" / "round1.zip"
    save_annotation_package("analyst", str(pkg_v1))
    p1 = preview_annotation_import("manager", [str(pkg_v1)])
    assert p1["new_annotation_count"] == 2, p1["new_annotation_count"]
    r1 = apply_annotation_import("manager", p1["preview_id"])
    assert r1["imported_annotation_count"] == 2, r1
    assert boxes("manager", ANALYST) == {"aaa1": [10, 10, 50, 40], "bbb2": [100, 100, 140, 130]}

    # =====================================================================
    # Round 2: analyst corrects one box and deletes the other.
    # This is the exact scenario that silently failed before T5.
    # =====================================================================
    save_scene_json("analyst", "s1", "annotations", [ann("aaa1", [11, 11, 51, 41])])
    pkg_v2 = root / "out" / "round2.zip"
    save_annotation_package("analyst", str(pkg_v2))

    p2 = preview_annotation_import("manager", [str(pkg_v2)])
    plan = [item for item in p2["scene_plans"] if item["owner_email"] == ANALYST][0]
    assert plan["mode"] == "replace", plan
    assert (plan["added_count"], plan["changed_count"], plan["removed_count"]) == (0, 1, 1), plan
    assert plan["before_count"] == 2 and plan["after_count"] == 1, plan
    # Dropping 1 of 2 crosses the destructive ratio -> needs an explicit decision.
    assert plan["requires_confirmation"] is True, plan
    assert p2["confirmation_required_scene_ids"] == ["s1"], p2["confirmation_required_scene_ids"]

    # A plain apply must NOT silently wipe: the plan is held back.
    r2_skipped = apply_annotation_import("manager", p2["preview_id"])
    assert r2_skipped["status"] == "no_changes", r2_skipped["status"]
    assert boxes("manager", ANALYST) == {"aaa1": [10, 10, 50, 40], "bbb2": [100, 100, 140, 130]}
    assert any("confirmation" in item["reason"] for item in r2_skipped["skipped_scene_plans"]), r2_skipped

    # Naming the scene IS the confirmation.
    p2b = preview_annotation_import("manager", [str(pkg_v2)])
    r2 = apply_annotation_import("manager", p2b["preview_id"], accepted_scene_ids=["s1"])
    assert r2["updated_annotation_count"] == 1 and r2["deleted_annotation_count"] == 1, r2

    final = boxes("manager", ANALYST)
    correction_applied = final.get("aaa1") == [11, 11, 51, 41]
    deletion_applied = "bbb2" not in final
    print("CORRECTION PROPAGATED:", correction_applied)
    print("DELETION PROPAGATED:  ", deletion_applied)
    assert correction_applied, final
    assert deletion_applied, final

    # Other owners are untouched by a scoped replacement.
    assert boxes("manager", OTHER) == {"ccc3": [200, 200, 240, 230]}, boxes("manager", OTHER)
    assert boxes("manager", MANAGER) == {"ddd4": [300, 300, 340, 330]}, boxes("manager", MANAGER)
    assert owners("manager") == sorted([ANALYST, OTHER, MANAGER]), owners("manager")

    # =====================================================================
    # Re-importing the same package changes nothing (still idempotent).
    # =====================================================================
    p3 = preview_annotation_import("manager", [str(pkg_v2)])
    assert (p3["new_annotation_count"], p3["changed_annotation_count"], p3["removed_annotation_count"]) == (0, 0, 0), p3
    assert p3["can_apply"] is False, p3["can_apply"]

    # =====================================================================
    # A package the manager cannot fully resolve must never delete.
    # =====================================================================
    make_project("analyst3", ANALYST, [
        ann("eee5", [10, 10, 50, 40], class_id=1),  # class "truck" is unknown downstream
    ], classes=[{"id": 1, "name": "truck", "color": "#00ff00"}])
    make_project("manager3", MANAGER, [ann("keep9", [1, 1, 9, 9], owner=ANALYST)])
    pkg_bad = root / "out" / "badclass.zip"
    save_annotation_package("analyst3", str(pkg_bad))

    p4 = preview_annotation_import("manager3", [str(pkg_bad)])
    bad_plan = p4["scene_plans"][0]
    assert bad_plan["block_reason"], bad_plan
    assert "class is missing" in bad_plan["block_reason"], bad_plan["block_reason"]
    assert p4["blocked_scene_ids"] == ["s1"], p4["blocked_scene_ids"]
    r4 = apply_annotation_import("manager3", p4["preview_id"], accepted_scene_ids=["s1"])
    # Even explicitly accepted, a blocked plan is not applied: replacing from an
    # incomplete package would delete existing work over a class problem.
    assert r4["deleted_annotation_count"] == 0, r4
    assert boxes("manager3", ANALYST) == {"keep9": [1, 1, 9, 9]}, boxes("manager3", ANALYST)

    # =====================================================================
    # A v1 (pre-scope) package is additive only — it can never remove.
    # =====================================================================
    make_project("analyst4", ANALYST, [ann("fff6", [20, 20, 60, 50])])
    make_project("manager4", MANAGER, [
        ann("fff6", [20, 20, 60, 50]),
        ann("ggg7", [70, 70, 90, 90]),  # absent from the package
    ])
    pkg_v2_style = root / "out" / "additive_src.zip"
    save_annotation_package("analyst4", str(pkg_v2_style))
    pkg_legacy = root / "out" / "additive_v1.zip"
    downgrade_to_v1(pkg_v2_style, pkg_legacy)

    p5 = preview_annotation_import("manager4", [str(pkg_legacy)])
    legacy_plan = p5["scene_plans"][0]
    assert legacy_plan["mode"] == "append", legacy_plan
    assert legacy_plan["removed_count"] == 0, legacy_plan
    assert legacy_plan["requires_confirmation"] is False, legacy_plan
    apply_annotation_import("manager4", p5["preview_id"], accepted_scene_ids=["s1"])
    assert "ggg7" in boxes("manager4", ANALYST), boxes("manager4", ANALYST)

print("Scoped replacement (T5) smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "Scoped replacement smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
