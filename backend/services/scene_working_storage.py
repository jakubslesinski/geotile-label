"""Read-only rozmiar plikow roboczych scen (`derived_scenes`).

Zastepuje licznik `preview_cache_info()`, ktory mierzyl wylacznie PNG-i w
`tile_catalogs/*/preview_cache` i po odejsciu konsumenta `tilePreviewUrl()` pokazywal
`0 B`. Zakres jest celowo zawezony do jednego katalogu, zeby suma w panelu byla
identyczna z rozmiarem folderu, ktory otwiera przycisk — zrodla, adnotacje i katalogi
kafli nie sa wliczane (DESIGN_DECISIONS.md, working-files).

Modul jest wylacznie odczytowy: nie tworzy `derived_scenes`, nie usuwa plikow, nie
naprawia stanow i nie czyta zawartosci rastrow. Czytane sa tylko metadane systemu
plikow, wiec czas skanu zalezy od LICZBY plikow, nie od ich rozmiaru.
"""

from __future__ import annotations

import heapq
import os
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from db.storage import project_paths

SCHEMA_NAME = "geotile_scene_working_storage"
SCHEMA_VERSION = 1

#: Jedyny katalog objety statystyka. Nazwa musi zgadzac sie z `working_view.py` i
#: `fullres_cog_builder.py`, ktore tam pisza.
WORKING_FILES_DIR_NAME = "derived_scenes"

CATEGORY_FULLRES_COG = "fullres_cog"
CATEGORY_DISPLAY_OVERVIEW = "display_overview"
CATEGORY_MATERIALIZED_VIEW = "materialized_view"
CATEGORY_VIRTUAL_VIEW = "virtual_view"
CATEGORY_METADATA = "metadata"
CATEGORY_INCOMPLETE = "incomplete"
CATEGORY_OTHER = "other"

#: Kolejnosc prezentacji; `other` jest ostatni, bo istnieje tylko po to, zeby suma
#: kategorii zawsze rownala sie `total`.
CATEGORY_ORDER = (
    CATEGORY_FULLRES_COG,
    CATEGORY_DISPLAY_OVERVIEW,
    CATEGORY_MATERIALIZED_VIEW,
    CATEGORY_VIRTUAL_VIEW,
    CATEGORY_METADATA,
    CATEGORY_INCOMPLETE,
    CATEGORY_OTHER,
)

#: Nazwy z `fullres_cog_builder.py`. Powielone tutaj swiadomie: import buildera
#: sciagnalby GDAL do sciezki, ktora ma czytac wylacznie `stat()`.
_FULLRES_DIR_NAME = "fullres"
_FULLRES_COG_NAME = "fullres.tif"
_INCOMPLETE_NAMES = frozenset({"fullres.candidate.tif", "fullres.raw", "fullres.vrt"})

_RASTER_SUFFIXES = frozenset({".tif", ".tiff", ".cog", ".jp2"})
_METADATA_SUFFIXES = frozenset({".json", ".jsonl", ".log", ".txt"})

_MAX_LARGEST_LIMIT = 25
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def classify_working_file(relative_parts: tuple[str, ...]) -> str:
    """Przypisz plik do dokladnie jednej kategorii (§4.3).

    `relative_parts` to sciezka wzgledem `derived_scenes` rozbita na segmenty. Reguly
    stosowane sa w podanej kolejnosci, wiec `.partial` wygrywa z `.ovr`, a `fullres.tif`
    w katalogu `fullres/` z ogolna regula rastra.
    """

    name = relative_parts[-1]

    # 1. Pliki przejsciowe — zanim cokolwiek innego je przechwyci. `.partial` lapie
    #    zarowno `.overview.vrt.partial.vrt.ovr`, jak i `.fullres.tif.partial`.
    #    `.strip_*.raw` to kawalki BSQ zostawione przez przerwana budowe COG; plan
    #    wymienia przyklady po "np.", a bez tej reguly gigabajty ladowalyby w `other`
    #    dokladnie w scenariuszu E, ktory ma je pokazac.
    if ".partial" in name:
        return CATEGORY_INCOMPLETE
    if name in _INCOMPLETE_NAMES:
        return CATEGORY_INCOMPLETE
    if name.startswith(".strip_") and name.endswith(".raw"):
        return CATEGORY_INCOMPLETE

    # 2. Opublikowany derywat 1x — rozpoznawany po polozeniu, nie po rozmiarze.
    if len(relative_parts) >= 2 and relative_parts[-2:] == (_FULLRES_DIR_NAME, _FULLRES_COG_NAME):
        return CATEGORY_FULLRES_COG

    suffix = os.path.splitext(name)[1].lower()

    # 3. Piramidy projektowe (sidecar `.ovr` obok VRT).
    if suffix == ".ovr":
        return CATEGORY_DISPLAY_OVERVIEW

    # 4. Materializowane widoki robocze, przede wszystkim `rgb_pansharpened.cog.tif`.
    if suffix in _RASTER_SUFFIXES:
        return CATEGORY_MATERIALIZED_VIEW

    # 5. Lekkie widoki wirtualne.
    if suffix == ".vrt":
        return CATEGORY_VIRTUAL_VIEW

    # 6. Manifesty, profile, logi.
    if suffix in _METADATA_SUFFIXES:
        return CATEGORY_METADATA

    return CATEGORY_OTHER


