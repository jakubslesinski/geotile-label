"""P1.2 contracts for bounded, batched whole-scene inference."""

from __future__ import annotations

import pathlib
import sys
import tempfile
import threading
from typing import Any

import numpy as np
import pytest
from PIL import Image

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from models.prediction import PredictionConfig  # noqa: E402
from services.inference_stitching import nms_aabb_classwise  # noqa: E402
from services.predictor import (  # noqa: E402
    PredictionCancelled,
    execute_prediction,
    iter_prediction_windows,
    resolve_inference_batch_size,
)


class FakeTensor:
    def __init__(self, value: Any):
        self.value = np.asarray(value)

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.value


class FakeBoxes:
    def __init__(self):
        self.xyxy = FakeTensor([[8.0, 8.0, 24.0, 24.0]])
        self.cls = FakeTensor([0])
        self.conf = FakeTensor([0.9])

    def __len__(self):
        return 1


class FakeObb:
    def __init__(self):
        self.xyxyxyxy = FakeTensor(
            [[
                [15.7574, 10.1005],
                [29.8995, 24.2426],
                [24.2426, 29.8995],
                [10.1005, 15.7574],
            ]]
        )
        self.cls = FakeTensor([0])
        self.conf = FakeTensor([0.9])

    def __len__(self):
        return 1


class FakeResult:
    def __init__(self, *, obb: bool = False):
        self.obb = FakeObb() if obb else None
        self.boxes = None if obb else FakeBoxes()


class FakeYOLO:
    instances: list["FakeYOLO"] = []
    task = "detect"

    def __init__(self, _model_path: str):
        self.names = {0: "object"}
        self.calls: list[int] = []
        type(self).instances.append(self)

    def predict(self, *, source, **_kwargs):
        count = len(source) if isinstance(source, list) else 1
        self.calls.append(count)
        return [FakeResult() for _ in range(count)]


class FakeObbYOLO(FakeYOLO):
    instances: list["FakeObbYOLO"] = []
    task = "obb"

    def predict(self, *, source, **_kwargs):
        count = len(source) if isinstance(source, list) else 1
        self.calls.append(count)
        return [FakeResult(obb=True) for _ in range(count)]


class FakeOomYOLO(FakeYOLO):
    instances: list["FakeOomYOLO"] = []

    def predict(self, *, source, **_kwargs):
        count = len(source) if isinstance(source, list) else 1
        self.calls.append(count)
        if count > 2:
            raise RuntimeError("CUDA out of memory")
        return [FakeResult() for _ in range(count)]


def _install_fake_ultralytics(monkeypatch, model_class=FakeYOLO) -> None:
    import ultralytics

    model_class.instances.clear()
    monkeypatch.setattr(ultralytics, "YOLO", model_class)


def _scene(path: pathlib.Path, width: int = 384, height: int = 256) -> pathlib.Path:
    x = np.arange(width, dtype=np.uint8)[None, :]
    y = np.arange(height, dtype=np.uint8)[:, None]
    rgb = np.stack(
        [np.broadcast_to(x, (height, width)), np.broadcast_to(y, (height, width)), (x + y) % 255],
        axis=-1,
    )
    Image.fromarray(rgb, mode="RGB").save(path)
    return path


def _consume(generator):
    events = []
    while True:
        try:
            events.append(next(generator))
        except StopIteration as stopped:
            return stopped.value, events


def _canonical(predictions: list[dict]) -> list[tuple]:
    return sorted(
        (
            item["class_id"],
            tuple(round(float(value), 5) for value in item["bbox"]),
            round(float(item["confidence"]), 5),
            item.get("geometry_type"),
        )
        for item in predictions
    )


def test_window_iterator_preserves_legacy_origin_sequence_without_materializing():
    assert list(iter_prediction_windows(130, 70, 64, 16)) == [
        (0, 0, 0),
        (1, 48, 0),
        (2, 96, 0),
        (3, 0, 48),
        (4, 48, 48),
        (5, 96, 48),
    ]


def test_batch_one_and_batch_three_produce_equivalent_aabb_results(monkeypatch):
    _install_fake_ultralytics(monkeypatch)
    with tempfile.TemporaryDirectory(prefix="geotile-inference-parity-") as name:
        scene = _scene(pathlib.Path(name) / "scene.png")
        performance_one: dict = {}
        predictions_one, _events_one = _consume(
            execute_prediction(
                scene,
                PredictionConfig(
                    model_path="fake.pt",
                    tile_size=128,
                    buffer=0,
                    batch_size=1,
                    prefetch_batches=1,
                ),
                performance_callback=performance_one.update,
            )
        )
        calls_one = list(FakeYOLO.instances[-1].calls)

        performance_three: dict = {}
        predictions_three, events_three = _consume(
            execute_prediction(
                scene,
                PredictionConfig(
                    model_path="fake.pt",
                    tile_size=128,
                    buffer=0,
                    batch_size=3,
                    prefetch_batches=2,
                ),
                performance_callback=performance_three.update,
            )
        )

    assert _canonical(predictions_one) == _canonical(predictions_three)
    assert calls_one == [1, 1, 1, 1, 1, 1]
    assert FakeYOLO.instances[-1].calls == [3, 3]
    assert events_three[-1]["done"] == 6
    assert performance_three["windows_processed"] == 6
    assert performance_three["peak_queued_batches"] <= 2
    assert performance_three["peak_queued_bytes"] <= performance_three["queue_capacity_bytes_estimate"]
    assert performance_three["merge_backend"] == "torchvision_batched_nms"


