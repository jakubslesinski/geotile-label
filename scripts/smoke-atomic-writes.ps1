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
import json
import os
import sys
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="geotile-atomic-") as temp_dir:
    root = Path(temp_dir)
    os.environ["DATA_DIR"] = str(root)
    os.environ["MODELS_ROOT"] = str(root / "models")
    sys.path.insert(0, "backend")

    import db.storage as storage
    from db.storage import load_scene_json, save_scene_json

    project_id = "proj-atomic"
    scene_id = "scene-1"

    old = [{"id": "a1", "class_id": 0, "bbox": [1, 2, 3, 4]},
           {"id": "a2", "class_id": 1, "bbox": [5, 6, 7, 8]}]
    save_scene_json(project_id, scene_id, "annotations", old)
    target = storage.scene_dir(project_id, scene_id) / "annotations.json"
    assert target.is_file(), "initial write did not land"
    # save/load normalize annotations (created_at, source_annotation_id, ...), so the
    # normalized read-back is the baseline the destination must preserve on failure.
    baseline = load_scene_json(project_id, scene_id, "annotations", default=[])
    assert [a["class_id"] for a in baseline] == [0, 1]

    # --- Force a crash mid-write: os.replace raises after the temp file exists ---
    # The old open("w")+json.dump truncated the destination on open, so a failure
    # here would leave annotations.json empty/half-written. The atomic writer only
    # ever touches the destination via os.replace, so the old content must survive.
    real_replace = os.replace
    def exploding_replace(src, dst):
        raise RuntimeError("simulated crash before replace")
    os.replace = exploding_replace

    new = [{"id": "a1", "class_id": 9, "bbox": [9, 9, 9, 9]}]
    crashed = False
    try:
        save_scene_json(project_id, scene_id, "annotations", new)
    except RuntimeError as exc:
        crashed = "simulated crash" in str(exc)
    finally:
        os.replace = real_replace
    assert crashed, "expected the simulated crash to propagate"

    # Destination is intact — full old content, valid JSON, not truncated.
    raw = target.read_text(encoding="utf-8")
    assert raw.strip(), "destination was truncated to empty"
    reloaded = json.loads(raw)
    assert reloaded == baseline, ("destination corrupted by failed write", reloaded)

    # No leftover temp files (the finally block cleans them even on failure).
    leftovers = [p.name for p in target.parent.iterdir() if p.name.endswith(".tmp")]
    assert not leftovers, ("temp files leaked", leftovers)

    # Recovery: a normal write after the fault applies cleanly.
    save_scene_json(project_id, scene_id, "annotations", new)
    recovered = load_scene_json(project_id, scene_id, "annotations", default=[])
    assert [a["class_id"] for a in recovered] == [9], recovered
    assert recovered != baseline

    # Temp names never collide with the *.json globs used elsewhere.
    storage._atomic_write_bytes(target.parent / "probe.json", b'{"ok": true}')
    assert json.loads((target.parent / "probe.json").read_text(encoding="utf-8")) == {"ok": True}

print("Atomic writes (T0) smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "Atomic writes smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
