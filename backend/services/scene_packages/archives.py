"""Indeksowanie archiwow dostaw BEZ rozpakowywania (DESIGN_DECISIONS.md, scene-import P1.3a).

Problem z sekcji 4.5 i 4.6: discovery w ogole nie widzi archiwow. Dla WV2 oznacza to, ze
aplikacja nie wie, iz ZIP i katalog obok to ta sama dostawa. Dla Airbusa oznacza to, ze
JEDYNA paczka PNEO w calym korpusie — rozpakowana w 2 z 16 plikow, bez obu rastrow po 676 MiB —
znika z preview bez sladu.

Modul czyta WYLACZNIE central directory (`zipfile.ZipFile.infolist()`), czyli kilkadziesiat
kilobajtow z konca pliku. Pomiar na udziale SMB: 0,068 s dla archiwum 395 MB i 0,231 s dla
1,37 GB. Nic tu nie jest rozpakowywane i nic nie jest zapisywane — przygotowanie produktu z
archiwum to odlozony etap P1.3b.

Bezpieczenstwo jest sprawdzane JUZ NA ETAPIE INDEKSU, a nie dopiero przy ekstrakcji. Wpis z
`..`, ze sciezka bezwzgledna albo z litera dysku nigdy nie trafia do porownania z dyskiem i
jest raportowany osobno. Dzieki temu nazwa wpisu z archiwum nie zamienia sie w sciezke na
dysku nawet przez pomylke w kodzie wywolujacym.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

#: Statusy pakietu-archiwum. Sa rozlaczne i wyczerpujace dla czytelnego archiwum.
ARCHIVE_STATUS_ONLY = "archive_only"
ARCHIVE_STATUS_DUPLICATE = "archive_duplicate"
ARCHIVE_STATUS_INCOMPLETE = "archive_extracted_incomplete"
ARCHIVE_STATUS_UNREADABLE = "archive_unreadable"

ARCHIVE_STATUSES = frozenset({
    ARCHIVE_STATUS_ONLY,
    ARCHIVE_STATUS_DUPLICATE,
    ARCHIVE_STATUS_INCOMPLETE,
    ARCHIVE_STATUS_UNREADABLE,
})

#: Czytamy tylko te formaty, ktore da sie zindeksowac ze stdlib bez rozpakowania.
#: Pozostale archiwa sa widoczne, ale z jawnym `unsupported_archive_format`.
INDEXABLE_SUFFIXES = {".zip"}
ARCHIVE_SUFFIXES = {".zip", ".tar", ".gz", ".7z", ".rar"}

#: Gorny limit wpisow w indeksie. Central directory z milionem wpisow to znany wektor
#: wyczerpania pamieci; obcinamy i mowimy o tym wprost zamiast udawac pelny indeks.
MAX_INDEXED_ENTRIES = 20_000
#: Ile brakujacych plikow wymieniamy z nazwy. Reszta jest tylko policzona.
MAX_REPORTED_MISSING = 50


def is_archive(path: Path | str) -> bool:
    return PurePosixPath(str(path)).suffix.casefold() in ARCHIVE_SUFFIXES


def is_unsafe_entry(name: str) -> bool:
    """Czy nazwa wpisu moglaby wyprowadzic ekstrakcje poza katalog docelowy.

    Sprawdzamy tez separator wsteczny: ZIP-y tworzone na Windowsie potrafia go zawierac mimo
    specyfikacji, a `PurePosixPath` nie uznalby wpisu z `..` po backslashu za wyjscie w gore.
    """
    if not name or name.startswith(("/", "\\")):
        return True
    if "\\" in name:
        return True
    if len(name) >= 2 and name[1] == ":":
        return True
    return any(part == ".." for part in PurePosixPath(name).parts)


@dataclass(frozen=True)
class ArchiveEntry:
    name: str
    size: int


@dataclass(frozen=True)
class ArchiveIndex:
    """Wynik odczytu central directory. Nigdy nie zawiera danych plikow."""

    entries: tuple[ArchiveEntry, ...] = ()
    unsafe_names: tuple[str, ...] = ()
    truncated: bool = False
    error: str | None = None

    @property
    def is_readable(self) -> bool:
        return self.error is None

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    @property
    def uncompressed_bytes(self) -> int:
        return sum(entry.size for entry in self.entries)

    @property
    def top_level_names(self) -> tuple[str, ...]:
        return tuple(sorted({PurePosixPath(entry.name).parts[0] for entry in self.entries}))

    @property
    def delivery_root(self) -> str | None:
        """Wspolny katalog najwyzszego poziomu, jesli archiwum ma dokladnie jeden."""
        names = self.top_level_names
        return names[0] if len(names) == 1 else None


EMPTY_INDEX = ArchiveIndex()


def read_archive_index(path: Path) -> ArchiveIndex:
    """Zindeksuj archiwum, czytajac wylacznie central directory."""

    suffix = path.suffix.casefold()
    if suffix not in INDEXABLE_SUFFIXES:
        return ArchiveIndex(error="unsupported_archive_format")
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
    except (OSError, zipfile.BadZipFile, NotImplementedError) as exc:
        return ArchiveIndex(error=f"{type(exc).__name__}: {exc}")

    entries: list[ArchiveEntry] = []
    unsafe: list[str] = []
    truncated = False
    for info in infos:
        if info.is_dir():
            continue
        if is_unsafe_entry(info.filename):
            unsafe.append(info.filename)
            continue
        if len(entries) >= MAX_INDEXED_ENTRIES:
            truncated = True
            break
        entries.append(ArchiveEntry(name=info.filename, size=int(info.file_size)))
    return ArchiveIndex(
        entries=tuple(entries),
        unsafe_names=tuple(unsafe),
        truncated=truncated,
    )


@dataclass(frozen=True)
class ExtractionMatch:
    """Porownanie zawartosci archiwum z tym, co faktycznie lezy na dysku."""

    base_relative: str | None = None
    present: int = 0
    total: int = 0
    missing: tuple[str, ...] = ()
    size_mismatch: tuple[str, ...] = ()
    checked_bases: tuple[str, ...] = field(default=(), repr=False)

    @property
    def status(self) -> str:
        if self.total == 0 or self.present == 0:
            return ARCHIVE_STATUS_ONLY
        if self.present == self.total and not self.size_mismatch:
            return ARCHIVE_STATUS_DUPLICATE
        return ARCHIVE_STATUS_INCOMPLETE


def _match_against(base: Path, index: ArchiveIndex) -> tuple[int, list[str], list[str]]:
    present = 0
    missing: list[str] = []
    mismatch: list[str] = []
    for entry in index.entries:
        candidate = base.joinpath(*PurePosixPath(entry.name).parts)
        try:
            stat = candidate.stat()
        except OSError:
            missing.append(entry.name)
            continue
        present += 1
        # Rozmiar nieskompresowany z central directory jest darmowym dowodem, ze plik na dysku
        # to ten sam plik, a nie ucieta ekstrakcja przerwana w polowie ostatniego rastra.
        if int(stat.st_size) != entry.size:
            mismatch.append(entry.name)
    return present, missing, mismatch


def match_extracted(archive_path: Path, index: ArchiveIndex, source_root: Path) -> ExtractionMatch:
    """Znajdz rozpakowana kopie archiwum obok niego.

    Sprawdzamy dwie konwencje zaobserwowane w korpusie i wybieramy te, ktora pasuje lepiej:

    - `foo.zip` → katalog `foo/` obok (Airbus: `dimapV2_PHR1A_...zip` → `dimapV2_PHR1A_.../`),
    - katalog archiwum → wpisy niosa wlasny katalog dostawy (WV2: `014679500010_01_003/...`
      lezy bezposrednio w katalogu, w ktorym stoi ZIP).

    Zaden wpis nie jest tu doklejany do sciezki bez wczesniejszej walidacji w
    `read_archive_index()`, wiec porownanie nie moze wyjsc poza katalog bazowy.
    """
    if not index.is_readable or not index.entries:
        return ExtractionMatch(total=index.entry_count)

    parent = archive_path.parent
    bases = [parent / archive_path.stem, parent]
    checked = tuple(str(item) for item in bases)
    best: ExtractionMatch | None = None
    for base in bases:
        if not base.is_dir():
            continue
        present, missing, mismatch = _match_against(base, index)
        try:
            base_relative = base.relative_to(source_root).as_posix()
        except ValueError:
            base_relative = base.name
        candidate = ExtractionMatch(
            base_relative=base_relative or ".",
            present=present,
            total=index.entry_count,
            missing=tuple(missing),
            size_mismatch=tuple(mismatch),
            checked_bases=checked,
        )
        if best is None or candidate.present > best.present:
            best = candidate
        if candidate.present == candidate.total:
            break
    if best is None or best.present == 0:
        # Zaden kandydat nie zawiera ani jednego pliku z archiwum, wiec nie ma rozpakowanej
        # kopii. Podawanie wtedy `base_relative` i listy „brakujacych" plikow sugerowaloby
        # niepelna ekstrakcje tam, gdzie jej po prostu nie ma.
        return ExtractionMatch(total=index.entry_count, checked_bases=checked)
    return best
