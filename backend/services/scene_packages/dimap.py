"""Odczyt kolejnosci kanalow z DIMAP Airbusa (DESIGN_DECISIONS.md, scene-import P0.5).

Kolejnosc pasm RGB **jest zadeklarowana w metadanych** i nie wolno jej zgadywac z konwencji.
Pomiar na rzeczywistych dostawach:

- PHR: `<Band_Display_Order>` podaje `B2/B1/B0`, a pasma pliku ida w kolejnosci `B0..B3`,
  wiec barwy naturalne to pasma **3, 2, 1**;
- PNEO PMS-FS: dostawa ma DWA pliki jednego produktu i **osobny blok `<Raster_Display>` na
  kazdy z nich** — dla `_RGB` jest to `R/G/B` z indeksami 1/2/3, dla `_NED` `NIR/RE/DB`,
  rowniez z indeksami 1/2/3 w obrebie swojego pliku.

Ta druga obserwacja jest powodem, dla ktorego indeksy czytamy per plik, a nie z globalnej
listy `<BAND_ID>`. Lista PNEO ma szesc pozycji (`R, G, B, NIR, RE, DB`), a kazdy plik ma trzy
pasma; przelozenie pozycji z listy na numer pasma byloby poprawne wylacznie dla pierwszego
pliku i po cichu bledne dla drugiego.

Modul czyta WYLACZNIE XML; nie otwiera rastrow.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

#: Gorny rozmiar czytanego DIMAP-u. Pliki w korpusie maja 22-28 kB.
MAX_DIMAP_BYTES = 8 * 1024 * 1024


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].upper()


@dataclass(frozen=True)
class FileBands:
    """Opis pasm JEDNEGO pliku obrazowego produktu."""

    display_order: tuple[str, ...] = ()
    band_index: dict[str, int] = field(default_factory=dict)
    #: Wartosci specjalne zadeklarowane przez dostawce, np. `{"NODATA": 0, "SATURATED": 255}`.
    special_values: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class DimapBands:
    """Co DIMAP mowi o pasmach produktu."""

    #: Identyfikatory pasm w kolejnosci wystapienia (`B0, B1, …` albo `R, G, B, …`).
    band_ids: tuple[str, ...] = ()
    #: Globalna kolejnosc kanalow, gdy dostawa nie rozroznia plikow (PHR).
    display_order: tuple[str, ...] = ()
    #: Opis per plik obrazowy (PNEO).
    files: dict[str, FileBands] = field(default_factory=dict)
    #: Wartosci specjalne wspolne dla calego dokumentu.
    special_values: dict[str, float] = field(default_factory=dict)
    error: str | None = None

    def nodata(self, filename: str | None = None) -> float | None:
        """Zadeklarowana wartosc `NODATA`, jesli dostawca ja podaje."""
        return self._special(filename, "NODATA")

    def saturated(self, filename: str | None = None) -> float | None:
        return self._special(filename, "SATURATED")

    def _special(self, filename: str | None, name: str) -> float | None:
        entry = self.files.get(PurePosixPath(str(filename or "")).name)
        if entry is not None and name in entry.special_values:
            return entry.special_values[name]
        return self.special_values.get(name)

    @property
    def is_readable(self) -> bool:
        return self.error is None

    def rgb_bands(self, filename: str | None = None) -> list[int] | None:
        """Numery pasm RGB dla wskazanego pliku produktu.

        Zwraca `None`, gdy metadane nie pozwalaja tego rozstrzygnac — wolajacy ma wtedy
        poprosic uzytkownika o potwierdzenie, a nie przyjac wartosc domyslna.
        """
        entry = self.files.get(PurePosixPath(str(filename or "")).name)
        if entry is not None and entry.display_order:
            bands = [entry.band_index.get(band_id) for band_id in entry.display_order]
            if len(bands) == 3 and all(value is not None for value in bands):
                return [int(value) for value in bands]  # type: ignore[arg-type]

        if not self.display_order or not self.band_ids:
            return None
        positions = {band_id: index for index, band_id in enumerate(self.band_ids, start=1)}
        bands = [positions.get(band_id) for band_id in self.display_order]
        if len(bands) != 3 or any(value is None for value in bands):
            return None
        return [int(value) for value in bands]  # type: ignore[arg-type]


EMPTY_BANDS = DimapBands()


def _display_order(node: ET.Element) -> tuple[str, ...]:
    for candidate in node.iter():
        if _local(candidate.tag) != "BAND_DISPLAY_ORDER":
            continue
        channels = {_local(child.tag): (child.text or "").strip() for child in candidate}
        ordered = [channels.get(key) for key in ("RED_CHANNEL", "GREEN_CHANNEL", "BLUE_CHANNEL")]
        if all(ordered):
            return tuple(str(value) for value in ordered)
    return ()


def _band_index(node: ET.Element) -> dict[str, int]:
    index: dict[str, int] = {}
    for candidate in node.iter():
        if _local(candidate.tag) != "RASTER_INDEX":
            continue
        values = {_local(child.tag): (child.text or "").strip() for child in candidate}
        band_id, band_index = values.get("BAND_ID"), values.get("BAND_INDEX")
        if band_id and str(band_index or "").isdigit():
            index[band_id] = int(band_index)
    return index


def _special_values(node: ET.Element) -> dict[str, float]:
    """Wartosci specjalne z `<Special_Value>`.

    UWAGA CO DO NAZWY POLA: wartosc siedzi w elemencie `SPECIAL_VALUE_COUNT`, ktorego nazwa
    sugeruje liczbe wystapien. Ze rzeczywiscie jest to WARTOSC PIKSELA, ustalono pomiarem:
    dostawy deklaruja `NODATA = 0`, a w rastrach PNEO i PHR zer we wszystkich pasmach jest
    odpowiednio 37% i 54% powierzchni — czyli ramka geokodowania. Liczba wystapien rowna
    zeru byla by z tym sprzeczna.
    """
    values: dict[str, float] = {}
    for candidate in node.iter():
        if _local(candidate.tag) != "SPECIAL_VALUE":
            continue
        fields = {_local(child.tag): (child.text or "").strip() for child in candidate}
        name = fields.get("SPECIAL_VALUE_TEXT")
        raw = fields.get("SPECIAL_VALUE_COUNT")
        if not name or raw is None:
            continue
        try:
            values[name.upper()] = float(raw)
        except ValueError:
            continue
    return values


def _hrefs(node: ET.Element) -> list[str]:
    names: list[str] = []
    for candidate in node.iter():
        if _local(candidate.tag) != "DATA_FILE_PATH":
            continue
        href = candidate.attrib.get("href") or (candidate.text or "").strip()
        if href:
            names.append(PurePosixPath(str(href).replace("\\", "/")).name)
    return names


def parse_dimap_bands(text: str) -> DimapBands:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return DimapBands(error=f"ParseError: {exc}")

    band_ids: list[str] = []
    for node in root.iter():
        if _local(node.tag) == "BAND_ID":
            value = (node.text or "").strip()
            if value and value not in band_ids:
                band_ids.append(value)

    # Plik obrazowy i opisujacy go `<Raster_Display>` leza w tym samym `<Data_Files>`
    # (albo `<Data_Access>`), jako rodzenstwo — nie jako zagniezdzenie. Szukamy wiec
    # najmniejszego kontenera, ktory ma oba, i to on definiuje powiazanie.
    files: dict[str, FileBands] = {}
    for container in ("DATA_FILES", "DATA_ACCESS"):
        # NAJPIERW `Data_Files` — pomiar na PNEO pokazal dwa takie bloki, po jednym na plik.
        # `Data_Access` obejmuje oba i uzyty jako pierwszy przypisalby obu plikom te sama
        # kolejnosc kanalow. Jest wiec wylacznie awaryjny, dla dostaw bez `Data_Files`.
        if files:
            break
        for node in root.iter():
            if _local(node.tag) != container:
                continue
            order = _display_order(node)
            index = _band_index(node)
            specials = _special_values(node)
            if not order and not index and not specials:
                continue
            for name in _hrefs(node):
                files.setdefault(name, FileBands(
                    display_order=order, band_index=index, special_values=specials
                ))

    # Wartosci wspolne dla dokumentu bierzemy tylko wtedy, gdy sa SPOJNE — dostawa,
    # w ktorej pliki deklaruja rozne `NODATA`, nie ma jednej wartosci na scene.
    per_file = [entry.special_values for entry in files.values() if entry.special_values]
    shared: dict[str, float] = {}
    document_level = _special_values(root)
    for name in {key for values in per_file for key in values} or set(document_level):
        candidates = {values.get(name) for values in per_file} or {document_level.get(name)}
        candidates.discard(None)
        if len(candidates) == 1:
            shared[name] = float(candidates.pop())

    return DimapBands(
        band_ids=tuple(band_ids),
        display_order=_display_order(root),
        files=files,
        special_values=shared,
    )


def read_dimap_bands(path: Path) -> DimapBands:
    """Wczytaj DIMAP z dysku. Brak pliku albo blad odczytu to pusty wynik, nie wyjatek."""
    try:
        if path.stat().st_size > MAX_DIMAP_BYTES:
            return DimapBands(error="dimap_too_large")
        return parse_dimap_bands(path.read_text(encoding="utf-8", errors="ignore"))
    except OSError as exc:
        return DimapBands(error=f"{type(exc).__name__}: {exc}")
