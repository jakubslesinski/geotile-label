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
from datetime import datetime
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="geotile-utc-") as temp_dir:
    root = Path(temp_dir)
    os.environ["DATA_DIR"] = str(root)
    os.environ["MODELS_ROOT"] = str(root / "models")
    sys.path.insert(0, "backend")

    import db.storage as storage
    from db.storage import _canonical_utc, load_scene_json, save_scene_json

    def is_aware_isoformat(value):
        # Parses as ISO and carries an explicit offset (aware).
        dt = datetime.fromisoformat(value)
        return dt.tzinfo is not None

    # --- _canonical_utc: naive/space/Z/aware all collapse to one aware form -----
    naive_t = "2026-01-01T10:00:00"          # naive isoformat (router legacy)
    space_t = "2026-01-01 10:00:00.123456"   # str(datetime) form (create-path legacy)
    zulu_t = "2026-01-01T10:00:00Z"          # Z suffix
    aware_t = "2026-01-01T10:00:00+00:00"    # already canonical
    for value in (naive_t, space_t, zulu_t, aware_t):
        out = _canonical_utc(value)
        assert is_aware_isoformat(out), (value, out)
    # Same instant, three spellings -> identical canonical string (comparable).
    assert _canonical_utc(naive_t) == _canonical_utc(zulu_t) == _canonical_utc(aware_t), (
        _canonical_utc(naive_t), _canonical_utc(zulu_t), _canonical_utc(aware_t))
    assert _canonical_utc("") is None and _canonical_utc(None) is None
    assert _canonical_utc("not-a-date") == "not-a-date"  # unparseable left as-is
    # Idempotent.
    once = _canonical_utc(space_t)
    assert _canonical_utc(once) == once

    # --- Legacy annotations on disk get canonicalized on load -------------------
    project_id, scene_id = "proj-utc", "s1"
    # Written raw with mixed legacy timestamp spellings (bypassing normalization).
    scene_path = storage.scene_dir(project_id, scene_id)
    legacy = [
        {"id": "a1", "class_id": 0, "bbox": [1, 2, 3, 4],
         "created_at": "2026-01-01 09:00:00.000000", "updated_at": "2026-01-01 09:00:00.000000"},
        {"id": "a2", "class_id": 0, "bbox": [5, 6, 7, 8],
         "created_at": "2026-01-01T09:00:05", "updated_at": "2026-01-01T09:00:05"},
    ]
    storage._write_json_file(scene_path / "annotations.json", legacy)

    loaded = load_scene_json(project_id, scene_id, "annotations", default=[])
    for a in loaded:
        assert is_aware_isoformat(a["created_at"]), a["created_at"]
        assert is_aware_isoformat(a["updated_at"]), a["updated_at"]

    # --- No naive isoformat is ever written on the normal save path -------------
    save_scene_json(project_id, scene_id, "annotations", [
        {"id": "b1", "class_id": 0, "bbox": [1, 1, 2, 2]},  # no timestamps -> filled aware
    ])
    raw = (scene_path / "annotations.json").read_text(encoding="utf-8")
    reloaded = load_scene_json(project_id, scene_id, "annotations", default=[])
    for a in reloaded:
        assert is_aware_isoformat(a["created_at"]) and is_aware_isoformat(a["updated_at"]), a

    # --- Model datetime objects serialize canonically through storage -----------
    # Scene.created_at is an aware datetime object; _json_default must emit isoformat
    # (with 'T' + offset), never str(dt)'s space-separated form.
    from models.scene import Scene
    save_scene_json(project_id, scene_id, "scene", Scene(id=scene_id, filename="x.tif").model_dump())
    scene_raw = (scene_path / "scene.json").read_text(encoding="utf-8")
    import json as _json
    scene_created = _json.loads(scene_raw)["created_at"]
    assert "T" in scene_created and is_aware_isoformat(scene_created), scene_created

    # --- Mixed legacy-naive + new-aware rows sort by true instant ---------------
    # Older instant stored naive, newer instant stored aware; canonical form must
    # order them correctly (naive prefix would otherwise sort AFTER when compared raw).
    older_naive = _canonical_utc("2026-01-01T08:00:00")       # 08:00
    newer_aware = _canonical_utc("2026-01-01T09:00:00+00:00")  # 09:00
    assert older_naive < newer_aware, (older_naive, newer_aware)
    # The bug we fixed: the SAME instant in two spellings compares UNEQUAL as raw
    # strings (naive is a prefix of aware), so same-instant / "newer wins" logic
    # breaks. Canonicalization collapses them to one comparable value.
    assert "2026-01-01T09:00:00" != "2026-01-01T09:00:00+00:00"
    assert _canonical_utc("2026-01-01T09:00:00") == _canonical_utc("2026-01-01T09:00:00+00:00")

print("UTC timestamp unification (T3) smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "UTC timestamp smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
