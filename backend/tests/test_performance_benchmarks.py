"""Small deterministic B0 benchmark fixtures suitable for CI."""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import time

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("GEOTILE_BENCHMARK_READ_ONLY", "1")

from benchmarks.benchmark_catalog import group_by_scene  # noqa: E402
from benchmarks.benchmark_content_identity import run_content_identity_benchmark  # noqa: E402
from benchmarks.benchmark_dataset_build import run_synthetic_dataset_benchmark  # noqa: E402
from benchmarks.benchmark_embedding_search import run_synthetic_embedding_benchmark  # noqa: E402
from benchmarks.benchmark_job_manager import run_job_manager_benchmark  # noqa: E402
from benchmarks.benchmark_inference import prediction_signature  # noqa: E402
from benchmarks.benchmark_project_summary import _scene_count  # noqa: E402
from benchmarks.benchmark_scene_import import (  # noqa: E402
    ProcessTreeResourceSampler,
    _overview_creation_options,
    balanced_scene_groups,
    discover_scene_paths,
    overview_factors,
)
from benchmarks.verify_inference_parity import compare_predictions  # noqa: E402


def _assert_report(path: pathlib.Path, operation: str) -> dict:
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["schema_name"] == "geotile_performance_report"
    assert report["schema_version"] == 1
    assert report["operation"] == operation
    assert report["status"] == "completed"
    return report


def test_catalog_grouping_fixture():
    rows = [
        {"scene_id": "b", "value": 1},
        {"scene_id": "a", "value": 2},
        {"scene_id": "b", "value": 3},
    ]
    grouped = group_by_scene(rows)
    assert [item["value"] for item in grouped["b"]] == [1, 3]
    assert [item["value"] for item in grouped["a"]] == [2]


def test_project_summary_scene_count_contract():
    assert _scene_count({"scene_count": 7, "per_scene": []}) == 7
    assert _scene_count({"per_scene": [{"scene_id": "a"}, {"scene_id": "b"}]}) == 2


def test_synthetic_dataset_benchmark_fixture():
    with tempfile.TemporaryDirectory(prefix="geotile-benchmark-fixture-") as temp_name:
        output = pathlib.Path(temp_name) / "dataset.json"
        run_synthetic_dataset_benchmark(
            output=output,
            tile_count=4,
            tile_size=64,
            seed=7,
        )
        report = _assert_report(output, "dataset_build_synthetic")
        assert report["metrics"]["tiles_written"] == 4
        assert report["metrics"]["tiles_per_second"] > 0
        assert len(report["metrics"]["output_signature"]) == 64
        pipeline = report["metrics"]["pipeline"]
        assert pipeline["executor_count"] == 1
        assert pipeline["tiles"] == 4
        assert pipeline["estimated_peak_buffer_bytes"] <= pipeline["memory_budget_bytes"]


def test_synthetic_embedding_benchmark_fixture():
    with tempfile.TemporaryDirectory(prefix="geotile-embedding-fixture-") as temp_name:
        output = pathlib.Path(temp_name) / "embedding.json"
        run_synthetic_embedding_benchmark(
            output=output,
            objects=32,
            dimensions=8,
            threshold=0.999,
            max_pairs=20,
            seed=7,
        )
        report = _assert_report(output, "embedding_search_synthetic")
        assert report["metrics"]["embedding_bytes"] == 32 * 8 * 4
        assert report["metrics"]["objects_per_second"] > 0


def test_content_identity_benchmark_fixture():
    with tempfile.TemporaryDirectory(prefix="geotile-identity-fixture-") as temp_name:
        output = pathlib.Path(temp_name) / "identity.json"
        run_content_identity_benchmark(output=output, file_mib=4, sample_mib=1)
        report = _assert_report(output, "content_identity_synthetic")
        assert report["metrics"]["sampled_signature_prefix"] == "sig1"
        assert report["metrics"]["full_sha256_length"] == 64


def test_job_manager_benchmark_fixture():
    with tempfile.TemporaryDirectory(prefix="geotile-job-manager-fixture-") as temp_name:
        output = pathlib.Path(temp_name) / "jobs.json"
        run_job_manager_benchmark(
            output=output,
            jobs=2,
            updates_per_job=1,
            events_per_job=1,
        )
        report = _assert_report(output, "job_manager_synthetic")
        assert report["metrics"]["listed_jobs"] == 2
        assert report["metrics"]["create_latency"]["count"] == 2
        assert report["metrics"]["state_update_latency"]["count"] == 2
        assert report["metrics"]["event_append_latency"]["count"] == 2


