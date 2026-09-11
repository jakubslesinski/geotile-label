"""Deterministic contracts for the JP2 full-resolution E0 workload."""

from __future__ import annotations

import pathlib
import math
import struct
import sys


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.benchmark_jp2_fullres_strategies import (  # noqa: E402
    _combine_checksums,
    _duration_stats,
    _edge_windows,
    _e3_performance,
    _random_windows,
    _tile_bounds_3857_runtime,
    _viewport_bounds,
    _runtime_summary_lines,
    build_parser,
    inspect_jp2_codestream,
)
from benchmarks.diagnose_jp2_warp_parity import (  # noqa: E402
    _array_difference,
    _boundary_tiles,
)


def _segment(marker: int, payload: bytes) -> bytes:
    return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload


def _synthetic_jp2() -> bytes:
    siz = struct.pack(
        ">HIIIIIIIIH",
        0,
        4096,
        2048,
        0,
        0,
        2048,
        1024,
        0,
        0,
        1,
    ) + bytes((15, 1, 1))
    cod = bytes(
        (
            0x01,  # explicit precincts
            0x02,  # RPCL
            0x00,
            0x01,  # one quality layer
            0x00,  # no MCT
            0x01,  # one decomposition / two resolutions
            0x04,
            0x04,  # 64x64 code block
            0x00,
            0x01,  # reversible 5/3
            0x66,
            0x77,
        )
    )
    plt = _segment(0x58, b"\x00\x01")
    tile_payload = plt + b"\xff\x93" + b"\x00\x01"
    tile_part_length = 12 + len(tile_payload)
    sot = b"\xff\x90" + struct.pack(">HHIBB", 10, 0, tile_part_length, 0, 1)
    codestream = (
        b"\xff\x4f"
        + _segment(0x51, siz)
        + _segment(0x52, cod)
        + _segment(0x55, b"\x00\x00")
        + sot
        + tile_payload
        + b"\xff\xd9"
    )
    signature_box = struct.pack(">I4s", 12, b"jP  ") + b"\x0d\x0a\x87\x0a"
    codestream_box = struct.pack(">I4s", len(codestream) + 8, b"jp2c") + codestream
    return signature_box + codestream_box


def test_jp2_codestream_inspection_reports_access_characteristics(tmp_path: pathlib.Path):
    source = tmp_path / "fixture.jp2"
    source.write_bytes(_synthetic_jp2())

    result = inspect_jp2_codestream(source)

    assert result["container"] == "jp2"
    assert result["siz"]["image_width"] == 4096
    assert result["siz"]["image_height"] == 2048
    assert result["siz"]["tile_count"] == 4
    assert result["cod"]["progression"] == "RPCL"
    assert result["cod"]["resolution_levels"] == 2
    assert result["cod"]["precincts"] == [
        {"resolution": 0, "width": 64, "height": 64},
        {"resolution": 1, "width": 128, "height": 128},
    ]
    assert result["tlm_present"] is True
    assert result["plt_present"] is True
    assert result["tile_part_count_observed"] == 1


def test_e0_random_and_edge_windows_are_deterministic_and_bounded():
    first = _random_windows(60_476, 43_476, 1024, 20260901, (1024, 1024))
    second = _random_windows(60_476, 43_476, 1024, 20260901, (1024, 1024))
    edges = _edge_windows(60_476, 43_476, 1024, (1024, 1024))

    assert first == second
    assert len(first) == 60
    assert len(edges) == 20
    assert len({window["id"] for window in first + edges}) == 80
    assert all(window["x"] % 1024 == 0 and window["y"] % 1024 == 0 for window in first)
    assert all(
        0 <= window["x"] <= 60_476 - 1024
        and 0 <= window["y"] <= 43_476 - 1024
        for window in first + edges
    )
    assert {window["edge"] for window in edges} == {"top", "right", "bottom", "left"}


def test_runtime_statistics_and_signatures_are_deterministic():
    stats = _duration_stats([5.0, 1.0, 3.0, 2.0, 4.0])

    assert stats["count"] == 5
    assert stats["minimum_ms"] == 1.0
    assert stats["p50_ms"] == 3.0
    assert stats["p95_ms"] == 4.8
    assert stats["maximum_ms"] == 5.0
    assert _combine_checksums(["a", "b"]) == _combine_checksums(["a", "b"])
    assert _combine_checksums(["a", "b"]) != _combine_checksums(["b", "a"])


def test_runtime_webmercator_viewport_is_exact_union_of_tiles():
    tiles = [
        {"z": 3, "x": 4, "y": 2},
        {"z": 3, "x": 5, "y": 2},
        {"z": 3, "x": 4, "y": 3},
        {"z": 3, "x": 5, "y": 3},
    ]
    individual = [_tile_bounds_3857_runtime(**tile) for tile in tiles]
    viewport = _viewport_bounds(tiles)

    assert viewport == (
        min(bounds[0] for bounds in individual),
        min(bounds[1] for bounds in individual),
        max(bounds[2] for bounds in individual),
        max(bounds[3] for bounds in individual),
    )
    assert math.isclose(individual[0][2], individual[1][0], rel_tol=0.0, abs_tol=1e-8)
    assert math.isclose(individual[0][1], individual[2][3], rel_tol=0.0, abs_tol=1e-8)


