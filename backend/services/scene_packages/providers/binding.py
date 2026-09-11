"""Wiazanie sidecarow do produktow — algorytm wspolny dla dostawcow (P0.3, P0.4).

Algorytm powstal przy ICEYE (sekcja 4.3: sidecar VID nadajacy produktowi GRD
`product_level=VID`), ale problem nie jest specyficzny dla ICEYE. Capella ma go w wariancie
miedzy-akwizycyjnym (sekcja 4.4: "moze powiazac metadata z innej akwizycji"), a WorldView
w wariancie miedzy-komponentowym (sekcja 4.6). Rozni je wylacznie GRAMATYKA NAZW, nie sposob
dopasowania — dlatego sam algorytm mieszka tutaj, a kazdy dostawca dostarcza funkcje klucza.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable


@dataclass(frozen=True)
class BindingKey:
    """Klucze jednego pliku, od najmocniejszego do najslabszego.

    `declares_product` mowi, czy nazwa sama deklaruje produkt. Sidecar, ktory go deklaruje,
    NIE moze zwiazac sie przez sam klucz akwizycji — inaczej metadane produktu VID trafilyby
    na produkt GRD z tego samego przelotu.
    """

    product: str | None = None
    acquisition: str | None = None
    declares_product: bool = False


KeyFunction = Callable[[str], BindingKey]


def _stem(path: str) -> str:
    return PurePosixPath(str(path).replace("\\", "/")).stem.casefold()


def bind(
    measurement_paths: "list[str] | tuple[str, ...]",
    metadata_paths: "list[str] | tuple[str, ...]",
    key_of: KeyFunction,
) -> dict[str, list[str]]:
    """Przypisz kazdy sidecar do rastra, ktorego dotyczy.

    Zwraca mape `raster -> sidecary`. Sidecar, ktorego nie da sie przypisac jednoznacznie,
    nie jest przypisywany do niczego — to jest cala roznica wobec plaskiej puli metadanych.

    Kolejnosc dopasowania, od najmocniejszego:

    1. **identyczny trzon nazwy** — sidecar lezy obok rastra i nazywa sie tak samo,
    2. **klucz produktu** — ten sam produkt tej samej akwizycji,
    3. **klucz akwizycji** — tylko dla sidecara, ktory nie deklaruje wlasnego produktu.
    """
    bound: dict[str, list[str]] = {str(path): [] for path in measurement_paths}
    if not bound:
        return bound

    by_stem: dict[str, list[str]] = {}
    by_product: dict[str, list[str]] = {}
    by_acquisition: dict[str, list[str]] = {}
    for raster in bound:
        key = key_of(raster)
        by_stem.setdefault(_stem(raster), []).append(raster)
        if key.product:
            by_product.setdefault(key.product, []).append(raster)
        if key.acquisition:
            by_acquisition.setdefault(key.acquisition, []).append(raster)

    for metadata in metadata_paths:
        metadata = str(metadata)
        key = key_of(metadata)
        targets = by_stem.get(_stem(metadata))
        if not targets and key.product:
            targets = by_product.get(key.product)
        if not targets and not key.declares_product and key.acquisition:
            targets = by_acquisition.get(key.acquisition)
        for raster in targets or ():
            bound[raster].append(metadata)

    return bound


def orphans(
    measurement_paths: "list[str] | tuple[str, ...]",
    metadata_paths: "list[str] | tuple[str, ...]",
    key_of: KeyFunction,
) -> list[str]:
    """Sidecary, ktore nie naleza do ZADNEGO produktu w paczce.

    Rozroznienie jest istotne dla diagnostyki: sidecar innego produktu tej samej dostawy jest
    normalnym skladnikiem paczki wieloproduktowej, wiec ostrzeganie o nim byloby szumem.
    Ostrzezenia wart jest dopiero sidecar, ktory nie pasuje do niczego.
    """
    placed = {item for values in bind(measurement_paths, metadata_paths, key_of).values() for item in values}
    return sorted(set(str(path) for path in metadata_paths) - placed)
