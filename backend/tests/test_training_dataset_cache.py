"""P2.3 gates for rebuildable OBB training-dataset staging."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

import training_worker
from services import training_dataset


@pytest.fixture()
def cache_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    project_root = tmp_path / "project"
    dataset = project_root / "dataset_runs" / "run-1"
    for split in ("train", "val", "test"):
        images = dataset / split / "images"
        labels = dataset / split / "labels_obb"
        images.mkdir(parents=True)
        labels.mkdir(parents=True)
        for index in range(2):
            (images / f"tile-{index}.jpg").write_bytes((f"image-{split}-{index}" * 32).encode())
            if not (split == "test" and index == 1):
                (labels / f"tile-{index}.txt").write_text(
                    "0 0.1 0.1 0.9 0.1 0.9 0.9 0.1 0.9\n",
                    encoding="utf-8",
                )
    yaml_text = (
        "path: .\ntrain: train/images\nval: val/images\ntest: test/images\n"
        "names:\n  0: vessel\n"
    )
    (dataset / "data.yaml").write_text(yaml_text, encoding="utf-8")
    (dataset / "data_obb.yaml").write_text(yaml_text, encoding="utf-8")
    (dataset / "dataset_run_manifest.json").write_text(
        json.dumps({
            "run_id": "run-1",
            "input_hash": "a" * 64,
            "status": "complete",
            "preprocessing_profile": {"profile_hash": "profile-a"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(training_dataset, "project_dir", lambda _project_id: project_root)
    return project_root, dataset


def _resolve(dataset: Path, **overrides):
    options = {
        "project_id": "project-1",
        "dataset_run_id": "run-1",
        "dataset_dir": dataset,
        "dataset_input_hash": "a" * 64,
        "preprocessing_hash": "profile-a",
        "fallback_target": dataset.parent.parent / "training_runs" / "fallback" / "dataset_obb",
        "cache_enabled": True,
    }
    options.update(overrides)
    return training_dataset.resolve_obb_training_dataset(**options)


def test_second_identical_resolution_reuses_staging_and_ultralytics_location(
    cache_workspace: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    _project_root, dataset = cache_workspace
    first = _resolve(dataset)
    assert first["cache_hit"] is False
    data_yaml = Path(first["data_yaml"])
    assert data_yaml.is_file()
    parsed = yaml.safe_load(data_yaml.read_text(encoding="utf-8"))
    assert Path(parsed["path"]) == data_yaml.parent.resolve()
    assert (data_yaml.parent / "train" / "labels" / "tile-0.txt").is_file()
    assert (data_yaml.parent / "test" / "labels" / "tile-1.txt").read_text() == ""

    # A cache hit must not call the O(N) materializer again. A shared marker models
    # the labels.cache file that Ultralytics adds during the first training.
    ultralytics_cache = data_yaml.parent / "train" / "labels.cache"
    ultralytics_cache.write_bytes(b"ultralytics-cache")

    def forbidden_materialize(*_args, **_kwargs):
        raise AssertionError("identical staging was rebuilt")

    monkeypatch.setattr(training_dataset, "_materialize_obb_dataset", forbidden_materialize)
    second = _resolve(dataset)
    assert second["cache_hit"] is True
    assert second["cache_key"] == first["cache_key"]
    assert second["data_yaml"] == first["data_yaml"]
    assert ultralytics_cache.read_bytes() == b"ultralytics-cache"


def test_cache_manifest_is_versioned_bounded_and_invalidated_by_inputs(
    cache_workspace: tuple[Path, Path],
):
    _project_root, dataset = cache_workspace
    first = _resolve(dataset)
    manifest = json.loads(Path(first["cache_manifest"]).read_text(encoding="utf-8"))
    assert manifest["schema_name"] == "geotile_training_dataset_cache"
    assert manifest["schema_version"] == training_dataset.TRAINING_DATASET_CACHE_SCHEMA_VERSION
    assert manifest["exporter_version"] == training_dataset.TRAINING_DATASET_EXPORTER_VERSION
    assert manifest["source_fingerprint"]
    assert manifest["payload_file_count"] == 13  # 6 images + 6 labels + data.yaml
    assert len(manifest["dependencies"]) == 6  # aggregate roots, not one JSON item per tile
    assert manifest["logical_size_bytes"] > 0
    assert manifest["allocated_size_estimate_bytes"] <= manifest["logical_size_bytes"]
    if manifest["storage"]["hardlinked"]:
        assert manifest["allocated_size_estimate_bytes"] < manifest["logical_size_bytes"]

    changed_profile = _resolve(dataset, preprocessing_hash="profile-b")
    assert changed_profile["cache_hit"] is False
    assert changed_profile["cache_key"] != first["cache_key"]

    # A change to a small immutable source descriptor also invalidates the cache even
    # if the caller accidentally provides the old input hash.
    (dataset / "data_obb.yaml").write_text(
        (dataset / "data_obb.yaml").read_text(encoding="utf-8") + "# revision\n",
        encoding="utf-8",
    )
    changed_yaml = _resolve(dataset)
    assert changed_yaml["cache_key"] not in {first["cache_key"], changed_profile["cache_key"]}


def test_deleting_cache_preserves_dataset_run(cache_workspace: tuple[Path, Path]):
    _project_root, dataset = cache_workspace
    source_image = dataset / "train" / "images" / "tile-0.jpg"
    source_label = dataset / "train" / "labels_obb" / "tile-0.txt"
    before_image = source_image.read_bytes()
    before_label = source_label.read_bytes()
    resolved = _resolve(dataset)

    deleted = training_dataset.delete_training_dataset_cache("project-1", resolved["cache_key"])
    assert deleted["removed"] == [resolved["cache_key"]]
    assert not Path(resolved["data_yaml"]).parent.exists()
    assert source_image.read_bytes() == before_image
    assert source_label.read_bytes() == before_label
    assert (dataset / "dataset_run_manifest.json").is_file()


def test_legacy_path_remains_available_when_flag_is_disabled(
    cache_workspace: tuple[Path, Path],
):
    project_root, dataset = cache_workspace
    target = project_root / "training_runs" / "run-a" / "dataset_obb"
    resolved = training_dataset.resolve_obb_training_dataset(
        project_id="project-1",
        dataset_run_id="run-1",
        dataset_dir=dataset,
        dataset_input_hash="a" * 64,
        preprocessing_hash="profile-a",
        fallback_target=target,
        cache_enabled=False,
    )
    assert resolved["cache_enabled"] is False
    assert resolved["cache_key"] is None
    assert Path(resolved["data_yaml"]) == target / "data.yaml"
    assert not (project_root / "artifacts" / "training_datasets").exists()


def test_training_worker_records_cache_miss_then_hit(
    cache_workspace: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    project_root, dataset = cache_workspace
    monkeypatch.setenv(training_dataset.TRAINING_DATASET_CACHE_FLAG, "1")
    job = {
        "project_id": "project-1",
        "dataset_run_id": "run-1",
        "dataset_dir": str(dataset),
        "data_yaml": str(dataset / "data_obb.yaml"),
        "dataset_input_hash": "a" * 64,
        "preprocessing_profile_hash": "profile-a",
        "task": "obb",
    }

    first_run = project_root / "training_runs" / "first"
    first_state = training_worker.JobState(first_run, {})
    first_yaml = training_worker.resolve_data_yaml(job, first_run, first_state)
    assert first_state.state["training_dataset_cache"]["cache_hit"] is False
    assert "artifacts" in Path(first_yaml).parts
    assert not (first_run / "dataset_obb").exists()

    second_run = project_root / "training_runs" / "second"
    second_state = training_worker.JobState(second_run, {})
    second_yaml = training_worker.resolve_data_yaml(job, second_run, second_state)
    assert second_yaml == first_yaml
    assert second_state.state["training_dataset_cache"]["cache_hit"] is True


def test_lru_cleanup_uses_manifest_access_time(cache_workspace: tuple[Path, Path]):
    _project_root, dataset = cache_workspace
    old = _resolve(dataset, preprocessing_hash="old")
    fresh = _resolve(dataset, preprocessing_hash="fresh")
    old_manifest_path = Path(old["cache_manifest"])
    old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8"))
    old_manifest["last_accessed_at"] = (
        datetime.now(timezone.utc) - timedelta(days=60)
    ).isoformat()
    training_dataset.write_json_atomic(old_manifest_path, old_manifest)

    result = training_dataset.cleanup_training_dataset_cache("project-1", retention_days=30)
    assert result["removed"] == [old["cache_key"]]
    assert not old_manifest_path.parent.exists()
    assert Path(fresh["cache_manifest"]).is_file()
    assert (dataset / "dataset_run_manifest.json").is_file()
