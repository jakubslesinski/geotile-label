"""P2.5 gates for bounded training auto-configuration and telemetry."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import training_worker
from services import training_preflight
from services.training_resources import benchmark_image_loader, recommend_training_resources

GIB = 1024 ** 3


def _loader(**overrides):
    value = {
        "sample_count": 24,
        "decoded_count": 24,
        "failure_count": 0,
        "train_image_count": 8_000,
        "image_count": 10_000,
        "read_seconds": 1.0,
        "decode_seconds": 0.2,
        "resize_seconds": 0.1,
        "read_mib_per_second": 80.0,
        "images_per_second": 18.0,
        "estimated_ram_cache_bytes": 12 * GIB,
    }
    value.update(overrides)
    return value


def _system(**overrides):
    value = {
        "physical_cpu_count": 16,
        "logical_cpu_count": 32,
        "memory_total_bytes": 128 * GIB,
        "memory_available_bytes": 100 * GIB,
        "device": "cuda",
        "multiprocessing_start_method": "spawn",
        "gpu_name": "test-gpu",
        "vram_total_bytes": 80 * GIB,
        "vram_free_bytes": 76 * GIB,
    }
    value.update(overrides)
    return value


def test_io_profile_recommends_bounded_workers_and_safe_ram_cache():
    recommendation = recommend_training_resources(
        loader=_loader(),
        system=_system(),
        requested_device="cuda",
        imgsz=640,
        current_batch=64,
        advanced_options={"workers": 12, "cache": False},
        estimated_vram_gb=4,
    )

    assert recommendation["bottleneck"]["kind"] == "io"
    assert recommendation["recommended_options"] == {
        "batch": -1,
        "workers": 2,
        "cache": "ram",
    }
    assert recommendation["memory_safety"]["ram_cache_safe"] is True
    assert recommendation["memory_safety"]["cache_copy_factor"] == 3
    assert recommendation["thread_budget"]["blas_threads_per_process"] <= 2
    assert set(recommendation["overrides"]) == {"batch", "workers", "cache"}


def test_ram_cache_is_never_recommended_without_required_headroom():
    recommendation = recommend_training_resources(
        loader=_loader(estimated_ram_cache_bytes=20 * GIB),
        system=_system(memory_total_bytes=32 * GIB, memory_available_bytes=22 * GIB),
        requested_device="cuda",
        imgsz=1024,
        current_batch=-1,
        advanced_options={"cache": "ram"},
    )

    assert recommendation["memory_safety"]["ram_cache_safe"] is False
    assert recommendation["recommended_options"]["cache"] is False
    assert "ram_cache_unsafe" in recommendation["warnings"]


def test_spawn_workers_account_for_possible_full_ram_cache_copies():
    recommendation = recommend_training_resources(
        loader=_loader(
            read_seconds=0.03,
            decode_seconds=0.6,
            resize_seconds=0.1,
            estimated_ram_cache_bytes=12 * GIB,
        ),
        system=_system(memory_total_bytes=128 * GIB, memory_available_bytes=104 * GIB),
        requested_device="cuda",
        imgsz=640,
        current_batch=-1,
        advanced_options={"workers": 8, "cache": False},
    )

    memory = recommendation["memory_safety"]
    assert recommendation["bottleneck"]["kind"] == "cpu_decode"
    assert memory["cache_copy_factor"] == 9
    assert memory["effective_ram_cache_bytes"] == 108 * GIB
    assert memory["ram_cache_safe"] is False
    assert recommendation["recommended_options"]["cache"] is False


def test_smallest_worker_count_within_five_percent_of_measured_peak_is_selected():
    loader = _loader(
        concurrency_profiles=[
            {"workers": 1, "decoded_count": 24, "images_per_second": 40.0},
            {"workers": 2, "decoded_count": 24, "images_per_second": 76.0},
            {"workers": 4, "decoded_count": 24, "images_per_second": 100.0},
            {"workers": 8, "decoded_count": 24, "images_per_second": 103.0},
        ],
        estimated_ram_cache_bytes=2 * GIB,
    )
    recommendation = recommend_training_resources(
        loader=loader,
        system=_system(memory_total_bytes=128 * GIB, memory_available_bytes=100 * GIB),
        requested_device="cuda",
        imgsz=640,
        current_batch=-1,
        advanced_options={},
    )

    assert recommendation["recommended_options"]["workers"] == 4
    assert recommendation["memory_safety"]["cache_copy_factor"] == 5


def test_loader_probe_is_bounded_and_does_not_create_a_cache(tmp_path: Path):
    for split in ("train", "val", "test"):
        directory = tmp_path / split / "images"
        directory.mkdir(parents=True)
        for index in range(5):
            image = np.full((48 + index, 64 + index, 3), index * 20, dtype=np.uint8)
            assert cv2.imwrite(str(directory / f"tile-{index}.png"), image)

    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    result = benchmark_image_loader(tmp_path, imgsz=64, sample_limit=3)
    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    assert result["sample_count"] == 3
    assert result["decoded_count"] == 3
    assert result["image_count"] == 15
    assert result["estimated_ram_cache_bytes"] > 0
    assert before == after
    assert not list(tmp_path.rglob("*.cache"))


def test_preflight_blocks_an_explicit_unsafe_ram_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    for split in ("train", "val"):
        images = tmp_path / split / "images"
        labels = tmp_path / split / "labels"
        images.mkdir(parents=True)
        labels.mkdir(parents=True)
        (images / "tile.jpg").write_bytes(b"image")
        (labels / "tile.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
    (tmp_path / "data.yaml").write_text(
        "path: .\ntrain: train/images\nval: val/images\nnames:\n  0: object\n",
        encoding="utf-8",
    )
    unsafe = recommend_training_resources(
        loader=_loader(estimated_ram_cache_bytes=20 * GIB),
        system=_system(memory_total_bytes=32 * GIB, memory_available_bytes=22 * GIB, device="cpu"),
        requested_device="cpu",
        imgsz=640,
        current_batch=4,
        advanced_options={"cache": "ram"},
    )
    monkeypatch.setattr(
        training_preflight,
        "build_training_resource_recommendation",
        lambda **_kwargs: unsafe,
    )

    result = training_preflight.run_preflight(
        dataset_dir=tmp_path,
        task="detect",
        requested_device="cpu",
        project_classes=["object"],
        imgsz=640,
        batch=4,
        advanced_options={"cache": "ram"},
    )

    assert result["can_start"] is False
    assert any(item["name"] == "cache-memory" and item["status"] == "error" for item in result["checks"])


def test_worker_thread_budget_tracks_dataloader_workers(monkeypatch: pytest.MonkeyPatch):
    job = {
        "advanced_options": {"workers": 7},
        "resource_preflight": {"system": {"physical_cpu_count": 16}},
    }
    config = training_worker._configure_worker_threads(job)

    assert config["data_loader_workers"] == 7
    assert config["blas_threads_per_process"] == 2
    assert config["opencv_threads_per_process"] == 0
    assert config["environment"]["OMP_NUM_THREADS"] == "2"


def test_epoch_telemetry_records_throughput_wait_and_manifest(tmp_path: Path):
    class Clock:
        value = 0.0

        def __call__(self):
            return self.value

    clock = Clock()
    state = training_worker.JobState(tmp_path, {})
    telemetry = training_worker.TrainingTelemetry(
        tmp_path,
        state,
        {"data_loader_workers": 2, "blas_threads_per_process": 1},
        clock=clock,
    )
    trainer = SimpleNamespace(epoch=0, batch=SimpleNamespace(shape=(4, 3, 64, 64)))

    telemetry.on_train_start(trainer)
    clock.value = 1.0
    telemetry.on_epoch_start(trainer)
    clock.value = 1.5
    telemetry.on_batch_start(trainer)
    clock.value = 2.5
    telemetry.on_batch_end(trainer)
    clock.value = 2.75
    telemetry.on_batch_start(trainer)
    clock.value = 3.75
    telemetry.on_batch_end(trainer)
    clock.value = 4.0
    entry = telemetry.on_epoch_end(trainer)
    payload = telemetry.finalize()

    assert entry["epoch_time_seconds"] == 3.0
    assert entry["images"] == 8
    assert entry["images_per_second"] == pytest.approx(8 / 3, abs=0.001)
    assert entry["data_wait_seconds"] == 0.75
    assert entry["data_wait_semantics"] == "inter_batch_callback_gap_proxy"
    assert payload["summary"]["epochs_recorded"] == 1
    persisted = json.loads((tmp_path / "training_performance.json").read_text(encoding="utf-8"))
    assert persisted["schema_name"] == "geotile_training_performance"
    assert persisted["epochs"][0]["images"] == 8


def test_epoch_telemetry_uses_loader_dataset_size_when_batch_is_not_exposed(tmp_path: Path):
    class Clock:
        value = 0.0

        def __call__(self):
            return self.value

    clock = Clock()
    telemetry = training_worker.TrainingTelemetry(
        tmp_path,
        training_worker.JobState(tmp_path, {}),
        {"data_loader_workers": 2},
        clock=clock,
    )
    trainer = SimpleNamespace(
        epoch=0,
        train_loader=SimpleNamespace(dataset=list(range(7)), batch_size=4, num_workers=2),
    )

    telemetry.on_train_start(trainer)
    clock.value = 1.0
    telemetry.on_epoch_start(trainer)
    clock.value = 1.5
    telemetry.on_batch_start(trainer)
    clock.value = 2.0
    telemetry.on_batch_end(trainer)
    clock.value = 3.0
    entry = telemetry.on_epoch_end(trainer)

    assert entry["images"] == 7
    assert entry["images_per_second"] == 3.5
    assert "peak_process_tree_rss_bytes" in entry
    assert telemetry.resource_config["effective_batch_size"] == 4
    assert telemetry.resource_config["effective_data_loader_workers"] == 2
