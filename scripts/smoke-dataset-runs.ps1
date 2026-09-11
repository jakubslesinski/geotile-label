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

$ResolvedPython = (Get-Command $PythonPath -ErrorAction Stop).Source
$EnvironmentRoot = Split-Path -Parent $ResolvedPython
$PreviousGdalDriverPath = $env:GDAL_DRIVER_PATH
$PreviousGdalData = $env:GDAL_DATA
$PreviousProjData = $env:PROJ_DATA
$PreviousProjLib = $env:PROJ_LIB
$PreviousPath = $env:PATH
$PreviousYolo = $env:GEOTILE_ENABLE_YOLO
$PreviousCatalogSnapshot = $env:GEOTILE_CATALOG_SNAPSHOT
$GdalPluginDir = Join-Path $EnvironmentRoot "Library\lib\gdalplugins"
$GdalDataDir = Join-Path $EnvironmentRoot "Library\share\gdal"
$ProjData = Join-Path $EnvironmentRoot "Library\share\proj"
$RuntimeBin = Join-Path $EnvironmentRoot "Library\bin"
if (Test-Path $GdalPluginDir) {
    $env:GDAL_DRIVER_PATH = $GdalPluginDir
}
if (Test-Path $GdalDataDir) {
    $env:GDAL_DATA = $GdalDataDir
}
if (Test-Path $ProjData) {
    $env:PROJ_DATA = $ProjData
    $env:PROJ_LIB = $ProjData
}
if (Test-Path $RuntimeBin) {
    $env:PATH = "$RuntimeBin;$EnvironmentRoot;$PreviousPath"
}
$env:GEOTILE_ENABLE_YOLO = "0"

$Script = @'
import json
import os
import sys
import tempfile
import asyncio
from contextlib import ExitStack
from pathlib import Path

import numpy as np
from PIL import Image

