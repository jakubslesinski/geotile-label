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
$env:GEOTILE_TRAIN_MOCK_EPOCH_SECONDS = "0.05"

$Script = @'
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "backend")

import tempfile
temp_dir = tempfile.mkdtemp(prefix="geotile-training-")
root = Path(temp_dir)
os.environ["DATA_DIR"] = str(root)
os.environ["MODELS_ROOT"] = str(root / "models")

from db.storage import project_dir, save_json
from routers.training import (
    TrainingStartRequest,
    get_training_run,
    get_training_runs,
    post_cancel_training_run,
    post_training_preflight,
    post_training_run,
)
from services.dataset_runs import (
    dataset_run_dir,
    register_dataset_run,
    set_dataset_run_publication,
    write_run_json,
)
from services.training_models import base_models_dir
from services.training_runs import resolve_job_state, training_run_dir, write_job_state

PROJECT = "proj-train"
DATASET_RUN = "20260101T100000000000Z_abcdef12"

# --- project, weights and a dataset run that looks like a real YOLO export ----
save_json(PROJECT, "project", {
    "id": PROJECT, "name": PROJECT, "app_version": "0.1.23",
    "profile": {"modality": "EO", "georeferencing": "NO_GEO", "annotation_mode": "bbox",
                "labeling_author_email": "a@example.com"},
})
save_json(PROJECT, "classes", [
    {"id": 0, "name": "vehicle", "color": "#ff0000"},
    {"id": 1, "name": "ship", "color": "#00ff00"},
])

weights_dir = base_models_dir()
weights_dir.mkdir(parents=True, exist_ok=True)
(weights_dir / "yolo11s.pt").write_bytes(b"x" * 2048)

