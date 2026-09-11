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
import asyncio
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

with tempfile.TemporaryDirectory(prefix="geotile-exemplar-") as temp_dir:
    root = Path(temp_dir)
    os.environ["DATA_DIR"] = str(root)
    os.environ["MODELS_ROOT"] = str(root / "models")
    sys.path.insert(0, "backend")

    # --- Synthetic "parking lot": one textured patch stamped repeatedly -----
    # Textured (not solid) so correlation peaks are sharp — one match per object,
    # which is how real vehicles behave and what template matching relies on.
    scenes_dir = root / "scenes-src"
    scenes_dir.mkdir()
    img = np.full((600, 2400, 3), 40, dtype=np.uint8)
    obj_w, obj_h = 40, 24
    patch = np.random.default_rng(7).integers(60, 255, (obj_h, obj_w, 3), dtype=np.uint8)
    centers = [(120, 120), (220, 120), (320, 120), (120, 220), (220, 220)]
    far_center = (2200, 120)
    for (cx, cy) in [*centers, far_center]:
        img[cy - obj_h // 2: cy + obj_h // 2, cx - obj_w // 2: cx + obj_w // 2] = patch
    Image.fromarray(img).save(scenes_dir / "lot.tif")
    vrt_bands = []
    for band, color in ((1, "Red"), (2, "Green"), (3, "Blue")):
        vrt_bands.append(f"""
  <VRTRasterBand dataType="Byte" band="{band}">
    <ColorInterp>{color}</ColorInterp>
    <SimpleSource>
      <SourceFilename relativeToVRT="1">lot.tif</SourceFilename>
      <SourceBand>{band}</SourceBand>
      <SrcRect xOff="0" yOff="0" xSize="2400" ySize="600"/>
      <DstRect xOff="0" yOff="0" xSize="2400" ySize="600"/>
    </SimpleSource>
  </VRTRasterBand>""")
    (scenes_dir / "lot.vrt").write_text(
        '<VRTDataset rasterXSize="2400" rasterYSize="600">' + ''.join(vrt_bands) + '</VRTDataset>',
        encoding="utf-8",
    )

    from db.storage import save_json, save_scene_json, load_scene_json

    project_id = "proj-ex"
    scene_id = "scene-1"
    save_json(project_id, "project", {
        "id": project_id, "name": "Exemplar smoke",
        "scene_folder": str(scenes_dir),
        "profile": {"modality": "EO", "georeferencing": "NO_GEO", "annotation_mode": "bbox",
                    "labeling_author_email": "a@example.com"},
    })
    save_json(project_id, "classes", [{"id": 0, "name": "vehicle", "color": "#ff0000"}])
    save_scene_json(project_id, scene_id, "scene", {
        "id": scene_id, "filename": "lot.vrt", "status": "pending",
        "annotation_count": 0, "working_grid_uid": "grid-lot",
    })
    save_scene_json(project_id, scene_id, "annotations", [])

    from routers.assist import FindSimilarRequest, exemplar_find_similar
    from routers.predictions import AcceptRejectRequest, accept_predictions, list_predictions

    # Exemplar = the first planted object.
    ex = centers[0]
    exemplar_bbox = [ex[0] - obj_w / 2, ex[1] - obj_h / 2, ex[0] + obj_w / 2, ex[1] + obj_h / 2]

    result = asyncio.run(exemplar_find_similar(
        project_id, scene_id, FindSimilarRequest(exemplar_bbox=exemplar_bbox, class_id=0, threshold=0.7),
    ))
    # 5 identical objects, exemplar excluded -> the other 4 proposed.
    assert result["found"] == 4, result["found"]
    props = result["proposals"]
    assert all(p["class_id"] == 0 and p["status"] == "pending" for p in props)
    assert all(p["source_model"] == "template_ncc_v2" for p in props)

    # Proposed boxes sit on the other planted centres (not the exemplar).
    def center(b):
        return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    found_centers = sorted(tuple(round(c) for c in center(p["bbox"])) for p in props)
    expected = sorted(centers[1:])
    for (fx, fy), (ex2, ey2) in zip(found_centers, expected):
        assert abs(fx - ex2) <= 2 and abs(fy - ey2) <= 2, (found_centers, expected)

    # Search ranges: viewport limits matching, while whole-scene mode crosses
    # the internal 2048-px processing-block boundary without duplicate hits.
    from services.exemplar_assist import find_similar
    viewport_props = find_similar(
        project_id, scene_id,
        exemplar_bbox=exemplar_bbox, class_id=0, threshold=0.7,
        search_mode="viewport", search_bbox=[0, 0, 280, 180],
    )
    assert len(viewport_props) == 1, len(viewport_props)
    assert abs(center(viewport_props[0]["bbox"])[0] - centers[1][0]) <= 2

    scene_props = find_similar(
        project_id, scene_id,
        exemplar_bbox=exemplar_bbox, class_id=0, threshold=0.7,
        search_mode="scene",
    )
    assert len(scene_props) == 5, len(scene_props)
    scene_centers = [center(proposal["bbox"]) for proposal in scene_props]
    assert any(abs(cx - far_center[0]) <= 2 and abs(cy - far_center[1]) <= 2
               for cx, cy in scene_centers), scene_centers

    # Proposals flow through the shared staging + accept.
    listed = asyncio.run(list_predictions(project_id, scene_id))
    assert len(listed) == 4
    accept = asyncio.run(accept_predictions(project_id, scene_id, AcceptRejectRequest(all=True)))
    assert accept["accepted"] == 4, accept
    anns = load_scene_json(project_id, scene_id, "annotations", default=[])
    assert all(a["annotation_source"] == "assisted_exemplar" for a in anns), [a["annotation_source"] for a in anns]

    angle = 25.0
    front = [float(np.cos(np.deg2rad(angle))), float(np.sin(np.deg2rad(angle)))]
    oriented = asyncio.run(exemplar_find_similar(
        project_id, scene_id, FindSimilarRequest(
            exemplar_bbox=exemplar_bbox,
            class_id=0,
            threshold=0.7,
            geometry_type="rotated_bbox",
            exemplar_rotated_bbox={
                "cx": ex[0], "cy": ex[1], "width": 36.0, "height": 18.0, "angle_deg": angle,
            },
            exemplar_front_vector=front,
        ),
    ))
    assert oriented["found"] == 4, oriented["found"]
    for proposal in oriented["proposals"]:
        assert proposal["geometry_type"] == "rotated_bbox", proposal
        assert proposal["rotated_bbox"] is not None, proposal
        assert abs(proposal["rotated_bbox"]["angle_deg"] - angle) < 1e-6, proposal
        assert np.allclose(proposal["front_vector_scene_px"], front), proposal

    # Matching v2 refinement identifies moderate scale and rotation changes,
    # combines intensity/edge responses, and propagates them to OBB geometry.
    import cv2
    from services.exemplar_assist import (
        _combined_response,
        _gradient_image,
        _template_variants,
        _transform_template,
        transfer_exemplar_geometry,
    )

    gray_template = cv2.cvtColor(patch, cv2.COLOR_RGB2GRAY)
    transformed = _transform_template(gray_template, 1.1, 20.0)
    refinement_canvas = np.full(
        (transformed.shape[0] + 20, transformed.shape[1] + 20), 40, dtype=np.uint8,
    )
    refinement_canvas[10:10 + transformed.shape[0], 10:10 + transformed.shape[1]] = transformed
    refinement_edges = _gradient_image(refinement_canvas)
    best = None
    for variant in _template_variants(gray_template, 0.1, 20):
        response = _combined_response(refinement_canvas, refinement_edges, variant, True)
        if response.size == 0:
            continue
        _, score, _, location = cv2.minMaxLoc(response)
        candidate = (score, variant.scale, variant.angle_deg, location)
        if best is None or candidate[0] > best[0]:
            best = candidate
    assert best is not None and best[0] > 0.75, best
    assert abs(best[1] - 1.1) < 1e-6 and abs(best[2] - 20.0) < 1e-6, best

    transferred = transfer_exemplar_geometry(
        [{
            "bbox": [10.0, 10.0, 10.0 + transformed.shape[1], 10.0 + transformed.shape[0]],
            "_match_scale": 1.1,
            "_match_angle_deg": 20.0,
        }],
        exemplar_bbox=exemplar_bbox,
        geometry_type="rotated_bbox",
        exemplar_rotated_bbox={
            "cx": ex[0], "cy": ex[1], "width": 36.0, "height": 18.0, "angle_deg": angle,
        },
        exemplar_front_vector=front,
    )[0]
    assert abs(transferred["rotated_bbox"]["width"] - 39.6) < 1e-6, transferred
    assert abs(transferred["rotated_bbox"]["angle_deg"] - 45.0) < 1e-6, transferred
    expected_front = [
        front[0] * np.cos(np.deg2rad(20.0)) - front[1] * np.sin(np.deg2rad(20.0)),
        front[0] * np.sin(np.deg2rad(20.0)) + front[1] * np.cos(np.deg2rad(20.0)),
    ]
    assert np.allclose(transferred["front_vector_scene_px"], expected_front), transferred

    # --- SAR uses the same template workflow with SAR display preprocessing --
    save_json(project_id, "project", {
        "id": project_id, "name": "Exemplar smoke",
        "scene_folder": str(scenes_dir),
        "profile": {"modality": "SAR", "georeferencing": "NO_GEO", "annotation_mode": "bbox"},
    })
    sar_result = asyncio.run(exemplar_find_similar(
        project_id, scene_id, FindSimilarRequest(
            exemplar_bbox=exemplar_bbox,
            class_id=0,
            threshold=0.7,
            search_mode="scene",
        ),
    ))
    assert sar_result["found"] == 5, sar_result["found"]
    assert all(p["source_model"] == "template_ncc_v2" for p in sar_result["proposals"])

print("Exemplar assist Matching v2 smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "Exemplar assist smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
