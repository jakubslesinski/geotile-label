"""Gramatyka nazw i grupowanie akwizycji Airbus DIMAP (DESIGN_DECISIONS.md, scene-import P0.5).

Sekcja 4.5 wymienia trzy obserwacje z rzeczywistego zrodla; wszystkie sprowadzaja sie do tego,
ze resolver rozpoznawal WYLACZNIE jeden wzorzec nazwy PNEO:

- dziesiec dostaw ma identyfikatory `PHR1A`/`PHR1B` i konczy jako `wymaga decyzji`,
- zadeklarowanie zrodla jako PNEO zapisuje bledny sensor,
- katalog z niepelna ekstrakcja przechodzil bez sladu (zamkniete w P1.3a).

Konwencja nazw jest wspolna dla obu misji, ale KOLEJNOSC tokenow sie rozni:

    IMG_PNEO4_202305150851506_PMS-FS_ORT_698037a1-...-2bfb3dc1ee9f_RGB_R1C1.TIF
        satelita, czas,       spektrum, poziom, uuid,             wariant, kafel

    IMG_PHR1B_PMS_202111010946043_ORT_2a562ee2-...-5d6e9fd4c711_R1C1.TIF
        satelita, spektrum, czas,     poziom, uuid,            kafel

Dlatego nie parsujemy pozycyjnie, tylko szukamy kazdego tokenu osobno.

**Kluczem produktu jest UUID z nazwy pliku, nie zestaw tokenow.** Dostawca nadaje ten sam
identyfikator plikowi `DIM_*` i wszystkim `IMG_*` tego produktu, wiec wiazanie metadanych
sprowadza sie do porownania uuid — bez heurystyk na wspolnym przedrostku. PNEO PMS-FS
dostarcza DWA pliki obrazowe tego samego produktu (`_RGB` i `_NED`) opisane jednym DIMAP-em;
rozdziela je wariant, a laczy akwizycja.

Modul jest CZYSTY: operuje na napisach, nie dotyka dysku.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from services.scene_packages.providers import binding

#: Misje rozpoznawane w nazwach plikow.
SATELLITES = ("PNEO3", "PNEO4", "PHR1A", "PHR1B")

#: Warianty spektralne. `PMS-FS` i `PMS` to produkty pansharpened (obraz do etykietowania),
#: `MS-FS`/`MS` to same pasma wielospektralne, `P`/`PAN` to panchromatyczny.
SPECTRAL_MODES = ("PMS-FS", "MS-FS", "PMS", "MS", "PAN", "P")

#: Warianty pasmowe PNEO. `RGB` to barwy naturalne; `NED` (NIR / Red Edge / Deep Blue) jest
#: obrazem w barwach umownych i NIE jest domyslnym produktem do etykietowania.
BAND_VARIANTS = ("RGB", "NED")

#: Kolejnosc preferencji przy wyborze produktu do etykietowania.
LABELING_SPECTRAL_PREFERENCE = ("PMS-FS", "PMS")

_SATELLITE_PATTERN = re.compile(r"(?:^|[_\-])(" + "|".join(SATELLITES) + r")(?=[_\-.]|$)", re.IGNORECASE)
_SPECTRAL_PATTERN = re.compile(r"(?:^|[_\-])(" + "|".join(SPECTRAL_MODES) + r")(?=[_\-.]|$)", re.IGNORECASE)
_VARIANT_PATTERN = re.compile(r"(?:^|[_\-])(" + "|".join(BAND_VARIANTS) + r")(?=[_\-.]|$)")
_LEVEL_PATTERN = re.compile(r"(?:^|[_\-])(ORT|PRJ|SEN)(?=[_\-.]|$)", re.IGNORECASE)
#: Znacznik czasu Airbusa: `YYYYMMDDHHMMSSt` — 15 cyfr, z dziesiatymi czesciami sekundy.
_TIMESTAMP_PATTERN = re.compile(r"(?:^|[_\-])(\d{15})(?=[_\-.]|$)")
_UUID_PATTERN = re.compile(
    r"(?:^|[_\-])([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?=[_\-.]|$)",
    re.IGNORECASE,
)
_PART_PATTERN = re.compile(r"(?:^|[_\-])(R\d+C\d+)(?=[_\-.]|$)", re.IGNORECASE)


@dataclass(frozen=True)
class AirbusKey:
    """Rozlozona nazwa pliku Airbusa.

    `acquisition_key` celowo NIE zawiera wariantu pasmowego ani kafla: `_RGB` i `_NED` sa
    dwoma produktami tej samej akwizycji, a `R1C1`/`R1C2` czesciami jednego produktu.
    """

    satellite: str | None = None
    mission: str | None = None
    spectral: str | None = None
    level: str | None = None
    timestamp: str | None = None
    product_uuid: str | None = None
    variant: str | None = None
    part: str | None = None

    @property
    def acquisition_key(self) -> str | None:
        if not self.satellite or not self.timestamp:
            return None
        return f"{self.satellite}_{self.timestamp}"

    @property
    def product_key(self) -> str | None:
        """UUID produktu plus wariant pasmowy, gdy dostawa go rozroznia."""
        if not self.product_uuid:
            return None
        return f"{self.product_uuid}_{self.variant}" if self.variant else self.product_uuid


def parse_path(relative_path: str) -> AirbusKey:
    """Rozloz nazwe pliku Airbusa. Czyta CALA sciezke — uuid bywa tylko w nazwie pliku."""
    text = str(relative_path or "").replace("\\", "/")
    name = PurePosixPath(text).name

    satellite_match = _SATELLITE_PATTERN.search(text)
    satellite = satellite_match.group(1).upper() if satellite_match else None
    mission = None
    if satellite:
        mission = "PNEO" if satellite.startswith("PNEO") else "PHR"

    spectral_match = _SPECTRAL_PATTERN.search(name)
    level_match = _LEVEL_PATTERN.search(name)
    timestamp_match = _TIMESTAMP_PATTERN.search(name)
    uuid_match = _UUID_PATTERN.search(name)
    variant_match = _VARIANT_PATTERN.search(name)
    part_match = _PART_PATTERN.search(name)

    return AirbusKey(
        satellite=satellite,
        mission=mission,
        spectral=spectral_match.group(1).upper() if spectral_match else None,
        level=level_match.group(1).upper() if level_match else None,
        timestamp=timestamp_match.group(1) if timestamp_match else None,
        product_uuid=uuid_match.group(1).lower() if uuid_match else None,
        variant=variant_match.group(1).upper() if variant_match else None,
        part=part_match.group(1).upper() if part_match else None,
    )


def binding_key(relative_path: str) -> binding.BindingKey:
    """Klucz wiazania metadanych.

    Sidecar `DIM_*` nie ma wariantu pasmowego, wiec nie deklaruje produktu — trafia do
    wszystkich produktow swojej akwizycji. Jest to poprawne: jeden DIMAP PNEO opisuje
    zarowno plik `_RGB`, jak i `_NED`.
    """
    key = parse_path(relative_path)
    return binding.BindingKey(
        product=key.product_key,
        acquisition=key.acquisition_key,
        declares_product=key.variant is not None,
    )


def bind_metadata_to_products(
    measurement_paths: "list[str] | tuple[str, ...]",
    metadata_paths: "list[str] | tuple[str, ...]",
) -> dict[str, list[str]]:
    return binding.bind(measurement_paths, metadata_paths, binding_key)


def orphan_metadata(
    measurement_paths: "list[str] | tuple[str, ...]",
    metadata_paths: "list[str] | tuple[str, ...]",
) -> list[str]:
    return binding.orphans(measurement_paths, metadata_paths, binding_key)


def group_by_product(paths: "list[str] | tuple[str, ...]") -> dict[str, list[str]]:
    """Pogrupuj rastry po produkcie (uuid + wariant). Bez uuid — po nazwie pliku."""
    groups: dict[str, list[str]] = {}
    for path in paths:
        key = parse_path(str(path)).product_key or PurePosixPath(str(path)).name
        groups.setdefault(key, []).append(str(path))
    for value in groups.values():
        value.sort()
    return groups


def is_labeling_product(relative_path: str) -> bool:
    """Czy plik jest kandydatem na scene do etykietowania.

    Odrzucamy wariant `NED`: to obraz w barwach umownych (NIR / Red Edge / Deep Blue),
    a nie barwy naturalne. Pozostaje dostepny jako alternatywa, ale nie jest wyborem
    domyslnym.
    """
    key = parse_path(relative_path)
    if key.variant == "NED":
        return False
    return key.spectral in LABELING_SPECTRAL_PREFERENCE


def spectral_rank(relative_path: str) -> int:
    """Pozycja wariantu spektralnego w kolejnosci preferencji; nizej znaczy lepiej."""
    spectral = parse_path(relative_path).spectral
    if spectral in LABELING_SPECTRAL_PREFERENCE:
        return LABELING_SPECTRAL_PREFERENCE.index(spectral)
    return len(LABELING_SPECTRAL_PREFERENCE)
