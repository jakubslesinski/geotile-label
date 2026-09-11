"""Reproducible P2.5 preflight/loader probe; never starts training or writes a cache."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.training_resources import build_training_resource_recommendation  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def parse_cache(value: str) -> bool | str:
    normalized = value.strip().lower()
    if normalized in {"false", "off", "none"}:
        return False
    if normalized in {"ram", "disk"}:
        return normalized
    raise argparse.ArgumentTypeError("cache must be false, ram or disk")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=-1)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--cache", type=parse_cache, default=False)
    parser.add_argument("--storage-profile", default="unspecified")
    args = parser.parse_args()

    dataset_dir = args.dataset_dir.resolve()
    manifest_path = dataset_dir / "dataset_run_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {}
    )
    advanced: dict[str, Any] = {"cache": args.cache}
    if args.workers is not None:
        advanced["workers"] = args.workers

    started_at = utc_now()
    started = time.perf_counter()
    recommendation = build_training_resource_recommendation(
        dataset_dir=dataset_dir,
        requested_device=args.device,
        imgsz=args.imgsz,
        current_batch=args.batch,
        advanced_options=advanced,
    )
    elapsed = time.perf_counter() - started
    report = {
        "schema_name": "geotile_training_resource_benchmark",
        "schema_version": 1,
        "started_at": started_at,
        "completed_at": utc_now(),
        "wall_seconds": round(elapsed, 6),
        "cache_semantics": (
            "The operating-system file cache is not cleared; the probe creates no "
            "Ultralytics RAM/disk cache and does not mutate the dataset."
        ),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "python_executable": str(Path(sys.executable).resolve()),
            "storage_profile": args.storage_profile,
        },
        "input": {
            "dataset_dir": str(dataset_dir),
            "dataset_run_id": manifest.get("run_id") or dataset_dir.name,
            "dataset_input_hash": manifest.get("input_hash"),
            "device": args.device,
            "imgsz": args.imgsz,
            "batch": args.batch,
            "advanced_options": advanced,
        },
        "recommendation": recommendation,
    }
    write_json_atomic(args.output.resolve(), report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