run_dir = dataset_run_dir(PROJECT, DATASET_RUN)
run_dir.mkdir(parents=True, exist_ok=True)
for split in ("train", "val", "test"):
    (run_dir / split / "images").mkdir(parents=True, exist_ok=True)
    (run_dir / split / "labels").mkdir(parents=True, exist_ok=True)
    for index in range(3):
        (run_dir / split / "images" / f"tile_{index}.jpg").write_bytes(b"\xff\xd8\xff")
        (run_dir / split / "labels" / f"tile_{index}.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
(run_dir / "data.yaml").write_text(
    "path: .\ntrain: train/images\nval: val/images\ntest: test/images\n"
    "names:\n  0: vehicle\n  1: ship\n", encoding="utf-8")
dataset_manifest = {
    "run_id": DATASET_RUN, "created_at": "2026-01-01T10:00:00+00:00",
    "status": "complete", "input_hash": "d" * 64,
    "dataset_config": {"split_mode": "scene_split", "split_seed": 42},
    "preprocessing_profile": {"profile_id": "eo_rgb_percentile"},
    "statistics": {"total_tiles": 9, "validation_report": {"status": "ok"}},
    "audit_summary": {"status": "ok", "readiness": "ready"},
}
write_run_json(run_dir, "dataset_run_manifest", dataset_manifest)
register_dataset_run(PROJECT, dataset_manifest)
set_dataset_run_publication(PROJECT, DATASET_RUN, status="published", label="smoke-v1")


def request(**overrides):
    payload = {"dataset_run_id": DATASET_RUN, "base_model": "yolo11s.pt",
               "epochs": 3, "device": "cpu"}
    payload.update(overrides)
    return TrainingStartRequest(**payload)


def wait_for(run_id, statuses, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = resolve_job_state(PROJECT, run_id)
        if state.get("status") in statuses:
            return state
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} did not reach {statuses}; last={resolve_job_state(PROJECT, run_id)}")


# === Gate A: preflight judges before anything starts =========================
pre = post_training_preflight(PROJECT, request())
assert pre["can_start"] is True, pre["checks"]
assert pre["task"] == "detect", pre["task"]
assert pre["next_attempt"] == 1 and pre["previous_runs"] == [], pre
assert any(c["name"] == "classes" and c["status"] == "ok" for c in pre["checks"]), pre["checks"]
# CPU is allowed but must warn about the time cost.
assert any(c["name"] == "device" and c["status"] == "warning" for c in pre["checks"]), pre["checks"]
fingerprint = pre["config_fingerprint"]

# === Gate B: a run completes and leaves a manifest ===========================
started = post_training_run(PROJECT, request())
first_run = started["training_run_id"]
assert started["attempt"] == 1
assert started["config_fingerprint"] == fingerprint, "preflight and start must agree"

state = wait_for(first_run, {"completed", "failed"})
assert state["status"] == "completed", state
detail = get_training_run(PROJECT, first_run)
assert detail["manifest"] is not None, "a finished run must leave a manifest"
assert detail["manifest"]["base_model"] == "yolo11s.pt"
assert detail["manifest"]["dataset_run_id"] == DATASET_RUN
# Full reproduction context is recorded, not just the ids.
for key in ("dataset_input_hash", "dataset_split_seed", "class_names", "seed",
            "imgsz", "batch", "base_model_sha256", "torch", "app_version"):
    assert key in detail["manifest"], f"manifest is missing {key}"
assert "base_model_path" not in detail["manifest"], "absolute local paths do not belong in the manifest"
run_path = training_run_dir(PROJECT, first_run)
assert (run_path / "results.csv").is_file() and (run_path / "weights" / "best.pt").is_file()
assert detail["metrics"]["mAP50"] > 0

# === Gate C: same config repeats as a new attempt ============================
again = post_training_run(PROJECT, request())
assert again["config_fingerprint"] == fingerprint, "identical config must hash identically"
assert again["training_run_id"] != first_run, "each execution needs its own id"
assert again["attempt"] == 2, again
wait_for(again["training_run_id"], {"completed", "failed"})

pre_again = post_training_preflight(PROJECT, request())
assert pre_again["next_attempt"] == 3 and len(pre_again["previous_runs"]) == 2, pre_again

# A different hyperparameter is a different configuration.
other = post_training_run(PROJECT, request(epochs=5))
assert other["config_fingerprint"] != fingerprint
assert other["attempt"] == 1, "a new configuration starts its own attempt count"
wait_for(other["training_run_id"], {"completed", "failed"})

# === Gate D: cancelling stops the process and leaves no manifest =============
os.environ["GEOTILE_TRAIN_MOCK_EPOCH_SECONDS"] = "2"
long_run = post_training_run(PROJECT, request(epochs=50))["training_run_id"]
running = wait_for(long_run, {"running"})
pid = running.get("pid")
assert pid, "a running job must record its pid"

cancelled = post_cancel_training_run(PROJECT, long_run)
assert cancelled["status"] == "cancelled", cancelled
time.sleep(1.0)
import psutil
assert not psutil.pid_exists(int(pid)), "cancelling must actually kill the process"
assert not (training_run_dir(PROJECT, long_run) / "training_manifest.json").exists(), \
    "an unfinished run must not leave a manifest"

# === Gate E: a killed worker is reported as interrupted, not 'running' =======
os.environ["GEOTILE_TRAIN_MOCK_EPOCH_SECONDS"] = "2"
killed_run = post_training_run(PROJECT, request(epochs=50))["training_run_id"]
running = wait_for(killed_run, {"running"})
psutil.Process(int(running["pid"])).kill()
time.sleep(1.0)
state = resolve_job_state(PROJECT, killed_run)
assert state["status"] == "interrupted", state
assert "without recording a result" in (state.get("error") or ""), state
# The reclassification is persisted, so it survives a backend restart.
stored = json.loads((training_run_dir(PROJECT, killed_run) / "job_state.json").read_text(encoding="utf-8"))
assert stored["status"] == "interrupted", stored

# === Gate F: listing is scoped to one dataset run ============================
listing = get_training_runs(PROJECT, DATASET_RUN)
assert listing["run_count"] == 5, listing["run_count"]
statuses = {item["status"] for item in listing["runs"]}
assert {"completed", "cancelled", "interrupted"} <= statuses, statuses
assert get_training_runs(PROJECT, "nie-ma-takiego")["run_count"] == 0

# Best-effort cleanup: the temp tree cannot use a context manager because the worker
# subprocess must outlive the call that starts it.
import shutil
shutil.rmtree(temp_dir, ignore_errors=True)

print("Training run lifecycle (M3) smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "Training run smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
