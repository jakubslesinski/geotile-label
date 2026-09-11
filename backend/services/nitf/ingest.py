"""Open a NITF container, materialize a sensor-geometry working raster, and
build a scene manifest with a GCP-TPS geometry block.

The working raster is a tiled UInt16 GeoTIFF in the ORIGINAL pixel geometry
(no warp/reproject) with overviews and the GCPs preserved as tags. Downstream
8-bit rendering is applied on read via ``pan_uint16_percentile`` (decision D1),
so interactive stretch stays possible (D3) and raw DN is retained for export.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from models.project import APP_VERSION
from services.nitf.metadata import extract_nitf_metadata
from services.sensor_geometry import SceneGeoModel

EARTH_RADIUS_M = 6_371_008.8

MANIFEST_SCHEMA_VERSION = 1
FOOTPRINT_DENSIFY_PX = 64.0
TPS_WARNING = "Przybliżona georeferencja TPS z punktów georeferencyjnych obrazu; produkt nie jest ortorektyfikacją."


class NitfIngestError(RuntimeError):
    """Raised when a NITF scene cannot be ingested under v1 assumptions."""


@dataclass(frozen=True)
class NitfIngestResult:
    working_raster: str
    manifest: dict[str, Any]


def _open_nitf(path: str):
    from osgeo import gdal

    gdal.UseExceptions()
    if gdal.GetDriverByName("NITF") is None:
        raise NitfIngestError("Bieżąca instalacja GDAL nie zawiera sterownika NITF.")
    dataset = gdal.OpenEx(
        path,
        gdal.OF_RASTER | gdal.OF_READONLY,
        open_options=["VALIDATE=YES", "FAIL_IF_VALIDATION_ERROR=NO"],
    )
    if dataset is None:
        raise NitfIngestError(f"Nie można otworzyć NITF: {path}")
    if dataset.GetDriver().ShortName != "NITF":
        raise NitfIngestError(f"Plik nie jest odczytywany sterownikiem NITF: {path}")
    return dataset


def _read_gcps(dataset) -> tuple[list[dict[str, Any]], str]:
    from osgeo import osr

    gcps = dataset.GetGCPs()
    if not gcps or len(gcps) < 4:
        raise NitfIngestError(
            f"Scena wymaga co najmniej czterech GCP dla TPS (znaleziono {len(gcps) if gcps else 0})."
        )
    srs = dataset.GetGCPSpatialRef()
    gcp_crs = "EPSG:4326"
    if srs is not None:
        manager = getattr(osr, "ExceptionMgr", None)
        context = manager(useExceptions=False) if manager is not None else None
        if context is not None:
            context.__enter__()
        try:
            srs.AutoIdentifyEPSG()
            authority = srs.GetAuthorityName(None)
            code = srs.GetAuthorityCode(None)
            if authority and code:
                gcp_crs = f"{authority}:{code}".upper()
        finally:
            if context is not None:
                context.__exit__(None, None, None)
    points = [
        {
            "pixel": float(g.GCPPixel),
            "line": float(g.GCPLine),
            "lon": float(g.GCPX),
            "lat": float(g.GCPY),
            "z": float(g.GCPZ),
        }
        for g in gcps
    ]
    return points, gcp_crs


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_working_raster(dataset, output_path: Path, block: int = 512) -> None:
    from osgeo import gdal

    output_path.parent.mkdir(parents=True, exist_ok=True)
    translate_options = gdal.TranslateOptions(
        format="GTiff",
        creationOptions=[
            "TILED=YES",
            f"BLOCKXSIZE={block}",
            f"BLOCKYSIZE={block}",
            # Fast DEFLATE (level 1) cuts transcode time — the dominant import cost.
            # (PREDICTOR is not applicable: source samples are 10-bit, not 8/16/32/64.)
            "COMPRESS=DEFLATE",
            "ZLEVEL=1",
            "BIGTIFF=IF_SAFER",
        ],
    )
    result = gdal.Translate(str(output_path), dataset, options=translate_options)
    if result is None:
        raise NitfIngestError("GDAL Translate nie utworzył rastra roboczego.")
    # Overviews for fast pan/zoom preview (sensor pyramid). AVERAGE for continuous imagery.
    levels = []
    factor = 2
    longest = max(dataset.RasterXSize, dataset.RasterYSize)
    while longest // factor >= block:
        levels.append(factor)
        factor *= 2
    if levels:
        result.BuildOverviews("AVERAGE", levels)
    result.FlushCache()
    result = None


def ingest_nitf(
    source_path: str | Path,
    workspace_dir: str | Path,
    *,
    block: int = 512,
) -> NitfIngestResult:
    """Ingest a single-segment panchromatic UInt16 NITF scene.

    Produces a working GeoTIFF under ``workspace_dir`` and returns it together
    with a scene manifest carrying the GCP-TPS geometry block and metadata subset.
    """
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(f"Nie istnieje plik NITF: {source}")
    workspace = Path(workspace_dir)

    dataset = _open_nitf(str(source))
    try:
        if dataset.GetSubDatasets():
            raise NitfIngestError(
                "Kontener wielosegmentowy nie jest obsługiwany w v1 (założono pojedynczy segment)."
            )
        if dataset.RasterCount != 1:
            raise NitfIngestError(
                f"v1 obsługuje sceny jednopasmowe (panchromatyczne); pasm: {dataset.RasterCount}."
            )
        band = dataset.GetRasterBand(1)
        from osgeo import gdal

        if band.DataType != gdal.GDT_UInt16:
            raise NitfIngestError(
                f"v1 obsługuje UInt16; typ pasma: {gdal.GetDataTypeName(band.DataType)}."
            )

        metadata = extract_nitf_metadata(dataset)
        gcps, gcp_crs = _read_gcps(dataset)
        geo_model = SceneGeoModel.from_gcps(gcps, gcp_crs)
        width, height = int(dataset.RasterXSize), int(dataset.RasterYSize)
        corners = [[0, 0], [width, 0], [width, height], [0, height], [0, 0]]
        footprint = geo_model.pixel_to_wgs84(corners, densify_px=FOOTPRINT_DENSIFY_PX)

        working_raster = workspace / f"{source.stem}_sensor.tif"
        _build_working_raster(dataset, working_raster, block=block)
    finally:
        dataset = None

    source_sha256 = _sha256(source)
    acquisition = metadata["acquisition"]
    provenance = metadata["provenance"]
    sensor_model = metadata["sensor_model"]
    target = metadata["target"]
    gsd = _estimate_gsd(geo_model, width, height)

    scene_number = None
    if provenance.get("scene_number"):
        try:
            scene_number = int(provenance["scene_number"])
        except (TypeError, ValueError):
            scene_number = None

    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "raster_kind": "nitf_sensor",
        "modality": "AERIAL_EO",
        "georeferencing": "SENSOR_GEO",
        "filename": source.name,
        "source_path": str(source),
        "source_file_sha256": source_sha256,
        "provider": acquisition.get("platform"),
        "sensor": acquisition.get("sensor"),
        "acquisition_datetime_utc": acquisition.get("acquisition_datetime_utc"),
        # B1: identity / provenance surfaced for dataset filtering and the scene list.
        "display_name": provenance.get("image_id") or source.stem,
        "production_datetime_utc": provenance.get("production_datetime_utc"),
        "mission_id": acquisition.get("mission"),
        "flight_no": provenance.get("flight_no"),
        "scene_number": scene_number,
        "target_area_id": target.get("tgt_id"),
        "platform_altitude_m": sensor_model.get("platform_altitude_m"),
        "focal_length_mm": sensor_model.get("focal_length_mm"),
        # B5: approximate ground sample distance (anisotropic on oblique scenes).
        "gsd_m": gsd.get("gsd_m"),
        "gsd_col_m": gsd.get("gsd_col_m"),
        "gsd_row_m": gsd.get("gsd_row_m"),
        "gsd_approximate": True,
        "image": metadata["image"],
        # No affine transform for sensor-geometry scenes; geo lives in `geometry`.
        "geospatial": {"has_geo": False},
        "geometry": {
            "model": "gcp_tps",
            "gcp_crs": gcp_crs,
            "axis_order": "traditional_gis_xy",
            "approximate": True,
            "orthorectified": False,
            "gcps": gcps,
            "footprint_wgs84": footprint,
            "warnings": [TPS_WARNING],
        },
        "metadata": {
            "acquisition": acquisition,
            "provenance": provenance,       # B1
            "sensor_model": sensor_model,   # B2 (retention)
            "target": target,
            "classification": metadata["classification"],
            "tre_names": metadata["tre_names"],
            "metadata_domains": metadata["metadata_domains"],
        },
        "working_raster": str(working_raster),
        "software": {"app": APP_VERSION, "gdal": _gdal_version()},
    }
    return NitfIngestResult(working_raster=str(working_raster), manifest=manifest)


def _gdal_version() -> str:
    from osgeo import gdal

    return gdal.VersionInfo("RELEASE_NAME")


def _haversine_m(a: list[float], b: list[float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def _estimate_gsd(geo_model: SceneGeoModel, width: int, height: int) -> dict[str, Any]:
    """Approximate ground sample distance from the TPS-mapped scene corners.

    Oblique scenes have strongly anisotropic, position-varying GSD, so this is a
    single coarse figure flagged ``approximate`` (spec: not an accuracy claim).
    """
    upper_left, upper_right, lower_left = geo_model.pixel_to_wgs84(
        [[0, 0], [width, 0], [0, height]]
    )
    across_m = _haversine_m(upper_left, upper_right)
    along_m = _haversine_m(upper_left, lower_left)
    gsd_col = across_m / width if width else None
    gsd_row = along_m / height if height else None
    gsd = math.sqrt(gsd_col * gsd_row) if gsd_col and gsd_row else None
    return {
        "gsd_m": round(gsd, 3) if gsd else None,
        "gsd_col_m": round(gsd_col, 3) if gsd_col else None,
        "gsd_row_m": round(gsd_row, 3) if gsd_row else None,
        "gsd_approximate": True,
    }
