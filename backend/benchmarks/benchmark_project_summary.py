"""Benchmark project annotation summary in cold or application-cache-warm mode."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.common import add_common_arguments, configure_environment, output_path


OPERATION = "project_summary"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser, project_required=True)
    return parser


def run(args: argparse.Namespace) -> Path:
    configure_environment(args)

    from services.annotation_summary import compute_project_annotation_summary
    from services.performance_metrics import PerformanceRecorder

    destination = output_path(args, OPERATION)
    if args.cache_state == "warm":
        compute_project_annotation_summary(args.project_id, use_cache=True)

    with PerformanceRecorder(
        OPERATION,
        output_path=destination,
        project_id=args.project_id,
        cache_state=args.cache_state,
        storage_profile=args.storage_profile,
        metadata_value={
            "read_only": True,
            "cache_semantics": (
                "warm pre-fills the in-process summary cache; cold bypasses that cache; "
                "the operating-system disk cache is not flushed"
            ),
        },
    ) as recorder:
        stage_name = "summary_cache_hit" if args.cache_state == "warm" else "summary_uncached"
        with recorder.stage(stage_name) as timer:
            summary = compute_project_annotation_summary(
                args.project_id, use_cache=args.cache_state == "warm"
            )
            scene_count = _scene_count(summary)
            annotation_count = _annotation_count(summary)
            timer.record(scenes=scene_count, annotations=annotation_count)

        recorder.set_metric("scene_count", _scene_count(summary))
        recorder.set_metric("annotation_count", _annotation_count(summary))
        recorder.set_metric("class_count", len(summary.get("classes") or summary.get("per_class") or []))
    return destination


def _annotation_count(summary: dict[str, Any]) -> int:
    for key in ("annotation_count", "total_annotations", "annotations"):
        value = summary.get(key)
        if isinstance(value, int):
            return value
    scenes = summary.get("per_scene") or summary.get("scenes") or []
    return sum(int(scene.get("annotation_count") or 0) for scene in scenes if isinstance(scene, dict))


def _scene_count(summary: dict[str, Any]) -> int:
    value = summary.get("scene_count")
    if isinstance(value, int):
        return value
    return len(summary.get("per_scene") or summary.get("scenes") or [])


def main() -> None:
    args = build_parser().parse_args()
    destination = run(args)
    print(destination)


if __name__ == "__main__":
    main()
