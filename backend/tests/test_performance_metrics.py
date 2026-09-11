"""Contract tests for B0 performance reports and benchmark read-only mode."""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db import storage  # noqa: E402
from services.performance_metrics import (  # noqa: E402
    PERFORMANCE_SCHEMA_NAME,
    PERFORMANCE_SCHEMA_VERSION,
    PerformanceRecorder,
)


def test_completed_report_contract_and_atomic_write():
    with tempfile.TemporaryDirectory(prefix="geotile-performance-test-") as temp_name:
        output = pathlib.Path(temp_name) / "nested" / "performance.json"
        with PerformanceRecorder(
            "unit_test",
            output_path=output,
            cache_state="cold",
            inputs={"records": 3},
        ) as recorder:
            with recorder.stage("read") as timer:
                values = [1, 2, 3]
                timer.record(records=len(values), bytes=12)
            recorder.set_metric("sum", sum(values))

        report = json.loads(output.read_text(encoding="utf-8"))
        assert report["schema_name"] == PERFORMANCE_SCHEMA_NAME
        assert report["schema_version"] == PERFORMANCE_SCHEMA_VERSION
        assert report["status"] == "completed"
        assert report["cache_state"] == "cold"
        assert report["stages"]["read"]["calls"] == 1
        assert report["stages"]["read"]["metrics"]["records"] == 3
        assert report["metrics"]["sum"] == 6
        assert report["totals"]["wall_seconds"] >= 0
        assert not list(output.parent.glob("*.tmp"))


def test_repeated_stage_is_aggregated():
    recorder = PerformanceRecorder("repeat_test")
    with recorder.stage("batch") as first:
        first.record(items=2)
    with recorder.stage("batch") as second:
        second.record(items=3)
    recorder.finish("completed")
    stage = recorder.report["stages"]["batch"]
    assert stage["calls"] == 2
    assert stage["metrics"]["items"] == 5


def test_failed_context_persists_failed_report():
    with tempfile.TemporaryDirectory(prefix="geotile-performance-failed-") as temp_name:
        output = pathlib.Path(temp_name) / "failed.json"
        try:
            with PerformanceRecorder("failure_test", output_path=output) as recorder:
                with recorder.stage("explode"):
                    raise RuntimeError("expected failure")
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected RuntimeError")
        report = json.loads(output.read_text(encoding="utf-8"))
        assert report["status"] == "failed"
        assert "expected failure" in report["error"]
        assert report["stages"]["explode"]["errors"]


def test_benchmark_read_only_skips_lazy_annotation_write():
    with tempfile.TemporaryDirectory(prefix="geotile-performance-readonly-") as temp_name:
        project_root = pathlib.Path(temp_name)
        scene_root = project_root / "scenes" / "scene"
        scene_root.mkdir(parents=True)
        annotation_path = scene_root / "annotations.json"
        original = [{"class_id": 2, "bbox": [1, 2, 3, 4]}]
        annotation_path.write_text(json.dumps(original), encoding="utf-8")

        real_project_paths = storage.project_paths
        previous_read_only = os.environ.get("GEOTILE_BENCHMARK_READ_ONLY")
        storage.project_paths = lambda _project_id, create=False: storage.ProjectPaths(
            project_id="project", root=project_root
        )
        os.environ["GEOTILE_BENCHMARK_READ_ONLY"] = "1"
        try:
            normalized = storage.load_scene_json("project", "scene", "annotations", default=[])
        finally:
            storage.project_paths = real_project_paths
            if previous_read_only is None:
                os.environ.pop("GEOTILE_BENCHMARK_READ_ONLY", None)
            else:
                os.environ["GEOTILE_BENCHMARK_READ_ONLY"] = previous_read_only

        assert normalized[0].get("id"), "read result should still be normalized in memory"
        assert json.loads(annotation_path.read_text(encoding="utf-8")) == original


if __name__ == "__main__":
    for name, function in list(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
            print(f"OK {name}")
    print("all performance metric tests passed")
