"""Odczyt manifestow kafli dostawy (DESIGN_DECISIONS.md, scene-import P0.6).

Bramka P0.6 wymaga, zeby lista i kolejnosc czesci pochodzily z TIL, a nie z sortowania nazw.
Roznica nie jest kosmetyczna:

- **sortowanie nazw nie wie, ilu czesci brakuje.** Katalog z trzema plikami `R1C1`, `R1C2`,
  `R2C1` wyglada dokladnie jak kompletna dostawa trzyczesciowa. TIL deklaruje szesc, wiec
  dopiero on pozwala odroznic niepelna ekstrakcje od poprawnej sceny (sekcja 4.6),
- **kolejnosc `natural_key` jest zalozeniem, nie faktem.** Dziala dla `RnCn`, ale to dostawca
  deklaruje uklad kafli i tylko jego deklaracja jest wiazaca.

Format TIL jest tekstowym slownikiem grup:

    BEGIN_GROUP = TILESET
        numTiles = 6;
        BEGIN_GROUP = TILE_1
            filename = "18APR08_PAN_R1C1.TIF";
        END_GROUP = TILE_1
        ...
    END_GROUP = TILESET
    END;

Parser jest CELOWO tolerancyjny: interesuje nas `numTiles` i uporzadkowana lista nazw plikow.
Manifest, ktorego nie da sie odczytac, daje pusty wynik, a nie wyjatek — brak deklaracji ma
prowadzic do zachowawczej decyzji, a nie do przerwania importu calej dostawy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_NUM_TILES = re.compile(r"\bnumTiles\s*=\s*(\d+)\s*;", re.IGNORECASE)
_FILENAME = re.compile(r"\bfilename\s*=\s*\"([^\"]+)\"\s*;", re.IGNORECASE)

#: Limit odczytu manifestu. TIL realnej dostawy ma kilka kilobajtow; wszystko powyzej to albo
#: nie jest TIL, albo jest uszkodzone — i tak nie chcemy wciagnac tego do pamieci.
MAX_MANIFEST_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class TileManifest:
    """Deklaracja czesci odczytana z jednego pliku TIL."""

    declared_parts: tuple[str, ...] = ()
    declared_count: int | None = None
    source: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.declared_parts and self.declared_count is None

    @property
    def expected_count(self) -> int:
        """Ile czesci dostawca deklaruje.

        `numTiles` ma pierwszenstwo przed dlugoscia listy: jesli manifest deklaruje szesc
        kafli, a wymienia trzy, to sam manifest jest niepelny i traktowanie go jako
        trzyczesciowego ukryloby brak.
        """
        if self.declared_count is not None:
            return self.declared_count
        return len(self.declared_parts)

    @property
    def unnamed_declared_count(self) -> int:
        """Liczba kafli zadeklarowanych przez `numTiles`, ale niewymienionych z nazwy.

        Taki TIL jest wewnetrznie niespojny. Nie umiemy nazwac brakujacych plikow, ale nie
        wolno przez to uznac ekstrakcji za kompletna.
        """
        return max(0, self.expected_count - len(self.declared_parts))

    def order_key(self, filename: str) -> int:
        """Pozycja pliku w deklarowanej kolejnosci; nieznane trafiaja na koniec."""
        name = PurePosixPath(str(filename).replace("\\", "/")).name.casefold()
        for index, declared in enumerate(self.declared_parts):
            if PurePosixPath(declared.replace("\\", "/")).name.casefold() == name:
                return index
        return len(self.declared_parts)

    def missing(self, present_filenames: "list[str] | tuple[str, ...]") -> tuple[str, ...]:
        """Czesci zadeklarowane, ktorych nie ma na dysku."""
        present = {
            PurePosixPath(str(item).replace("\\", "/")).name.casefold() for item in present_filenames
        }
        return tuple(
            declared
            for declared in self.declared_parts
            if PurePosixPath(declared.replace("\\", "/")).name.casefold() not in present
        )


EMPTY_MANIFEST = TileManifest()


def parse_tile_manifest(text: str, source: str | None = None) -> TileManifest:
    """Odczytaj deklaracje czesci z tresci pliku TIL."""
    count_match = _NUM_TILES.search(text)
    parts = tuple(_FILENAME.findall(text))
    return TileManifest(
        declared_parts=parts,
        declared_count=int(count_match.group(1)) if count_match else None,
        source=source,
    )


def read_tile_manifest(path: Path) -> TileManifest:
    """Odczytaj TIL z dysku. Blad odczytu daje pusty manifest, nie wyjatek."""
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            return EMPTY_MANIFEST
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return EMPTY_MANIFEST
    return parse_tile_manifest(text, source=path.name)