def test_cooperative_cancellation_is_checked_between_batches(monkeypatch):
    _install_fake_ultralytics(monkeypatch)
    with tempfile.TemporaryDirectory(prefix="geotile-inference-cancel-") as name:
        scene = _scene(pathlib.Path(name) / "scene.png")
        cancelled = False
        generator = execute_prediction(
            scene,
            PredictionConfig(
                model_path="fake.pt",
                tile_size=128,
                buffer=0,
                batch_size=1,
                progress_interval_ms=50,
            ),
            should_cancel=lambda: cancelled,
        )
        first = next(generator)
        assert first["done"] == 1
        cancelled = True
        with pytest.raises(PredictionCancelled):
            next(generator)


def test_auto_batch_retries_cuda_oom_with_smaller_chunks(monkeypatch):
    _install_fake_ultralytics(monkeypatch, FakeOomYOLO)
    monkeypatch.setenv("GEOTILE_INFERENCE_AUTO_BATCH", "4")
    with tempfile.TemporaryDirectory(prefix="geotile-inference-oom-") as name:
        scene = _scene(pathlib.Path(name) / "scene.png")
        performance: dict = {}
        predictions, _events = _consume(
            execute_prediction(
                scene,
                PredictionConfig(
                    model_path="fake.pt",
                    tile_size=128,
                    buffer=0,
                    device="cuda",
                    batch_size="auto",
                ),
                performance_callback=performance.update,
            )
        )
    assert len(predictions) == 6
    assert performance["resolved_batch_size"] == 4
    assert performance["oom_retries"] >= 1
    assert max(FakeOomYOLO.instances[-1].calls) == 4
    assert 2 in FakeOomYOLO.instances[-1].calls


def test_obb_path_preserves_oriented_geometry(monkeypatch):
    _install_fake_ultralytics(monkeypatch, FakeObbYOLO)
    with tempfile.TemporaryDirectory(prefix="geotile-inference-obb-") as name:
        scene = _scene(pathlib.Path(name) / "scene.png", width=128, height=128)
        performance: dict = {}
        predictions, _events = _consume(
            execute_prediction(
                scene,
                PredictionConfig(
                    model_path="fake-obb.pt",
                    tile_size=128,
                    buffer=0,
                    batch_size=1,
                ),
                performance_callback=performance.update,
            )
        )
    assert len(predictions) == 1
    prediction = predictions[0]
    assert prediction["geometry_type"] == "rotated_bbox"
    assert prediction["rotated_bbox"]["width"] == pytest.approx(20.0, rel=1e-3)
    assert prediction["rotated_bbox"]["angle_deg"] == pytest.approx(45.0, abs=1e-3)
    assert performance["geometry_type"] == "rotated_bbox"
    assert performance["merge_backend"] == "ultralytics_fast_nms_probiou"


def test_rasterio_handle_is_opened_and_used_by_reader_thread(monkeypatch):
    import rasterio
    from rasterio.transform import Affine

    _install_fake_ultralytics(monkeypatch)
    with tempfile.TemporaryDirectory(prefix="geotile-inference-reader-") as name:
        scene = pathlib.Path(name) / "scene.tif"
        with rasterio.open(
            scene,
            "w",
            driver="GTiff",
            width=128,
            height=128,
            count=1,
            dtype="uint8",
            transform=Affine.identity(),
        ) as destination:
            destination.write(np.arange(128 * 128, dtype=np.uint8).reshape(1, 128, 128))

        original_open = rasterio.open
        open_threads: list[str] = []

        def tracked_open(*args, **kwargs):
            open_threads.append(threading.current_thread().name)
            return original_open(*args, **kwargs)

        monkeypatch.setattr(rasterio, "open", tracked_open)
        predictions, _events = _consume(
            execute_prediction(
                scene,
                PredictionConfig(
                    model_path="fake.pt",
                    tile_size=128,
                    buffer=0,
                    batch_size=1,
                ),
            )
        )
    assert predictions
    assert open_threads == ["prediction-window-reader"]


def test_native_aabb_nms_is_class_aware():
    detections = [
        {"bbox": [0, 0, 10, 10], "confidence": 0.9, "class_id": 0},
        {"bbox": [0, 0, 10, 10], "confidence": 0.8, "class_id": 0},
        {"bbox": [0, 0, 10, 10], "confidence": 0.7, "class_id": 1},
    ]
    kept, backend = nms_aabb_classwise(detections, 0.5)
    assert backend == "torchvision_batched_nms"
    assert [(item["class_id"], item["confidence"]) for item in kept] == [(0, 0.9), (1, 0.7)]


def test_auto_batch_override_and_validation(monkeypatch):
    monkeypatch.setenv("GEOTILE_INFERENCE_AUTO_BATCH", "7")
    assert resolve_inference_batch_size(PredictionConfig(device="cuda")) == 7
    with pytest.raises(ValueError):
        PredictionConfig(batch_size=0)
    with pytest.raises(ValueError):
        PredictionConfig(batch_size=65)
