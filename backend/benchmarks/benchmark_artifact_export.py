"""Synthetic P1.6 benchmark for streamed ZIP creation and warm cache reuse."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.common import add_common_arguments, configure_environment, output_path
from services.performance_metrics import PerformanceRecorder


OPERATION = "artifact_export_synthetic"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument("--payload-mib", type=int, default=32)
    parser.add_argument("--formats", nargs="+", default=["yolo", "coco"])
    return parser


def run(args: argparse.Namespace) -> Path:
    configure_environment(args)
    if args.payload_mib < 1:
        raise ValueError("payload-mib must be positive")
    destination = output_path(args, OPERATION)

    from services import archive_io, artifact_exports

    with tempfile.TemporaryDirectory(prefix="geotile-benchmark-export-") as temp_name:
        temp_root = Path(temp_name)
        project_root = temp_root / "project"
        dataset = project_root / "dataset_runs" / "benchmark-run"
        images = dataset / "train" / "images"
        images.mkdir(parents=True)
        payload = images / "payload.png"
        remaining = args.payload_mib * 1024 * 1024
        with payload.open("wb") as handle:
            while remaining:
                chunk = os.urandom(min(1024 * 1024, remaining))
                handle.write(chunk)
                remaining -= len(chunk)

        old_project_dir = artifact_exports.project_dir
        old_load_json = artifact_exports.load_json
        old_builder = artifact_exports.build_dataset_package
        old_runtime = archive_io.ARCHIVE_RUNTIME_DIR
        old_registry = archive_io.PARTIAL_REGISTRY_PATH

        def fake_builder(_project_id, _dataset_dir, *, formats, package_dir, **_kwargs):
            target = Path(package_dir)
            for export_format in formats:
                output = target / export_format / "images" / "payload.png"
                output.parent.mkdir(parents=True, exist_ok=True)
                try:
                    output.hardlink_to(payload)
                except OSError:
                    import shutil

                    shutil.copy2(payload, output)
            (target / "manifest.txt").write_text(",".join(formats), encoding="utf-8")
            return target

        artifact_exports.project_dir = lambda _project_id: project_root
        artifact_exports.load_json = lambda *_args, **_kwargs: {"name": "Benchmark"}
        artifact_exports.build_dataset_package = fake_builder
        archive_io.ARCHIVE_RUNTIME_DIR = temp_root / "runtime" / "archives"
        archive_io.PARTIAL_REGISTRY_PATH = temp_root / "runtime" / "archive_partials.json"
        try:
            with PerformanceRecorder(
                OPERATION,
                output_path=destination,
                cache_state=args.cache_state,
                storage_profile=args.storage_profile,
                inputs={"payload_mib": args.payload_mib, "formats": args.formats},
                metadata_value={"synthetic": True, "zip_buffering": "disk_streamed"},
            ) as recorder:
                with recorder.stage("cold_export") as timer:
                    cold = artifact_exports.create_dataset_export_artifact(
                        "benchmark-project",
                        dataset,
                        dataset_run_id="benchmark-run",
                        formats=args.formats,
                    )
                    timer.record(
                        cache_hit=cold["cache_hit"],
                        archive_size_bytes=cold["archive_size_bytes"],
                    )
                with recorder.stage("warm_retry") as timer:
                    warm = artifact_exports.create_dataset_export_artifact(
                        "benchmark-project",
                        dataset,
                        dataset_run_id="benchmark-run",
                        formats=args.formats,
                    )
                    timer.record(cache_hit=warm["cache_hit"])
                recorder.set_metric("cold_cache_hit", cold["cache_hit"])
                recorder.set_metric("warm_cache_hit", warm["cache_hit"])
                recorder.set_metric("archive_size_bytes", cold["archive_size_bytes"])
                recorder.set_metric("sha256", cold["archive_sha256"])
        finally:
            artifact_exports.project_dir = old_project_dir
            artifact_exports.load_json = old_load_json
            artifact_exports.build_dataset_package = old_builder
            archive_io.ARCHIVE_RUNTIME_DIR = old_runtime
            archive_io.PARTIAL_REGISTRY_PATH = old_registry
    return destination


def main() -> int:
    path = run(build_parser().parse_args())
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
