"""PNG/JPEG/GeoTIFF loader + thumbnail generation."""

import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from models.project import SceneInfo
from services.scene_characterization import characterize_raster, data_band_indexes
from utils.image import ensure_rgb_uint8, make_thumbnail

# Sceny to własne, lokalne pliki użytkownika (nie niezaufany upload z sieci), a benchmarki
# typu DOTA/FAIR1M miewają obrazy rzędu 28k×28k px. Wyłączamy anty-DoS limit PIL, żeby nie
# rzucał DecompressionBombError. Pamięciowo i tak chronimy się zdecymowanym odczytem miniatur.
Image.MAX_IMAGE_PIXELS = None


GDAL_RASTER_EXTENSIONS = {".tif", ".tiff", ".jp2", ".vrt", ".ntf", ".nitf"}
# v6 (P0.7): pixel spacing liczony z pelnej transformacji i z jednostek CRS. Podbicie jest
# KONIECZNE, bo bez niego istniejace sceny zachowalyby wartosc `gsd_m` policzona stara,
# bledna formula, a nowe dostawaly poprawna — cicha niespojnosc w obrebie jednego projektu.
SCENE_INFO_VERSION = "v6"

# --- Pixel spacing i GSD (DESIGN_DECISIONS.md, scene-import P0.7) ---
#
# Poprzednia implementacja liczyla spacing jako `abs(a)` i `abs(e)`, czyli IGNOROWALA czlony
# obrotu `b` i `d`. Dla obroconej geotransformacji zanizalo to wynik proporcjonalnie do
# `cos(obrotu)`. Zmierzone na rzeczywistym ICEYE CSI (obrot 103,26 stopnia): 0,0655 m zamiast
# 0,3475 m, czyli blad -81,2%.
#
# Drugi blad byl w jednostkach: przelicznik stopni wlaczal sie WYLACZNIE dla trzech literalow
# ("EPSG:4326", "OGC:CRS84", "CRS84"), wiec kazdy inny CRS geograficzny (np. ETRS89
# "EPSG:4258") dostawal stopnie traktowane jak metry — blad rzedu 10^5. Dodatkowo stale
# 111320/110540 pomijaly elipsoide i szerokosc geograficzna dla osi polnoc-poludnie.
#
# Teraz: dlugosc kroku o JEDEN piksel liczona z pelnej transformacji, a jednostki brane
# z definicji CRS. Dla CRS geograficznego uzywamy lokalnych promieni elipsoidy WGS84 zamiast
# stalych — dla odleglosci rzedu metra to praktycznie wartosc dokladna, a nie wymaga pyproj,
# ktorego spakowany runtime nie zawiera.
_WGS84_SEMI_MAJOR_M = 6378137.0
_WGS84_FLATTENING = 1.0 / 298.257223563
_WGS84_ECC_SQ = 2.0 * _WGS84_FLATTENING - _WGS84_FLATTENING * _WGS84_FLATTENING


def _geographic_degree_lengths_m(latitude_deg: float) -> tuple[float, float]:
    """Dlugosc jednego stopnia dlugosci i szerokosci [m] na danej szerokosci geograficznej."""
    import math

    latitude = math.radians(latitude_deg)
    sin_lat = math.sin(latitude)
    w = math.sqrt(1.0 - _WGS84_ECC_SQ * sin_lat * sin_lat)
    normal_radius = _WGS84_SEMI_MAJOR_M / w
    meridional_radius = _WGS84_SEMI_MAJOR_M * (1.0 - _WGS84_ECC_SQ) / (w ** 3)
    degree = math.radians(1.0)
    return degree * normal_radius * math.cos(latitude), degree * meridional_radius


