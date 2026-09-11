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

with tempfile.TemporaryDirectory(prefix="geotile-basemodels-") as temp_dir:
    root = Path(temp_dir)
    os.environ["DATA_DIR"] = str(root)
    os.environ["MODELS_ROOT"] = str(root / "models")
    sys.path.insert(0, "backend")

    from db.storage import save_json
    from services.training_models import (
        BASE_MODEL_CATALOG,
        TrainingModelError,
        base_models_dir,
        list_base_models,
        required_task_for,
        resolve_base_model,
    )

    def make_project(pid, annotation_mode):
        save_json(pid, "project", {
            "id": pid, "name": pid,
            "profile": {"modality": "EO", "georeferencing": "NO_GEO",
                        "annotation_mode": annotation_mode,
                        "labeling_author_email": "a@example.com"},
        })

    # === Gate A: with no weights nothing is available, but the catalog shows ==
    listing = list_base_models("bbox")
    assert listing["available_count"] == 0, listing["available_count"]
    assert len(listing["models"]) == len(BASE_MODEL_CATALOG)
    detect_entry = [m for m in listing["models"] if m["file"] == "yolo11s.pt"][0]
    assert detect_entry["present"] is False and detect_entry["available"] is False
    assert "missing locally" in detect_entry["reason"], detect_entry["reason"]
    # The licence is always reported: a trained model inherits its obligations.
    assert "AGPL" in detect_entry["license"], detect_entry["license"]

    # === Gate A2: scratch entries need no local file =========================
    # An architecture without published weights (YOLO12-OBB) is offered trained from
    # scratch. It ships inside ultralytics, so it must be usable with an EMPTY weights
    # directory — the whole point is that there is nothing to prepare.
    obb_listing = list_base_models("rotated_bbox")
    scratch = [m for m in obb_listing["models"] if not m["pretrained"]]
    assert scratch, "the catalog must offer at least one from-scratch architecture"
    assert all(m["task"] == "obb" for m in scratch), "scratch entries are OBB-only here"
    for entry in scratch:
        assert entry["available"] is True, (entry["name"], entry["reason"])
        assert entry["present"] is False, "there is no local file to be present"
        assert entry["sha256"] is None and entry["size"] is None
        assert entry["path"] == entry["file"], entry["path"]
        assert entry["file"].endswith(".yaml"), entry["file"]
        # Scratch skips pretrained weights but still trains with AGPL-licensed code.
        assert "No pretrained weights" in entry["license"], entry["license"]
        assert "AGPL" in entry["license"], entry["license"]

    # Resolving one must succeed with no weights on disk, and hand the worker the bare
    # config name that ultralytics resolves from its own package.
    resolved_scratch = resolve_base_model(scratch[0]["file"], "rotated_bbox")
    assert resolved_scratch["path"] == scratch[0]["file"], resolved_scratch["path"]
    assert resolved_scratch["sha256"] is None, "a config has no checkpoint to hash"

    # Geometry still governs: an OBB architecture stays refused in a bbox project even
    # though it needs no weights.
    try:
        resolve_base_model(scratch[0]["file"], "bbox")
        raise AssertionError("a scratch OBB model must be refused for bbox geometry")
    except TrainingModelError as exc:
        assert "obb" in str(exc) and "detect" in str(exc), exc

    # === Gate B: refusal BEFORE start, with instructions instead of a download =
    try:
        resolve_base_model("yolo11s.pt", "bbox")
        raise AssertionError("missing weights must be rejected")
    except TrainingModelError as exc:
        assert "are missing in" in str(exc), exc
        assert "fetch-base-models" in str(exc), "message must point at how to prepare weights"
        assert "never downloads" in str(exc), "message must state that nothing is downloaded"

    # === Gate C: allowlist - an unknown file does not become a model =========
    weights_dir = base_models_dir()
    weights_dir.mkdir(parents=True, exist_ok=True)
    (weights_dir / "untrusted-model.pt").write_bytes(b"we do not trust this file")
    listing = list_base_models("bbox")
    assert all(m["file"] != "untrusted-model.pt" for m in listing["models"]), \
        "a dropped-in .pt must never appear in the architecture catalog"
    try:
        resolve_base_model("untrusted-model.pt", "bbox")
        raise AssertionError("an unknown architecture must be rejected")
    except TrainingModelError as exc:
        assert "not a known base architecture" in str(exc), exc

    # === Gate D: project geometry must match the model task ==================
    assert required_task_for("bbox") == "detect"
    assert required_task_for("rotated_bbox") == "obb"
    try:
        required_task_for("mask")
        raise AssertionError("mask geometry is not supported for training")
    except TrainingModelError as exc:
        assert "mask" in str(exc), exc

    # Drop in placeholder weights (content is irrelevant - they are never loaded).
    for entry in BASE_MODEL_CATALOG:
        (weights_dir / str(entry["file"])).write_bytes(b"x" * 2048)

    listing = list_base_models("bbox")
    detect_models = [m for m in listing["models"] if m["task"] == "detect"]
    obb_models = [m for m in listing["models"] if m["task"] == "obb"]
    assert all(m["available"] for m in detect_models), "detect must be available for bbox"
    assert all(not m["available"] for m in obb_models), "obb must be unavailable for bbox"
    assert all("does not match the project geometry" in m["reason"] for m in obb_models), obb_models[0]["reason"]
    assert listing["required_task"] == "detect"

    listing_obb = list_base_models("rotated_bbox")
    assert all(m["available"] for m in listing_obb["models"] if m["task"] == "obb")
    assert all(not m["available"] for m in listing_obb["models"] if m["task"] == "detect")

    # Cross use is rejected, naming both sides of the mismatch.
    try:
        resolve_base_model("yolo11s-obb.pt", "bbox")
        raise AssertionError("an OBB model in a bbox project must be rejected")
    except TrainingModelError as exc:
        assert "obb" in str(exc) and "detect" in str(exc), exc

    # === Gate E: SHA-256 computed and cached =================================
    resolved = resolve_base_model("yolo11s.pt", "bbox")
    assert len(resolved["sha256"]) == 64, resolved["sha256"]
    assert resolved["path"].endswith("yolo11s.pt")
    assert (weights_dir / "base_models_index.json").is_file(), "the hash cache must be created"
    # Changing the file must invalidate the cache (key covers size and mtime).
    first_hash = resolved["sha256"]
    (weights_dir / "yolo11s.pt").write_bytes(b"y" * 4096)
    second = resolve_base_model("yolo11s.pt", "bbox")
    assert second["sha256"] != first_hash, "changing the file must change the reported hash"

    # === Gate F: no network traffic whatsoever ===============================
    # Block sockets: if the registry tried to fetch anything it would raise here.
    import socket

    class BlockedSocket(socket.socket):
        def __init__(self, *args, **kwargs):
            raise AssertionError("the model registry must not open network connections")

    real_socket = socket.socket
    real_create = socket.create_connection
    socket.socket = BlockedSocket
    socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("the model registry must not open network connections"))
    try:
        list_base_models("bbox")
        resolve_base_model("yolo11m.pt", "bbox")
    finally:
        socket.socket = real_socket
        socket.create_connection = real_create

print("Base model registry (M2) smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "Base model registry smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
