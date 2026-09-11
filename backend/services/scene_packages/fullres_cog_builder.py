"""Budowa pelnorozdzielczego COG z jednokaflowego JP2 (R1.3, wykonanie).

Spina trzy juz przetestowane czesci: polityke z `fullres_build_policy`, watchdog
z `process_watchdog` i maszyne stanow z `fullres_publication`. Sam nie podejmuje decyzji —
tutaj jest tylko wykonanie.

Dlaczego dekod idzie poza GDAL
------------------------------
Zrodlo ma jeden kafel codestreamu na caly obraz, wiec GDAL czyta pelna rozdzielczosc
blokami przez `opj_set_decode_area`, a kazde wywolanie parsuje naglowki pakietow od
poczatku kafla. Zmierzone w E4: ~2,26 s na blok, czyli **~97 min** na scene. Ten sam
material przez `opj_decompress` w pasach zajmuje **~19 min**.

Plik surowy lezy OBOK VRT-a i jest adresowany nazwa, bo GDAL od 3.9 odrzuca
`VRTRawRasterBand` wskazujacy plik spoza katalogu VRT-a bez
`GDAL_VRT_RAWRASTERBAND_ALLOWED_SOURCE`. Rozluznianie tego ustawienia globalnie byloby
zdejmowaniem zabezpieczenia dla wygody jednego przypadku.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from xml.sax.saxutils import escape

from services.scene_packages.fullres_build_policy import (
    MEMORY_HARD_ABORT_BYTES,
    InsufficientDiskError,
    InsufficientMemoryError,
    StripPlanner,
    check_disk,
    check_host_memory,
    estimate_disk_requirement,
)
from services.scene_packages.fullres_derivative import COG_PROFILE, COG_PROFILE_VERSION
from services.scene_packages.process_watchdog import (
    ABORT_CANCELLED,
    ABORT_MEMORY,
    MemoryWatchdog,
    sample_process_tree_rss,
)

FULLRES_DIR_NAME = "fullres"
FULLRES_COG_NAME = "fullres.tif"
FULLRES_CANDIDATE_NAME = "fullres.candidate.tif"
FULLRES_STATE_NAME = "fullres.state.json"
_RAW_NAME = "fullres.raw"
_VRT_NAME = "fullres.vrt"


class BuildCancelled(RuntimeError):
    pass


class BuildAborted(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str = "build_aborted",
        peak_rss_bytes: int = 0,
        y0: int | None = None,
        y1: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.peak_rss_bytes = int(peak_rss_bytes)
        self.y0 = y0
        self.y1 = y1
    """Przerwane przez watchdoga — najczesciej limit pamieci."""


@dataclass
class BuildResult:
    cog_path: Path
    raw_path: Path
    vrt_path: Path
    seconds: float
    peak_rss_bytes: int
    strips: list[dict[str, Any]] = field(default_factory=list)
    decode_mode: str = "strips"
    profile_version: int = COG_PROFILE_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "cog_path": str(self.cog_path),
            "raw_path": str(self.raw_path),
            "vrt_path": str(self.vrt_path),
            "seconds": round(self.seconds, 2),
            "peak_rss_bytes": self.peak_rss_bytes,
            "strip_count": len(self.strips),
            "strips": self.strips,
            "profile_version": self.profile_version,
            "decode_mode": self.decode_mode,
            "cog_bytes": self.cog_path.stat().st_size if self.cog_path.exists() else 0,
        }


def fullres_dir(project_dir_path: Path, scene_id: str, variant_id: str | None) -> Path:
    return project_dir_path / "derived_scenes" / scene_id / (variant_id or "_default") / FULLRES_DIR_NAME


def fullres_cog_path(project_dir_path: Path, scene_id: str, variant_id: str | None) -> Path:
    return fullres_dir(project_dir_path, scene_id, variant_id) / FULLRES_COG_NAME


def fullres_state_path(project_dir_path: Path, scene_id: str, variant_id: str | None) -> Path:
    return fullres_dir(project_dir_path, scene_id, variant_id) / FULLRES_STATE_NAME


def published_fullres_cog(
    project_dir_path: Path,
    scene_id: str,
    variant_id: str | None,
    *,
    source_fingerprint: str | None = None,
) -> Path | None:
    """Sciezka gotowego derywatu albo None. Tania — sam `is_file`."""
    from services.scene_packages.fullres_publication import (
        STATE_ACTIVE,
        read_publication_record,
    )

    path = fullres_cog_path(project_dir_path, scene_id, variant_id)
    record = read_publication_record(fullres_state_path(project_dir_path, scene_id, variant_id))
    if record is None or record.state != STATE_ACTIVE or not path.is_file():
        return None
    payload = record.payload
    if int(payload.get("cog_profile_version") or 0) != COG_PROFILE_VERSION:
        return None
    if payload.get("variant_id") != variant_id:
        return None
    if source_fingerprint is None or payload.get("source_fingerprint") != source_fingerprint:
        return None
    return path


def cleanup_legacy_fullres(
    project_dir_path: Path,
    scene_id: str,
    variant_id: str | None,
) -> list[str]:
    """Lazily remove v1/interrupted products, preserving source and preview OVR."""
    from services.scene_packages.fullres_publication import read_publication_record

    directory = fullres_dir(project_dir_path, scene_id, variant_id)
    state_path = fullres_state_path(project_dir_path, scene_id, variant_id)
    record = read_publication_record(state_path)
    if record is not None and int(record.payload.get("cog_profile_version") or 0) >= 2:
        return []
    removable_names = {
        FULLRES_COG_NAME,
        FULLRES_CANDIDATE_NAME,
        _RAW_NAME,
        _VRT_NAME,
        f".{FULLRES_COG_NAME}.partial",
        f".{FULLRES_CANDIDATE_NAME}.partial",
    }
    removed: list[str] = []
    if directory.is_dir():
        for candidate in directory.iterdir():
            if candidate.name in removable_names or candidate.name.startswith(".strip_"):
                try:
                    candidate.unlink(missing_ok=True)
                    removed.append(candidate.name)
                except OSError:
                    pass
    try:
        state_path.unlink(missing_ok=True)
    except OSError:
        pass
    return removed


def cleanup_incomplete_fullres(
    project_dir_path: Path,
    scene_id: str,
    variant_id: str | None,
) -> list[str]:
    """Remove only regenerable v2 build artifacts; keep state/log/plan and active COG."""

    directory = fullres_dir(project_dir_path, scene_id, variant_id)
    names = {
        FULLRES_CANDIDATE_NAME,
        _RAW_NAME,
        _VRT_NAME,
        f".{FULLRES_CANDIDATE_NAME}.partial",
    }
    removed: list[str] = []
    if not directory.is_dir():
        return removed
    for candidate in directory.iterdir():
        if candidate.name in names or candidate.name.startswith(".strip_"):
            try:
                candidate.unlink(missing_ok=True)
                removed.append(candidate.name)
            except OSError:
                pass
    return removed


def _opj_decompress() -> Path:
    import sys

    candidate = Path(sys.executable).parent / "Library" / "bin" / "opj_decompress.exe"
    if candidate.is_file():
        return candidate
    found = shutil.which("opj_decompress")
    if found:
        return Path(found)
    raise FileNotFoundError("Nie znaleziono opj_decompress — budowa derywatu niedostepna")


def _raw_vrt_xml(
    *, raw_name: str, width: int, height: int, band_count: int, dtype: str,
    itemsize: int, crs_wkt: str | None, geotransform: list[float],
    color_interpretation: list[str] | None = None,
    nodata_values: list[float | int | None] | None = None,
) -> str:
    plane = width * height * itemsize
    bands = []
    color_names = {
        "gray": "Gray", "grey": "Gray", "alpha": "Alpha", "red": "Red",
        "green": "Green", "blue": "Blue", "undefined": "Undefined",
    }
    for index in range(band_count):
        color = ""
        if color_interpretation and index < len(color_interpretation):
            normalized = str(color_interpretation[index] or "").lower()
            value = color_names.get(normalized)
            if value:
                color = f"\n    <ColorInterp>{value}</ColorInterp>"
        nodata = ""
        if nodata_values and index < len(nodata_values) and nodata_values[index] is not None:
            nodata = f"\n    <NoDataValue>{nodata_values[index]}</NoDataValue>"
        bands.append(
            f"""  <VRTRasterBand dataType="{dtype}" band="{index + 1}" subClass="VRTRawRasterBand">
    <SourceFilename relativeToVRT="1">{raw_name}</SourceFilename>
    <ImageOffset>{index * plane}</ImageOffset>
    <PixelOffset>{itemsize}</PixelOffset>
    <LineOffset>{width * itemsize}</LineOffset>
    <ByteOrder>LSB</ByteOrder>{color}{nodata}
  </VRTRasterBand>"""
        )
    srs = f"  <SRS>{escape(crs_wkt)}</SRS>\n" if crs_wkt else ""
    geo = "  <GeoTransform>" + ", ".join(f"{value!r}" for value in geotransform) + "</GeoTransform>\n"
    return (
        f'<VRTDataset rasterXSize="{width}" rasterYSize="{height}">\n'
        + srs + geo + "\n".join(bands) + "\n</VRTDataset>\n"
    )


def _gdal_geotransform(rasterio_transform: list[float]) -> list[float]:
    """rasterio (a,b,c,d,e,f) -> GDAL (c,a,b,f,d,e).

    Obie sa szescioelementowymi listami tych samych liczb, wiec pomylka nie wywala sie
    glosno — daje raster z bezbledna trescia i rozjechana georeferencja. E4 zlapalo to
    dopiero bramka porownujaca transformacje, nie piksele.
    """
    a, b, c, d, e, f = rasterio_transform[:6]
    return [c, a, b, f, d, e]


def _decode_strip(
    source: Path, target: Path, *, width: int, y0: int, y1: int, log,
    cancel_check: Callable[[], bool] | None,
    memory_limit_bytes: int = MEMORY_HARD_ABORT_BYTES,
) -> dict[str, Any]:
    """Jeden pas przez `opj_decompress`, pilnowany watchdogiem procesu potomnego."""
    import psutil

    command = [
        str(_opj_decompress()), "-i", str(source), "-o", str(target),
        "-OutFor", "RAWL", "-d", f"0,{y0},{width},{y1}",
    ]
    started = time.perf_counter()
    creationflags = 0
    if os.name == "nt":
        creationflags = int(getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
        creationflags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    process = subprocess.Popen(
        command,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    probe = psutil.Process(process.pid)

    watchdog = MemoryWatchdog(
        limit_bytes=memory_limit_bytes,
        sample_rss=lambda: sample_process_tree_rss(probe),
        terminate=process.kill,
        cancel_check=cancel_check,
    )
    with watchdog:
        returncode = process.wait()
    elapsed = time.perf_counter() - started

    if watchdog.result.aborted:
        if watchdog.result.reason == ABORT_CANCELLED:
            raise BuildCancelled("Budowa derywatu anulowana")
        raise BuildAborted(
            f"Przerwano pas {y0}-{y1}: {watchdog.result.reason}, "
            f"szczyt {watchdog.result.peak_rss_bytes / 1024 ** 3:.2f} GiB",
            error_code=str(watchdog.result.reason),
            peak_rss_bytes=watchdog.result.peak_rss_bytes,
            y0=y0,
            y1=y1,
        )
    if returncode != 0:
        raise RuntimeError(f"opj_decompress zwrocil {returncode} dla pasa {y0}-{y1}")
    return {
        "y0": y0,
        "y1": y1,
        "rows": y1 - y0,
        "seconds": round(elapsed, 2),
        "peak_rss_bytes": watchdog.result.peak_rss_bytes,
        "bytes": target.stat().st_size if target.exists() else 0,
    }


def _copy_strip_into_bsq(
    raw_handle,
    piece: Path,
    *,
    width: int,
    height: int,
    band_count: int,
    itemsize: int,
    y0: int,
    y1: int,
) -> None:
    """Place strip-BSQ bytes in full-image BSQ planes without interleaving bands."""

    rows = y1 - y0
    row_bytes = width * itemsize
    band_piece_bytes = rows * row_bytes
    expected = band_piece_bytes * band_count
    actual = piece.stat().st_size
    if actual != expected:
        raise RuntimeError(
            f"decoded_strip_size_mismatch: expected={expected}, actual={actual}, "
            f"range={y0}-{y1}, bands={band_count}"
        )
    full_plane_bytes = width * height * itemsize
    with piece.open("rb") as source_handle:
        for band in range(band_count):
            raw_handle.seek(band * full_plane_bytes + y0 * row_bytes)
            remaining = band_piece_bytes
            while remaining:
                block = source_handle.read(min(32 * 1024 * 1024, remaining))
                if not block:
                    raise RuntimeError("decoded_strip_ended_early")
                raw_handle.write(block)
                remaining -= len(block)


def _whole_decode_is_safe(
    *,
    installed_bytes: int,
    available_bytes: int,
    band_count: int,
    scene_profile: dict[str, Any],
) -> tuple[bool, int]:
    """Conservative R4 gate; two-band input remains strip-only until benchmarked."""

    gib = 1024 ** 3
    if band_count != 1 or available_bytes < 24 * gib:
        return False, 0
    measured_peak = int(scene_profile.get("whole_decode_measured_peak_bytes") or 12.65 * gib)
    child_limit = min(16 * gib, max(0, available_bytes - 8 * gib))
    safe = (
        available_bytes - 8 * gib >= int(measured_peak * 1.5)
        and child_limit >= int(measured_peak * 1.10)
        and installed_bytes >= 24 * gib
    )
    return safe, child_limit


def cleanup_build_intermediates(result: BuildResult, *, remove_candidate: bool = False) -> None:
    """Remove regenerable heavy files while retaining logs and diagnostic reports."""

    result.raw_path.unlink(missing_ok=True)
    result.vrt_path.unlink(missing_ok=True)
    if remove_candidate:
        result.cog_path.unlink(missing_ok=True)


def validate_fullres_candidate(
    result: BuildResult,
    *,
    scene_profile: dict[str, Any],
) -> dict[str, Any]:
    """Validate structure, semantics and pixels against the still-present RAW VRT."""

    from osgeo import gdal

    gdal.UseExceptions()
    candidate = gdal.Open(str(result.cog_path), gdal.GA_ReadOnly)
    raw = gdal.Open(str(result.vrt_path), gdal.GA_ReadOnly)
    if candidate is None or raw is None:
        raise RuntimeError("validation_failed: candidate_or_raw_vrt_unreadable")
    width = int(scene_profile["width"])
    height = int(scene_profile["height"])
    bands = int(scene_profile.get("band_count") or 1)
    if (candidate.RasterXSize, candidate.RasterYSize, candidate.RasterCount) != (
        width, height, bands
    ):
        raise RuntimeError("validation_failed: dimensions_or_band_count")
    if candidate.GetDriver().ShortName != "GTiff":
        raise RuntimeError("validation_failed: driver_is_not_gtiff")
    if candidate.GetMetadataItem("LAYOUT", "IMAGE_STRUCTURE") != "COG":
        raise RuntimeError("validation_failed: invalid_cog_layout")
    if tuple(round(v, 12) for v in candidate.GetGeoTransform()) != tuple(
        round(v, 12) for v in _gdal_geotransform(list(scene_profile["transform"]))
    ):
        raise RuntimeError("validation_failed: geotransform")
    if (candidate.GetProjectionRef() or "") != (raw.GetProjectionRef() or ""):
        raise RuntimeError("validation_failed: projection")

    boundaries = sorted(
        {int(item["y0"]) for item in result.strips} | {int(item["y1"]) for item in result.strips}
    )
    probes = {0, max(0, height - 8), max(0, height // 2 - 4)}
    for boundary in boundaries:
        probes.add(max(0, min(height - 1, boundary - 4)))
        probes.add(max(0, min(height - 1, boundary)))
    x_offsets = sorted({0, max(0, width // 2 - 32), max(0, width - 64)})
    expected_colors = [str(v or "").lower() for v in scene_profile.get("color_interpretation") or []]
    expected_nodata = list(scene_profile.get("nodata_values") or [])
    for band_index in range(1, bands + 1):
        cb = candidate.GetRasterBand(band_index)
        rb = raw.GetRasterBand(band_index)
        if cb.DataType != rb.DataType:
            raise RuntimeError(f"validation_failed: dtype_band_{band_index}")
        if cb.GetOverviewCount() <= 0:
            raise RuntimeError(f"validation_failed: missing_overviews_band_{band_index}")
        if expected_colors and band_index <= len(expected_colors):
            actual_color = gdal.GetColorInterpretationName(cb.GetColorInterpretation()).lower()
            if actual_color != expected_colors[band_index - 1]:
                raise RuntimeError(f"validation_failed: color_interp_band_{band_index}")
        if band_index <= len(expected_nodata):
            wanted_nodata = expected_nodata[band_index - 1]
            actual_nodata = cb.GetNoDataValue()
            if wanted_nodata is None and actual_nodata is not None:
                raise RuntimeError(f"validation_failed: nodata_band_{band_index}")
            if wanted_nodata is not None and actual_nodata != wanted_nodata:
                raise RuntimeError(f"validation_failed: nodata_band_{band_index}")
        for y0 in sorted(probes):
            rows = min(8, height - y0)
            if rows <= 0:
                continue
            for x0 in x_offsets:
                cols = min(64, width - x0)
                expected = rb.ReadRaster(x0, y0, cols, rows)
                actual = cb.ReadRaster(x0, y0, cols, rows)
                if expected != actual:
                    raise RuntimeError(
                        f"validation_failed: pixels_band_{band_index}_{x0}_{y0}"
                    )
    if "alpha" in expected_colors and bands > 1:
        data_band = candidate.GetRasterBand(1)
        if not (data_band.GetMaskFlags() & gdal.GMF_ALPHA):
            raise RuntimeError("validation_failed: alpha_mask_semantics")
    candidate = None
    raw = None
    return {
        "status": "valid",
        "bands": bands,
        "probe_rows": sorted(probes),
        "strip_boundaries": boundaries,
    }


def build_fullres_cog(
    *,
    source: Path,
    target_dir: Path,
    scene_profile: dict[str, Any],
    source_bytes: int,
    cancel_check: Callable[[], bool] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> BuildResult:
    """Build a validated-later v2 COG candidate without publishing it."""

    from osgeo import gdal
    import psutil

    gdal.UseExceptions()
    width = int(scene_profile["width"])
    height = int(scene_profile["height"])
    band_count = int(scene_profile.get("band_count") or 1)
    itemsize = int(scene_profile.get("itemsize") or 2)
    row_bytes = width * itemsize
    total_raw_bytes = row_bytes * height * band_count

    memory = psutil.virtual_memory()
    verdict = check_host_memory(memory.total, memory.available)
    if not verdict.ok:
        raise InsufficientMemoryError(
            "insufficient_memory: " + json.dumps(verdict.as_dict())
        )
    target_dir.mkdir(parents=True, exist_ok=True)
    requirement = estimate_disk_requirement(
        width=width,
        height=height,
        band_count=band_count,
        itemsize=itemsize,
        source_bytes=source_bytes,
    )
    if not check_disk(requirement, shutil.disk_usage(target_dir).free):
        raise InsufficientDiskError(
            "insufficient_disk: " + json.dumps(requirement.as_dict())
        )

    raw_path = target_dir / _RAW_NAME
    vrt_path = target_dir / _VRT_NAME
    candidate = target_dir / FULLRES_CANDIDATE_NAME
    partial = candidate.with_name(f".{candidate.name}.partial")
    plan_path = target_dir / "strip-plan.json"
    error_path = target_dir / "build-error.json"
    for stale in (raw_path, vrt_path, candidate, partial):
        stale.unlink(missing_ok=True)
    for piece in target_dir.glob(".strip_*.raw"):
        piece.unlink(missing_ok=True)

    planner = StripPlanner(
        width=width,
        height=height,
        band_count=band_count,
        itemsize=itemsize,
        target_bytes=verdict.target_bytes,
        hard_limit_bytes=verdict.child_limit_bytes,
    )
    strips: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    peak_rss = 0
    started = time.perf_counter()
    decode_mode = "strips"

    try:
        # Preallocation makes every plane position deterministic and avoids sparse
        # accidental append layouts for multiband strip output.
        with raw_path.open("w+b") as raw_handle:
            raw_handle.truncate(total_raw_bytes)
            with (target_dir / "decode.log").open(
                "w", encoding="utf-8", errors="replace"
            ) as log:
                whole_safe, whole_limit = _whole_decode_is_safe(
                    installed_bytes=memory.total,
                    available_bytes=memory.available,
                    band_count=band_count,
                    scene_profile=scene_profile,
                )
                if whole_safe:
                    piece = target_dir / ".strip_00000000_whole.raw"
                    try:
                        observation = _decode_strip(
                            source,
                            piece,
                            width=width,
                            y0=0,
                            y1=height,
                            log=log,
                            cancel_check=cancel_check,
                            memory_limit_bytes=whole_limit,
                        )
                        _copy_strip_into_bsq(
                            raw_handle,
                            piece,
                            width=width,
                            height=height,
                            band_count=band_count,
                            itemsize=itemsize,
                            y0=0,
                            y1=height,
                        )
                        piece.unlink(missing_ok=True)
                        strips.append(observation)
                        peak_rss = observation["peak_rss_bytes"]
                        decode_mode = "whole_decode"
                    except BuildAborted as exc:
                        piece.unlink(missing_ok=True)
                        if exc.error_code != ABORT_MEMORY:
                            raise
                        attempts.append(
                            {
                                "mode": "whole_decode",
                                "status": "fallback_to_strips",
                                "peak_rss_bytes": exc.peak_rss_bytes,
                            }
                        )
                        decode_mode = "strips_after_whole_fallback"

                if not strips:
                    y0 = 0
                    while y0 < height:
                        if cancel_check and cancel_check():
                            raise BuildCancelled("Budowa derywatu anulowana")
                        rows = min(planner.next_rows(), height - y0)
                        y1 = y0 + rows
                        piece = target_dir / f".strip_{y0:08d}_{y1:08d}.raw"
                        try:
                            observation = _decode_strip(
                                source,
                                piece,
                                width=width,
                                y0=y0,
                                y1=y1,
                                log=log,
                                cancel_check=cancel_check,
                                memory_limit_bytes=verdict.child_limit_bytes,
                            )
                        except BuildAborted as exc:
                            piece.unlink(missing_ok=True)
                            peak_rss = max(peak_rss, exc.peak_rss_bytes)
                            attempts.append(
                                {
                                    "y0": y0,
                                    "y1": y1,
                                    "rows": rows,
                                    "status": "memory_retry",
                                    "peak_rss_bytes": exc.peak_rss_bytes,
                                }
                            )
                            if exc.error_code != ABORT_MEMORY:
                                raise
                            if rows <= 1:
                                raise BuildAborted(
                                    "Minimal strip still exceeds the memory limit",
                                    error_code=ABORT_MEMORY,
                                    peak_rss_bytes=exc.peak_rss_bytes,
                                    y0=y0,
                                    y1=y1,
                                ) from exc
                            planner.retry_rows(rows)
                            continue

                        peak_rss = max(peak_rss, observation["peak_rss_bytes"])
                        _copy_strip_into_bsq(
                            raw_handle,
                            piece,
                            width=width,
                            height=height,
                            band_count=band_count,
                            itemsize=itemsize,
                            y0=y0,
                            y1=y1,
                        )
                        piece.unlink(missing_ok=True)
                        strips.append(observation)
                        attempts.append({**observation, "status": "completed"})
                        planner.observe(rows, observation["peak_rss_bytes"])
                        y0 = y1
                        if progress is not None:
                            progress(
                                {
                                    "rows_done": y0,
                                    "rows_total": height,
                                    "strips": len(strips),
                                }
                            )
            raw_handle.flush()
            os.fsync(raw_handle.fileno())

        plan_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "decode_mode": decode_mode,
                    "memory": verdict.as_dict(),
                    "attempts": attempts,
                    "completed_strips": strips,
                    "dangerous_rows": planner.dangerous_rows,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        vrt_path.write_text(
            _raw_vrt_xml(
                raw_name=raw_path.name,
                width=width,
                height=height,
                band_count=band_count,
                dtype=str(scene_profile.get("gdal_dtype") or "UInt16"),
                itemsize=itemsize,
                crs_wkt=scene_profile.get("crs_wkt"),
                geotransform=_gdal_geotransform(list(scene_profile["transform"])),
                color_interpretation=list(scene_profile.get("color_interpretation") or []),
                nodata_values=list(scene_profile.get("nodata_values") or []),
            ),
            encoding="utf-8",
        )
        if cancel_check and cancel_check():
            raise BuildCancelled("Budowa derywatu anulowana")

        cpu_threads = max(1, (os.cpu_count() or 2) // 2)
        options = [
            f"COMPRESS={COG_PROFILE['compression']}",
            f"BLOCKSIZE={COG_PROFILE['blocksize']}",
            f"PREDICTOR={COG_PROFILE['predictor']}",
            f"OVERVIEWS={COG_PROFILE['overviews']}",
            f"RESAMPLING={COG_PROFILE['resampling']}",
            "BIGTIFF=YES",
            f"NUM_THREADS={cpu_threads}",
        ]
        worker_process = psutil.Process()
        previous_priority = None
        try:
            try:
                previous_priority = worker_process.nice()
                if os.name == "nt":
                    worker_process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            except Exception:
                previous_priority = None
            dataset = gdal.Translate(
                str(partial), str(vrt_path), format="COG", creationOptions=options
            )
        finally:
            if previous_priority is not None:
                try:
                    worker_process.nice(previous_priority)
                except Exception:
                    pass
        if dataset is None:
            raise RuntimeError("gdal.Translate did not return a dataset")
        dataset = None
        os.replace(partial, candidate)
        error_path.unlink(missing_ok=True)
    except BaseException as exc:
        partial.unlink(missing_ok=True)
        candidate.unlink(missing_ok=True)
        raw_path.unlink(missing_ok=True)
        vrt_path.unlink(missing_ok=True)
        for piece in target_dir.glob(".strip_*.raw"):
            piece.unlink(missing_ok=True)
        try:
            error_path.write_text(
                json.dumps(
                    {
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "error_code": getattr(exc, "error_code", None),
                        "peak_rss_bytes": peak_rss,
                        "attempts": attempts,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass
        raise

    return BuildResult(
        cog_path=candidate,
        raw_path=raw_path,
        vrt_path=vrt_path,
        seconds=time.perf_counter() - started,
        peak_rss_bytes=peak_rss,
        strips=strips,
        decode_mode=decode_mode,
    )
