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
$env:GEOTILE_TRAIN_MOCK = "1"
$env:GEOTILE_TRAIN_MOCK_EPOCH_SECONDS = "0.02"

$Script = @'
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "backend")

temp_dir = tempfile.mkdtemp(prefix="geotile-registry-")
root = Path(temp_dir)
os.environ["DATA_DIR"] = str(root)
os.environ["MODELS_ROOT"] = str(root / "models")

from db.storage import load_json, project_dir, save_json
from fastapi import HTTPException
from routers.training import (
    TrainingStartRequest,
    get_model_lineage,
    get_models,
    get_test_evaluation,
    post_promote_model,
    post_register_model,
    post_test_evaluation,
    post_training_run,
)
from services.dataset_runs import dataset_run_dir, register_dataset_run, set_dataset_run_publication, write_run_json
from services.parquet_io import write_parquet
from services.training_models import base_models_dir
from services.training_runs import resolve_job_state, training_run_dir

PROJECT = "proj-registry"
DATASET_RUN = "20260101T100000000000Z_cafe0001"
CATALOG_ID = "cat-lineage"

save_json(PROJECT, "project", {
    "id": PROJECT, "name": PROJECT, "app_version": "0.1.23",
    "profile": {"modality": "EO", "georeferencing": "NO_GEO", "annotation_mode": "bbox",
                "labeling_author_email": "manager@example.com"},
})
save_json(PROJECT, "classes", [{"id": 0, "name": "vehicle", "color": "#ff0000"}])

weights_dir = base_models_dir()
weights_dir.mkdir(parents=True, exist_ok=True)
for name in ("yolo11s.pt", "yolo11s-obb.pt"):
    (weights_dir / name).write_bytes(b"x" * 2048)

