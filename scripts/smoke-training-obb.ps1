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
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "backend")

temp_dir = tempfile.mkdtemp(prefix="geotile-obb-")
root = Path(temp_dir)
os.environ["DATA_DIR"] = str(root)
os.environ["MODELS_ROOT"] = str(root / "models")

from db.storage import save_json
from routers.training import (
    TrainingStartRequest,
    post_training_preflight,
    post_training_run,
)
from services.dataset_runs import (
    dataset_run_dir,
    register_dataset_run,
    set_dataset_run_publication,
    write_run_json,
)
from services.training_dataset import obb_label_stats, stage_obb_dataset
from services.training_models import base_models_dir
from services.training_runs import resolve_job_state, training_run_dir

PROJECT = "proj-obb"
DATASET_RUN = "20260101T100000000000Z_deadbeef"

# Axis-aligned and oriented labels are made deliberately distinguishable: 5 fields
# versus 9. Whatever ends up in the staged labels/ therefore proves which set the
# trainer would actually read.
DETECT_LABEL = "0 0.5 0.5 0.2 0.2\n"
OBB_LABEL = "0 0.1 0.1 0.9 0.1 0.9 0.9 0.1 0.9\n"


def build_dataset(*, with_obb=True, obb_empty=False):
    run_dir = dataset_run_dir(PROJECT, DATASET_RUN)
    if run_dir.exists():
        shutil.rmtree(run_dir)
    for split in ("train", "val", "test"):
        (run_dir / split / "images").mkdir(parents=True, exist_ok=True)
        (run_dir / split / "labels").mkdir(parents=True, exist_ok=True)
        if with_obb:
            (run_dir / split / "labels_obb").mkdir(parents=True, exist_ok=True)
        for index in range(3):
            (run_dir / split / "images" / f"tile_{index}.jpg").write_bytes(b"\xff\xd8\xff")
            (run_dir / split / "labels" / f"tile_{index}.txt").write_text(DETECT_LABEL, encoding="utf-8")
            if with_obb:
                (run_dir / split / "labels_obb" / f"tile_{index}.txt").write_text(
                    "" if obb_empty else OBB_LABEL, encoding="utf-8")
    for name in ("data.yaml", "data_obb.yaml"):
        (run_dir / name).write_text(
            "path: .\ntrain: train/images\nval: val/images\ntest: test/images\n"
            "names:\n  0: ship\n", encoding="utf-8")
    manifest = {
        "run_id": DATASET_RUN, "created_at": "2026-01-01T10:00:00+00:00",
        "status": "complete", "input_hash": "e" * 64,
        "dataset_config": {"split_mode": "scene_split", "split_seed": 7},
        "preprocessing_profile": {"profile_id": "eo_rgb_percentile"},
        "statistics": {"total_tiles": 9, "validation_report": {"status": "ok"}},
        "audit_summary": {"status": "ok", "readiness": "ready"},
    }
    write_run_json(run_dir, "dataset_run_manifest", manifest)
    register_dataset_run(PROJECT, manifest)
    set_dataset_run_publication(PROJECT, DATASET_RUN, status="published", label="smoke-obb-v1")
    return run_dir


def set_project(annotation_mode):
    save_json(PROJECT, "project", {
        "id": PROJECT, "name": PROJECT, "app_version": "0.1.23",
        "profile": {"modality": "EO", "georeferencing": "NO_GEO",
                    "annotation_mode": annotation_mode,
                    "labeling_author_email": "a@example.com"},
    })
    save_json(PROJECT, "classes", [{"id": 0, "name": "ship", "color": "#00ff00"}])


weights_dir = base_models_dir()
weights_dir.mkdir(parents=True, exist_ok=True)
for name in ("yolo11s.pt", "yolo11s-obb.pt"):
    (weights_dir / name).write_bytes(b"x" * 2048)


def request(**overrides):
    payload = {"dataset_run_id": DATASET_RUN, "base_model": "yolo11s-obb.pt",
               "epochs": 2, "device": "cpu"}
    payload.update(overrides)
    return TrainingStartRequest(**payload)


def wait_for(run_id, statuses, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = resolve_job_state(PROJECT, run_id)
        if state.get("status") in statuses:
            return state
        time.sleep(0.2)
    raise AssertionError(f"run did not reach {statuses}: {resolve_job_state(PROJECT, run_id)}")


# === Gate A: staging puts the ORIENTED labels where ultralytics looks ========
set_project("rotated_bbox")
source = build_dataset()
staged_dir = root / "staged"
data_yaml = stage_obb_dataset(source, staged_dir)
assert data_yaml.is_file(), data_yaml

for split in ("train", "val", "test"):
    staged_label = staged_dir / split / "labels" / "tile_0.txt"
    assert staged_label.is_file(), staged_label
    content = staged_label.read_text(encoding="utf-8")
    # The decisive assertion: the staged labels/ must carry the 9-field oriented
    # geometry, never the 5-field axis-aligned one.
    assert content == OBB_LABEL, f"{split}: staged labels must be oriented, got {content!r}"
    assert len(content.split()) == 9, content
    assert (staged_dir / split / "images" / "tile_0.jpg").is_file()

# Images are linked, not duplicated, where the filesystem allows it.
original = source / "train" / "images" / "tile_0.jpg"
linked = staged_dir / "train" / "images" / "tile_0.jpg"
assert linked.stat().st_size == original.stat().st_size

# === Gate B: the worker stages automatically for an OBB run =================
started = post_training_run(PROJECT, request())
run_id = started["training_run_id"]
state = wait_for(run_id, {"completed", "failed"})
assert state["status"] == "completed", state
staged_by_worker = training_run_dir(PROJECT, run_id) / "dataset_obb"
assert staged_by_worker.is_dir(), "the worker must stage an OBB dataset"
worker_label = staged_by_worker / "train" / "labels" / "tile_0.txt"
assert worker_label.read_text(encoding="utf-8") == OBB_LABEL, worker_label.read_text(encoding="utf-8")

# === Gate C: a detect run is untouched by staging ===========================
set_project("bbox")
detect_run = post_training_run(PROJECT, request(base_model="yolo11s.pt"))["training_run_id"]
wait_for(detect_run, {"completed", "failed"})
assert not (training_run_dir(PROJECT, detect_run) / "dataset_obb").exists(), \
    "a detect run must not stage anything"

# === Gate D: preflight refuses when there is nothing oriented to train on ====
set_project("rotated_bbox")
stats = obb_label_stats(source)
assert stats["label_files"] == 9 and stats["non_empty_label_files"] == 9, stats

build_dataset(with_obb=True, obb_empty=True)
pre = post_training_preflight(PROJECT, request())
assert pre["can_start"] is False, pre["checks"]
assert any(c["name"] == "obb-labels" and c["status"] == "error" and "empty" in c["detail"]
           for c in pre["checks"]), pre["checks"]

build_dataset(with_obb=False)
pre = post_training_preflight(PROJECT, request())
assert pre["can_start"] is False, pre["checks"]
assert any(c["name"] == "obb-labels" and c["status"] == "error" and "No labels_obb" in c["detail"]
           for c in pre["checks"]), pre["checks"]

# Healthy dataset passes again.
build_dataset()
pre = post_training_preflight(PROJECT, request())
assert pre["can_start"] is True, pre["checks"]
assert any(c["name"] == "obb-labels" and c["status"] == "ok" for c in pre["checks"]), pre["checks"]

shutil.rmtree(temp_dir, ignore_errors=True)
print("OBB dataset staging (M3) smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "OBB staging smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
