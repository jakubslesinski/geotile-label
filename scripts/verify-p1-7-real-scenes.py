"""Read-only P1.7 regression for representative EO/SAR rasters.

The script uses the production scene characterization and thumbnail renderer. It
never creates an overview beside a source and verifies source size/mtime afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _configure_packed_gdal() -> dict[str, str]:
    runtime = Path(sys.executable).resolve().parent
    candidates = {
        "GDAL_DRIVER_PATH": runtime / "Library" / "lib" / "gdalplugins",
        "GDAL_DATA": runtime / "Library" / "share" / "gdal",
        "PROJ_LIB": runtime / "Library" / "share" / "proj",
    }
    configured: dict[str, str] = {}
    for name, path in candidates.items():
        if path.is_dir():
            os.environ.setdefault(name, str(path))
            configured[name] = str(path)
    library_bin = runtime / "Library" / "bin"
    if library_bin.is_dir():
        os.environ["PATH"] = f"{library_bin}{os.pathsep}{runtime}{os.pathsep}{os.environ.get('PATH', '')}"
    return configured


def _json_number(value: Any) -> float | int | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not result == result or result in {float("inf"), float("-inf")}:
        return None
    return int(result) if result.is_integer() else result


def _thumbnail_metrics(path: Path) -> dict[str, Any]:
    import numpy as np
    from PIL import Image

    image = np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)
    flattened = image.reshape(-1, 3)
    p1 = np.percentile(flattened, 1, axis=0)
    p99 = np.percentile(flattened, 99, axis=0)
    return {
        "width": int(image.shape[1]),
        "height": int(image.shape[0]),
        "mean": [round(float(value), 3) for value in flattened.mean(axis=0)],
        "std": [round(float(value), 3) for value in flattened.std(axis=0)],
        "p1": [round(float(value), 3) for value in p1],
        "p99": [round(float(value), 3) for value in p99],
        "black_fraction": round(float(np.all(flattened <= 1, axis=1).mean()), 6),
        "white_fraction": round(float(np.all(flattened >= 254, axis=1).mean()), 6),
        "dynamic_range": round(float(np.max(p99 - p1)), 3),
    }


def _nodata_audit(src: Any) -> list[dict[str, Any]]:
    import numpy as np

    results: list[dict[str, Any]] = []
    for index, (dtype_name, nodata) in enumerate(zip(src.dtypes, src.nodatavals), start=1):
        representable = True
        if nodata is not None:
            dtype = np.dtype(dtype_name)
            if np.issubdtype(dtype, np.integer):
                limits = np.iinfo(dtype)
                representable = limits.min <= float(nodata) <= limits.max
        results.append(
            {
                "band": index,
                "dtype": str(dtype_name),
                "nodata": _json_number(nodata),
                "representable": bool(representable),
            }
        )
    return results


def _verify_case(
    label: str,
    modality: str,
    source: Path,
    output_dir: Path,
) -> dict[str, Any]:
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling

    from services.scene_loader import _generate_geotiff_thumbnail, get_scene_info
    from services.scene_packages.working_view import source_has_internal_overviews

    source = source.resolve(strict=True)
    before = (source.stat().st_size, source.stat().st_mtime_ns)
    started = time.perf_counter()
    info = get_scene_info(source, modality=modality)
    characterization_seconds = time.perf_counter() - started
    info_doc = info.model_dump()
    low_zoom_started = time.perf_counter()
    with rasterio.open(source) as src:
        indexes = [int(value) for value in (info_doc.get("data_band_indexes") or [])]
        if not indexes:
            indexes = list(range(1, min(3, src.count) + 1))
        indexes = indexes[:3]
        sample = src.read(
            indexes=indexes,
            out_shape=(len(indexes), min(512, src.height), min(512, src.width)),
            resampling=Resampling.bilinear,
        )
        color_interpretation = [
            str(getattr(value, "name", value)).split(".")[-1].lower()
            for value in src.colorinterp
        ]
        alpha_indexes = [index for index, value in enumerate(color_interpretation, start=1) if value == "alpha"]
        nodata = _nodata_audit(src)
        driver = str(src.driver)
        overviews = list(src.overviews(indexes[0])) if indexes else []
        dimensions = [int(src.width), int(src.height), int(src.count)]
    low_zoom_seconds = time.perf_counter() - low_zoom_started

    thumbnail = output_dir / f"{label}.png"
    thumbnail_started = time.perf_counter()
    _generate_geotiff_thumbnail(source, thumbnail, scene_info=info)
    thumbnail_seconds = time.perf_counter() - thumbnail_started
    thumbnail_stats = _thumbnail_metrics(thumbnail)
    sample_values = np.asarray(sample, dtype=np.float64)
    finite = sample_values[np.isfinite(sample_values)]
    production_native = bool(source_has_internal_overviews(source))
    after = (source.stat().st_size, source.stat().st_mtime_ns)

    errors: list[str] = []
    warnings: list[str] = []
    layout = str(info_doc.get("spectral_layout") or "unknown")
    display_mode = info_doc.get("display_mode")
    normalized_modality = modality.strip().upper()
    if normalized_modality == "EO":
        if layout == "sar":
            errors.append("EO raster classified as SAR")
        if display_mode in {"sar_db", "uint16_log"}:
            errors.append(f"EO raster received SAR/log display mode: {display_mode}")
    elif normalized_modality == "SAR" and layout != "sar":
        errors.append(f"SAR raster classified as {layout}")
    if set(alpha_indexes) & set(indexes):
        errors.append("Alpha band was selected as display data")
    if any(not item["representable"] for item in nodata):
        errors.append("At least one nodata value is not representable by its band dtype")
    if not finite.size:
        errors.append("Low-zoom read returned no finite pixels")
    if thumbnail_stats["dynamic_range"] < 8.0:
        errors.append("Rendered thumbnail has insufficient dynamic range")
    if thumbnail_stats["black_fraction"] > 0.98:
        errors.append("Rendered thumbnail is almost entirely black")
    if thumbnail_stats["white_fraction"] > 0.98:
        errors.append("Rendered thumbnail is almost entirely white")
    if info_doc.get("native_overviews") != production_native:
        warnings.append("Characterization and production native-overview checks differ")
    if before != after:
        errors.append("Source size or mtime changed during verification")

    return {
        "label": label,
        "modality": normalized_modality,
        "source": str(source),
        "source_bytes": before[0],
        "source_unchanged": before == after,
        "driver": driver,
        "dimensions": dimensions,
        "dtype": info_doc.get("dtype"),
        "spectral_layout": layout,
        "spectral_processing": info_doc.get("spectral_processing"),
        "classification_source": info_doc.get("classification_source"),
        "classification_confidence": info_doc.get("classification_confidence"),
        "display_mode": display_mode,
        "display_min": info_doc.get("display_min"),
        "display_max": info_doc.get("display_max"),
        "display_stats": info_doc.get("display_stats"),
        "data_band_indexes": indexes,
        "alpha_band_indexes": alpha_indexes,
        "nodata": nodata,
        "native_overviews": overviews,
        "production_overview_status": "native" if production_native else "pending",
        "low_zoom_seconds": round(low_zoom_seconds, 6),
        "low_zoom_min": _json_number(finite.min()) if finite.size else None,
        "low_zoom_max": _json_number(finite.max()) if finite.size else None,
        "characterization_seconds": round(characterization_seconds, 6),
        "thumbnail_seconds": round(thumbnail_seconds, 6),
        "thumbnail": str(thumbnail),
        "thumbnail_metrics": thumbnail_stats,
        "warnings": warnings,
        "errors": errors,
        "status": "passed" if not errors else "failed",
    }


def _audit_raster_metadata(source: Path) -> dict[str, Any]:
    from osgeo import gdal
    import rasterio

    gdal.UseExceptions()
    source = source.resolve(strict=True)
    before = (source.stat().st_size, source.stat().st_mtime_ns)
    started = time.perf_counter()
    try:
        with rasterio.open(source) as src:
            nodata = _nodata_audit(src)
            overviews = list(src.overviews(1)) if src.count else []
            result = {
                "source": str(source),
                "source_bytes": before[0],
                "driver": str(src.driver),
                "dimensions": [int(src.width), int(src.height), int(src.count)],
                "dtypes": [str(value) for value in src.dtypes],
                "nodata": nodata,
                "native_overviews": overviews,
                "invalid_nodata": any(not item["representable"] for item in nodata),
                "status": "passed",
                "error": None,
            }
        vrt_messages: list[dict[str, Any]] = []

        def capture_gdal_message(error_class: int, error_number: int, message: str) -> None:
            vrt_messages.append(
                {
                    "class": int(error_class),
                    "number": int(error_number),
                    "message": str(message),
                }
            )

        virtual_path = f"/vsimem/geotile-p1-7-audit-{os.getpid()}.vrt"
        gdal.PushErrorHandler(capture_gdal_message)
        try:
            dataset = gdal.BuildVRT(virtual_path, [str(source)])
            if dataset is None:
                raise RuntimeError("GDAL BuildVRT returned no dataset")
            dataset = None
        finally:
            gdal.PopErrorHandler()
            gdal.Unlink(virtual_path)
        result["vrt_messages"] = vrt_messages
    except Exception as exc:
        result = {
            "source": str(source),
            "source_bytes": before[0],
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "invalid_nodata": False,
        }
    after = (source.stat().st_size, source.stat().st_mtime_ns)
    result["source_unchanged"] = before == after
    result["metadata_seconds"] = round(time.perf_counter() - started, 6)
    if before != after:
        result["status"] = "failed"
        result["error"] = "Source size or mtime changed during metadata audit"
    return result


def _audit_roots(roots: list[str]) -> list[dict[str, Any]]:
    extensions = {".tif", ".tiff", ".jp2"}
    paths: set[Path] = set()
    for raw_root in roots:
        root = Path(raw_root).expanduser().resolve(strict=True)
        paths.update(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.casefold() in extensions
        )
    return [_audit_raster_metadata(path) for path in sorted(paths, key=lambda item: str(item).casefold())]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        action="append",
        nargs=3,
        metavar=("LABEL", "MODALITY", "PATH"),
        default=[],
    )
    parser.add_argument("--audit-root", action="append", default=[])
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    if not args.case and not args.audit_root:
        parser.error("at least one --case or --audit-root is required")

    configured = _configure_packed_gdal()
    output_dir = Path(args.output_dir).expanduser().resolve(strict=False)
    output_dir.mkdir(parents=True, exist_ok=True)
    cases: list[dict[str, Any]] = []
    for label, modality, raw_path in args.case:
        try:
            cases.append(_verify_case(label, modality, Path(raw_path), output_dir))
        except Exception as exc:
            cases.append(
                {
                    "label": label,
                    "modality": modality,
                    "source": raw_path,
                    "status": "failed",
                    "errors": [f"{type(exc).__name__}: {exc}"],
                }
            )

    failed = [item for item in cases if item.get("status") != "passed"]
    metadata_audit = _audit_roots(args.audit_root)
    audit_failed = [item for item in metadata_audit if item.get("status") != "passed"]
    invalid_nodata = [item for item in metadata_audit if item.get("invalid_nodata")]
    vrt_warnings = [item for item in metadata_audit if item.get("vrt_messages")]
    overall_status = (
        "failed"
        if failed or audit_failed
        else "warning"
        if invalid_nodata or vrt_warnings
        else "passed"
    )
    report = {
        "schema_name": "geotile_p1_7_real_scene_regression",
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "configured_gdal_environment": configured,
        "case_count": len(cases),
        "passed_count": len(cases) - len(failed),
        "failed_count": len(failed),
        "cases": cases,
        "audit_roots": [str(Path(value).expanduser().resolve(strict=True)) for value in args.audit_root],
        "audit_raster_count": len(metadata_audit),
        "audit_failed_count": len(audit_failed),
        "audit_invalid_nodata_count": len(invalid_nodata),
        "audit_vrt_warning_count": len(vrt_warnings),
        "metadata_audit": metadata_audit,
        "status": overall_status,
    }
    report_path = output_dir / "report.json"
    temporary = report_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, report_path)
    print(report_path)
    return 0 if not failed and not audit_failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