class _Bucket:
    """Akumulator jednej kategorii. `scenes` trzyma identyfikatory, nie liczniki,
    bo ta sama scena wnosi wiele plikow do tej samej kategorii."""

    __slots__ = ("bytes", "file_count", "scenes")

    def __init__(self) -> None:
        self.bytes = 0
        self.file_count = 0
        self.scenes: set[str] = set()

    def add(self, size: int, scene_id: str | None) -> None:
        self.bytes += size
        self.file_count += 1
        if scene_id is not None:
            self.scenes.add(scene_id)

    def as_dict(self) -> dict[str, int]:
        return {
            "bytes": self.bytes,
            "file_count": self.file_count,
            "scene_count": len(self.scenes),
        }


class _SceneTotals:
    __slots__ = ("bytes", "file_count", "categories")

    def __init__(self) -> None:
        self.bytes = 0
        self.file_count = 0
        self.categories: dict[str, int] = {}

    def add(self, size: int, category: str) -> None:
        self.bytes += size
        self.file_count += 1
        self.categories[category] = self.categories.get(category, 0) + size


def _is_reparse_point(entry: os.DirEntry) -> bool:
    """Nie wchodzic w dowiazania. Na Windowsie junction nie zawsze jest symlinkiem
    w rozumieniu `is_symlink()`, wiec sprawdzamy takze atrybut reparse point."""

    try:
        if entry.is_symlink():
            return True
    except OSError:
        return True
    try:
        attributes = entry.stat(follow_symlinks=False).st_file_attributes  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _walk(root: Path) -> Iterator[tuple[tuple[str, ...], os.stat_result | None]]:
    """Iteracyjny obchod bez `rglob()`, ktory zbudowalby w pamieci liste wszystkich
    plikow. Zwraca `None` zamiast `stat_result`, gdy pliku nie da sie odczytac —
    pojedynczy blad zwieksza licznik, ale nie przerywa skanu."""

    stack: list[tuple[Path, tuple[str, ...]]] = [(root, ())]
    while stack:
        directory, prefix = stack.pop()
        try:
            iterator = os.scandir(directory)
        except OSError:
            yield prefix + ("<unreadable>",), None
            continue
        with iterator:
            while True:
                try:
                    entry = next(iterator)
                except StopIteration:
                    break
                except OSError:
                    yield prefix + ("<unreadable>",), None
                    break
                parts = prefix + (entry.name,)
                try:
                    if _is_reparse_point(entry):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append((Path(entry.path), parts))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    yield parts, entry.stat(follow_symlinks=False)
                except OSError:
                    yield parts, None


def _read_scene_display_names(root: Path) -> dict[str, str]:
    """Nazwy scen jednym odczytem gotowego indeksu, bez `load_scene_json()` per scena
    i bez wymuszania przebudowy — indeks jest ozdoba panelu, nie jego trescia."""

    import json

    for candidate in (root / "indexes" / "scenes_index_v2.json", root / "scenes_index.json"):
        try:
            raw = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            index = json.loads(raw)
        except json.JSONDecodeError:
            continue
        entries = index.get("scenes") if isinstance(index, dict) else None
        if not isinstance(entries, list):
            continue
        names: dict[str, str] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            scene_id = entry.get("scene_id")
            label = entry.get("display_name") or entry.get("filename")
            if scene_id and label:
                names[str(scene_id)] = str(label)
        if names:
            return names
    return {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def summarize_scene_working_storage(
    project_id: str,
    *,
    largest_limit: int = 10,
) -> dict[str, Any]:
    """Zsumuj `derived_scenes` projektu. Nigdy nie tworzy katalogu ani pliku.

    Brak katalogu to poprawny wynik zerowy z `directory_exists=false`, a nie blad:
    projekt bez derywatow jest zwyklym stanem, nie awaria.
    """

    limit = max(0, min(int(largest_limit), _MAX_LARGEST_LIMIT))
    started = time.perf_counter()

    # `project_paths(...)` rozwiazuje sciezke bez tworzenia jej; `project_dir()` jest
    # aliasem `ensure_project_dir()` i zalozylby katalog projektu przy odczycie.
    root = project_paths(project_id).root
    working_dir = root / WORKING_FILES_DIR_NAME

    buckets: dict[str, _Bucket] = {name: _Bucket() for name in CATEGORY_ORDER}
    total = _Bucket()
    scenes: dict[str, _SceneTotals] = {}
    scan_errors = 0

    if working_dir.is_dir():
        for parts, info in _walk(working_dir):
            if info is None:
                scan_errors += 1
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            size = int(info.st_size)
            # Scena to pierwszy segment pod `derived_scenes`; plik lezacy bezposrednio
            # w katalogu glownym nie nalezy do zadnej i nie zawyza `scene_count`.
            scene_id = parts[0] if len(parts) > 1 else None
            category = classify_working_file(parts)
            buckets[category].add(size, scene_id)
            total.add(size, scene_id)
            if scene_id is not None:
                scenes.setdefault(scene_id, _SceneTotals()).add(size, category)

    display_names = _read_scene_display_names(root) if scenes and limit else {}
    largest = heapq.nlargest(
        limit,
        scenes.items(),
        key=lambda item: (item[1].bytes, item[0]),
    ) if limit else []

    return {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "project_id": project_id,
        "generated_at": _utc_now_iso(),
        "scan_duration_ms": int(round((time.perf_counter() - started) * 1000)),
        "working_files_dir": str(working_dir),
        "directory_exists": working_dir.is_dir(),
        "total": total.as_dict(),
        "categories": {name: buckets[name].as_dict() for name in CATEGORY_ORDER},
        "largest_scenes": [
            {
                "scene_id": scene_id,
                "display_name": display_names.get(scene_id),
                "bytes": totals.bytes,
                "file_count": totals.file_count,
                "categories": {
                    name: totals.categories[name]
                    for name in CATEGORY_ORDER
                    if totals.categories.get(name)
                },
            }
            for scene_id, totals in largest
        ],
        "scan_errors": scan_errors,
    }
