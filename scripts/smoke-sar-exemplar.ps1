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
# Deterministic mock SAR backbone — exercises the A3 orchestration offline.
$env:GEOTILE_SAR_EXEMPLAR_MOCK = "1"

$Script = @'
import asyncio
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

with tempfile.TemporaryDirectory(prefix="geotile-sar-ex-") as temp_dir:
    root = Path(temp_dir)
    os.environ["DATA_DIR"] = str(root)
    os.environ["MODELS_ROOT"] = str(root / "models")
    sys.path.insert(0, "backend")

    # --- Synthetic SAR-ish scene: a bright textured signature stamped repeatedly
    # on a dark, speckled sea. The mock backbone embeds patches and matches by
    # cosine similarity — the same plumbing a real SARATR-X/FG-MAE backbone uses.
    scenes_dir = root / "scenes-src"
    scenes_dir.mkdir()
    rng = np.random.default_rng(11)
    img = rng.integers(10, 45, (600, 800, 3), dtype=np.uint8)  # speckled background
    obj_w, obj_h = 40, 24
    # A structured, low-frequency target signature (a ramp) — this is what a
    # learned backbone embedding keys on (shape/structure), and it survives
    # downsampling, unlike raw speckle. Stamped identically at every object.
    yy, xx = np.mgrid[0:obj_h, 0:obj_w]
    sig = (70 + 120 * (xx / obj_w) + 50 * (yy / obj_h)).astype(np.uint8)
    patch = np.stack([sig, sig, sig], axis=2)
    centers = [(140, 130), (260, 130), (380, 130), (140, 250)]
    for (cx, cy) in centers:
        img[cy - obj_h // 2: cy + obj_h // 2, cx - obj_w // 2: cx + obj_w // 2] = patch
    Image.fromarray(img).save(scenes_dir / "sar.png")

    from db.storage import save_json, save_scene_json, load_scene_json

    project_id = "proj-sar-ex"
    scene_id = "scene-1"
    save_json(project_id, "project", {
        "id": project_id, "name": "SAR exemplar smoke",
        "scene_folder": str(scenes_dir),
        "profile": {"modality": "SAR", "georeferencing": "NO_GEO", "annotation_mode": "bbox",
                    "labeling_author_email": "a@example.com"},
    })
    save_json(project_id, "classes", [{"id": 3, "name": "ship", "color": "#00ffcc"}])
    save_scene_json(project_id, scene_id, "scene", {
        "id": scene_id, "filename": "sar.png", "status": "pending",
        "annotation_count": 0, "working_grid_uid": "grid-sar",
    })
    save_scene_json(project_id, scene_id, "annotations", [])

    from models.assistance import AssistanceProposal
    from routers.predictions import AcceptRejectRequest, accept_predictions, list_predictions
    from services.assistance_sessions import create_session
    from services.sar_exemplar_assist import find_similar_sar, resolve_backbone

    ex = centers[0]
    exemplar_bbox = [ex[0] - obj_w / 2, ex[1] - obj_h / 2, ex[0] + obj_w / 2, ex[1] + obj_h / 2]

    payloads = find_similar_sar(
        project_id, scene_id,
        exemplar_bbox=exemplar_bbox,
        class_id=3,
        threshold=0.8,
        backbone=resolve_backbone(None),
    )
    proposals = [AssistanceProposal(**payload) for payload in payloads]
    session = create_session(
        project_id,
        scene_id,
        "exemplar_sar",
        proposals,
        model_name="sar_mock_descriptor",
        working_grid_uid="grid-sar",
    )
    result = {
        "found": len(proposals),
        "proposals": [proposal.to_legacy_prediction() for proposal in session.proposals],
    }
    # 4 identical signatures, exemplar excluded -> the other 3 proposed.
    assert result["found"] == 3, result["found"]
    props = result["proposals"]
    assert all(p["class_id"] == 3 and p["status"] == "pending" for p in props)
    # SAR track carries the SAR-native backbone name (mock here), not template_ncc.
    assert all(p["source_model"] == "sar_mock_descriptor" for p in props), [p["source_model"] for p in props]

    def center(b):
        return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    found_centers = sorted(tuple(round(c) for c in center(p["bbox"])) for p in props)
    expected = sorted(centers[1:])
    for (fx, fy), (ex2, ey2) in zip(found_centers, expected):
        # sliding-window stride tolerance
        assert abs(fx - ex2) <= obj_w and abs(fy - ey2) <= obj_h, (found_centers, expected)

    # Proposals flow through the shared staging and accept with SAR provenance.
    listed = asyncio.run(list_predictions(project_id, scene_id))
    assert len(listed) == 3, len(listed)
    accept = asyncio.run(accept_predictions(project_id, scene_id, AcceptRejectRequest(all=True)))
    assert accept["accepted"] == 3, accept
    anns = load_scene_json(project_id, scene_id, "annotations", default=[])
    assert all(a["annotation_source"] == "assisted_exemplar_sar" for a in anns), \
        [a["annotation_source"] for a in anns]

    # --- No backbone configured (mock off) -> clear "not configured" error ----
    os.environ["GEOTILE_SAR_EXEMPLAR_MOCK"] = "0"
    from fastapi import HTTPException
    from services.sar_exemplar_assist import ExemplarError
    try:
        resolve_backbone(None)
        raise AssertionError("expected missing backbone to raise")
    except ExemplarError as exc:
        assert "backbone" in str(exc).lower(), str(exc)

    # Real backbone adapter is deferred (needs weights + GPU) — must not pretend.
    from services.sar_exemplar_assist import SaratrxFeatureBackend
    try:
        SaratrxFeatureBackend(Path("nope.pt")).embed([np.zeros((8, 8), dtype=np.uint8)])
        raise AssertionError("expected real backbone to be unavailable")
    except ExemplarError as exc:
        assert "gpu" in str(exc).lower() or "weights" in str(exc).lower(), str(exc)

print("SAR exemplar (A3.1) smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "SAR exemplar smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