def test_scene_import_benchmark_balances_disjoint_groups():
    with tempfile.TemporaryDirectory(prefix="geotile-scene-import-benchmark-") as temp_name:
        root = pathlib.Path(temp_name)
        sizes = [90, 70, 50, 30, 20, 10]
        paths = []
        for index, size in enumerate(sizes):
            path = root / f"scene_{index}.tif"
            path.write_bytes(bytes(size))
            paths.append(path.resolve())

        discovered = discover_scene_paths([], root)
        groups = balanced_scene_groups(discovered, 3)
        flattened = [path for group in groups for path in group]

        assert len(discovered) == len(paths)
        assert len(flattened) == len(set(flattened)) == len(paths)
        totals = [sum(path.stat().st_size for path in group) for group in groups]
        assert max(totals) - min(totals) <= max(sizes)


def test_scene_import_overview_factor_contract():
    assert overview_factors(4096, 2048) == [2, 4]
    assert overview_factors(4096, 2048, first_factor=4) == [4]
    assert overview_factors(256, 256) == [2]
    try:
        overview_factors(4096, 4096, first_factor=3)
    except ValueError:
        pass
    else:
        raise AssertionError("non-power-of-two first_factor must be rejected")


def test_scene_import_explicit_overview_options_contract():
    options = _overview_creation_options(
        {
            "compression": "ZSTD",
            "gdal_threads": "2",
            "predictor": 2,
            "zstd_level": 3,
            "block_size": 256,
            "max_z_error": None,
        }
    )

    assert options == [
        "LOCATION=EXTERNAL",
        "COMPRESS=ZSTD",
        "BIGTIFF=YES",
        "NUM_THREADS=2",
        "PREDICTOR=2",
        "BLOCKSIZE=256",
        "ZSTD_LEVEL=3",
    ]


def test_scene_import_explicit_overview_options_reject_invalid_combinations():
    invalid = {
        "compression": "DEFLATE",
        "gdal_threads": "1",
        "predictor": 2,
        "zstd_level": 3,
        "block_size": 300,
        "max_z_error": None,
    }
    try:
        _overview_creation_options(invalid)
    except ValueError as exc:
        assert "block_size" in str(exc)
    else:
        raise AssertionError("invalid block size must be rejected")

    invalid["block_size"] = 256
    try:
        _overview_creation_options(invalid)
    except ValueError as exc:
        assert "zstd_level" in str(exc)
    else:
        raise AssertionError("ZSTD level with DEFLATE must be rejected")


def test_scene_import_process_tree_resource_sampler_contract():
    with ProcessTreeResourceSampler(interval_seconds=0.01) as sampler:
        payload = bytearray(1024 * 1024)
        time.sleep(0.04)
        assert len(payload) == 1024 * 1024

    summary = sampler.summary()
    assert summary["status"] in {"completed", "completed_with_warning"}
    assert summary["sample_count"] >= 2
    assert summary["aggregate_rss_peak_bytes"] > 0
    assert summary["available_memory_min_bytes"] > 0
    assert summary["max_child_processes"] >= 0


def test_inference_prediction_signature_ignores_runtime_identity_and_order():
    first = [
        {
            "id": "runtime-a",
            "class_id": 1,
            "confidence": 0.91234567,
            "bbox": [1.0, 2.0, 3.0, 4.0],
            "geometry_type": "bbox",
            "created_at": "now",
        },
        {
            "id": "runtime-b",
            "class_id": 2,
            "confidence": 0.8,
            "bbox": [5.0, 6.0, 7.0, 8.0],
            "geometry_type": "bbox",
        },
    ]
    second = [{**first[1], "id": "other"}, {**first[0], "id": "different"}]
    assert prediction_signature(first) == prediction_signature(second)


def test_inference_parity_accepts_small_numeric_batch_drift():
    baseline = [
        {
            "class_id": 1,
            "confidence": 0.9,
            "bbox": [1.0, 2.0, 3.0, 4.0],
            "geometry_type": "rotated_bbox",
            "rotated_bbox": {
                "cx": 2.0,
                "cy": 3.0,
                "width": 2.0,
                "height": 1.0,
                "angle_deg": 179.99,
            },
        }
    ]
    candidate = [
        {
            **baseline[0],
            "confidence": 0.9002,
            "bbox": [1.01, 2.0, 3.0, 4.0],
            "rotated_bbox": {**baseline[0]["rotated_bbox"], "angle_deg": 0.01},
        }
    ]

    assert compare_predictions(baseline, candidate)["passed"] is True


def test_inference_parity_rejects_class_count_change():
    baseline = [
        {"class_id": 1, "confidence": 0.9, "bbox": [1, 2, 3, 4]}
    ]
    candidate = [
        {"class_id": 2, "confidence": 0.9, "bbox": [1, 2, 3, 4]}
    ]

    report = compare_predictions(baseline, candidate)

    assert report["passed"] is False
    assert report["group_count_mismatches"]


if __name__ == "__main__":
    for name, function in list(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
            print(f"OK {name}")
    print("all performance benchmark fixture tests passed")
