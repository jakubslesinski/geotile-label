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

with tempfile.TemporaryDirectory(prefix="geotile-publish-") as temp_dir:
    root = Path(temp_dir)
    os.environ["DATA_DIR"] = str(root)
    os.environ["MODELS_ROOT"] = str(root / "models")
    sys.path.insert(0, "backend")

    from db.storage import project_dir, save_json
    from services.dataset_runs import (
        DatasetPublicationError,
        dataset_run_dependents,
        dataset_run_dir,
        list_dataset_runs,
        read_publication,
        register_dataset_run,
        set_dataset_run_publication,
        write_run_json,
    )

    project_id = "proj-pub"
    save_json(project_id, "project", {
        "id": project_id, "name": project_id,
        "profile": {"modality": "EO", "georeferencing": "NO_GEO", "annotation_mode": "bbox",
                    "labeling_author_email": "manager@example.com"},
    })

    def make_run(run_id, *, status="complete", validation="ok", readiness="ready"):
        manifest = {
            "run_id": run_id,
            "created_at": f"2026-01-01T10:00:0{run_id[-1]}+00:00",
            "completed_at": "2026-01-01T11:00:00+00:00",
            "status": status,
            "input_hash": "h" * 64,
            "tile_catalog_id": "cat-1",
            "dataset_config": {"split_mode": "scene_split", "split_seed": 42},
            "tiling_config": {"tile_size": 640},
            "preprocessing_profile": {"profile_id": "eo_rgb_percentile"},
            "statistics": {
                "total_tiles": 100, "positive_tiles": 80, "negative_tiles": 20,
                "total_annotations": 250,
                "validation_report": {"status": validation, "warnings": [], "errors": []},
            },
            "audit_summary": {"status": "ok", "readiness": readiness, "quality_score": 0.9},
        }
        run_dir = dataset_run_dir(project_id, run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        write_run_json(run_dir, "dataset_run_manifest", manifest)
        register_dataset_run(project_id, manifest)
        return manifest

    # === Gate A: istniejące runy są draft bez żadnej akcji użytkownika ======
    make_run("run-1")
    index = list_dataset_runs(project_id)
    entry = index["runs"][0]
    assert entry["publication_status"] == "draft", entry
    assert entry["publication_label"] is None, entry
    # Stan publikacji nie jest jeszcze zapisany na dysku — dopiero domyślny odczyt.
    assert not (dataset_run_dir(project_id, "run-1") / "publication.json").exists()
    assert read_publication(project_id, "run-1")["status"] == "draft"

    # === Gate B: publikacja wymaga etykiety ================================
    try:
        set_dataset_run_publication(project_id, "run-1", status="published")
        raise AssertionError("publication without a label should be rejected")
    except DatasetPublicationError as exc:
        assert "label" in str(exc).lower(), exc

    # === Gate C: publikacja działa i widać ją w indeksie ===================
    published = set_dataset_run_publication(project_id, "run-1", status="published", label="v1")
    assert published["status"] == "published" and published["label"] == "v1", published
    assert published["updated_by"] is None or isinstance(published["updated_by"], str)
    index = list_dataset_runs(project_id)
    entry = [item for item in index["runs"] if item["run_id"] == "run-1"][0]
    assert entry["publication_status"] == "published" and entry["publication_label"] == "v1", entry
    # Manifest pozostaje nietknięty — publikacja to osobny plik.
    manifest_on_disk = json.loads(
        (dataset_run_dir(project_id, "run-1") / "dataset_run_manifest.json").read_text(encoding="utf-8"))
    assert "publication_status" not in manifest_on_disk, "manifest must stay immutable"
    assert (dataset_run_dir(project_id, "run-1") / "publication.json").exists()

    # === Gate D: etykieta unikalna w projekcie =============================
    make_run("run-2")
    try:
        set_dataset_run_publication(project_id, "run-2", status="published", label="v1")
        raise AssertionError("duplicate label should be rejected")
    except DatasetPublicationError as exc:
        assert "already used" in str(exc), exc
    # Ta sama etykieta na tym samym runie jest w porządku (ponowna publikacja).
    set_dataset_run_publication(project_id, "run-1", status="published", label="v1")

    # === Gate E: bramka jakości ===========================================
    make_run("run-bad-audit", readiness="not_ready")
    try:
        set_dataset_run_publication(project_id, "run-bad-audit", status="published", label="v9")
        raise AssertionError("not_ready audit should block publication")
    except DatasetPublicationError as exc:
        assert "not_ready" in str(exc), exc

    make_run("run-bad-split", validation="error")
    try:
        set_dataset_run_publication(project_id, "run-bad-split", status="published", label="v8")
        raise AssertionError("split validation errors should block publication")
    except DatasetPublicationError as exc:
        assert "validation" in str(exc).lower(), exc

    make_run("run-incomplete", status="running")
    try:
        set_dataset_run_publication(project_id, "run-incomplete", status="published", label="v7")
        raise AssertionError("incomplete run should not be publishable")
    except DatasetPublicationError as exc:
        assert "completed" in str(exc).lower(), exc

    # Ostrzeżenia (nie błędy) publikację przepuszczają.
    make_run("run-warn", validation="warning", readiness="ready_with_warnings")
    set_dataset_run_publication(project_id, "run-warn", status="published", label="v2")

    # === Gate F: wycofanie i powrót do wersji roboczej =====================
    set_dataset_run_publication(project_id, "run-warn", status="deprecated")
    assert read_publication(project_id, "run-warn")["status"] == "deprecated"
    # Etykieta przetrwa wycofanie — rodowód modeli musi dalej ją rozwiązywać.
    assert read_publication(project_id, "run-warn")["label"] == "v2"
    set_dataset_run_publication(project_id, "run-warn", status="draft")
    assert read_publication(project_id, "run-warn")["status"] == "draft"

    # === Gate G: ochrona rodowodu (helper gotowy przed M3) =================
    assert dataset_run_dependents(project_id, "run-1") == []
    training_dir = project_dir(project_id) / "training_runs" / "t-1"
    training_dir.mkdir(parents=True, exist_ok=True)
    (training_dir / "training_manifest.json").write_text(
        json.dumps({"dataset_run_id": "run-1"}), encoding="utf-8")
    assert dataset_run_dependents(project_id, "run-1") == ["t-1"], dataset_run_dependents(project_id, "run-1")
    assert dataset_run_dependents(project_id, "run-2") == []

print("Dataset publication (M1) smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "Dataset publication smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
