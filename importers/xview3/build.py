"""Złożenie kanonicznego projektu GeoTile z xView3 — sterowanie writerami backendu.

xView3: SAR Sentinel-1 (UTM 10 m), rastry **Float16** (rasterio: KeyError 15). Rozwiązanie:
per scena tworzymy lekki **VRT rzutujący VV_dB Float16→Float32** (bez kopiowania danych) i to
VRT jest rastrem roboczym sceny (app czyta Float32). Tożsamość (`source_scene_uid`) liczymy
z ORYGINALNEGO VV_dB.tif (prowenansja wskazuje źródło, nie VRT).

Etykiety są punktowe (row/col, część z HBB) → geometry_type="bbox"; atrybuty (lat/lon,
distance_from_shore, is_vessel…) w rekordzie pod v11/v12.

Wymaga środowiska backendu (rasterio/GDAL + osgeo). Import backendu leniwy; env wcześniej.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

from xview3.parse import (
    build_classes,
    classify,
    load_detections_by_scene,
    to_canonical,
)


def _scene_id(xview3_scene_id: str) -> str:
    return hashlib.sha256(f"xview3:{xview3_scene_id}".encode("utf-8")).hexdigest()[:12]


def _materialize_uint8_tif(src_vv: Path, dst_tif: Path) -> None:
    """Float16 dB → uint8 GeoTIFF (globalny stretch percentyl 2-98 dB, DEFLATE).

    Build datasetu (preprocessing_profiles.py) kieruje do rasterio TYLKO `.tif/.tiff` —
    `.vrt` spada do PIL („cannot identify image file"). Dlatego raster roboczy musi być
    realnym `.tif`. uint8 jest też lekki i czytelny wszędzie. Percentyl liczony z
    tymczasowego VRT (Float32), bo rasterio nie mapuje Float16 (KeyError: 15).
    Tożsamość liczona osobno z oryginalnego VV_dB (nie z tego pliku).
    """
    import numpy as np  # noqa: E402
    import rasterio  # noqa: E402
    from osgeo import gdal  # noqa: E402
    gdal.UseExceptions()
    dst_tif.parent.mkdir(parents=True, exist_ok=True)
    tmp_vrt = dst_tif.with_suffix(".f32.vrt")
    gdal.Translate(str(tmp_vrt), str(src_vv.resolve()), format="VRT", outputType=gdal.GDT_Float32)
    try:
        with rasterio.open(tmp_vrt) as ds:
            arr = ds.read(1, out_shape=(2048, 2048), masked=True)
        v = np.asarray(arr, dtype=np.float32).ravel()
        v = v[np.isfinite(v)]
        v = v[v > -30000.0]  # odrzuć nodata (-32768)
        if v.size < 100:
            p2, p98 = -30.0, 5.0
        else:
            p2, p98 = (float(x) for x in np.percentile(v, [2, 98]))
        if not (p98 > p2):
            p98 = p2 + 1.0
        gdal.Translate(
            str(dst_tif), str(tmp_vrt), outputType=gdal.GDT_Byte, bandList=[1],
            scaleParams=[[p2, p98, 0, 255]],
            creationOptions=["COMPRESS=DEFLATE", "TILED=YES", "BLOCKXSIZE=512", "BLOCKYSIZE=512", "PREDICTOR=2"],
        )
    finally:
        tmp_vrt.unlink(missing_ok=True)


def build_project(
    xview3_root: str | Path,
    project_location: str | Path,
    *,
    name: str = "xView3",
    splits: list[str] | None = None,
    limit: int | None = None,
    author_email: str | None = None,
    backend_path: str | Path,
) -> dict[str, Any]:
    xview3_root = Path(xview3_root)
    splits = splits or ["train", "validation"]

    sys.path.insert(0, str(backend_path))
    from db.storage import (  # noqa: E402
        create_project_root,
        load_json,
        load_scene_json,
        project_dir,
        save_json,
        save_scene_json,
    )
    from models.project import Project, default_project_profile  # noqa: E402
    from models.scene import Scene  # noqa: E402
    from services.preprocessing_profiles import ensure_preprocessing_profiles  # noqa: E402
    from services.scene_loader import get_scene_info  # noqa: E402
    from services.scene_manifest import rebuild_scenes_index, write_scene_manifest  # noqa: E402

    classes, class_lookup = build_classes()

    # SAR + GEO + etykiety AABB → bbox. Profil: sar_linear_percentile (percentyl BEZ log1p) —
    # xView3 to dB (ujemne); sar_log_percentile robi log1p(clip(x,0)) → wyzerowałoby dB.
    profile = default_project_profile(
        modality="SAR",
        georeferencing="GEO",
        sensors=["Sentinel-1"],
        annotation_mode="bbox",
    )
    profile.default_preprocessing_profile = "sar_linear_percentile"
    if author_email:
        profile.labeling_author_email = author_email

    project_id = hashlib.sha256(f"xview3-project:{name}".encode()).hexdigest()[:12]
    root = create_project_root(project_id, name, str(project_location))
    project = Project(
        id=project_id,
        name=name,
        scene_folder=str(xview3_root / "scenes"),
        project_root=str(root),
        created_in_appdata=False,
        profile=profile,
        source_type="local_scenes",
    )
    save_json(project_id, "project", project.model_dump())
    save_json(project_id, "classes", classes)
    save_json(project_id, "tiling_config", {"tile_size": 1024, "buffer": 200})
    save_json(project_id, "dataset_config", {
        "train_ratio": 0.7, "val_ratio": 0.2, "test_ratio": 0.1,
        "min_box_fraction": 0.3, "negative_ratio": 0.1,
        "split_mode": profile.default_split_strategy, "split_seed": 42,
        "block_size_tiles": 5,
        "preprocessing_profile_id": profile.default_preprocessing_profile,
    })
    ensure_preprocessing_profiles(project_id)
    project_data = load_json(project_id, "project", default={})
    proot = project_dir(project_id)

    stats = {
        "scenes": 0, "annotations": 0, "missing_vv": 0,
        "no_detections": 0, "skipped_no_geom": 0,
    }
    split_map: dict[str, dict[str, str]] = {}

    for split in splits:
        scenes_dir = xview3_root / "scenes" / split
        csv_path = xview3_root / "labels" / f"{split}.csv"
        if not scenes_dir.is_dir():
            continue
        detections = load_detections_by_scene(csv_path) if csv_path.is_file() else {}
        scene_dirs = sorted(p for p in scenes_dir.iterdir() if p.is_dir())
        if limit is not None:
            scene_dirs = scene_dirs[:limit]

        for sdir in scene_dirs:
            xv_id = sdir.name
            src_vv = sdir / "VV_dB.tif"
            if not src_vv.is_file():
                stats["missing_vv"] += 1
                continue
            sid = _scene_id(xv_id)

            # uint8 GeoTIFF w storage projektu (raster roboczy = .tif, czytelny w buildzie)
            u8_rel = f"derived_scenes/{sid}/vv_u8.tif"
            _materialize_uint8_tif(src_vv, proot / u8_rel)
            info = get_scene_info(proot / u8_rel)

            scene = Scene(
                id=sid,
                filename=f"{split}/{xv_id}/VV_dB.tif",
                display_name=xv_id,
                modality="SAR",
                georeferencing="GEO" if info.has_geo else "NO_GEO",
                sensor="Sentinel-1",
                provider="xView3",
                raster_kind="direct",
                preparation_status="ready",
                status="pending",
                scene_info=info,
            ).model_dump()
            # raster_ref MUSI żyć w manifest.working_view — write_scene_manifest wyprowadza
            # scene.raster_ref z working_view (scene_summary_from_manifest), zerując wartość
            # ustawioną wprost na Scene. Bez tego resolver spada do surowego VV_dB (Float16).
            scene["working_view"] = {
                "variant_id": "xview3_vv_u8",
                "raster_kind": "direct",
                "raster_ref": {"storage": "project", "relative_path": u8_rel},
                "preparation_status": "ready",
                "working_grid_uid": None,
                "processing_manifest": None,
                "locked": False,
            }
            save_scene_json(project_id, sid, "scene", scene)
            # scene_path = ŹRÓDŁO VV_dB → source_scene_uid liczony z oryginału (nie z VRT)
            write_scene_manifest(project_id, sid, project_data, scene, src_vv)

            rows = detections.get(xv_id, [])
            if not rows:
                stats["no_detections"] += 1
            annotations: list[dict[str, Any]] = []
            for i, row in enumerate(rows):
                class_id = class_lookup[classify(row.get("is_vessel"), row.get("is_fishing"))]
                rec = to_canonical(row, scene_id=sid, class_id=class_id, index=i)
                if rec is None:
                    stats["skipped_no_geom"] += 1
                    continue
                annotations.append(rec)
            save_scene_json(project_id, sid, "annotations", annotations)

            scene_now = load_scene_json(project_id, sid, "scene", default={})
            scene_now["annotation_count"] = len(annotations)
            save_scene_json(project_id, sid, "scene", scene_now)

            split_map[sid] = {"split": split, "xview3_scene_id": xv_id}
            stats["scenes"] += 1
            stats["annotations"] += len(annotations)

    save_json(project_id, "benchmark_import", {
        "benchmark": "xView3", "splits": splits,
        "raster": "VV_dB", "dtype_conversion": "float16 dB -> uint8 GeoTIFF (percentyl 2-98%)",
        "preprocessing_profile": "sar_linear_percentile",
        "aux_bands_available": ["VH_dB", "bathymetry", "owiMask", "owiWindSpeed", "owiWindDirection", "owiWindQuality"],
        "scene_split": split_map,
    })
    rebuild_scenes_index(project_id)
    project_data = load_json(project_id, "project", default={})
    project_data["scene_count"] = stats["scenes"]
    save_json(project_id, "project", project_data)

    return {
        "project_id": project_id,
        "project_root": str(root),
        "class_count": len(classes),
        **stats,
    }
