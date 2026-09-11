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
import csv
import json
import os
import sys
import tempfile
from pathlib import Path
from PIL import Image

sys.path.insert(0, "backend")
from services.dataset_audit import generate_dataset_audit

with tempfile.TemporaryDirectory(prefix="geotile-audit-") as temp_dir:
    run_dir = Path(temp_dir) / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "train" / "images").mkdir(parents=True)
    (run_dir / "val" / "images").mkdir(parents=True)
    duplicate = Image.new("L", (32, 32), 20)
    duplicate.putpixel((8, 8), 240)
    duplicate.save(run_dir / "train" / "images" / "tile-a.png")
    duplicate.save(run_dir / "val" / "images" / "tile-b.png")

    def write(name, value):
        path = run_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    write("dataset_run_manifest.json", {
        "run_id": "run-1",
        "dataset_config": {"split_mode": "random_tile"},
        "preprocessing_profile": {"profile_id": "eo_rgb_percentile", "profile_hash": "a" * 64},
    })
    write("dataset_stats.json", {
        "total_tiles": 2,
        "positive_tiles": 1,
        "negative_tiles": 1,
        "total_annotations": 1,
        "split_mode": "random_tile",
        "class_stats": [{"name": "vehicle"}],
        "split_stats": [{"split": "train", "images": 2, "positive_tiles": 1, "negative_tiles": 1, "annotations": 1}],
        "scene_stats": [{"scene_id": "scene-1", "filename": "scene.tif", "used_tiles": 2, "annotations": 1}],
        "unused_classes": [],
        "source_only_classes": [],
        "scenes_without_annotations": [],
        "scenes_without_dataset_classes": [],
    })
    write("split_manifest.json", {"assignments": {"tile.png": "train"}})
    write("tile_annotations.json", {"tile.png": [[0, 1, 1, 2, 2]]})
    write("tile_annotation_links.json", {"schema_name": "links", "annotations": [{"source_annotation_id": "ann-1"}]})
    write("source_annotations.json", {"scene-1": [{
        "id": "ann-1",
        "source_annotation_id": "ann-1",
        "annotation_source": "prediction",
        "annotator_email": None,
        "attributes": {"attribute_status": "computed"},
    }]})
    write("scene_manifests.json", {"scene-1": {
        "scene_id": "scene-1",
        "filename": "scene.tif",
        "sensor": "WorldView",
        "modality": "EO",
        "georeferencing": "GEO",
        "acquisition_datetime_utc": "2024-01-01T00:00:00Z",
        "geospatial": {"has_geo": True, "bounds_wgs84": [20, 50, 21, 51]},
    }})
    write("preprocessing_profile.json", {"profile_id": "eo_rgb_percentile", "profile_hash": "a" * 64})
    write("validation_report.json", {"status": "warning", "issues": [{
        "code": "random_tile_leakage_risk",
        "severity": "warning",
        "message": "random_tile can leak neighboring tiles",
    }]})

    report = generate_dataset_audit("project-1", run_dir)
    assert report["status"] == "error"
    assert report["readiness"] == "not_ready"
    assert report["annotation_summary"]["unreviewed_prediction_annotations"] == 1
    assert report["near_duplicate_summary"]["cross_split_pair_count"] == 1
    assert report["distributions"]["by_sensor"][0]["sensor"] == "WorldView"
    assert report["sidecar_summary"]["generated"] is False
    assert (run_dir / "metadata" / "dataset_audit.json").exists()
    assert (run_dir / "metadata" / "dataset_audit.csv").exists()
    with (run_dir / "metadata" / "dataset_audit.csv").open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert any(row["check_id"] == "unreviewed_prediction_annotations" for row in rows)

print("Dataset audit smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "Dataset audit smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
