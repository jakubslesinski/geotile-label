"""P1.5 contracts: deterministic bounded pipeline and atomic run publication."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from models.dataset_config import DatasetConfig
from models.preprocessing import PreprocessingProfile
from models.tiling_config import TileInfo
from services import dataset_runs
from services.dataset_builder import DatasetBuildCancelled, build_dataset


def _consume(generator):
    progress = []
    while True:
        try:
            progress.append(next(generator))
        except StopIteration as stop:
            return stop.value, progress


def _fixture(tmp_path: Path, *, tile_size: int = 64):
    rng = np.random.default_rng(17)
    tiles: list[TileInfo] = []
    annotations: dict[str, list[list[float]]] = {}
    source_map: dict[str, str] = {}
    context: dict[str, dict] = {}
    manifests: dict[str, dict] = {}
    index = 0
    for scene_index in range(3):
        scene_id = f"scene-{scene_index}"
        source_path = tmp_path / f"{scene_id}.png"
        image = rng.integers(0, 256, size=(tile_size * 2, tile_size * 2, 3), dtype=np.uint8)
        Image.fromarray(image, mode="RGB").save(source_path, "PNG", compress_level=1)
        source_map[scene_id] = str(source_path)
        context[scene_id] = {"filename": source_path.name}
        manifests[scene_id] = {}
        for row in range(2):
            for col in range(2):
                filename = f"{scene_id}__tile_{row}_{col}.png"
                tiles.append(
                    TileInfo(
                        tile_id=f"tile-{index}",
                        scene_id=scene_id,
                        filename=filename,
                        col=col,
                        row=row,
                        x0=col * tile_size,
                        y0=row * tile_size,
                        x1=(col + 1) * tile_size,
                        y1=(row + 1) * tile_size,
                        review_status="reviewed",
                    )
                )
                annotations[filename] = [[index % 2, 0.5, 0.5, 0.25, 0.25]]
                index += 1
    profile = PreprocessingProfile(
        profile_id="p1-5-test",
        name="P1.5 Test",
        modality="EO",
        input_quantity="dn",
        radiometric_transform="none",
        percentile_stretch=False,
        rgb_conversion="native_rgb",
    )
    config = DatasetConfig(tile_selection="all", split_mode="random_tile", split_seed=123)
    return tiles, annotations, source_map, context, manifests, profile, config


def _tree_signature(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _normalized_stats(stats) -> dict:
    value = stats.model_dump()
    value["dataset_dir"] = "<dataset>"
    value["generated_at"] = "<timestamp>"
    return value


def test_pipeline_is_deterministic_across_worker_counts(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path)
    outputs = []
    for workers in (1, 4):
        monkeypatch.setenv("GEOTILE_DATASET_WRITE_WORKERS", str(workers))
        monkeypatch.setenv("GEOTILE_DATASET_RAM_MIB", "1")
        destination = tmp_path / f"dataset-{workers}"
        metrics: dict = {}
        generator = build_dataset(
            fixture[0],
            fixture[1],
            {},
            destination,
            fixture[6],
            {0: "even", 1: "odd"},
            scene_context=fixture[3],
            source_annotations_by_scene={key: [] for key in fixture[3]},
            scene_source_map=fixture[2],
            preprocessing_profile=fixture[5],
            tile_size=64,
            scene_manifests=fixture[4],
            pipeline_metrics=metrics,
        )
        stats, progress = _consume(generator)
        outputs.append((_tree_signature(destination), _normalized_stats(stats)))
        assert len(progress) == len(fixture[0])
        assert metrics["executor_count"] == 1
        assert metrics["worker_count"] == workers
        assert metrics["estimated_peak_buffer_bytes"] <= metrics["memory_budget_bytes"]
        assert metrics["tiles"] == len(fixture[0])

    assert outputs[0] == outputs[1]


def test_pipeline_cancellation_is_observed_at_batch_boundary(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path)
    monkeypatch.setenv("GEOTILE_DATASET_WRITE_WORKERS", "4")
    destination = tmp_path / "cancelled"
    cancelled = False

    generator = build_dataset(
        fixture[0],
        fixture[1],
        {},
        destination,
        fixture[6],
        {0: "even", 1: "odd"},
        scene_context=fixture[3],
        source_annotations_by_scene={key: [] for key in fixture[3]},
        scene_source_map=fixture[2],
        preprocessing_profile=fixture[5],
        tile_size=64,
        scene_manifests=fixture[4],
        should_cancel=lambda: cancelled,
    )
    first = next(generator)
    assert first["done"] == 1
    cancelled = True
    with pytest.raises(DatasetBuildCancelled):
        while True:
            next(generator)
    # At most the already completed batch is committed after the request.
    assert len(list(destination.glob("*/labels/*.txt"))) <= 4


def test_dataset_run_is_published_by_atomic_directory_rename(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_runs, "project_dir", lambda _project_id: tmp_path / "project")
    project_id = "project-1"
    run_id = "run-1"
    partial = dataset_runs.prepare_dataset_run_partial(project_id, run_id)
    (partial / "train").mkdir()
    (partial / "train" / "proof.txt").write_text("complete", encoding="utf-8")
    dataset_runs.write_run_json(
        partial,
        "dataset_run_manifest",
        {"run_id": run_id, "status": "complete", "created_at": "2026-01-01T00:00:00Z"},
    )

    final = dataset_runs.publish_dataset_run(project_id, run_id)

    assert final.is_dir()
    assert not partial.exists()
    assert (final / "train" / "proof.txt").read_text(encoding="utf-8") == "complete"
    listed = dataset_runs.list_dataset_runs(project_id)
    assert [item["run_id"] for item in listed["runs"]] == [run_id]


def test_partial_or_incomplete_run_is_never_listed_or_published(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_runs, "project_dir", lambda _project_id: tmp_path / "project")
    project_id = "project-1"
    run_id = "run-2"
    partial = dataset_runs.prepare_dataset_run_partial(project_id, run_id)
    dataset_runs.write_run_json(
        partial,
        "dataset_run_manifest",
        {"run_id": run_id, "status": "building", "created_at": "2026-01-01T00:00:00Z"},
    )

    assert dataset_runs.list_dataset_runs(project_id)["runs"] == []
    with pytest.raises(ValueError, match="complete run manifest"):
        dataset_runs.publish_dataset_run(project_id, run_id)
    assert partial.exists()
    assert not dataset_runs.dataset_run_dir(project_id, run_id).exists()
    assert dataset_runs.cleanup_dataset_run_partial(project_id, run_id) is True
    assert not partial.exists()


def test_startup_cleanup_removes_only_valid_dataset_staging_directories(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    runs = project_root / "dataset_runs"
    stale = runs / "20260829T120000000000Z_abcd1234.partial"
    unrelated = runs / "manual.notes.partial"
    complete = runs / "20260829T120000000000Z_abcd1234"
    stale.mkdir(parents=True)
    unrelated.mkdir()
    complete.mkdir()
    monkeypatch.setattr(
        dataset_runs,
        "load_projects_index",
        lambda: {"projects": [{"project_root": str(project_root)}]},
    )

    assert dataset_runs.cleanup_stale_dataset_run_partials() == 1
    assert not stale.exists()
    assert unrelated.exists()
    assert complete.exists()