# --- dataset run with a selection and a catalog link table -------------------
run_dir = dataset_run_dir(PROJECT, DATASET_RUN)
for split in ("train", "val", "test"):
    (run_dir / split / "images").mkdir(parents=True, exist_ok=True)
    (run_dir / split / "labels").mkdir(parents=True, exist_ok=True)
    for index in range(2):
        (run_dir / split / "images" / f"t{index}.jpg").write_bytes(b"\xff\xd8\xff")
        (run_dir / split / "labels" / f"t{index}.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
(run_dir / "data.yaml").write_text(
    "path: .\ntrain: train/images\nval: val/images\ntest: test/images\nnames:\n  0: vehicle\n",
    encoding="utf-8")
dataset_manifest = {
    "run_id": DATASET_RUN, "created_at": "2026-01-01T10:00:00+00:00", "status": "complete",
    "input_hash": "a" * 64, "tile_catalog_id": CATALOG_ID,
    "dataset_config": {"split_mode": "scene_split", "split_seed": 42},
    "preprocessing_profile": {"profile_id": "eo_rgb_percentile"},
    "statistics": {"total_tiles": 6, "validation_report": {"status": "ok"}},
    "audit_summary": {"status": "ok", "readiness": "ready"},
}
write_run_json(run_dir, "dataset_run_manifest", dataset_manifest)
register_dataset_run(PROJECT, dataset_manifest)
set_dataset_run_publication(PROJECT, DATASET_RUN, status="published", label="v1")

# used_tiles decides which annotations actually reached the model; tile-3 is present
# in the catalog but NOT used, so its author must not appear in the lineage.
write_run_json(run_dir, "dataset_selection", {
    "schema_name": "geotile_dataset_selection", "catalog_id": CATALOG_ID,
    "used_tiles": [
        {"tile_id": "tile-1", "scene_id": "scene-a", "filename": "t0.jpg", "split": "train"},
        {"tile_id": "tile-2", "scene_id": "scene-b", "filename": "t1.jpg", "split": "val"},
    ],
})
catalog_dir = project_dir(PROJECT) / "tile_catalogs" / CATALOG_ID
catalog_dir.mkdir(parents=True, exist_ok=True)
write_parquet(catalog_dir / "tile_annotation_links.parquet", [
    {"catalog_tile_id": "tile-1", "source_annotation_id": "ann-1", "annotator_email": "ola@example.com",
     "annotation_source": "manual", "class_name": "vehicle", "class_id": 0},
    {"catalog_tile_id": "tile-1", "source_annotation_id": "ann-2", "annotator_email": "ola@example.com",
     "annotation_source": "assisted_sam", "class_name": "vehicle", "class_id": 0},
    {"catalog_tile_id": "tile-2", "source_annotation_id": "ann-3", "annotator_email": "piotr@example.com",
     "annotation_source": "manual", "class_name": "vehicle", "class_id": 0},
    {"catalog_tile_id": "tile-3", "source_annotation_id": "ann-9", "annotator_email": "nieuzyty@example.com",
     "annotation_source": "manual", "class_name": "vehicle", "class_id": 0},
])


def wait_for(run_id, statuses, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = resolve_job_state(PROJECT, run_id)
        if state.get("status") in statuses:
            return state
        time.sleep(0.2)
    raise AssertionError(f"run did not reach {statuses}")


def train(**overrides):
    payload = {"dataset_run_id": DATASET_RUN, "base_model": "yolo11s.pt", "epochs": 2, "device": "cpu"}
    payload.update(overrides)
    started = post_training_run(PROJECT, TrainingStartRequest(**payload))
    wait_for(started["training_run_id"], {"completed", "failed"})
    return started["training_run_id"]


first_run = train()
second_run = train(epochs=3)

# === Gate A: only a finished run can be registered ===========================
unfinished = training_run_dir(PROJECT, "fake-run")
unfinished.mkdir(parents=True, exist_ok=True)
(unfinished / "job.json").write_text("{}", encoding="utf-8")
try:
    post_register_model(PROJECT, "fake-run")
    raise AssertionError("a run without a manifest must not be registrable")
except HTTPException as exc:
    assert exc.status_code == 400 and "manifest" in exc.detail, exc.detail

entry = post_register_model(PROJECT, first_run)
assert entry["status"] == "archived", entry
assert entry["dataset_label"] == "v1", "the dataset version label must travel with the model"
assert entry["validation_metrics"]["mAP50"] > 0, entry
# Registering twice is idempotent rather than duplicating.
assert post_register_model(PROJECT, first_run)["model_id"] == entry["model_id"]
post_register_model(PROJECT, second_run)
assert get_models(PROJECT)["model_count"] == 2

# === Gate B: promotion is recorded and reversible ============================
promoted = post_promote_model(PROJECT, first_run)
assert promoted["replaced_model_id"] is None, promoted
config = load_json(PROJECT, "prediction_config", default={})
assert config["model_path"].endswith("best.pt"), config
registry = get_models(PROJECT)
assert registry["current_model_id"] == first_run
assert [m["status"] for m in registry["models"] if m["model_id"] == first_run] == ["current"]

promoted_again = post_promote_model(PROJECT, second_run)
assert promoted_again["replaced_model_id"] == first_run, promoted_again
registry = get_models(PROJECT)
assert registry["current_model_id"] == second_run
assert len(registry["promotions"]) == 2, registry["promotions"]
# Going back is a matter of promoting the previous entry — nothing was lost.
post_promote_model(PROJECT, first_run)
assert get_models(PROJECT)["current_model_id"] == first_run

# === Gate C: geometry mismatch blocks promotion ==============================
save_json(PROJECT, "project", {
    **load_json(PROJECT, "project", default={}),
    "profile": {**(load_json(PROJECT, "project", default={}).get("profile") or {}),
                "annotation_mode": "rotated_bbox"},
})
try:
    post_promote_model(PROJECT, first_run)
    raise AssertionError("a detect model must not be promotable in a rotated-geometry project")
except HTTPException as exc:
    assert exc.status_code == 400, exc.status_code
    assert "detect" in exc.detail and "obb" in exc.detail, exc.detail
# restore
save_json(PROJECT, "project", {
    **load_json(PROJECT, "project", default={}),
    "profile": {**(load_json(PROJECT, "project", default={}).get("profile") or {}),
                "annotation_mode": "bbox"},
})

# === Gate D: lineage reaches annotation authorship ===========================
lineage = get_model_lineage(PROJECT, first_run)
assert lineage["truncated"] is False, lineage
assert lineage["dataset_run"]["label"] == "v1"
assert lineage["dataset_run"]["split_seed"] == 42
assert lineage["tile_catalog_id"] == CATALOG_ID
assert lineage["scene_count"] == 2, lineage["scene_count"]
assert lineage["annotation_count"] == 3, lineage["annotation_count"]
assert lineage["authors"] == {"ola@example.com": 2, "piotr@example.com": 1}, lineage["authors"]
# An annotation that never reached the dataset must not be attributed to the model.
assert "nieuzyty@example.com" not in lineage["authors"], lineage["authors"]
assert lineage["annotation_sources"] == {"assisted_sam": 1, "manual": 2}, lineage["annotation_sources"]
assert lineage["training_run"]["base_model"] == "yolo11s.pt"

# === Gate E: test evaluation is written once and marked final ================
status = get_test_evaluation(PROJECT, first_run)
assert status["evaluated"] is False, status

post_test_evaluation(PROJECT, first_run)
deadline = time.time() + 30
while time.time() < deadline and not get_test_evaluation(PROJECT, first_run)["evaluated"]:
    time.sleep(0.2)
status = get_test_evaluation(PROJECT, first_run)
assert status["evaluated"] is True, status
assert status["evaluation"]["final"] is True, status["evaluation"]
assert status["evaluation"]["split"] == "test", status["evaluation"]
assert status["evaluation"]["metrics"]["mAP50"] > 0

# A second evaluation is refused: repeating it would turn the test split into a
# second validation set and inflate the estimate.
try:
    post_test_evaluation(PROJECT, first_run)
    raise AssertionError("a second test evaluation must be refused")
except HTTPException as exc:
    assert exc.status_code == 409, exc.status_code
    assert "final" in exc.detail, exc.detail

# Validation metrics are untouched by the test evaluation.
metrics = json.loads((training_run_dir(PROJECT, first_run) / "metrics.json").read_text(encoding="utf-8"))
assert metrics["summary"]["mAP50"] == 0.70, metrics["summary"]

shutil.rmtree(temp_dir, ignore_errors=True)
print("Model registry and test evaluation (M5) smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "Model registry smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
