"""Hard-cancellation smoke test for a real GDAL display-overview build.

The source raster is opened read-only. All VRT/OVR and durable-job files live in an
isolated temporary project and are removed at the end of the test.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _wait_until(predicate, timeout_s: float, description: str):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for {description}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Large TIFF without internal overviews")
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve(strict=True)
    if not source.is_file():
        raise FileNotFoundError(source)
    source_before = source.stat()

    with tempfile.TemporaryDirectory(prefix="geotile-p1-7-cancel-") as temp_name:
        root = Path(temp_name)
        os.environ["DATA_DIR"] = str(root / "data")
        os.environ["SCENES_ROOT"] = str(source.parent)
        os.environ["GEOTILE_JOB_IO_LIMIT"] = "1"

        from db.storage import create_project_root, load_scene_json, save_json, save_scene_json
        from models.job import JobCreateRequest, JobType, PriorityClass, ResourceClass
        from services.jobs.scheduler import cancel_job, get_scheduler, submit_job
        from services.jobs.store import read_state
        from services.scene_packages.working_view import cleanup_partial_scene_products

        project_id = "p17cancel"
        scene_id = "scene01"
        project_root = create_project_root(project_id, "P1.7 cancellation")
        save_json(
            project_id,
            "project",
            {
                "id": project_id,
                "name": "P1.7 cancellation",
                "source_type": "local_scenes",
                "scene_folder": str(source.parent),
            },
        )
        save_scene_json(
            project_id,
            scene_id,
            "scene",
            {
                "id": scene_id,
                "filename": source.name,
                "raster_kind": "direct",
                "overview_status": "pending",
            },
        )

        scheduler = get_scheduler()
        submitted = submit_job(
            project_id,
            JobCreateRequest(
                job_type=JobType.SCENE_OVERVIEW,
                resource_class=ResourceClass.IO_HEAVY,
                priority_class=PriorityClass.INTERACTIVE,
                payload={"scene_id": scene_id},
                dedupe_key=f"scene-overview:{project_id}:{scene_id}",
            ),
        )
        job_id = str(submitted["job"]["job_id"])
        derived = project_root / "derived_scenes" / scene_id / "_direct_overview"
        partial_ovr = derived / ".overview.vrt.partial.vrt.ovr"
        final_vrt = derived / "overview.vrt"
        final_ovr = derived / "overview.vrt.ovr"

        try:
            _wait_until(
                lambda: (
                    partial_ovr.is_file()
                    and partial_ovr.stat().st_size > 0
                    and (load_scene_json(project_id, scene_id, "scene", default={}) or {}).get(
                        "overview_status"
                    )
                    == "building"
                ),
                args.timeout,
                "an active GDAL BuildOverviews sidecar",
            )
            state_before_cancel = read_state(project_id, job_id)
            cancelled = cancel_job(project_id, job_id)
            terminal = _wait_until(
                lambda: (
                    state
                    if (state := read_state(project_id, job_id)).get("status") == "cancelled"
                    else None
                ),
                15.0,
                "cancelled durable job state",
            )
            if final_vrt.exists() or final_ovr.exists():
                raise AssertionError("A cancelled overview was published as a final artifact")
            removed_partials = cleanup_partial_scene_products()
            remaining_partials = [
                str(path) for path in project_root.rglob("*") if path.is_file() and ".partial" in path.name
            ]
            if remaining_partials:
                raise AssertionError({"remaining_partials": remaining_partials})
            source_after = source.stat()
            source_unchanged = (
                source_before.st_size == source_after.st_size
                and source_before.st_mtime_ns == source_after.st_mtime_ns
            )
            if not source_unchanged:
                raise AssertionError("Source raster changed during cancellation test")
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "source": str(source),
                        "job_id": job_id,
                        "state_before_cancel": state_before_cancel.get("status"),
                        "cancel_result": cancelled.get("status"),
                        "terminal_status": terminal.get("status"),
                        "partial_cleanup_count": removed_partials,
                        "final_artifact_published": False,
                        "source_unchanged": True,
                    },
                    ensure_ascii=False,
                )
            )
        finally:
            scheduler.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
