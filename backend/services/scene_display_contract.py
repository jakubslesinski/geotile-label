"""Kontrakt wyswietlania sceny: uklad wspolrzednych, dostepny zoom i rodzaj assetu.

Realizuje R0.1 i R0.2 (DESIGN_DECISIONS.md, jp2-fullres).

Dlaczego to jest osobny modul, a nie kilka pol dolozonych do `_scene_render_context`
-----------------------------------------------------------------------------------
Do tej pory `max_zoom` pelnil TRZY role naraz: ograniczal dopuszczalne zadania, wyznaczal
przeliczenie kafla na piksele zrodla (`TILE_SIZE * 2**(max_zoom - z)`) i byl poziomem
referencyjnym dla `map.unproject()` oraz geometrii adnotacji. Obnizenie go do poziomu,
ktory piramida faktycznie potrafi obsluzyc, przesunelo by okna odczytu **i adnotacje**.

Dlatego rozdzielamy:

* `source_max_zoom` — STALY uklad wspolrzednych zrodla; nie zmienia sie nigdy w cyklu
  zycia sceny i tylko wzgledem niego liczy sie okna;
* `available_native_zoom` — najwyzszy poziom, ktory renderer potrafi teraz obsluzyc
  interaktywnie; zmienia sie, gdy pojawi sie albo zniknie derywat.

Zadanie powyzej `available_native_zoom` jest odrzucane, ale nigdy nie zmienia sposobu
liczenia okien.

Druga rzecz, ktora rozdzielamy, to ZRODLO i AKTYWNA SCIEZKA DEKODOWANIA. Klasyfikacja po
samym zrodle jest blednam po opublikowaniu COG (zrodlem dalej jest JP2, ale renderer czyta
niezalezny GeoTIFF), a klasyfikacja po rozszerzeniu sciezki odczytu byla blednam, odkad
sciezka jest `overview.vrt` opakowujacy JP2. Liczy sie graf odczytu, nie nazwa pliku.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.scene_overviews import source_overviews_are_display_ready

TILE_SIZE = 256

#: Rodzaje assetu wyswietlania. `jp2_backed_vrt` to lekki VRT z sidecarem `.ovr`, ktory
#: dla poziomow zredukowanych czyta z `.ovr`, ale dla 1x schodzi do JP2 — dlatego jest
#: osobnym rodzajem, a nie `geotiff`.
DISPLAY_KIND_SOURCE = "source"
DISPLAY_KIND_JP2_BACKED_VRT = "jp2_backed_vrt"
DISPLAY_KIND_PREVIEW_GEOTIFF = "preview_geotiff"
DISPLAY_KIND_COG = "cog"
DISPLAY_KIND_GEOTIFF = "geotiff"

SOURCE_KIND_JP2 = "jp2"
SOURCE_KIND_GEOTIFF = "geotiff"
SOURCE_KIND_OTHER = "other"

_JP2_SUFFIXES = {".jp2", ".j2k", ".jpf", ".jpx"}
_GEOTIFF_SUFFIXES = {".tif", ".tiff"}

#: Gdy scena wymaga dekodu JP2 w 1x, a nie wiadomo, od ktorego poziomu zaczyna sie
#: piramida projektu, zakladamy 2 — najlagodniejsze mozliwe ograniczenie. Lepiej odjac
#: jeden poziom niz obiecac rozdzielczosc, ktorej nie da sie obsluzyc.
DEFAULT_JP2_BASE_FACTOR = 2


def compute_source_max_zoom(width: int, height: int) -> int:
    """Poziom referencyjny ukladu wspolrzednych sceny.

    Zalezy WYLACZNIE od wymiarow zrodla, wiec jest stabilny przez caly cykl zycia sceny.
    """
    max_dim = max(int(width or 0), int(height or 0))
    if max_dim <= TILE_SIZE:
        return 0
    return math.ceil(math.log2(max_dim / TILE_SIZE))


#: Obwod Ziemi w metrach na rowniku, uzywany przez siatke Web Mercator.
_WEBMERCATOR_EQUATOR_METERS = 156543.033928041


def compute_native_xyz_zoom(scene_info: dict[str, Any] | None) -> int | None:
    """Poziom XYZ, na ktorym kafel 256 px odpowiada natywnej rozdzielczosci sceny.

    Liczony z NAJDROBNIEJSZEGO probkowania (`min` z rozstawu X i Y), bo to ono wyznacza
    najwyzszy poziom niosacy jeszcze nowa informacje. Dla rastra w EPSG:4326 piksele sa
    kwadratowe w stopniach, ale nie w metrach — na szerokosci 44 deg rozstaw wschod-zachod
    jest wyrazniej drobniejszy niz polnoc-poludnie, wiec branie samego `gsd_m` (sredniej)
    zanizaloby wynik o ulamek poziomu.
    """
    scene_info = scene_info or {}
    if not scene_info.get("has_geo"):
        return None
    candidates = [
        value
        for value in (scene_info.get("pixel_spacing_x_m"), scene_info.get("pixel_spacing_y_m"))
        if isinstance(value, (int, float)) and value > 0
    ]
    resolution = min(candidates) if candidates else scene_info.get("gsd_m")
    if not isinstance(resolution, (int, float)) or resolution <= 0:
        return None
    bounds = scene_info.get("bounds") or []
    latitude = (float(bounds[1]) + float(bounds[3])) / 2.0 if len(bounds) == 4 else 0.0
    latitude = max(-85.0, min(85.0, latitude))
    meters_per_pixel_at_z0 = _WEBMERCATOR_EQUATOR_METERS * math.cos(math.radians(latitude))
    if meters_per_pixel_at_z0 <= 0:
        return None
    return max(0, int(round(math.log2(meters_per_pixel_at_z0 / float(resolution)))))


@dataclass(frozen=True)
class DisplayAssets:
    """Czym jest zrodlo i czym jest to, co renderer faktycznie czyta."""

    source_asset_kind: str
    display_asset_kind: str
    display_requires_jp2_decode: bool
    finest_display_factor: int = 1
    detail: dict[str, Any] = field(default_factory=dict)


def classify_display_assets(
    *,
    source_path: Path | None,
    display_path: Path | None,
    raster_kind: str | None,
    scene_info: dict[str, Any] | None,
    overview_factors: list[int] | None = None,
) -> DisplayAssets:
    """Rozpoznaj graf odczytu: co jest zrodlem, a co aktywnym assetem wyswietlania."""

    scene_info = scene_info or {}
    source_suffix = (source_path.suffix.lower() if source_path else "")
    display_suffix = (display_path.suffix.lower() if display_path else "")

    if source_suffix in _JP2_SUFFIXES:
        source_kind = SOURCE_KIND_JP2
    elif source_suffix in _GEOTIFF_SUFFIXES:
        source_kind = SOURCE_KIND_GEOTIFF
    else:
        source_kind = SOURCE_KIND_OTHER

    same_asset = display_path is None or source_path is None or display_path == source_path
    if same_asset:
        display_kind = DISPLAY_KIND_SOURCE
    elif display_suffix == ".vrt":
        # VRT jest lekkim opakowaniem: o kosztach decyduje to, co jest pod spodem.
        display_kind = (
            DISPLAY_KIND_JP2_BACKED_VRT if source_kind == SOURCE_KIND_JP2 else DISPLAY_KIND_GEOTIFF
        )
    elif display_suffix == ".ovr":
        # Project-local JP2 preview is a self-contained GeoTIFF.  At permitted zooms
        # it never opens the source codestream and belongs in the normal TIFF lane.
        display_kind = DISPLAY_KIND_PREVIEW_GEOTIFF
    elif display_suffix in _GEOTIFF_SUFFIXES:
        # Pelnorozdzielczy derywat jest samodzielnym GeoTIFF-em — zrodlo przestaje byc
        # czytane przy wyswietlaniu, wiec jego format nie ma juz znaczenia dla kosztu.
        display_kind = DISPLAY_KIND_COG
    else:
        display_kind = DISPLAY_KIND_SOURCE

    requires_jp2 = source_kind == SOURCE_KIND_JP2 and display_kind in {
        DISPLAY_KIND_SOURCE,
        DISPLAY_KIND_JP2_BACKED_VRT,
    }

    finest = 1
    if display_kind == DISPLAY_KIND_PREVIEW_GEOTIFF:
        factors = [int(value) for value in (overview_factors or []) if int(value or 0) > 1]
        finest = min(factors) if factors else DEFAULT_JP2_BASE_FACTOR
    elif requires_jp2:
        # Male JP2 z uzytecznymi poziomami natywnymi czytaja sie w 1x wystarczajaco
        # szybko — nie ograniczamy ich.
        state = (scene_info.get("source_overviews") or {})
        display_ready = source_overviews_are_display_ready(
            state, width=scene_info.get("width"), height=scene_info.get("height")
        )
        if not display_ready:
            factors = [int(value) for value in (overview_factors or []) if int(value or 0) > 1]
            finest = min(factors) if factors else DEFAULT_JP2_BASE_FACTOR

    return DisplayAssets(
        source_asset_kind=source_kind,
        display_asset_kind=display_kind,
        display_requires_jp2_decode=requires_jp2,
        finest_display_factor=finest,
        detail={"source_suffix": source_suffix, "display_suffix": display_suffix},
    )


@dataclass(frozen=True)
class ZoomContract:
    """Cztery pola kontraktu z R0.1."""

    source_max_zoom: int
    available_native_zoom: int
    available_native_xyz_zoom: int | None
    display_asset_revision: str | None
    assets: DisplayAssets

    def allows(self, zoom: int) -> bool:
        return 0 <= zoom <= self.available_native_zoom

    def as_api_fields(self) -> dict[str, Any]:
        return {
            "source_max_zoom": self.source_max_zoom,
            "reference_zoom": self.source_max_zoom,
            "available_native_zoom": self.available_native_zoom,
            "available_native_xyz_zoom": self.available_native_xyz_zoom,
            "display_asset_revision": self.display_asset_revision,
            "source_asset_kind": self.assets.source_asset_kind,
            "display_asset_kind": self.assets.display_asset_kind,
            "display_requires_jp2_decode": self.assets.display_requires_jp2_decode,
        }


#: Stany derywatu pelnej rozdzielczosci (R0.4). `missing` znaczy "nie ma i nic nie trwa",
#: `stale` — "jest, ale nie odpowiada juz zrodlu albo profilowi".
FULLRES_MISSING = "missing"
FULLRES_QUEUED = "queued"
FULLRES_BUILDING = "building"
FULLRES_VALIDATING = "validating"
FULLRES_READY = "ready"
FULLRES_ERROR = "error"
FULLRES_STALE = "stale"

#: Statusy zadania, ktore maja pierwszenstwo nad wnioskowaniem z kontraktu — jesli cos
#: wlasnie trwa, to jest wazniejsze niz to, co da sie w tej chwili obsluzyc.
_JOB_DRIVEN_STATUSES = {FULLRES_QUEUED, FULLRES_BUILDING, FULLRES_VALIDATING, FULLRES_ERROR}


def derive_display_statuses(
    *,
    preview_status: str | None,
    contract: "ZoomContract",
    job_status: str | None = None,
) -> dict[str, Any]:
    """Rozdziel "podglad gotowy" od "dostepne pelne 1x" (R0.4).

    Dotychczasowy `overview_status` znaczyl oba naraz: dla duzego JP2 raportowal `ready`,
    gdy istniala piramida od 2x — czyli **obiecywal pelna rozdzielczosc, ktorej nie bylo**.
    Frontend mapowal to na "High-resolution view ready" i uzytkownik dowiadywal sie prawdy
    dopiero przy dojechaniu zoomem.

    `preview_status` zachowuje dotychczasowa semantyke i wartosci, wiec starsi klienci
    czytaja dokladnie to co wczesniej. Nowy jest `fullres_derivative_status`, wyprowadzany
    z kontraktu: 1x jest dostepne wtedy i tylko wtedy, gdy `available_native_zoom` siega
    poziomu referencyjnego.
    """
    if job_status in _JOB_DRIVEN_STATUSES:
        fullres = job_status
    elif contract.available_native_zoom >= contract.source_max_zoom:
        fullres = FULLRES_READY
    else:
        fullres = FULLRES_MISSING
    return {
        "preview_status": preview_status,
        "fullres_derivative_status": fullres,
        "available_native_zoom": contract.available_native_zoom,
    }


def compute_zoom_contract(
    *,
    scene_info: dict[str, Any] | None,
    assets: DisplayAssets,
    display_asset_revision: str | None = None,
    native_xyz_zoom: int | None = None,
) -> ZoomContract:
    """Zlóż kontrakt zoomu ze stalego ukladu i aktualnie dostepnego poziomu.

    `available_native_zoom` schodzi ponizej `source_max_zoom` dokladnie o tyle poziomow,
    ile wynosi log2 najdrobniejszego czynnika, ktory renderer potrafi obsluzyc. Dla
    wszystkiego poza wolnym JP2 jest to 1, wiec kontrakt nie zmienia niczego dla TIFF,
    SAR i NITF.
    """
    scene_info = scene_info or {}
    source_max_zoom = compute_source_max_zoom(scene_info.get("width", 0), scene_info.get("height", 0))
    steps = int(round(math.log2(max(1, assets.finest_display_factor))))
    available = max(0, source_max_zoom - steps)
    available_xyz = None
    if native_xyz_zoom is not None:
        available_xyz = max(0, int(native_xyz_zoom) - steps)
    return ZoomContract(
        source_max_zoom=source_max_zoom,
        available_native_zoom=available,
        available_native_xyz_zoom=available_xyz,
        display_asset_revision=display_asset_revision,
        assets=assets,
    )
