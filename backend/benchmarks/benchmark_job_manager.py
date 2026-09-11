"""Synthetic durability/latency benchmark for the JSON Job Manager (P1.1)."""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.common import add_common_arguments, configure_environment, output_path


OPERATION = "job_manager_synthetic"


@contextmanager
def _temporary_data_dir(storage_module, path: Path) -> Iterator[None]:
    previous = storage_module.DATA_DIR
    storage_module.DATA_DIR = path
    try:
        yield
    finally:
        storage_module.DATA_DIR = previous


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument("--jobs", type=int, default=40)
    parser.add_argument("--updates-per-job", type=int, default=4)
    parser.add_argument("--events-per-job", type=int, default=2)
    return parser


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def _latency_metrics(values: list[float]) -> dict[str, float]:
    milliseconds = [value * 1000.0 for value in values]
    return {
        "count": len(milliseconds),
        "mean_ms": round(statistics.fmean(milliseconds), 6) if milliseconds else 0.0,
        "p50_ms": round(_percentile(milliseconds, 0.50), 6),
        "p95_ms": round(_percentile(milliseconds, 0.95), 6),
        "max_ms": round(max(milliseconds), 6) if milliseconds else 0.0,
    }


def run_job_manager_benchmark(
    *,
    output: Path,
    jobs: int,
    updates_per_job: int,
    events_per_job: int,
    cache_state: str = "unspecified",
    storage_profile: str = "unspecified",
) -> Path:
    if jobs < 1 or updates_per_job < 1 or events_per_job < 0:
        raise ValueError("jobs and updates-per-job must be positive; events-per-job cannot be negative")

    from db import storage
    from models.job import (
        PRIORITY_VALUES,
        JobSpec,
        JobType,
        PriorityClass,
        ResourceClass,
    )
    from services.jobs.events import append_event
    from services.jobs.store import create_job, list_jobs, mutate_state, utc_now
    from services.performance_metrics import PerformanceRecorder

    with tempfile.TemporaryDirectory(prefix="geotile-job-manager-benchmark-") as temp_name:
        data_root = Path(temp_name)
        with _temporary_data_dir(storage, data_root):
            project_id = "benchmark"
            project_root = data_root / "projects" / project_id
            project_root.mkdir(parents=True)
            (project_root / "project.json").write_text(
                '{"id":"benchmark","name":"Job Manager benchmark"}',
                encoding="utf-8",
            )

            create_latencies: list[float] = []
            update_latencies: list[float] = []
            event_latencies: list[float] = []
            with PerformanceRecorder(
                OPERATION,
                output_path=output,
                cache_state=cache_state,
                storage_profile=storage_profile,
                inputs={
                    "jobs": jobs,
                    "updates_per_job": updates_per_job,
                    "events_per_job": events_per_job,
                },
                metadata_value={"synthetic": True, "persistence": "atomic_json_and_jsonl"},
            ) as recorder:
                with recorder.stage("create_jobs"):
                    for index in range(jobs):
                        started = time.perf_counter()
                        create_job(
                            JobSpec(
                                job_id=f"job-{index:05d}",
                                job_type=JobType.DATASET_BUILD,
                                project_id=project_id,
                                resource_class=ResourceClass.IO_HEAVY,
                                priority_class=PriorityClass.USER_BACKGROUND,
                                priority=PRIORITY_VALUES[PriorityClass.USER_BACKGROUND],
                                payload={"benchmark_index": index},
                                created_at=utc_now(),
                            )
                        )
                        create_latencies.append(time.perf_counter() - started)

                with recorder.stage("atomic_state_updates"):
                    for index in range(jobs):
                        for update in range(updates_per_job):
                            started = time.perf_counter()
                            mutate_state(
                                project_id,
                                f"job-{index:05d}",
                                lambda state, value=update: {**state, "current": value + 1},
                            )
                            update_latencies.append(time.perf_counter() - started)

                with recorder.stage("durable_events"):
                    for index in range(jobs):
                        for event in range(events_per_job):
                            started = time.perf_counter()
                            append_event(
                                project_id,
                                f"job-{index:05d}",
                                "progress",
                                {"current": event + 1, "total": events_per_job},
                            )
                            event_latencies.append(time.perf_counter() - started)

                with recorder.stage("list_jobs") as timer:
                    listed = list_jobs(project_id)
                    timer.record(rows=len(listed))

                recorder.set_metric("create_latency", _latency_metrics(create_latencies))
                recorder.set_metric("state_update_latency", _latency_metrics(update_latencies))
                recorder.set_metric("event_append_latency", _latency_metrics(event_latencies))
                recorder.set_metric("listed_jobs", len(listed))
    return output


def run(args: argparse.Namespace) -> Path:
    configure_environment(args)
    return run_job_manager_benchmark(
        output=output_path(args, OPERATION),
        jobs=args.jobs,
        updates_per_job=args.updates_per_job,
        events_per_job=args.events_per_job,
        cache_state=args.cache_state,
        storage_profile=args.storage_profile,
    )


def main() -> None:
    args = build_parser().parse_args()
    destination = run(args)
    print(destination)


if __name__ == "__main__":
    main()
