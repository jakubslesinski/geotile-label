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
$env:GEOTILE_SAM_MOCK = "1"

$Script = @'
import asyncio
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

with tempfile.TemporaryDirectory(prefix="geotile-sam-") as temp_dir:
    root = Path(temp_dir)
    os.environ["DATA_DIR"] = str(root)
    os.environ["MODELS_ROOT"] = str(root / "models")  # isolate bundled-SAM lookups
    sys.path.insert(0, "backend")

    # --- Pure geometry: window snapped, zoom-independent, clamped ------------
    from services.sam_assist import (
        compute_window, encode_key, mask_to_bbox, mask_to_rotated_bbox,
        sam_model_family, sam_model_info, sam_predictor_class_for_checkpoint,
        UltralyticsSamBackend, Window, WINDOW_PX, WINDOW_SNAP,
    )

    assert sam_model_family("mobile_sam.pt") == "sam1"
    assert sam_model_family("sam_b.pt") == "sam1"
    assert sam_model_family("sam2.1_b.pt") == "sam2"
    assert sam_model_family("sam3.pt") == "sam3"
    assert sam_model_family("FastSAM-s.pt") == "fastsam"
    assert sam_model_info("FastSAM-s.pt")["supported"] is True
    assert sam_predictor_class_for_checkpoint("sam2.1_b.pt").__name__ == "SAM2Predictor"
    assert sam_predictor_class_for_checkpoint("sam3.pt").__name__ == "SAM3Predictor"

    win = compute_window(2000, 1500, 4000, 3000)
    assert win.width == WINDOW_PX and win.height == WINDOW_PX
    # Origin is snapped to the WINDOW_SNAP grid.
    assert win.x0 % WINDOW_SNAP == 0 and win.y0 % WINDOW_SNAP == 0
    # A click at the window centre snaps back to the same window (encode reuse).
    center = compute_window(win.x0 + win.width // 2, win.y0 + win.height // 2, 4000, 3000)
    assert (center.x0, center.y0) == (win.x0, win.y0)
    far = compute_window(2000 + WINDOW_PX, 1500 + WINDOW_PX, 4000, 3000)
    assert (far.x0, far.y0) != (win.x0, win.y0)
    # Click near an edge clamps the window inside the scene.
    edge = compute_window(10, 10, 4000, 3000)
    assert edge.x0 == 0 and edge.y0 == 0
    # Tiny scene shrinks the window.
    tiny = compute_window(50, 50, 100, 80)
    assert tiny.width == 100 and tiny.height == 80

    # Mask → geometry.
    mask = np.zeros((200, 200), dtype=bool)
    mask[40:80, 30:130] = True
    assert mask_to_bbox(mask) == [30, 40, 130, 80]
    rot = mask_to_rotated_bbox(mask)
    assert rot is not None and rot["width"] > 0 and rot["height"] > 0

    # Real-backend state regression without loading model weights. A single
    # predictor serves cached windows, therefore A -> B -> A must explicitly
    # restore A's features and source context before prompt decoding.
    class FakeTensor:
        def __init__(self, value):
            self.value = value
        def __len__(self):
            return len(self.value)
        def detach(self):
            return self
        def cpu(self):
            return self
        def numpy(self):
            return self.value

    class FakeMasks:
        def __init__(self, value):
            self.data = FakeTensor(value)

    class FakeResult:
        def __init__(self, value):
            self.masks = FakeMasks(value)

    class FakePredictor:
        def __init__(self):
            self.features = None
            self.last_source = None
        def set_image(self, source):
            self.features = tuple(int(v) for v in source[0, 0])
        def __call__(self, *, source, points, labels):
            expected = tuple(int(v) for v in source[0, 0])
            assert self.features == expected, (self.features, expected)
            self.last_source = source
            return [FakeResult(np.ones((1, source.shape[0], source.shape[1]), dtype=np.uint8))]

    real_backend = UltralyticsSamBackend.__new__(UltralyticsSamBackend)
    real_backend._predictor = FakePredictor()
    rgb_a = np.full((8, 8, 3), [10, 20, 30], dtype=np.uint8)
    rgb_b = np.full((8, 8, 3), [40, 50, 60], dtype=np.uint8)
    state_a = real_backend.encode(rgb_a)
    real_backend.encode(rgb_b)
    restored_a = real_backend.decode(state_a, [[4, 4]], [])
    assert restored_a.shape == (8, 8)
    assert tuple(real_backend._predictor.last_source[0, 0]) == (30, 20, 10), "RGB must be passed to Ultralytics as BGR"

    key_window = Window(0, 0, 1024, 1024)
    key_a = encode_key(
        scene_identity="project:p:scene:a", working_grid_uid=None,
        window=key_window, model_sha="model", device="cpu", device_precision="fp32",
    )
    key_b = encode_key(
        scene_identity="project:p:scene:b", working_grid_uid=None,
        window=key_window, model_sha="model", device="cpu", device_precision="fp32",
    )
    key_fp16 = encode_key(
        scene_identity="project:p:scene:a", working_grid_uid=None,
        window=key_window, model_sha="model", device="cuda", device_precision="fp16",
    )
    assert key_a != key_b and key_a != key_fp16

    # --- Synthetic EO scene + project ---------------------------------------
    from db.storage import save_json, save_scene_json

    scenes_dir = root / "scenes-src"
    scenes_dir.mkdir()
    # Larger than WINDOW_PX so different clicks yield different windows.
    Image.fromarray(np.random.default_rng(1).integers(0, 255, (1600, 2000, 3), dtype=np.uint8)).save(scenes_dir / "eo.png")

    project_id = "proj-sam"
    scene_id = "scene-1"
    save_json(project_id, "project", {
        "id": project_id, "name": "SAM assist smoke",
        "scene_folder": str(scenes_dir),
        "profile": {"modality": "EO", "georeferencing": "NO_GEO", "annotation_mode": "bbox",
                    "labeling_author_email": "a@example.com"},
    })
    save_json(project_id, "classes", [{"id": 0, "name": "vehicle", "color": "#ff0000"}])
    save_scene_json(project_id, scene_id, "scene", {
        "id": scene_id, "filename": "eo.png", "status": "pending",
        "annotation_count": 0, "working_grid_uid": "grid-eo",
    })
    save_scene_json(project_id, scene_id, "annotations", [])

    from routers.assist import SamClickRequest, SamTextRequest, sam_click, sam_text
    from routers.predictions import AcceptRejectRequest, accept_predictions, delete_predictions, list_predictions
    from services.sam_assist import ENCODE_CALLS, clear_encode_cache

    clear_encode_cache()
    ENCODE_CALLS["count"] = 0

    # --- bbox click ----------------------------------------------------------
    res = asyncio.run(sam_click(project_id, scene_id, SamClickRequest(x=700, y=600, geometry_type="bbox", class_id=0)))
    assert res["session_id"]
    bbox = res["proposal"]["bbox"]
    assert bbox[0] < 700 < bbox[2] and bbox[1] < 600 < bbox[3], bbox
    assert res["needs_front_direction"] is False
    assert ENCODE_CALLS["count"] == 1, "first click encodes the window"
    # The score of the chosen mask must reach the proposal. It used to be dropped, so the
    # review panel showed 0.000 for every click while text mode showed real values.
    assert res["proposal"]["confidence"] == 1.0, res["proposal"]["confidence"]

    # Zoom-independence: identical click → identical window and bbox.
    res2 = asyncio.run(sam_click(project_id, scene_id, SamClickRequest(x=700, y=600, geometry_type="bbox", class_id=0)))
    assert res2["source_window"] == res["source_window"]
    assert res2["proposal"]["bbox"] == bbox
    assert ENCODE_CALLS["count"] == 1, "same window must not re-encode"

    # --- Encode-once-click-many: a different click in the same window reuses --
    win = res["source_window"]  # [x0, y0, w, h]
    center = (win[0] + win[2] // 2, win[1] + win[3] // 2)
    asyncio.run(sam_click(project_id, scene_id, SamClickRequest(x=center[0], y=center[1], geometry_type="bbox", class_id=0)))
    assert ENCODE_CALLS["count"] == 1, "a click in the same window must reuse the encode"
    # A click far away lands in a different window → a new encode.
    asyncio.run(sam_click(project_id, scene_id, SamClickRequest(x=win[0] + win[2] + 600, y=win[1] + 700, geometry_type="bbox", class_id=0)))
    assert ENCODE_CALLS["count"] == 2, "a different window encodes again"

    # --- rotated_bbox click: default front, 90-degree rotation, annotation -----
    rot_res = asyncio.run(sam_click(project_id, scene_id, SamClickRequest(x=700, y=600, geometry_type="rotated_bbox", class_id=0)))
    assert rot_res["needs_front_direction"] is True
    assert rot_res["rotated_bbox"] is not None
    assert abs(rot_res["rotated_bbox"]["cx"] - 700) < 300
    prop = rot_res["proposal"]
    assert prop["geometry_type"] == "rotated_bbox"
    front = prop["front_vector_scene_px"]
    assert front is not None and (abs(front[0]) + abs(front[1])) > 0, ("no default front", prop)

    # Each action rotates clockwise by 90 degrees; four actions form a cycle.
    from routers.assist import rotate_proposal_front
    rotated = asyncio.run(rotate_proposal_front(project_id, scene_id, prop["id"]))
    rotated_front = rotated["proposal"]["front_vector_scene_px"]
    assert abs(rotated_front[0] + front[1]) < 1e-6, (front, rotated_front)
    assert abs(rotated_front[1] - front[0]) < 1e-6, (front, rotated_front)
    for _ in range(3):
        rotated = asyncio.run(rotate_proposal_front(project_id, scene_id, prop["id"]))
    ff = rotated["proposal"]["front_vector_scene_px"]
    assert abs(ff[0] - front[0]) < 1e-6 and abs(ff[1] - front[1]) < 1e-6, (front, ff)
    assert abs(rotated["proposal"]["rotated_bbox"]["angle_deg"] - prop["rotated_bbox"]["angle_deg"]) < 1e-6

    # Proposals accumulate in one active session, projected to the UI contract.
    listed = list_predictions(project_id, scene_id)
    assert len(listed) == 5, len(listed)

    # A false positive can be permanently removed before acceptance.
    removed = delete_predictions(
        project_id,
        scene_id,
        AcceptRejectRequest(prediction_ids=[res2["proposal"]["id"]]),
    )
    assert removed["deleted"] == 1, removed
    assert len(list_predictions(project_id, scene_id)) == 4

    # --- Accept → annotation with SAM provenance + OBB front -----------------
    accept = accept_predictions(project_id, scene_id, AcceptRejectRequest(all=True))
    assert accept["accepted"] == 4, accept
    from db.storage import load_scene_json
    anns = load_scene_json(project_id, scene_id, "annotations", default=[])
    assert all(a["annotation_source"] == "assisted_sam" for a in anns), [a["annotation_source"] for a in anns]
    obb = [a for a in anns if a["geometry_type"] == "rotated_bbox"]
    assert len(obb) == 1, len(obb)
    assert obb[0]["front_vector_scene_px"] is not None, obb[0]
    # The accepted front matches the flipped proposal front.
    assert abs(obb[0]["front_vector_scene_px"][0] - ff[0]) < 1e-6, (obb[0]["front_vector_scene_px"], ff)

    # --- Checkpoint resolution + capabilities --------------------------------
    from services.sam_assist import (
        resolve_sam_checkpoint, sam_capabilities, bundled_sam_dir, scan_sam_models,
    )
    caps = sam_capabilities()
    assert caps["mock"] is True and caps["available"] is True
    assert "runtime_available" in caps and caps["reason"] is None
    # A configured checkpoint that exists wins over bundled.
    ckpt = root / "sam_b.pt"
    ckpt.write_bytes(b"not-a-real-model")
    assert resolve_sam_checkpoint(str(ckpt)) == str(ckpt)
    # No config + no bundled file -> None.
    assert resolve_sam_checkpoint(None) is None
    # Unsupported .pt files are never picked as an automatic fallback.
    sam_dir = bundled_sam_dir()
    sam_dir.mkdir(parents=True, exist_ok=True)
    (sam_dir / "yolo_detect.pt").write_bytes(b"unsupported")
    assert resolve_sam_checkpoint(None) is None
    # Bundled file is picked up when present.
    (sam_dir / "mobile_sam.pt").write_bytes(b"bundled")
    assert resolve_sam_checkpoint(None) == str(sam_dir / "mobile_sam.pt")

    # A project-specific folder can be anywhere and is scanned recursively.
    custom_sam_dir = root / "external-models" / "sam"
    nested_sam_dir = custom_sam_dir / "sam2"
    nested_sam_dir.mkdir(parents=True)
    custom_checkpoint = nested_sam_dir / "sam2.1_t.pt"
    custom_checkpoint.write_bytes(b"custom")
    custom_models = scan_sam_models(custom_sam_dir)
    assert [item["path"] for item in custom_models if item["supported"]] == [str(custom_checkpoint)]
    assert resolve_sam_checkpoint(None, custom_sam_dir) == str(custom_checkpoint)
    save_json(project_id, "prediction_config", {
        "sam_models_dir": str(custom_sam_dir),
        "sam_checkpoint": None,
    })
    from routers.predictions import list_sam_models
    listed_models = list_sam_models(project_id)
    assert listed_models["custom_models_dir"] is True
    assert listed_models["models_dir_path"] == str(custom_sam_dir.resolve())
    assert listed_models["default_checkpoint"] == str(custom_checkpoint)

    # --- ST: SAM3 text mode (mock backend) -> multiple instance proposals ----
    # Scena EO ma 2000x1600 px (patrz fixture wyzej).
    tw = min(2000, 1024)
    th = min(1600, 1024)
    text_result = asyncio.run(sam_text(
        project_id,
        scene_id,
        SamTextRequest(class_id=0, search_bbox=[0, 0, tw, th], confidence_threshold=0.5),
    ))
    assert text_result["found"] >= 1, text_result
    assert len(text_result["proposals"]) == text_result["found"]
    for prop in text_result["proposals"]:
        bx = prop["bbox"]
        assert 0 <= bx[0] < bx[2] <= tw and 0 <= bx[1] < bx[3] <= th, bx
        assert prop["class_id"] == 0
    # Confidence filter drops low-score instances (mock scores: 0.86 / 0.71 / 0.58).
    high = asyncio.run(sam_text(
        project_id,
        scene_id,
        SamTextRequest(class_id=0, search_bbox=[0, 0, tw, th], confidence_threshold=0.8),
    ))
    assert high["found"] < text_result["found"], (high["found"], text_result["found"])

    # --- SAR uses the same SAM workflow after deterministic first-band stretch
    save_json(project_id, "project", {
        "id": project_id, "name": "SAM assist smoke",
        "scene_folder": str(scenes_dir),
        "profile": {"modality": "SAR", "georeferencing": "NO_GEO", "annotation_mode": "bbox"},
    })
    sar16 = np.random.default_rng(19).integers(20, 8000, (1600, 2000), dtype=np.uint16)
    Image.fromarray(sar16).save(scenes_dir / "sar16.tif")
    save_scene_json(project_id, scene_id, "scene", {
        "id": scene_id, "filename": "sar16.tif", "modality": "SAR", "status": "pending",
        "annotation_count": 0, "working_grid_uid": "grid-sar16",
    })
    from services.sam_assist import Window, read_rgb_window
    sar_rgb = read_rgb_window(scenes_dir / "sar16.tif", Window(0, 0, 512, 512), modality="SAR")
    assert sar_rgb.shape == (512, 512, 3) and sar_rgb.dtype == np.uint8, sar_rgb.shape
    assert np.array_equal(sar_rgb[:, :, 0], sar_rgb[:, :, 1])
    assert int(sar_rgb.max()) > int(sar_rgb.min()), (sar_rgb.min(), sar_rgb.max())
    clear_encode_cache()
    sar_result = asyncio.run(sam_click(
        project_id, scene_id, SamClickRequest(x=450, y=400, class_id=0),
    ))
    sar_bbox = sar_result["proposal"]["bbox"]
    assert sar_bbox[0] < 450 < sar_bbox[2] and sar_bbox[1] < 400 < sar_bbox[3], sar_bbox

print("SAM assist (A1.1-A1.3) smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "SAM assist smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
