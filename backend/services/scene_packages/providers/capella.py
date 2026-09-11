"""Gramatyka nazw i grupowanie akwizycji Capella (DESIGN_DECISIONS.md, scene-import P0.4).

Sekcja 4.4 wymienia piec obserwacji z rzeczywistej dostawy. Po naprawie rol w S1 podglady
`_preview.tif` juz nie sa kandydatami, zostaja wiec trzy problemy, wszystkie z jednego braku
— resolver nie ma pojecia PRODUKTU ani AKWIZYCJI:

- rzeczywiste produkty to glownie `GEO`, a resolver rozpoznaje wylacznie `GEC`,
- co najmniej jeden folder zawiera wiecej niz jedna akwizycje i konczy jako jedna paczka,
- reczny wybor zostawia `product_type=UNRESOLVED` i moze zwiazac metadata z innej akwizycji.

Konwencja nazw Capella:

    CAPELLA_C08_SP_GEO_HH_20240604101500
    CAPELLA_C02_SP_GEC_HH_20230101_120000_120030      (wariant z zakresem czasu)

Rozdzielenie akwizycji opiera sie na parze (satelita, znacznik czasu). Sam znacznik czasu nie
wystarcza: dwa satelity moga zbierac o tej samej sekundzie, a sam satelita tym bardziej nie —
`CAPELLA_04_06_2024` z sekcji 4.4 zawiera dwie akwizycje roznych satelitow.

Modul jest CZYSTY: operuje na napisach, nie dotyka dysku.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from services.scene_packages.providers import binding

#: Typy produktow Capella. GEO i GEC to obrazy w geometrii naziemnej; SLC, SICD, SIDD i CPHD
#: sa produktami zespolonymi albo radarowo-specyficznymi i nie sa obrazami do etykietowania.
PRODUCT_TYPES = ("GEO", "GEC", "SIDD", "SICD", "SLC", "CPHD", "VS")

#: Produkty, ktore moga zostac wybrane automatycznie jako scena do etykietowania.
LABELING_PRODUCTS = ("GEO", "GEC")

#: Domyslna kolejnosc preferencji. GEO stoi PIERWSZE, bo tak wyglada rzeczywista dostawa
#: z sekcji 4.4 — zalozenie, ze GEC istnieje, bylo zrodlem usterki, a nie jej objawem.
DEFAULT_PRODUCT_PREFERENCE = ("GEO", "GEC")

#: Nadpisanie kolejnosci bez zmiany kodu — zakres P0.4 wymaga, zeby preferencja produktu byla
#: konfigurowalna. Wartosc to lista rozdzielona przecinkami, np. "GEC,GEO".
PRODUCT_PREFERENCE_ENV = "GEOTILE_CAPELLA_PRODUCT_PREFERENCE"

POLARIZATIONS = ("HH", "HV", "VH", "VV")

_PRODUCT_PATTERN = re.compile(r"(?:^|[_\-])(" + "|".join(PRODUCT_TYPES) + r")(?=[_\-.]|$)", re.IGNORECASE)
_SATELLITE_PATTERN = re.compile(r"(?:^|[_\-])(C\d{2})(?=[_\-.]|$)", re.IGNORECASE)
_POLARIZATION_PATTERN = re.compile(r"(?:^|[_\-])(" + "|".join(POLARIZATIONS) + r")(?=[_\-.]|$)")
#: Znacznik czasu: `20240604101500` albo `20240604_120000`. Bierzemy PIERWSZY — w wariancie
#: z zakresem drugi jest czasem konca zbierania i nie identyfikuje akwizycji.
_TIMESTAMP_PATTERN = re.compile(r"(\d{8})[T_]?(\d{6})(?=[_\-.]|$)")

_SIDECAR_SUFFIXES = (
    "_extended.json",
    "_extended",
    ".json",
    ".tif",
    ".tiff",
    ".xml",
    ".png",
    ".kml",
)


def product_preference() -> tuple[str, ...]:
    """Kolejnosc preferencji produktow, z uwzglednieniem nadpisania srodowiskowego.

    Nierozpoznane tokeny sa ignorowane, a produkty spoza listy dopisywane na koncu w
    kolejnosci domyslnej — zla konfiguracja ma zawezic wybor, a nie wywrocic import.
    """
    raw = str(os.environ.get(PRODUCT_PREFERENCE_ENV) or "").strip()
    if not raw:
        return DEFAULT_PRODUCT_PREFERENCE
    configured = [token.strip().upper() for token in raw.split(",") if token.strip()]
    ordered = [token for token in configured if token in LABELING_PRODUCTS]
    ordered += [token for token in DEFAULT_PRODUCT_PREFERENCE if token not in ordered]
    return tuple(ordered)


@dataclass(frozen=True)
class CapellaProductKey:
    """Tozsamosc produktu Capella odczytana z nazwy pliku."""

    satellite: str | None = None
    timestamp: str | None = None
    product_type: str | None = None
    polarization: str | None = None

    @property
    def acquisition_key(self) -> str | None:
        """Klucz akwizycji: satelita + znacznik czasu, BEZ typu produktu.

        GEO i GEC tego samego przelotu sa dwoma produktami jednej akwizycji, wiec typ
        produktu nie moze wchodzic do tego klucza.
        """
        parts = [part for part in (self.satellite, self.timestamp) if part]
        return "_".join(parts) if parts else None

    @property
    def product_key(self) -> str | None:
        if not self.acquisition_key:
            return None
        return "_".join(
            part for part in (self.acquisition_key, self.product_type, self.polarization) if part
        )

    @property
    def is_labeling_product(self) -> bool:
        return self.product_type in LABELING_PRODUCTS


def parse_product_name(value: str) -> CapellaProductKey:
    """Odczytaj tozsamosc produktu Capella z nazwy pliku lub sciezki."""
    stem = PurePosixPath(str(value).replace("\\", "/")).name
    lowered = stem.casefold()
    for suffix in _SIDECAR_SUFFIXES:
        if lowered.endswith(suffix):
            stem = stem[: -len(suffix)]
            break

    timestamp_match = _TIMESTAMP_PATTERN.search(stem)
    timestamp = "".join(timestamp_match.groups()) if timestamp_match else None

    satellite_match = _SATELLITE_PATTERN.search(stem)
    satellite = satellite_match.group(1).upper() if satellite_match else None

    product_match = _PRODUCT_PATTERN.search(stem)
    product_type = product_match.group(1).upper() if product_match else None

    polarization_match = _POLARIZATION_PATTERN.search(stem.upper())
    polarization = polarization_match.group(1) if polarization_match else None

    return CapellaProductKey(
        satellite=satellite,
        timestamp=timestamp,
        product_type=product_type,
        polarization=polarization,
    )


def group_by_acquisition(paths: "list[str] | tuple[str, ...]") -> dict[str, list[str]]:
    """Pogrupuj rastry wedlug akwizycji, zachowujac kolejnosc wejsciowa.

    Pliki bez rozpoznawalnego klucza trafiaja do grupy `""`. Nie sa scalane z zadna akwizycja
    ani miedzy soba udawane za jedna — wolajacy decyduje, co z nimi zrobic.
    """
    grouped: dict[str, list[str]] = {}
    for path in paths:
        key = parse_product_name(str(path)).acquisition_key or ""
        grouped.setdefault(key, []).append(str(path))
    return grouped


def preferred_products(paths: "list[str] | tuple[str, ...]") -> tuple[str | None, list[str]]:
    """Wybierz z jednej akwizycji produkty najbardziej preferowanego dostepnego typu.

    Zwraca `(typ, sciezki)`. Gdy zaden plik nie ma rozpoznawalnego typu etykietowalnego,
    zwraca `(None, [])` — brak wyboru jest tu poprawna odpowiedzia i prowadzi do decyzji
    uzytkownika, a nie do zgadywania.
    """
    by_type: dict[str, list[str]] = {}
    for path in paths:
        key = parse_product_name(str(path))
        if key.is_labeling_product and key.product_type:
            by_type.setdefault(key.product_type, []).append(str(path))
    for product_type in product_preference():
        if by_type.get(product_type):
            return product_type, by_type[product_type]
    return None, []


def binding_key(relative_path: str) -> binding.BindingKey:
    """Klucze wiazania dla jednego pliku Capella."""
    key = parse_product_name(relative_path)
    return binding.BindingKey(
        product=key.product_key,
        acquisition=key.acquisition_key,
        declares_product=key.product_type is not None,
    )


def bind_metadata_to_products(
    measurement_paths: "list[str] | tuple[str, ...]",
    metadata_paths: "list[str] | tuple[str, ...]",
) -> dict[str, list[str]]:
    """Przypisz `extended.json` i pozostale sidecary do rastra tej samej akwizycji."""
    return binding.bind(measurement_paths, metadata_paths, binding_key)


def orphan_metadata(
    measurement_paths: "list[str] | tuple[str, ...]",
    metadata_paths: "list[str] | tuple[str, ...]",
) -> list[str]:
    return binding.orphans(measurement_paths, metadata_paths, binding_key)