def pixel_spacing_from_transform(
    transform: "list[float] | tuple[float, ...]",
    *,
    is_geographic: bool,
    linear_unit_factor: float | None = None,
    latitude_deg: float | None = None,
) -> tuple[float | None, float | None]:
    """Odleglosc miedzy srodkami sasiednich pikseli wzdluz obu osi obrazu, w metrach.

    Krok o jedna KOLUMNE przesuwa punkt o `(a, d)`, krok o jeden WIERSZ o `(b, e)` — dlatego
    oba czlony wchodza do dlugosci wektora. Zwraca `(None, None)`, gdy jednostek nie da sie
    ustalic; cicha zamiana stopni na metry jest gorsza niz brak wyniku.
    """
    import math

    if not transform or len(transform) < 6:
        return None, None
    a, b, _c, d, e, _f = (float(value) for value in transform[:6])

    if is_geographic:
        if latitude_deg is None:
            return None, None
        lon_m, lat_m = _geographic_degree_lengths_m(latitude_deg)
        spacing_x = math.hypot(a * lon_m, d * lat_m)
        spacing_y = math.hypot(b * lon_m, e * lat_m)
    else:
        if not linear_unit_factor:
            return None, None
        spacing_x = math.hypot(a, d) * linear_unit_factor
        spacing_y = math.hypot(b, e) * linear_unit_factor

    if not (spacing_x > 0 and spacing_y > 0):
        return None, None
    return spacing_x, spacing_y


def gsd_from_pixel_spacing(spacing_x: float | None, spacing_y: float | None) -> float | None:
    """Skalarny GSD zgodny wstecznie z dotychczasowa semantyka pola `gsd_m`.

    ZACHOWUJEMY `min(x, y)`, mimo ze dla anizotropowego piksela wartosc grubsza jest
    uczciwszym opisem rozdzielczosci. Powod jest migracyjny: `gsd_m` zasila `gsd_tiler`,
    konfiguracje datasetow i sortowanie indeksu, wiec zmiana DEFINICJI to osobna decyzja
    z wlasnym raportem migracji (bramka P0.7). Ten etap naprawia *obliczenie*, nie definicje.
    Prawdziwe wartosci obu osi sa dostepne w `pixel_spacing_x_m` / `pixel_spacing_y_m`.
    """
    values = [value for value in (spacing_x, spacing_y) if value and value > 0]
    return round(min(values), 4) if values else None


def load_scene_array(path: str | Path) -> np.ndarray:
    """Load scene as numpy array (H, W, C) uint8 RGB."""
    path = Path(path)

    suffix = path.suffix.lower()

    if suffix in GDAL_RASTER_EXTENSIONS:
        return _load_geotiff(path)
    else:
        img = Image.open(path).convert("RGB")
        return np.array(img)


def _load_geotiff(path: Path) -> np.ndarray:
    import rasterio

    with rasterio.open(path) as src:
        # Read all bands → (bands, H, W)
        data = src.read()

    # Transpose to (H, W, bands)
    arr = np.transpose(data, (1, 2, 0))
    return ensure_rgb_uint8(arr)


def get_scene_info(
    path: str | Path,
    *,
    modality: str | None = None,
    product_type: str | None = None,
    selected_sensors: list[str] | tuple[str, ...] = (),
) -> SceneInfo:
    """Extract scene metadata without loading full image."""
    path = Path(path)

    suffix = path.suffix.lower()

    if suffix in GDAL_RASTER_EXTENSIONS:
        return _geotiff_info(
            path,
            modality=modality,
            product_type=product_type,
            selected_sensors=selected_sensors,
        )
    else:
        img = Image.open(path)
        w, h = img.size
        channels = len(img.getbands())
        return SceneInfo(
            width=w,
            height=h,
            channels=channels,
            dtype="uint8",
            has_geo=False,
            filename=path.name,
            file_size=path.stat().st_size,
        )


