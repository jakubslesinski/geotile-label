"""Packed-runtime smoke test for adaptive, process-isolated display overviews."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _create_raster(path: Path, value: int) -> None:
    from osgeo import gdal, osr

    gdal.UseExceptions()
    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(
        str(path),
        2048,
        2048,
        1,
        gdal.GDT_UInt16,
        options=["TILED=YES", "COMPRESS=LZW"],
    )
    if dataset is None:
        raise RuntimeError("Could not create P1.7 smoke raster")
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromEPSG(3857)
    dataset.SetProjection(spatial_ref.ExportToWkt())
    dataset.SetGeoTransform([500_000.0, 1.0, 0.0, 5_500_000.0, 0.0, -1.0])
    dataset.GetRasterBand(1).Fill(value)
    dataset = None


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="geotile-p1-7-smoke-") as temp_name:
        root = Path(temp_name)
        data_dir = root / "data"
        scene_root = root / "sources"
        scene_root.mkdir(parents=True)
        os.environ["DATA_DIR"] = str(data_dir)
        os.environ["SCENES_ROOT"] = str(scene_root)
        runtime_root = Path(sys.executable).resolve().parent
        gdal_data = runtime_root / "Library" / "share" / "gdal"
        proj_data = runtime_root / "Library" / "share" / "proj"
        if gdal_data.is_dir():
            os.environ.setdefault("GDAL_DATA", str(gdal_data))
        if proj_data.is_dir():
            os.environ.setdefault("PROJ_LIB", str(proj_data))

        # Storage constants must observe the isolated environment before their first import.
        from db.storage import create_project_root, load_scene_json, save_json, save_scene_json
        from routers.scene_import import _run_adaptive_overviews
        from services.scene_packages.working_view import (
            DIRECT_OVERVIEW_PROFILE_NAME,
            direct_overview_vrt,
        )

        project_id = "p17smoke"
        create_project_root(project_id, "P1.7 smoke")
        save_json(
            project_id,
            "project",
            {
                "id": project_id,
                "name": "P1.7 smoke",
                "source_type": "local_scenes",
                "scene_folder": str(scene_root),
            },
        )
        pending: list[tuple[str, str]] = []
        source_stats: dict[str, tuple[int, int]] = {}
        for index in range(2):
            scene_id = f"scene{index + 1}"
            filename = f"scene{index + 1}.tif"
            source = scene_root / filename
            _create_raster(source, 1000 + index)
            stat = source.stat()
            source_stats[scene_id] = (stat.st_size, stat.st_mtime_ns)
            save_scene_json(
                project_id,
                scene_id,
                "scene",
                {"id": scene_id, "filename": filename, "raster_kind": "direct"},
            )
            pending.append((scene_id, filename))

        started: list[str] = []
        finished: list[str] = []
        profile = {
            "workers": 2,
            "compression": "ZSTD",
            "predictor": "auto",
            "gdal_threads": "1",
            "resampling": "AVERAGE",
        }
        results = _run_adaptive_overviews(
            project_id,
            pending,
            profile=profile,
            cancel_check=lambda: False,
            on_started=lambda scene_id, _name: started.append(scene_id),
            on_finished=lambda scene_id, _name, _result: finished.append(scene_id),
        )

        for scene_id, _filename in pending:
            vrt = direct_overview_vrt(project_id, scene_id, None)
            if vrt is None:
                raise AssertionError(f"Missing overview for {scene_id}")
            profile_doc = json.loads((vrt.parent / DIRECT_OVERVIEW_PROFILE_NAME).read_text(encoding="utf-8"))
            if profile_doc.get("compression") not in {"ZSTD", "DEFLATE"}:
                raise AssertionError(profile_doc)
            status = load_scene_json(project_id, scene_id, "scene", default={}).get("overview_status")
            if status != "ready":
                raise AssertionError(f"Unexpected overview status for {scene_id}: {status}")
            source = scene_root / f"{scene_id}.tif"
            stat = source.stat()
            if source_stats[scene_id] != (stat.st_size, stat.st_mtime_ns):
                raise AssertionError(f"Source raster changed: {source}")

        if sorted(started) != ["scene1", "scene2"] or sorted(finished) != ["scene1", "scene2"]:
            raise AssertionError({"started": started, "finished": finished})
        if any(item.get("status") != "ready" for item in results):
            raise AssertionError(results)
        print(json.dumps({"status": "ok", "results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
