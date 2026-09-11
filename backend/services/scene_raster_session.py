"""Thread-affine sesja rastra dla serwowania kafli (R0.3).

Problem
-------
`_produce_geo_tile_uncached` otwiera dziś `rasterio.open` i buduje `WarpedVRT` na KAZDY
kafel. Cache blokow GDAL nalezy do konkretnego obiektu pasma, wiec przy uchwycie na kafel
nie jest wspoldzielony: blok zdekodowany dla jednego kafla jest dekodowany ponownie dla
sasiada. Zmierzone na viewporcie 32 kafli: **21,5 s z uchwytem na kafel wobec 6,2 s
z uchwytem na workera** — 3,5x bez zadnej zmiany kodeka.

Dlaczego thread-affine, a nie wspoldzielony uchwyt
-------------------------------------------------
Uchwyty GDAL nie sa bezpieczne przy rownoczesnym uzyciu z wielu watkow, a kazdy kafel to
osobne zadanie HTTP bez identyfikatora viewportu — nie ma czego "wspoldzielic w obrebie
viewportu". Uchwyt nalezy wiec WYLACZNIE do jednego workera puli i nigdy nie opuszcza jego
watku. Pule sa dlugowieczne, wiec cache blokow zyje miedzy kaflami tego samego workera,
a przy jednym workerze JP2 (domyslnie) daje to pelny zysk bez ryzyka.

Uniewaznianie miedzy watkami
---------------------------
Watek nie moze zamknac uchwytu nalezacego do innego watku. Zamiast tego trzymamy globalna
EPOKE: uniewaznienie ja podnosi, a kazdy watek przy najblizszym uzyciu sam zamyka swoje
nieaktualne wpisy. Klucz wpisu zawiera rewizje aktywnego assetu, wiec podmiana derywatu
i tak wymusza nowy uchwyt, nawet gdyby epoka nie zostala podniesiona.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

#: Ile otwartych rastrow trzyma jeden worker. Viewport dotyka jednej sceny, a przelaczanie
#: scen ma byc tanie — wiecej niz kilka wpisow na watek to tylko deskryptory i pamiec.
MAX_ENTRIES_PER_THREAD = 3

#: Bezczynny uchwyt zwalniamy, zeby nie trzymac pliku w nieskonczonosc po odejsciu od sceny
#: (na Windows otwarty plik blokuje usuniecie i podmiane derywatu).
IDLE_TTL_SECONDS = 300.0

_epoch_lock = threading.Lock()
_epoch = 0
_local = threading.local()


def current_epoch() -> int:
    with _epoch_lock:
        return _epoch


def invalidate_all() -> None:
    """Uniewaznij wszystkie sesje we wszystkich watkach.

    Wolane przy zmianie projektu, relinku i zatrzymaniu aplikacji. Nie zamyka niczego
    natychmiast — kazdy watek sprzata swoje wpisy przy najblizszym uzyciu albo przez
    `close_current_thread()`.
    """
    global _epoch
    with _epoch_lock:
        _epoch += 1


@dataclass
class _Entry:
    key: tuple
    epoch: int
    dataset: Any
    warped: Any
    source_all_valid: bool
    last_used: float


def _entries() -> "list[_Entry]":
    items = getattr(_local, "entries", None)
    if items is None:
        items = []
        _local.entries = items
    return items


def _close(entry: _Entry) -> None:
    for handle in (entry.warped, entry.dataset):
        try:
            if handle is not None:
                handle.close()
        except Exception:
            # Zamkniecie uchwytu nie moze wywrocic serwowania kafla.
            pass


def _drop_stale(now: float, epoch: int) -> None:
    keep: list[_Entry] = []
    for entry in _entries():
        if entry.epoch != epoch or (now - entry.last_used) > IDLE_TTL_SECONDS:
            _close(entry)
        else:
            keep.append(entry)
    _local.entries = keep


def close_current_thread() -> int:
    """Zamknij wszystkie uchwyty biezacego watku. Zwraca liczbe zamknietych."""
    items = _entries()
    for entry in items:
        _close(entry)
    _local.entries = []
    return len(items)


@dataclass
class RasterSession:
    """Otwarty raster wraz z jego `WarpedVRT` — oba nalezace do biezacego watku."""

    dataset: Any
    warped: Any
    source_all_valid: bool


@contextmanager
def display_raster_session(
    path: Path,
    *,
    revision: str | None,
    crs: str = "EPSG:3857",
    env_options: dict[str, str] | None = None,
) -> Iterator[RasterSession]:
    """Zwroc sesje rastra dla sciezki, ponownie uzywajac uchwytu tego watku.

    `revision` wchodzi w klucz, wiec podmiana derywatu nigdy nie zostanie obsluzona starym
    uchwytem. `env_options` obowiazuje tylko przy OTWIERANIU — po otwarciu uchwyt zyje
    dalej, wiec opcje zalezne od kafla nie moga tu trafiac.
    """
    import rasterio
    from rasterio.enums import MaskFlags
    from rasterio.vrt import WarpedVRT

    resolved = str(Path(path))
    key = (resolved, revision, crs)
    epoch = current_epoch()
    now = time.monotonic()
    _drop_stale(now, epoch)

    for entry in _entries():
        if entry.key == key:
            entry.last_used = now
            yield RasterSession(entry.dataset, entry.warped, entry.source_all_valid)
            return

    with rasterio.Env(**(env_options or {})):
        dataset = rasterio.open(resolved)
        try:
            source_all_valid = dataset.nodata is None and all(
                MaskFlags.all_valid in flags for flags in dataset.mask_flag_enums
            )
            warped = WarpedVRT(
                dataset,
                crs=crs,
                resampling=rasterio.enums.Resampling.bilinear,
                add_alpha=source_all_valid,
            )
        except BaseException:
            dataset.close()
            raise

    entry = _Entry(key, epoch, dataset, warped, source_all_valid, now)
    items = _entries()
    items.append(entry)
    # LRU: przy przekroczeniu limitu zamykamy najdawniej uzywany wpis TEGO watku.
    while len(items) > MAX_ENTRIES_PER_THREAD:
        oldest = min(items, key=lambda item: item.last_used)
        items.remove(oldest)
        _close(oldest)
    _local.entries = items

    yield RasterSession(entry.dataset, entry.warped, entry.source_all_valid)


def session_stats() -> dict[str, Any]:
    """Diagnostyka: ile uchwytow trzyma biezacy watek."""
    items = _entries()
    return {
        "epoch": current_epoch(),
        "entries_in_thread": len(items),
        "max_entries_per_thread": MAX_ENTRIES_PER_THREAD,
        "idle_ttl_seconds": IDLE_TTL_SECONDS,
        "keys": [entry.key[0] for entry in items],
    }