def _geotiff_info(
    path: Path,
    *,
    modality: str | None = None,
    product_type: str | None = None,
    selected_sensors: list[str] | tuple[str, ...] = (),
) -> SceneInfo:
    import rasterio
    from rasterio.transform import from_gcps

    source_stat = path.stat()
    with rasterio.open(path) as src:
        gcps, gcp_crs = src.gcps
        uses_gcps = src.crs is None and bool(gcps) and gcp_crs is not None
        crs = src.crs or (gcp_crs if uses_gcps else None)
        has_geo = crs is not None
        transform_list = None
        crs_text = None
        crs_proj4 = None
        bounds_4326 = None

        if has_geo:
            t = from_gcps(gcps) if uses_gcps else src.transform
            transform_list = [t.a, t.b, t.c, t.d, t.e, t.f]
            crs_text = str(crs)

            try:
                crs_proj4 = crs.to_proj4()
            except Exception:
                crs_proj4 = None

            from rasterio.warp import transform_bounds

            try:
                if uses_gcps:
                    xs = [gcp.x for gcp in gcps]
                    ys = [gcp.y for gcp in gcps]
                    source_bounds = (min(xs), min(ys), max(xs), max(ys))
                else:
                    source_bounds = src.bounds
                b = transform_bounds(crs, "EPSG:4326", *source_bounds, densify_pts=21)
                bounds_4326 = list(b)  # [west, south, east, north]
            except Exception:
                if crs_text and crs_text.upper() in {"EPSG:4326", "OGC:CRS84", "CRS84"}:
                    if uses_gcps:
                        xs = [gcp.x for gcp in gcps]
                        ys = [gcp.y for gcp in gcps]
                        bounds_4326 = [min(xs), min(ys), max(xs), max(ys)]
                    else:
                        bounds_4326 = [
                            src.bounds.left,
                            src.bounds.bottom,
                            src.bounds.right,
                            src.bounds.top,
                        ]
                else:
                    raise
        # GSD z rozmiaru piksela transformu — dla scen bez sidecarów metadanych (WV/PNEO itp.)
        # to jedyne źródło rozdzielczości. CRS projektowy (UTM) → jednostka metr; geograficzny
        # (deg) → przelicz przez ~111 km/° z korektą cos(lat) na osi wschód-zachód.
        gsd_m = None
        spacing_x = spacing_y = None
        gsd_source = gsd_method = None
        if has_geo and transform_list and crs is not None:
            is_geographic = bool(getattr(crs, "is_geographic", False))
            unit_factor = None
            if not is_geographic:
                try:
                    unit_factor = float(crs.linear_units_factor[1])
                except Exception:
                    unit_factor = None
            latitude = None
            if is_geographic and bounds_4326:
                latitude = (bounds_4326[1] + bounds_4326[3]) / 2.0
            spacing_x, spacing_y = pixel_spacing_from_transform(
                transform_list,
                is_geographic=is_geographic,
                linear_unit_factor=unit_factor,
                latitude_deg=latitude,
            )
            gsd_m = gsd_from_pixel_spacing(spacing_x, spacing_y)
            if gsd_m is not None:
                gsd_source = "transform"
                gsd_method = "affine_geographic_wgs84" if is_geographic else "affine_projected"

        characterization = characterize_raster(
            src,
            filename=path.name,
            source_path=path,
            modality=modality,
            product_type=product_type,
            selected_sensors=selected_sensors,
        )

        return SceneInfo(
            width=src.width,
            height=src.height,
            channels=src.count,
            dtype=str(src.dtypes[0]),
            nodata=_finite_or_none(src.nodata),
            mask_flags=_mask_flag_names(src),
            has_geo=has_geo,
            crs=crs_text,
            crs_proj4=crs_proj4,
            transform=transform_list if has_geo else None,
            bounds=bounds_4326,
            gsd_m=gsd_m,
            pixel_spacing_x_m=round(spacing_x, 4) if spacing_x else None,
            pixel_spacing_y_m=round(spacing_y, 4) if spacing_y else None,
            gsd_source=gsd_source,
            gsd_method=gsd_method,
            **characterization,
            characterization_source_size=source_stat.st_size,
            characterization_source_mtime_ns=source_stat.st_mtime_ns,
            filename=path.name,
            file_size=source_stat.st_size,
        )


