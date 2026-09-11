"""Synthetic benchmark of sampled identity versus opt-in full SHA-256."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.common import add_common_arguments, configure_environment, output_path


OPERATION = "content_identity_synthetic"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument("--file-mib", type=int, default=128)
    parser.add_argument("--sample-mib", type=int, default=8)
    return parser


def run_content_identity_benchmark(
    *,
    output: Path,
    file_mib: int,
    sample_mib: int,
    cache_state: str = "unspecified",
    storage_profile: str = "unspecified",
) -> Path:
    if file_mib < 1 or sample_mib < 1:
        raise ValueError("file-mib and sample-mib must be positive")

    from services.performance_metrics import PerformanceRecorder
    from services.scene_packages.identity import sampled_content_signature, sha256_file

    file_bytes = file_mib * 1024 * 1024
    sample_bytes = sample_mib * 1024 * 1024
    with tempfile.TemporaryDirectory(prefix="geotile-identity-benchmark-") as temp_dir:
        source = Path(temp_dir) / "source.bin"
        block = bytes(range(256)) * 4096  # deterministic 1 MiB
        with source.open("wb") as handle:
            remaining = file_bytes
            while remaining:
                chunk = block[: min(len(block), remaining)]
                handle.write(chunk)
                remaining -= len(chunk)

        with PerformanceRecorder(
            OPERATION,
            output_path=output,
            cache_state=cache_state,
            storage_profile=storage_profile,
            inputs={"file_bytes": file_bytes, "sample_bytes": sample_bytes},
            metadata_value={"synthetic": True},
        ) as recorder:
            with recorder.stage("sampled_content_signature") as timer:
                signature = sampled_content_signature(source, sample_bytes=sample_bytes)
                timer.record(bytes_read_upper_bound=min(file_bytes, 3 * sample_bytes))
            with recorder.stage("full_sha256") as timer:
                digest = sha256_file(source)
                timer.record(bytes_read=file_bytes)
            sampled_seconds = recorder.report["stages"]["sampled_content_signature"]["wall_seconds"]
            exact_seconds = recorder.report["stages"]["full_sha256"]["wall_seconds"]
            recorder.set_metric("sampled_signature_prefix", signature.split(":", 1)[0])
            recorder.set_metric("full_sha256_length", len(digest))
            recorder.set_metric(
                "exact_to_sampled_time_ratio",
                round(exact_seconds / max(sampled_seconds, 1e-9), 3),
            )
    return output


def run(args: argparse.Namespace) -> Path:
    configure_environment(args)
    return run_content_identity_benchmark(
        output=output_path(args, OPERATION),
        file_mib=args.file_mib,
        sample_mib=args.sample_mib,
        cache_state=args.cache_state,
        storage_profile=args.storage_profile,
    )


def main() -> None:
    args = build_parser().parse_args()
    destination = run(args)
    print(destination)


if __name__ == "__main__":
    main()
