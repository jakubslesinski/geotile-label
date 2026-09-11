"""GeoTIFF helpers."""

from pathlib import Path


def get_geotiff_info(path: str | Path) -> dict:
    """Extract CRS, transform and metadata from a GeoTIFF."""
    import rasterio

    with rasterio.open(path) as src:
        return {
            "crs": str(src.crs) if src.crs else None,
            "transform": list(src.transform)[:6],
            "width": src.width,
            "height": src.height,
            "count": src.count,
            "dtype": str(src.dtypes[0]),
            "bounds": list(src.bounds),
        }