with ExitStack() as cleanup:
    temp_dir = cleanup.enter_context(
        tempfile.TemporaryDirectory(prefix="geotile-runs-", ignore_cleanup_errors=True)
    )
    root = Path(temp_dir)
    os.environ["DATA_DIR"] = str(root)
    sys.path.insert(0, "backend")

    import main
    from routers.dataset import (
        DatasetAuditSaveRequest,
        generate_dataset,
        get_dataset_audit,
        get_dataset_run_stats,
        get_dataset_runs,
        save_dataset_audit,
    )
    from routers.export import export_yolo
    from services.jobs.processes import terminate_process_tree
    from services.jobs.scheduler import get_scheduler
    from services.jobs.store import list_jobs

    project_id = "project-1"

    def stop_smoke_jobs():
        get_scheduler().stop()
        for item in list_jobs(project_id):
            identity = item.get("state", {}).get("process")
            if isinstance(identity, dict):
                terminate_process_tree(identity, timeout_s=3.0)

    # ExitStack runs callbacks in reverse order: stop the scheduler and any surviving
    # worker BEFORE TemporaryDirectory tries to remove `.state.lock` on Windows.
    cleanup.callback(stop_smoke_jobs)
    project_dir = root / "projects" / project_id
    scene_dir = project_dir / "scenes" / "scene-1"
    tiles_dir = scene_dir / "tiles" / "images"
    tiles_dir.mkdir(parents=True)
    source_dir = root / "source-scenes"
    source_dir.mkdir()
    source_scene = source_dir / "scene.png"
    pixels = np.zeros((80, 80, 3), dtype=np.uint8)
    pixels[:, :, 0] = np.arange(80, dtype=np.uint8)[None, :] * 3
    pixels[:, :, 1] = np.arange(80, dtype=np.uint8)[:, None] * 3
    pixels[:, :, 2] = 96
    Image.fromarray(pixels).save(source_scene)

    def write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    project = {
        "id": project_id,
        "name": "Dataset runs smoke",
        "scene_folder": str(source_dir),
        "profile": {
            "modality": "EO",
            "allowed_modalities": ["EO"],
            "georeferencing": "NO_GEO",
            "allowed_georeferencing": ["NO_GEO"],
            "allow_mixed_scenes": False,
            "sensors": [],
            "annotation_mode": "bbox",
            "labeling_author_email": "analyst@example.com",
            "default_preprocessing_profile": "eo_rgb_percentile",
            "default_split_strategy": "random_tile",
        },
    }
    annotation = {
        "id": "annotation-1",
        "source_annotation_id": "annotation-1",
        "scene_id": "scene-1",
        "class_id": 0,
        "geometry_type": "bbox",
        "bbox": [10, 20, 50, 40],
        "is_negative": False,
        "annotation_source": "manual",
        "annotator_email": "analyst@example.com",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    annotation_other = {
        **annotation,
        "id": "annotation-2",
        "source_annotation_id": "annotation-2",
        "class_id": 1,
        "bbox": [30, 30, 60, 60],
        "annotator_email": "other@example.com",
    }
    tile = {
        "filename": "1_1_scene.png",
        "col": 1,
        "row": 1,
        "x0": 0,
        "y0": 0,
        "reviewed": True,
        "excluded": False,
    }
    (tiles_dir / tile["filename"]).write_bytes(b"png")

    write_json(project_dir / "project.json", project)
    write_json(project_dir / "classes.json", [
        {"id": 0, "name": "vehicle", "color": "#ff0000"},
        {"id": 1, "name": "other", "color": "#00ff00"},
    ])
    write_json(project_dir / "tiling_config.json", {"tile_size": 80, "buffer": 0})
    write_json(project_dir / "dataset_config.json", {
        "train_ratio": 1,
        "val_ratio": 0,
        "test_ratio": 0,
        "min_box_fraction": 0.3,
        "negative_ratio": 0,
        "split_mode": "random_tile",
        "split_seed": 42,
        "block_size_tiles": 5,
        "scene_ids": ["scene-1"],
        "class_ids": [0],
        "annotator_emails": ["analyst@example.com"],
        "annotation_sources": ["manual"],
    })
    write_json(scene_dir / "scene.json", {
        "id": "scene-1",
        "filename": "scene.png",
        "status": "tiled",
        "annotation_count": 1,
        "tile_count": 1,
    })
    write_json(scene_dir / "scene_manifest.json", {
        "schema_name": "geotile_scene_manifest",
        "schema_version": 2,
        "scene_id": "scene-1",
        "filename": "scene.png",
        "source_scene_uid": "sha256:dataset-runs-smoke-scene",
        "source_file_sha256": "dataset-runs-smoke-scene",
        "source_identity_status": "complete",
        "source_identity_version": 1,
        "modality": "EO",
        "georeferencing": "NO_GEO",
        "acquisition_datetime_utc": "2021-06-01T10:00:00Z",
        "image": {"width": 80, "height": 80},
        "geospatial": {"has_geo": False, "transform": [1, 0, 0, 0, -1, 80]},
    })
    write_json(scene_dir / "tiles.json", [tile])
    write_json(scene_dir / "annotations.json", [annotation, annotation_other])

    async def consume_generation():
        response = await generate_dataset(project_id)
        events = []
        async for event in response.body_iterator:
            events.append(event)
        return events

    first_events = asyncio.run(consume_generation())
    assert any(event.get("event") == "complete" for event in first_events), first_events

    first_index = get_dataset_runs(project_id)
    assert len(first_index["runs"]) == 1
    first_run_id = first_index["latest_run_id"]
    first_run_dir = project_dir / "dataset_runs" / first_run_id
    assert (first_run_dir / "dataset_run_manifest.json").exists()
    assert (first_run_dir / "dataset_stats.json").exists()
    assert (first_run_dir / "split_manifest.json").exists()
    assert (first_run_dir / "dataset_selection.json").exists()
    assert (project_dir / "dataset" / "dataset_run_manifest.json").exists()
    first_manifest = json.loads((first_run_dir / "dataset_run_manifest.json").read_text(encoding="utf-8"))
    assert first_manifest["catalog_snapshot"]["mode"] == "snapshot"
    assert first_manifest["catalog_snapshot"]["snapshot_id"]
    selection = json.loads((first_run_dir / "dataset_selection.json").read_text(encoding="utf-8"))
    assert first_manifest["schema_version"] == 2
    assert first_manifest["tile_catalog_id"]
    assert first_manifest["selection"]["used_tile_count"] == 1
    assert selection["catalog_id"] == first_manifest["tile_catalog_id"]
    assert selection["filters"]["scene_ids"] == ["scene-1"]
    assert selection["filters"]["class_ids"] == [0]
    assert selection["filters"]["annotator_emails"] == ["analyst@example.com"]
    assert selection["used_tiles"][0]["tile_id"]
    filtered_tile_annotations = json.loads((first_run_dir / "tile_annotations.json").read_text(encoding="utf-8"))
    assert all(int(label[0]) == 0 for labels in filtered_tile_annotations.values() for label in labels)
    assert [item["id"] for item in first_manifest["classes"]] == [0]
    assert first_manifest["preprocessing_profile"]["profile_id"] == "eo_rgb_percentile"
    assert len(first_manifest["preprocessing_profile"]["profile_hash"]) == 64
    assert (first_run_dir / "preprocessing_profile.json").exists()
    assert (first_run_dir / "validation_report.json").exists()
    assert first_manifest["statistics"]["validation_report"]["status"] == "warning"
    assert first_manifest["audit_summary"]["readiness"] == "ready_with_warnings"
    assert (first_run_dir / "metadata" / "dataset_audit.json").exists()
    assert (first_run_dir / "metadata" / "dataset_audit.csv").exists()

    # Rollback parity: the legacy loaders must produce the same semantic artifacts.
    parity_files = (
        "dataset_stats.json",
        "tile_annotations.json",
        "tile_annotation_links.json",
        "split_manifest.json",
    )
    snapshot_artifacts = {
        name: json.loads((first_run_dir / name).read_text(encoding="utf-8"))
        for name in parity_files
    }
    snapshot_labels = {
        path.relative_to(first_run_dir).as_posix(): path.read_text(encoding="utf-8")
        for path in first_run_dir.rglob("*.txt")
    }
    os.environ["GEOTILE_CATALOG_SNAPSHOT"] = "0"
    legacy_events = asyncio.run(consume_generation())
    assert any(event.get("event") == "complete" for event in legacy_events), legacy_events
    legacy_index = get_dataset_runs(project_id)
    legacy_run_id = legacy_index["latest_run_id"]
    legacy_run_dir = project_dir / "dataset_runs" / legacy_run_id
    legacy_manifest = json.loads((legacy_run_dir / "dataset_run_manifest.json").read_text(encoding="utf-8"))
    assert legacy_manifest["catalog_snapshot"]["mode"] == "legacy", legacy_manifest["catalog_snapshot"]
    for name, expected in snapshot_artifacts.items():
        actual = json.loads((legacy_run_dir / name).read_text(encoding="utf-8"))
        if name in {"tile_annotation_links.json", "split_manifest.json"}:
            expected = {**expected, "generated_at": None}
            actual = {**actual, "generated_at": None}
        if name == "dataset_stats.json":
            dynamic_keys = {"generated_at", "dataset_dir", "run_id", "run_dir", "is_latest"}
            expected = {key: value for key, value in expected.items() if key not in dynamic_keys}
            actual = {key: value for key, value in actual.items() if key not in dynamic_keys}
        assert actual == expected, {"artifact": name, "snapshot": expected, "legacy": actual}
    legacy_labels = {
        path.relative_to(legacy_run_dir).as_posix(): path.read_text(encoding="utf-8")
        for path in legacy_run_dir.rglob("*.txt")
    }
    assert legacy_labels == snapshot_labels
    os.environ["GEOTILE_CATALOG_SNAPSHOT"] = "1"
    run_count_before_seed_change = len(legacy_index["runs"])

    audit_api = get_dataset_audit(project_id, first_run_id)
    assert audit_api["run_id"] == first_run_id
    saved_audit = save_dataset_audit(project_id, DatasetAuditSaveRequest(
        output_path=str(root / "saved-audit"),
        format="json",
        run_id=first_run_id,
    ))
    assert Path(saved_audit["output_path"]).is_file()
    config = json.loads((project_dir / "dataset_config.json").read_text(encoding="utf-8"))
    config["split_seed"] = 99
    write_json(project_dir / "dataset_config.json", config)
    second_events = asyncio.run(consume_generation())
    assert any(event.get("event") == "complete" for event in second_events), second_events

    second_index = get_dataset_runs(project_id)
    assert len(second_index["runs"]) == run_count_before_seed_change + 1
    assert second_index["latest_run_id"] != first_run_id
    first_stats = get_dataset_run_stats(project_id, first_run_id)
    assert first_stats["run_id"] == first_run_id
    assert first_stats["is_latest"] is False
    latest_stats = get_dataset_run_stats(project_id, second_index["latest_run_id"])
    assert latest_stats["is_latest"] is True
    second_manifest = json.loads(
        (project_dir / "dataset_runs" / second_index["latest_run_id"] / "dataset_run_manifest.json").read_text(encoding="utf-8")
    )
    assert second_manifest["tile_catalog_id"] == first_manifest["tile_catalog_id"]

    export_response = asyncio.run(export_yolo(project_id, first_run_id))
    assert export_response["status"] == "ok", export_response
    assert (first_run_dir / "geotile_export_manifest.json").exists()
    assert (first_run_dir / "metadata" / "tile_metadata.csv").exists()
    assert (first_run_dir / "metadata" / "annotation_links.csv").exists()
    export_manifest = json.loads((first_run_dir / "geotile_export_manifest.json").read_text(encoding="utf-8"))
    assert export_manifest["preprocessing_profile"]["profile_id"] == "eo_rgb_percentile"
    assert export_manifest["preprocessing_profile"]["profile_hash"] == first_manifest["preprocessing_profile"]["profile_hash"]
    assert export_manifest["dataset_validation"]["status"] == "warning"
    refreshed_audit = json.loads((first_run_dir / "metadata" / "dataset_audit.json").read_text(encoding="utf-8"))
    assert refreshed_audit["sidecar_summary"]["complete"] is True
    assert refreshed_audit["schema_name"] == "geotile_dataset_audit"

print("Dataset runs smoke test passed.")
'@

Push-Location $RepoRoot
try {
    $Script | & $PythonPath -B -
    if ($LASTEXITCODE -ne 0) {
        throw "Dataset runs smoke test failed with exit code $LASTEXITCODE."
    }
} finally {
    $env:GDAL_DRIVER_PATH = $PreviousGdalDriverPath
    $env:GDAL_DATA = $PreviousGdalData
    $env:PROJ_DATA = $PreviousProjData
    $env:PROJ_LIB = $PreviousProjLib
    $env:PATH = $PreviousPath
    $env:GEOTILE_ENABLE_YOLO = $PreviousYolo
    $env:GEOTILE_CATALOG_SNAPSHOT = $PreviousCatalogSnapshot
    Pop-Location
}