def test_runtime_arm_cli_contract():
    args = build_parser().parse_args(
        [
            "run-arm",
            "--manifest",
            "manifest.json",
            "--source-id",
            "primary",
            "--strategy",
            "A0",
            "--driver",
            "JP2OpenJPEG",
            "--runtime-label",
            "production",
            "--output",
            "arm.json",
        ]
    )

    assert args.stage == "E2"
    assert args.warmups == 3
    assert args.single_repeats == 30
    assert args.trace_repeats == 10
    assert args.gdal_cache_mib == 256
    assert args.threads == 1

    summary = build_parser().parse_args(
        ["summarize", "--stage", "E3", "--run-dir", "run", "--expected-threads", "1,4"]
    )
    assert summary.expected_threads == "1,4"


def test_e3_boundary_tiles_cover_corners_and_outside():
    class _Vrt:
        RasterXSize = 1024
        RasterYSize = 512

        @staticmethod
        def GetGeoTransform():
            return (-1000.0, 1.0, 0.0, 2000.0, 0.0, -1.0)

    edges, outside = _boundary_tiles(_Vrt(), 19)

    assert len(edges) == 4
    assert len(outside) == 4
    assert all(tile["z"] == 19 for tile in edges + outside)
    assert outside[0]["x"] < min(tile["x"] for tile in edges)
    assert outside[1]["x"] > max(tile["x"] for tile in edges)
    assert outside[2]["y"] < min(tile["y"] for tile in edges)
    assert outside[3]["y"] > max(tile["y"] for tile in edges)


def _e3_arm(strategy: str, source_id: str, threads: int, scale: float = 1.0):
    def workload(workload_id, variant, p95, *, pan_p95=None, peak_delta=128 * 1024**2):
        details = {"stats": {"p95_ms": p95}, "warmup_ms": [p95]}
        if pan_p95 is not None:
            details["per_viewport_stats"] = {"p95_ms": pan_p95}
        return {
            "workload": workload_id,
            "variant": variant,
            "cache_scope": "handle_warm",
            "details": details,
            "resource": {
                "peak_rss_delta_bytes": peak_delta,
                "rss_before_bytes": 100 * 1024**2,
                "rss_after_bytes": 120 * 1024**2,
            },
        }

    return {
        "source_id": source_id,
        "strategy": strategy,
        "configuration": {"threads": threads},
        "environment": {"total_ram_bytes": 16 * 1024**3},
        "workloads": [
            workload("W4", "viewport_4x4_independent_tiles", 800.0 * scale),
            workload(
                "W6",
                "five_adjacent_viewports_4x4_tiles",
                1000.0 * scale,
                pan_p95=150.0 * scale,
            ),
            workload("W7", "return_to_first_viewport_4x4_tiles", 200.0 * scale),
        ],
    }


def test_e3_performance_gate_selects_common_thread_and_enforces_memory():
    by_arm = {}
    for source_id in ("primary", "secondary"):
        by_arm[(source_id, "A1", 1)] = _e3_arm("A1", source_id, 1, scale=5.0)
        by_arm[(source_id, "B1", 1)] = _e3_arm("B1", source_id, 1)

    result = _e3_performance(
        by_arm=by_arm,
        source_ids=["primary", "secondary"],
        threads=[1],
    )

    assert result["passed"] is True
    assert result["recommended_threads"] == 1
    assert all(item["absolute_latency_gate"] for item in result["candidates"])
    assert all(item["resource_gate"] for item in result["candidates"])


def test_warp_parity_diagnostic_separates_data_from_exact_alpha():
    import numpy as np

    first = np.array(
        [
            [[100, 200], [300, 400]],
            [[65535, 65535], [65535, 65535]],
        ],
        dtype=np.uint16,
    )
    second = first.copy()
    second[0, 0, 0] += 1

    result = _array_difference(first, second)

    assert result["shape_equal"] is True
    assert result["different_values"] == 1
    assert result["per_band"][0]["different_pixels"] == 1
    assert result["per_band"][0]["rmse"] == 0.5
    assert result["per_band"][0]["correlation"] > 0.999
    assert result["per_band"][1]["different_pixels"] == 0
    assert result["per_band"][1]["correlation"] == 1.0


def test_runtime_summary_reports_non_exact_warp_without_encoding_damage():
    lines = _runtime_summary_lines(
        stage="E2",
        manifest_id="manifest",
        arm_count=4,
        comparisons=[],
        warp_parity=[],
        correctness_checks={
            "a0_problem_reproduced": True,
            "all_cross_arm_direct_pixels_match": True,
        },
        correctness_passed=True,
        warped_outputs_exact=False,
        warped_differences_characterized=True,
    )
    text = "\n".join(lines)

    assert "Status: **DONE**" in text
    assert "Warped pixels W3–W7 exact: `False`" in text
    assert "�" not in text
