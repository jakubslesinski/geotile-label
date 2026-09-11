"""Unified pixel <-> WGS84 geometry for scenes.

Two scene kinds share one interface so every geo-derivation call site (tile
footprints, annotation geospatial attributes, spatial exports) stays agnostic:

- ``affine`` — satellite scenes with a linear 6-coefficient GeoTransform + CRS
  (``scene_manifest.geospatial``). Delegates to the exact same primitives the
  existing pipeline uses, so results are bit-for-bit identical.
- ``gcp_tps`` — airborne NITF sensor scenes with only ground control points and
  a Thin Plate Spline model (``scene_manifest.geometry``). TPS is non-linear:
  straight pixel edges become curved on the map, so rings MUST be densified
  before transformation (spec §15). Built on ``osgeo.gdal.Transformer`` with
  ``METHOD=GCP_TPS`` over an in-memory dataset carrying the GCPs.

Threading note (spec §6.4): a ``gdal.Transformer`` and its backing dataset must
not be shared across threads. ``SceneGeoModel`` therefore owns its transformer
per instance and is NOT globally cached here; call sites build one model per
scene within a single-threaded pass.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from services.attribute_engine.engine import pixel_to_native, transform_points

Point = Sequence[float]

WGS84 = "EPSG:4326"


class SceneGeometryError(RuntimeError):
    """Raised when a scene geometry model cannot be built or evaluated."""


def try_scene_geo_model(manifest: dict[str, Any] | None) -> "SceneGeoModel | None":
    """Build a model from a manifest, returning ``None`` on any failure.

    For bulk pipelines (tile catalogs, exports) a malformed geometry block must
    degrade to "no geo" rather than abort the whole run. Build ONCE per scene and
    reuse: a TPS model owns a gdal.Transformer that must not be rebuilt per tile.
    """
    try:
        return SceneGeoModel.from_manifest(manifest)
    except Exception:  # noqa: BLE001 — defensive: never let one scene break a batch
        return None


def densify_polyline_px(points: Sequence[Point], max_step_px: float | None) -> list[list[float]]:
    """Insert intermediate vertices so no segment is longer than ``max_step_px``.

    Consecutive input points are treated as an open polyline (the caller controls
    closure by repeating the first point). Corner points are preserved; only
    interior samples are added. Used to approximate curved TPS edges (spec §15).
    """
    pts = [[float(p[0]), float(p[1])] for p in points]
    if max_step_px is None or max_step_px <= 0 or len(pts) < 2:
        return pts
    out: list[list[float]] = []
    for index in range(len(pts) - 1):
        a = pts[index]
        b = pts[index + 1]
        out.append([a[0], a[1]])
        distance = math.hypot(b[0] - a[0], b[1] - a[1])
        if distance <= max_step_px:
            continue
        steps = int(math.floor(distance / max_step_px))
        for step in range(1, steps + 1):
            ratio = step * max_step_px / distance
            if ratio >= 1.0:
                break
            out.append([a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio])
    out.append([pts[-1][0], pts[-1][1]])
    return out


class SceneGeoModel:
    """Pixel <-> WGS84 mapping for one scene (affine or GCP TPS)."""

    def __init__(
        self,
        kind: str,
        *,
        transform: Sequence[float] | None = None,
        crs: str | None = None,
        gcps: list[dict[str, Any]] | None = None,
        gcp_crs: str | None = None,
    ) -> None:
        self.kind = kind
        self._transform = [float(v) for v in transform[:6]] if transform else None
        self._crs = crs
        self._gcps = gcps
        self._gcp_crs = gcp_crs or WGS84
        self._transformer = None  # gdal.Transformer (tps only)
        self._mem_ds = None  # backing gdal MEM dataset (keeps transformer valid)
        if kind == "gcp_tps":
            self._build_tps()
        elif kind != "affine":
            raise SceneGeometryError(f"Unsupported scene geometry kind: {kind!r}")

    # -- construction ---------------------------------------------------------

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any] | None) -> "SceneGeoModel | None":
        """Build a model from a scene manifest, or ``None`` if not georeferenced.

        ``geometry.model == 'gcp_tps'`` wins; otherwise fall back to the affine
        ``geospatial`` block (existing satellite scenes). Absence of both means
        the scene has no usable geo model.
        """
        manifest = manifest or {}
        geometry = manifest.get("geometry") or {}
        if geometry.get("model") == "gcp_tps":
            return cls.from_gcps(geometry.get("gcps") or [], geometry.get("gcp_crs") or WGS84)
        geospatial = manifest.get("geospatial") or {}
        transform = geospatial.get("transform")
        crs = geospatial.get("crs")
        if geospatial.get("has_geo") and transform and len(transform) >= 6 and crs:
            return cls("affine", transform=transform, crs=crs)
        return None

    @classmethod
    def from_gcps(cls, gcps: list[dict[str, Any]], gcp_crs: str = WGS84) -> "SceneGeoModel":
        if len(gcps) < 4:
            raise SceneGeometryError("TPS wymaga co najmniej czterech punktów georeferencyjnych.")
        return cls("gcp_tps", gcps=gcps, gcp_crs=gcp_crs)

    def _build_tps(self) -> None:
        from osgeo import gdal, osr

        gdal.UseExceptions()
        srs = osr.SpatialReference()
        if srs.SetFromUserInput(self._gcp_crs) != 0:
            raise SceneGeometryError(f"Nie można odczytać CRS punktów: {self._gcp_crs}")
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)  # X=lon, Y=lat (spec §8.1)

        gdal_gcps = [
            gdal.GCP(
                float(g["lon"]),
                float(g["lat"]),
                float(g.get("z", 0.0) or 0.0),
                float(g["pixel"]),
                float(g["line"]),
            )
            for g in self._gcps or []
        ]
        mem_ds = gdal.GetDriverByName("MEM").Create("", 1, 1, 1, gdal.GDT_Byte)
        mem_ds.SetGCPs(gdal_gcps, srs.ExportToWkt())
        transformer = gdal.Transformer(mem_ds, None, ["METHOD=GCP_TPS"])
        if transformer is None:
            raise SceneGeometryError("Nie można utworzyć transformera TPS.")
        self._mem_ds = mem_ds
        self._transformer = transformer

    # -- forward: pixel -> WGS84 ---------------------------------------------

    def pixel_to_wgs84(
        self,
        points: Sequence[Point],
        densify_px: float | None = None,
    ) -> list[list[float]]:
        """Map scene pixels to ``[lon, lat]``.

        For rings/edges pass ``densify_px`` so curved TPS edges are sampled; the
        affine branch ignores it (lines stay lines) and returns exactly the
        existing ``pixel_to_native``+``transform_points`` result.
        """
        source = densify_polyline_px(points, densify_px) if densify_px else [
            [float(p[0]), float(p[1])] for p in points
        ]
        if self.kind == "affine":
            native = [pixel_to_native(point, self._transform) for point in source]
            return transform_points(native, str(self._crs), WGS84)
        # gcp_tps
        mapped = [self._tps_forward(point[0], point[1]) for point in source]
        if not self._gcp_crs_is_wgs84():
            mapped = transform_points(mapped, self._gcp_crs, WGS84)
        return mapped

    # -- inverse: WGS84 -> pixel ---------------------------------------------

    def wgs84_to_pixel(self, points: Sequence[Point]) -> list[list[float]]:
        """Map ``[lon, lat]`` back to scene pixels (round-trip / map-drawn edits)."""
        if self.kind == "affine":
            native = transform_points(
                [[float(p[0]), float(p[1])] for p in points], WGS84, str(self._crs)
            )
            return [self._affine_inverse(point) for point in native]
        # gcp_tps
        source = [[float(p[0]), float(p[1])] for p in points]
        if not self._gcp_crs_is_wgs84():
            source = transform_points(source, WGS84, self._gcp_crs)
        return [self._tps_inverse(point[0], point[1]) for point in source]

    # -- internals ------------------------------------------------------------

    def _gcp_crs_is_wgs84(self) -> bool:
        return str(self._gcp_crs).upper() in {"EPSG:4326", "OGC:CRS84", "CRS84", "WGS84"}

    def _tps_forward(self, pixel: float, line: float) -> list[float]:
        ok, (x, y, _z) = self._transformer.TransformPoint(False, float(pixel), float(line), 0.0)
        if not ok:
            raise SceneGeometryError("TPS: transformacja pixel/line -> mapa nie powiodła się.")
        return [x, y]

    def _tps_inverse(self, x: float, y: float) -> list[float]:
        ok, (pixel, line, _z) = self._transformer.TransformPoint(True, float(x), float(y), 0.0)
        if not ok:
            raise SceneGeometryError("TPS: transformacja mapa -> pixel/line nie powiodła się.")
        return [pixel, line]

    def _affine_inverse(self, native: Point) -> list[float]:
        a, b, c, d, e, f = self._transform  # type: ignore[misc]
        determinant = a * e - b * d
        if abs(determinant) < 1e-15:
            raise SceneGeometryError("Osobliwa transformacja afiniczna — brak odwrotności.")
        x = float(native[0]) - c
        y = float(native[1]) - f
        return [(e * x - b * y) / determinant, (-d * x + a * y) / determinant]


# -- display space (Web Mercator) --------------------------------------------------------
#
# Widok mapy jest KONFOREMNY wzgledem elipsoidy: prostokat na ziemi jest w nim prostokatem.
# Piksele sceny w EPSG:4326 juz nie — stopien dlugosci i szerokosci maja tam rozna dlugosc
# w terenie, wiec prostokat dopasowany w PIKSELACH jest na ziemi (i na mapie) skosny. Na
# szerokosci 60 stopni ramka obrocona o 35 stopni traci na mapie 35 stopni prostopadloscia.
# Dopasowanie geometrii w tej przestrzeni jest wiec poprawnym rozwiazaniem, a nie kosmetyka.
#
# Jednostki: promien Ziemi razy Merkator, czyli metry na rowniku. Skala jest stala w calym
# przeblisku jednej sceny, wiec dopasowanie prostokata o najmniejszym polu jest niezmiennicze.

MERCATOR_RADIUS_M = 6378137.0


def pixel_to_display(model: "SceneGeoModel", points: Sequence[Point]) -> list[list[float]]:
    """Piksele sceny -> uklad wyswietlania (Web Mercator, metry na rowniku)."""
    out: list[list[float]] = []
    for lon, lat in model.pixel_to_wgs84([[float(p[0]), float(p[1])] for p in points]):
        clamped = max(-85.05112878, min(85.05112878, float(lat)))
        out.append([
            MERCATOR_RADIUS_M * math.radians(float(lon)),
            MERCATOR_RADIUS_M * math.log(math.tan(math.pi / 4.0 + math.radians(clamped) / 2.0)),
        ])
    return out


def display_to_pixel(model: "SceneGeoModel", points: Sequence[Point]) -> list[list[float]]:
    """Uklad wyswietlania -> piksele sceny."""
    wgs84 = [
        [
            math.degrees(float(x) / MERCATOR_RADIUS_M),
            math.degrees(2.0 * math.atan(math.exp(float(y) / MERCATOR_RADIUS_M)) - math.pi / 2.0),
        ]
        for x, y in points
    ]
    return model.wgs84_to_pixel(wgs84)


def rotated_corners_px(rotated_bbox: dict[str, Any]) -> list[list[float]]:
    """Cztery narozniki prostokata zorientowanego, w pikselach sceny."""
    cx = float(rotated_bbox["cx"])
    cy = float(rotated_bbox["cy"])
    half_w = float(rotated_bbox["width"]) / 2.0
    half_h = float(rotated_bbox["height"]) / 2.0
    angle = math.radians(float(rotated_bbox.get("angle_deg") or 0.0))
    ax, ay = math.cos(angle), math.sin(angle)
    nx, ny = -ay, ax
    return [
        [cx - ax * half_w - nx * half_h, cy - ay * half_w - ny * half_h],
        [cx + ax * half_w - nx * half_h, cy + ay * half_w - ny * half_h],
        [cx + ax * half_w + nx * half_h, cy + ay * half_w + ny * half_h],
        [cx - ax * half_w + nx * half_h, cy - ay * half_w + ny * half_h],
    ]


def rotated_bbox_from_polygon_px(polygon: Sequence[Point]) -> dict[str, float] | None:
    """Pikselowe podsumowanie `rotated_bbox` dla poligonu wiernego mapie.

    Poligon jest zrodlem prawdy ksztaltu; `rotated_bbox` zostaje jako zgodnosc wstecz
    (starsze konsumenty, eksporty). Na scenach niekonforemnych jest z natury przyblizeniem
    — prostokat pikselowy nie potrafi opisac scinania — dokladnie tak, jak liczy to
    frontend w `rotatedStateFromAnnotation`.
    """
    if polygon is None or len(polygon) < 4:
        return None
    p0, p1, p2 = polygon[0], polygon[1], polygon[2]
    width = math.hypot(float(p1[0]) - float(p0[0]), float(p1[1]) - float(p0[1]))
    height = math.hypot(float(p2[0]) - float(p1[0]), float(p2[1]) - float(p1[1]))
    if width <= 0.0 or height <= 0.0:
        return None
    return {
        "cx": round(sum(float(point[0]) for point in polygon[:4]) / 4.0, 3),
        "cy": round(sum(float(point[1]) for point in polygon[:4]) / 4.0, 3),
        "width": round(width, 3),
        "height": round(height, 3),
        "angle_deg": round(
            math.degrees(math.atan2(float(p1[1]) - float(p0[1]), float(p1[0]) - float(p0[0]))), 6
        ),
    }
