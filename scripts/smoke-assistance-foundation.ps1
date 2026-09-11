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

$EnvironmentRoot = Split-Path -Parent $PythonPath
$ProjData = Join-Path $EnvironmentRoot "Library\share\proj"
if (Test-Path $ProjData) {
    $env:PROJ_DATA = $ProjData
    $env:PROJ_LIB = $ProjData
}
$env:GEOTILE_ENABLE_YOLO = "1"

$Script = @'
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="geotile-assist-") as temp_dir:
    root = Path(temp_dir)
    os.environ["DATA_DIR"] = str(root)
    sys.path.insert(0, "backend")

    # --- Device auto-detection ------------------------------------------------
    from services.predictor import resolve_device

    os.environ["GEOTILE_FORCE_DEVICE"] = "cuda"
    assert resolve_device() == "cuda", "forced device must win"
    os.environ["GEOTILE_FORCE_DEVICE"] = "cpu"
    assert resolve_device() == "cpu"
    os.environ.pop("GEOTILE_FORCE_DEVICE", None)
    assert resolve_device() in ("cpu", "cuda"), "auto-detect returns a real device"

    # capabilities must report the resolved device, not a hardcoded "cpu".
    os.environ["GEOTILE_FORCE_DEVICE"] = "cuda"
    import main
    caps = asyncio.run(main.capabilities())
    expected = "cuda" if caps["yolo"] else "cpu"
    assert caps["device"] == expected, (caps["device"], caps["yolo"])
    os.environ.pop("GEOTILE_FORCE_DEVICE", None)

    # --- Project + scene fixture ----------------------------------------------
    from db.storage import save_json, save_scene_json, load_scene_json

    project_id = "proj-assist"
    scene_id = "scene-1"
    save_json(project_id, "project", {
        "id": project_id,
        "name": "Assist foundation smoke",
        "scene_folder": str(root / "scenes"),
        "profile": {
            "modality": "EO",
            "georeferencing": "NO_GEO",
            "annotation_mode": "bbox",
            "labeling_author_email": "analyst@example.com",
        },
    })
    save_json(project_id, "classes", [
        {"id": 0, "name": "vehicle", "color": "#ff0000"},
        {"id": 1, "name": "aircraft", "color": "#00ff00"},
    ])
    save_scene_json(project_id, scene_id, "scene", {
        "id": scene_id, "filename": "scene.png", "status": "pending",
        "annotation_count": 0, "working_grid_uid": "grid-abc",
    })
    save_scene_json(project_id, scene_id, "annotations", [])

    # --- Sessions never overwrite ---------------------------------------------
    from models.assistance import AssistanceProposal
    from services.assistance_sessions import (
        create_session, get_active_session, list_session_files, write_session,
    )

    def make_proposal(class_name, bbox):
        return AssistanceProposal(
            session_id="", source_tool="yolo_scene", geometry_type="bbox",
            bbox=bbox, class_name=class_name, confidence=0.9,
            model_name="test.pt", model_sha256="deadbeef",
            working_grid_uid="grid-abc", source_window=None,
            preprocessing_hash="ph01",
        )

    session_a = create_session(
        project_id, scene_id, "yolo_scene",
        [make_proposal("vehicle", [10, 20, 50, 60]), make_proposal("tank", [70, 70, 90, 90])],
        device="cpu", model_name="test.pt", model_sha="deadbeef",
        working_grid_uid="grid-abc", params={"conf": 0.25},
    )
    session_b = create_session(
        project_id, scene_id, "yolo_scene",
        [make_proposal("aircraft", [5, 5, 25, 25])],
        device="cpu", model_name="test.pt",
    )
    assert session_a.session_id != session_b.session_id
    files = list_session_files(project_id, scene_id)
    assert len(files) == 2, "a new run must not overwrite the previous session"
    active = get_active_session(project_id, scene_id)
    assert active.session_id == session_b.session_id, "newest session is active"

    # Proposals carry provenance and the session id was stamped in.
    assert session_a.proposals[0].session_id == session_a.session_id
    assert session_a.proposals[0].working_grid_uid == "grid-abc"

    # --- Legacy projection matches the prediction-UI contract -----------------
    from routers.predictions import (
        AcceptRejectRequest, accept_predictions, list_predictions, delete_predictions,
        clear_predictions,
    )

    listed = asyncio.run(list_predictions(project_id, scene_id))
    assert len(listed) == 1  # active session (b) has one proposal
    row = listed[0]
    assert set(row) >= {"id", "class_id", "class_name", "bbox", "confidence", "source_model", "status", "created_at"}
    assert row["class_name"] == "aircraft" and row["status"] == "pending"

    # --- Accept maps model class -> project class, creates annotation ---------
    accept = asyncio.run(accept_predictions(
        project_id, scene_id, AcceptRejectRequest(all=True),
    ))
    assert accept["accepted"] == 1, accept
    assert accept["total_annotations"] == 1
    annotations = load_scene_json(project_id, scene_id, "annotations", default=[])
    assert len(annotations) == 1
    assert annotations[0]["annotation_source"] == "model_assisted"
    assert annotations[0]["source_model"] == "test.pt"
    assert annotations[0]["bbox"] == [5, 5, 25, 25]
    # Active (b) session now resolved.
    active_after = get_active_session(project_id, scene_id)
    assert active_after.status == "resolved"
    assert active_after.proposals[0].status == "accepted"

    # Switch active to session_a to exercise unmapped-class reporting + reject.
    from services.assistance_sessions import set_active_session
    set_active_session(project_id, scene_id, session_a.session_id)
    accept_a = asyncio.run(accept_predictions(
        project_id, scene_id, AcceptRejectRequest(all=True),
    ))
    # "vehicle" maps (class 0); "tank" has no project class -> reported, not accepted.
    assert accept_a["accepted"] == 1, accept_a
    assert accept_a["missing_classes"] == ["tank"], accept_a
    assert accept_a["total_annotations"] == 2

    # "tank" stayed pending (unmapped class); discard it (delete) now.
    pending_ids = [p.proposal_id for p in get_active_session(project_id, scene_id).proposals if p.status == "pending"]
    assert len(pending_ids) == 1, pending_ids
    deleted = asyncio.run(delete_predictions(
        project_id, scene_id, AcceptRejectRequest(prediction_ids=pending_ids),
    ))
    assert deleted["deleted"] == 1, deleted
    active_a = get_active_session(project_id, scene_id)
    assert active_a.status == "resolved"

    # --- Clear removes every session ------------------------------------------
    asyncio.run(clear_predictions(project_id, scene_id))
    assert asyncio.run(list_predictions(project_id, scene_id)) == []
    assert list_session_files(project_id, scene_id) == []

print("Assistance foundation (A0) smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "Assistance foundation smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
