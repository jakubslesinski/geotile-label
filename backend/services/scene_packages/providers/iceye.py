"""Gramatyka nazw i wiazanie sidecarow ICEYE (DESIGN_DECISIONS.md, scene-import P0.3).

Sekcja 4.3 opisuje dwie usterki naraz i obie maja to samo zrodlo — brak modelu produktu:

1. **Scalenie topologiczne.** Arkusz zestawienia z `ICEYE` w nazwie, lezacy w korzeniu zrodla,
   wystarczyl, zeby `_looks_like_package_root()` uznal CALY katalog za jeden pakiet: 432 rastry
   i 611 plikow metadanych w jednej "scenie".
2. **Scalenie metadanych.** Skoro pakiet byl jeden, wszystkie sidecary trafialy do jednego
   wywolania parsera. Stad 914 konfliktow w 84 scenach i przykladowy GRD z `product_level=VID`
   — poziom przetworzenia przyszedl z cudzego pliku.

Ten modul dostarcza brakujacy element: czytanie nazwy produktu ICEYE. Nazwa niesie komplet
informacji potrzebnych do rozdzielenia — poziom przetworzenia, identyfikator zadania,
znacznik czasu i polaryzacje — wiec wiazanie sidecara z rastrem nie wymaga otwierania plikow.

Konwencja nazw ICEYE (obie generacje dostaw):

    ICEYE_X12_GRD_SM_7654321_20240605T091200        aktualna, z identyfikatorem satelity
    ICEYE_GRD_SLH_1234567_20240604T101500           starsza, bez niego

Modul jest CZYSTY: operuje na napisach, nie dotyka dysku.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from services.scene_packages.providers import binding

#: Poziomy przetworzenia ICEYE. Kolejnosc odpowiada preferencji przy etykietowaniu:
#: GRD jest produktem obrazowym w geometrii naziemnej, ORT jest ortorektyfikowany,
#: a SLC/CSI/VID nie sa obrazami do etykietowania w rozumieniu aplikacji.
PRODUCT_LEVELS = ("GRD", "ORT", "SLC", "CSI", "VID")

#: Poziomy, ktore moga zostac wybrane automatycznie jako scena do etykietowania.
LABELING_LEVELS = ("GRD", "ORT")

POLARIZATIONS = ("HH", "HV", "VH", "VV")

#: Rozszerzenia produktow zlozonych ICEYE. SLC bywa dostarczany jako HDF5 — bramka P0.3
#: wymaga wprost, zeby inwentarz NIE probowal otworzyc go jako obrazu do etykietowania.
COMPLEX_PRODUCT_SUFFIXES = {".h5", ".hdf5", ".nc"}

_LEVEL_PATTERN = re.compile(r"(?:^|[_\-])(" + "|".join(PRODUCT_LEVELS) + r")(?=[_\-.]|$)", re.IGNORECASE)
_POLARIZATION_PATTERN = re.compile(r"(?:^|[_\-])(" + "|".join(POLARIZATIONS) + r")(?=[_\-.]|$)")
_DATETIME_PATTERN = re.compile(r"(\d{8}T\d{6})")
#: Identyfikator zadania: dluga liczba, ktora nie jest data. Wymaganie >= 5 cyfr odsiewa
#: numery satelity (`X12`) i numery czesci, a jednoczesnie miesci wszystkie widziane taskId.
_TASK_ID_PATTERN = re.compile(r"(?:^|[_\-])(\d{5,})(?=[_\-.]|$)")


@dataclass(frozen=True)
class IceyeProductKey:
    """Tozsamosc produktu odczytana z nazwy pliku.

    `acquisition_key` celowo NIE zawiera poziomu przetworzenia ani polaryzacji: GRD, SLC i VID
    z jednego przelotu sa tym samym zdarzeniem obserwacyjnym i maja trafic pod jedna akwizycje.
    Rozdziela je dopiero `product_key`.
    """

    task_id: str | None = None
    datetime_utc: str | None = None
    product_level: str | None = None
    polarization: str | None = None

    @property
    def acquisition_key(self) -> str | None:
        parts = [part for part in (self.task_id, self.datetime_utc) if part]
        return "_".join(parts) if parts else None

    @property
    def product_key(self) -> str | None:
        """Klucz produktu. `None`, gdy nazwa nie pozwala go ustalic — wtedy NIE wiazemy."""
        if not self.acquisition_key:
            return None
        return "_".join(
            part for part in (self.acquisition_key, self.product_level, self.polarization) if part
        )

    @property
    def is_labeling_level(self) -> bool:
        return self.product_level in LABELING_LEVELS


def parse_product_name(value: str) -> IceyeProductKey:
    """Odczytaj tozsamosc produktu z nazwy pliku lub sciezki.

    Bierzemy sam trzon nazwy — katalog nadrzedny bywa powtorzeniem nazwy produktu i liczenie
    tokenow dwa razy niczego nie wnosi, a moze wprowadzic sprzecznosc.
    """
    stem = PurePosixPath(str(value).replace("\\", "/")).name
    for suffix in (".tif", ".tiff", ".xml", ".json", ".geojson", ".h5", ".hdf5", ".nc", ".png", ".kml"):
        if stem.casefold().endswith(suffix):
            stem = stem[: -len(suffix)]
            break

    datetime_match = _DATETIME_PATTERN.search(stem)
    datetime_utc = datetime_match.group(1) if datetime_match else None

    # Identyfikator zadania szukamy PO usunieciu znacznika czasu, inaczej `20240604T101500`
    # rozpadloby sie na osmiocyfrowa liczbe wygladajaca jak taskId.
    without_datetime = _DATETIME_PATTERN.sub("", stem)
    task_match = _TASK_ID_PATTERN.search(without_datetime)
    task_id = task_match.group(1) if task_match else None

    level_match = _LEVEL_PATTERN.search(stem)
    product_level = level_match.group(1).upper() if level_match else None

    polarization_match = _POLARIZATION_PATTERN.search(stem.upper())
    polarization = polarization_match.group(1) if polarization_match else None

    return IceyeProductKey(
        task_id=task_id,
        datetime_utc=datetime_utc,
        product_level=product_level,
        polarization=polarization,
    )


def is_complex_product(relative_path: str) -> bool:
    """Czy plik jest produktem zlozonym (SLC/CSI w kontenerze naukowym), a nie obrazem."""
    return PurePosixPath(str(relative_path).replace("\\", "/")).suffix.casefold() in COMPLEX_PRODUCT_SUFFIXES


def binding_key(relative_path: str) -> binding.BindingKey:
    """Klucze wiazania dla jednego pliku ICEYE."""
    key = parse_product_name(relative_path)
    return binding.BindingKey(
        product=key.product_key,
        acquisition=key.acquisition_key,
        declares_product=key.product_level is not None,
    )


def bind_metadata_to_products(
    measurement_paths: "list[str] | tuple[str, ...]",
    metadata_paths: "list[str] | tuple[str, ...]",
) -> dict[str, list[str]]:
    """Przypisz kazdy sidecar ICEYE do rastra, ktorego dotyczy.

    Algorytm jest wspolny dla dostawcow (`providers/binding.py`); ICEYE wnosi tu wylacznie
    gramatyke nazw. Sidecar VID nigdy nie zwiaze sie z rastrem GRD, bo deklaruje wlasny
    poziom przetworzenia — a to wlasnie ta pomylka dala `product_level=VID` na produkcie GRD.
    """
    return binding.bind(measurement_paths, metadata_paths, binding_key)


def orphan_metadata(
    measurement_paths: "list[str] | tuple[str, ...]",
    metadata_paths: "list[str] | tuple[str, ...]",
) -> list[str]:
    """Sidecary nienalezace do zadnego produktu ICEYE w paczce."""
    return binding.orphans(measurement_paths, metadata_paths, binding_key)
