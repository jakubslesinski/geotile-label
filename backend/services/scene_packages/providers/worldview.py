"""Gramatyka nazw i komponentow WorldView (DESIGN_DECISIONS.md, scene-import P0.6).

Sekcja 4.6 opisuje dwie natywne dostawy WV2 i cztery problemy. Po S1 podglady `LAYOUT.JPG`
i `BROWSE.JPG` juz nie sa kandydatami, wiec zostaja trzy:

1. **Brak galezi PAN-only.** Dostawa `014679...` jest poprawna scena panchromatyczna
   z szescioma czesciami, a konczy jako `decision_required` — resolver ma tylko sciezke
   "gotowy RGB" i "MUL + PAN".
2. **Metadata scalone miedzy komponentami.** `.IMD` produktu MUL i `.IMD` produktu PAN maja te
   sama role i nic nie wiaze ich z komponentem, wiec parser scala je w jeden obiekt. Wynikiem
   jest szesc POZORNYCH konfliktow (`band_id`, GSD, czas rozniacy sie o mikrosekundy),
   a wynikowa metadata opisuje glownie PAN, mimo ze working view ma byc pansharpened RGB.
3. **Niepelna ekstrakcja nierozroznialna od poprawnej sceny.** Trzy pliki `R1C1..R2C1` wygladaja
   jak kompletna dostawa trzyczesciowa. Odroznia je wylacznie TIL — patrz `tile_manifests.py`.

Konwencja nazw WorldView:

    014670314010_01/_MUL/14JUN11_MUL_R1C1.TIF      komponent w nazwie katalogu I pliku
    014679500010_01/_PAN/18APR08_PAN.TIL           sidecar komponentu

Modul jest CZYSTY: operuje na napisach, nie dotyka dysku.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from services.scene_packages.providers import binding

COMPONENT_MULTISPECTRAL = "multispectral"
COMPONENT_PANCHROMATIC = "panchromatic"
COMPONENT_PANSHARPENED = "pansharpened"

#: Tokeny komponentu, jakich uzywa dostawca — w nazwie katalogu (`_MUL/`) i pliku (`_MUL_`).
_COMPONENT_TOKENS = (
    (COMPONENT_PANSHARPENED, ("PSH", "PANSHARP", "M1BS_PSH")),
    (COMPONENT_MULTISPECTRAL, ("MUL", "M2AS", "M1BS", "MULTI")),
    (COMPONENT_PANCHROMATIC, ("PAN", "P1BS", "P2AS")),
)

#: Kolejnosc sprawdzania jest istotna: `PANSHARP` zawiera `PAN`, wiec musi zostac rozpoznany
#: pierwszy. Z tego samego powodu dopasowanie jest tokenowe, a nie przez `in`.
_COMPONENT_PATTERNS = tuple(
    (component, re.compile(r"(?:^|[_\-/])(" + "|".join(tokens) + r")(?=[_\-./]|$)", re.IGNORECASE))
    for component, tokens in _COMPONENT_TOKENS
)

_PART_PATTERN = re.compile(r"R(\d+)C(\d+)", re.IGNORECASE)
#: Identyfikator zamowienia: dlugi ciag cyfr w nazwie katalogu dostawy (`014670314010_01`).
_ORDER_PATTERN = re.compile(r"(?<!\d)(\d{10,}(?:_\d{2})?)(?!\d)")
#: Data i opcjonalny czas w nazwie pliku dostawcy: `14JUN11102144`, `18APR08100408`.
_DATE_PATTERN = re.compile(
    r"(?:^|[_\-])(\d{2}[A-Z]{3}\d{2})(\d{6})?(?=[_\-.]|$)",
    re.IGNORECASE,
)
#: Identyfikator produktu w ramach zamowienia. Dwa produkty `P001`/`P002` tej samej
#: akwizycji nie moga zostac przypadkowo zlozone w jedna mozaike.
_PRODUCT_PATTERN = re.compile(r"(?:^|[_\-])(P\d{3})(?=[_\-./]|$)", re.IGNORECASE)


@dataclass(frozen=True)
class WorldViewKey:
    """Tozsamosc pliku WorldView odczytana ze sciezki wzglednej."""

    order_id: str | None = None
    capture_date: str | None = None
    capture_time: str | None = None
    product_id: str | None = None
    component: str | None = None
    part_id: str | None = None

    @property
    def acquisition_key(self) -> str | None:
        """Klucz akwizycji: zamowienie + data, BEZ komponentu.

        MUL i PAN tej samej dostawy sa dwoma komponentami JEDNEJ akwizycji, wiec komponent
        nie moze wchodzic do tego klucza — inaczej pansharpening nie mialby czego laczyc.
        """
        capture = "".join(part for part in (self.capture_date, self.capture_time) if part) or None
        parts = [part for part in (self.order_id, capture, self.product_id) if part]
        return "_".join(parts) if parts else None

    @property
    def product_key(self) -> str | None:
        """Klucz produktu: akwizycja + komponent. To on rozdziela metadane MUL od PAN."""
        if not self.acquisition_key or not self.component:
            return None
        return f"{self.acquisition_key}_{self.component}"


def parse_path(relative_path: str) -> WorldViewKey:
    """Odczytaj tozsamosc z CALEJ sciezki wzglednej, nie z samej nazwy pliku.

    U WorldView komponent bywa zapisany wylacznie w nazwie katalogu (`_MUL/`, `_PAN/`),
    a identyfikator zamowienia wylacznie w katalogu dostawy — ograniczenie sie do nazwy pliku
    gubiloby jedno albo drugie.
    """
    path = str(relative_path).replace("\\", "/")
    name = PurePosixPath(path).name

    component = None
    for candidate, pattern in _COMPONENT_PATTERNS:
        if pattern.search(path):
            component = candidate
            break

    order_match = _ORDER_PATTERN.search(path)
    date_match = _DATE_PATTERN.search(name.upper())
    product_match = _PRODUCT_PATTERN.search(path)
    part_match = _PART_PATTERN.search(name)

    return WorldViewKey(
        order_id=order_match.group(1) if order_match else None,
        capture_date=date_match.group(1).upper() if date_match else None,
        capture_time=date_match.group(2) if date_match else None,
        product_id=product_match.group(1).upper() if product_match else None,
        component=component,
        part_id=f"R{int(part_match.group(1))}C{int(part_match.group(2))}" if part_match else None,
    )


def component_of(relative_path: str) -> str | None:
    return parse_path(relative_path).component


def binding_key(relative_path: str) -> binding.BindingKey:
    """Klucze wiazania dla jednego pliku WorldView.

    `declares_product=True` dla pliku z rozpoznanym komponentem jest tu sednem naprawy:
    `.IMD` komponentu PAN deklaruje swoj produkt, wiec nie moze zwiazac sie z rastrem MUL
    przez sam klucz akwizycji. To wlasnie ta droga dawala szesc pozornych konfliktow.
    """
    key = parse_path(relative_path)
    return binding.BindingKey(
        product=key.product_key,
        acquisition=key.acquisition_key,
        declares_product=key.component is not None,
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


def group_by_component(paths: "list[str] | tuple[str, ...]") -> dict[str, list[str]]:
    """Pogrupuj sciezki wedlug komponentu; nierozpoznane trafiaja pod klucz `""`."""
    grouped: dict[str, list[str]] = {}
    for path in paths:
        grouped.setdefault(component_of(str(path)) or "", []).append(str(path))
    return grouped


def group_by_acquisition(paths: "list[str] | tuple[str, ...]") -> dict[str, list[str]]:
    """Pogrupuj pomiary w logiczne sceny WorldView.

    Klucz zawiera zamowienie, znacznik czasu i `Pnnn`. Komponent jest celowo pominiety:
    MUL i PAN jednego produktu musza trafic do tej samej grupy, natomiast dwa produkty
    lub dwa przeloty w plaskim katalogu nie moga zostac polaczone.
    """
    grouped: dict[str, list[str]] = {}
    for path in paths:
        grouped.setdefault(parse_path(str(path)).acquisition_key or "", []).append(str(path))
    return grouped
