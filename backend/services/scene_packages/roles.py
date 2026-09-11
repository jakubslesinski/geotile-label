"""Klasyfikacja rol assetow dostawy (DESIGN_DECISIONS.md, scene-import P0.2).

Problem, ktory ten modul rozwiazuje, jest opisany w sekcjach 4.4, 4.6 i 17.7.2: rola assetu
wynikala WYLACZNIE z rozszerzenia pliku (`RASTER_EXTENSIONS` → `raster_candidate`, reszta →
`metadata`). Skutkiem bylo, ze `BROWSE.JPG`, `LAYOUT.JPG` i `CAPELLA_..._preview.tif` stawaly
sie kandydatami na obraz do etykietowania, a `.IMD`, `.TIL` i `.RPB` byly nierozroznialne
miedzy soba i miedzy komponentami produktu.

W repozytorium istnieje juz czesciowy filtr `ProviderResolver._labeling_rasters()`, ale
nie jest uzywany przez fallback WorldView, a jego lista wykluczen ma dwie wady: brakuje w niej
`layout`, a jest w niej `pan` — czyli odrzucalaby legalne measurement assets panchromatyczne,
ktore P0.6 ma uznac za poprawna scene. Ten modul zastepuje te heurystyke jawnymi rolami.

Klasyfikacja jest CZYSTA: dziala na sciezce wzglednej i rozszerzeniu, bez otwierania pliku.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from services.scene_packages.contracts import (
    ROLE_ARCHIVE,
    ROLE_AUXILIARY,
    ROLE_BROWSE,
    ROLE_DELIVERY_METADATA,
    ROLE_FOOTPRINT,
    ROLE_LAYOUT,
    ROLE_MEASUREMENT,
    ROLE_PRODUCT_METADATA,
    ROLE_RPC,
    ROLE_TILE_MANIFEST,
    ROLE_UNKNOWN,
)

RASTER_SUFFIXES = {".tif", ".tiff", ".jp2", ".ntf", ".nitf", ".img"}
#: Formaty, ktore u dostawcow WYSTEPUJA wylacznie jako podglad. Nigdy nie sa measurement.
PREVIEW_ONLY_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".bmp"}
ARCHIVE_SUFFIXES = {".zip", ".tar", ".gz", ".7z", ".rar"}
FOOTPRINT_SUFFIXES = {".kml", ".kmz", ".shp", ".geojson"}

#: Rozszerzenia sidecarow o jednoznacznej roli u dostawcow EO.
SUFFIX_ROLES = {
    ".til": ROLE_TILE_MANIFEST,
    ".rpb": ROLE_RPC,
    ".rpc": ROLE_RPC,
    ".imd": ROLE_PRODUCT_METADATA,
    ".tfw": ROLE_AUXILIARY,
    ".j2w": ROLE_AUXILIARY,
}

#: Tokeny nazwy oznaczajace podglad. `preview` celowo dopasowuje sie takze jako sufiks
#: (`<produkt>_preview.tif`), bo tak wygladaja pliki Capelli.
_BROWSE_PATTERN = re.compile(
    r"(?:^|[_\-.])(?:browse|quicklook|thumb|thumbnail|overview_image|preview)(?:[_\-.]|$)",
    re.IGNORECASE,
)
_LAYOUT_PATTERN = re.compile(r"(?:^|[_\-.])layout(?:[_\-.]|$)", re.IGNORECASE)
#: Metadane opisujace CALA dostawe, nie pojedynczy produkt.
_DELIVERY_METADATA_PATTERN = re.compile(
    r"(?:^|[_\-])(?:deliverymetadata|readme|order|license|eula)(?:[_\-.]|$)",
    re.IGNORECASE,
)


def classify_asset_role(relative_path: str) -> str:
    """Zwroc role assetu na podstawie sciezki wzglednej wewnatrz dostawy.

    Kolejnosc regul jest istotna: nazwa ma pierwszenstwo przed rozszerzeniem, bo
    `<produkt>_preview.tif` jest rastrem, ale nie jest measurement.
    """
    path = PurePosixPath(str(relative_path).replace("\\", "/"))
    name = path.name
    suffix = path.suffix.casefold()

    if suffix in ARCHIVE_SUFFIXES:
        return ROLE_ARCHIVE
    if _LAYOUT_PATTERN.search(name):
        return ROLE_LAYOUT
    if _BROWSE_PATTERN.search(name):
        return ROLE_BROWSE
    if suffix in PREVIEW_ONLY_SUFFIXES:
        # JPG/PNG u dostawcow to material pomocniczy, nawet bez tokenu w nazwie.
        return ROLE_BROWSE
    if suffix in FOOTPRINT_SUFFIXES:
        return ROLE_FOOTPRINT
    if suffix in SUFFIX_ROLES:
        return SUFFIX_ROLES[suffix]
    if _DELIVERY_METADATA_PATTERN.search(name):
        return ROLE_DELIVERY_METADATA
    if suffix in RASTER_SUFFIXES:
        return ROLE_MEASUREMENT
    if suffix in {".xml", ".json", ".txt", ".html", ".htm"}:
        return ROLE_PRODUCT_METADATA
    return ROLE_UNKNOWN


def is_selectable_measurement(relative_path: str) -> bool:
    """Czy asset moze zostac wybrany jako obraz do etykietowania."""
    return classify_asset_role(relative_path) == ROLE_MEASUREMENT