def _finite_or_none(value: Any) -> float | None:
    """`nodata` bywa NaN; JSON tego nie zapisze, a i tak nie da sie tego porownac."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _mask_flag_names(src: Any) -> list[str]:
    """Skad pochodzi maska wazności pikseli — jawnie, zamiast domyslania sie z `nodata`."""
    try:
        flags = src.mask_flag_enums
    except Exception:
        return []
    names: list[str] = []
    for band_flags in flags:
        for flag in band_flags:
            name = getattr(flag, "name", str(flag)).lower()
            if name not in names:
                names.append(name)
    return names


def generate_thumbnail(
    scene_path: str | Path,
    out_path: str | Path,
    scene_info: SceneInfo | dict | None = None,
) -> None:
    """Generate and save a thumbnail."""
    scene_path = Path(scene_path)
    out_path = Path(out_path)

    # Zdecymowany odczyt przez GDAL jest bezpieczny pamięciowo (nie dekoduje pełnej sceny)
    # i działa nie tylko dla GeoTIFF, ale też dla zwykłych PNG/JPG — dla 8-bitowych obrazów
    # nie stosuje rozciągania. Próbujemy go dla każdego rastra; PIL zostaje jako fallback dla
    # formatów, których GDAL nie otworzy. To eliminuje DecompressionBomb/OOM na wielkich scenach.
    try:
        _generate_geotiff_thumbnail(scene_path, out_path, scene_info=scene_info)
        return
    except Exception:
        pass

    arr = load_scene_array(scene_path)
    thumb = make_thumbnail(arr, max_width=1000)
    thumb.save(str(out_path), "PNG")


def _data_band_indexes(src) -> list[int]:
    """1-based indeksy pasm DANYCH (bez alpha).

    Dla wielkich JPEG2000 (np. PhrNeo PAN + alpha) odczyt pasma alpha/maski
    nie korzysta z overviews i dekoduje pełną rozdzielczość → praktyczny zawis
    całego importu. Dlatego przy podglądzie/estymacji zakresu czytamy wyłącznie
    pasma danych i nigdy nie sięgamy po alpha (ani `masked=True`).
    """
    return data_band_indexes(src)


def _generate_geotiff_thumbnail(
    scene_path: Path,
    out_path: Path,
    *,
    scene_info: SceneInfo | dict | None = None,
) -> None:
    import rasterio
    from rasterio.enums import Resampling

    max_width = 1000
    with rasterio.open(scene_path) as src:
        out_w = min(max_width, src.width)
        scale = out_w / src.width
        out_h = max(1, round(src.height * scale))
        stored = scene_info.model_dump() if isinstance(scene_info, SceneInfo) else (scene_info or {})
        indexes = [int(value) for value in stored.get("data_band_indexes") or []]
        if not indexes:
            indexes = _data_band_indexes(src)
        indexes = indexes[:3]
        if stored.get("characterization_version"):
            display_min = stored.get("display_min")
            display_max = stored.get("display_max")
            display_mode = stored.get("display_mode")
        else:
            characterization = characterize_raster(src, filename=scene_path.name)
            display_min = characterization.get("display_min")
            display_max = characterization.get("display_max")
            display_mode = characterization.get("display_mode")
        data = src.read(
            indexes=indexes,
            out_shape=(len(indexes), out_h, out_w),
            resampling=Resampling.bilinear,
        )

    arr = np.transpose(data, (1, 2, 0))
    thumb = Image.fromarray(ensure_rgb_uint8(arr, display_min, display_max, display_mode))
    thumb.save(str(out_path), "PNG")


def _estimate_geotiff_display_range(
    src,
    *,
    modality: str | None = None,
    product_type: str | None = None,
) -> tuple[float | None, float | None, str | None]:
    """Compatibility wrapper backed by the bounded P1.7 characterization pass."""
    result = characterize_raster(
        src,
        filename=Path(str(getattr(src, "name", ""))).name,
        modality=modality,
        product_type=product_type,
    )
    return result.get("display_min"), result.get("display_max"), result.get("display_mode")
