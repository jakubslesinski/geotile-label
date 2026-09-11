"""Polityka budowy derywatu 1x: budzety, podzial na pasy, preflight (R1.3, §20).

Wydzielona z wykonania, bo to tutaj sa decyzje, ktore trzeba umiec sprawdzic bez
uruchamiania dwudziestominutowego dekodu.

Skad te liczby
--------------
E4 zmierzylo, ze dekod DOWOLNEGO okna tego codestreamu ma stala podloge ~2,7-2,8 GiB —
region 1024x1024 dajacy 2 MiB wyniku kosztowal 2,79 GiB. Podloga nie zalezy od tego, ile
pikseli sie chce, wiec nie da sie jej obejsc doborem podzialu; mozna tylko sterowac czescia
zmienna. Stad:

* cel operacyjny 3,5-3,7 GiB — tam ma sie miescic typowy przebieg;
* twarde przerwanie przed 4,5 GiB, pilnowane watchdogiem PROCESU POTOMNEGO (dekod idzie
  w `opj_decompress`, wiec probkowanie samego siebie nie pilnuje niczego);
* prog 16 GB RAM hosta, ponizej ktorego w ogole nie zaczynamy.

Dlaczego podzial jest adaptacyjny, a nie ze wzoru
------------------------------------------------
Miedzy dwoma plikami 6% wiecej pikseli w pasie dalo 26% wiecej pamieci (ARSENYEV 61,9 Mpx
-> 3,82 GiB, CHABAROWSK 65,4 Mpx -> 4,80 GiB). Powierzchnia nie wystarcza jako predyktor,
bo wchodza wlasnosci konkretnego codestreamu. Dlatego pierwszy pas jest KALIBRACYJNY
i celowo konserwatywny, a kolejne skaluja sie z tego, co zmierzono.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

GIB = 1024 ** 3

#: Cel operacyjny (§20). Powyzej nie jest jeszcze bledem, ale podzial ma do tego dazyc.
MEMORY_TARGET_BYTES = int(3.6 * GIB)
#: Twarde przerwanie — powyzej tego przerywamy zadanie, nie probujemy dalej.
MEMORY_HARD_ABORT_BYTES = int(4.5 * GIB)
MEMORY_TARGET_HEADROOM_BYTES = int(0.6 * GIB)
MIN_CHILD_LIMIT_BYTES = int(3.25 * GIB)
SYSTEM_RESERVE_MIN_BYTES = 4 * GIB
SYSTEM_RESERVE_FRACTION = 0.20
#: Ponizej tylu zainstalowanych GB nie podejmujemy budowy (§20).
MIN_HOST_RAM_BYTES = 16 * GIB
#: Dostepna pamiec musi z zapasem pokryc twardy limit — sprawdzamy dostepna, nie
#: zainstalowana, bo to ona decyduje, czy proces sie zmiesci.
MIN_AVAILABLE_MEMORY_BYTES = MEMORY_HARD_ABORT_BYTES + GIB

#: Pierwszy pas jest celowo maly: sluzy pomiarowi, nie wydajnosci.
CALIBRATION_ROWS = 512
#: Granice, w ktorych wolno dobierac wysokosc pasa.
MIN_STRIP_ROWS = 256
MAX_STRIP_ROWS = 8192

#: Zapas na plik tymczasowy GDAL, logi i pozostalosci po przerwanym zadaniu.
DISK_SAFETY_MARGIN_BYTES = 2 * GIB
#: Zmierzony stosunek rozmiaru COG do zrodla (E4: x1,56 i x1,75) z zapasem.
COG_TO_SOURCE_RATIO = 2.0


class InsufficientMemoryError(RuntimeError):
    """Maszyna nie ma warunkow do zbudowania derywatu."""


class InsufficientDiskError(RuntimeError):
    """Za malo miejsca na maksymalny stan jednoczesny."""


@dataclass(frozen=True)
class MemoryVerdict:
    ok: bool
    reason: str
    installed_bytes: int
    available_bytes: int
    reserve_bytes: int = 0
    child_limit_bytes: int = 0
    target_bytes: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "installed_bytes": self.installed_bytes,
            "available_bytes": self.available_bytes,
            "min_installed_bytes": MIN_HOST_RAM_BYTES,
            "min_available_bytes": MIN_AVAILABLE_MEMORY_BYTES,
            "reserve_bytes": self.reserve_bytes,
            "child_limit_bytes": self.child_limit_bytes,
            "target_bytes": self.target_bytes,
        }


MEMORY_OK = "ok"
MEMORY_INSTALLED_TOO_SMALL = "installed_ram_below_minimum"
MEMORY_AVAILABLE_TOO_SMALL = "available_ram_below_minimum"


def check_host_memory(installed_bytes: int, available_bytes: int) -> MemoryVerdict:
    """Czy na tej maszynie wolno w ogole zaczac (§20).

    Sprawdzamy OBIE wartosci. Zainstalowane 16 GB nie wystarcza, jesli w danej chwili
    wolne jest 3 GB — proces potomny i tak by nie wszedl, a probowanie skonczyloby sie
    przemielona maszyna zamiast czytelna odmowa.
    """
    reserve = max(SYSTEM_RESERVE_MIN_BYTES, int(installed_bytes * SYSTEM_RESERVE_FRACTION))
    child_limit = min(MEMORY_HARD_ABORT_BYTES, max(0, available_bytes - reserve))
    target = max(0, child_limit - MEMORY_TARGET_HEADROOM_BYTES)
    if installed_bytes < MIN_HOST_RAM_BYTES:
        return MemoryVerdict(
            False, MEMORY_INSTALLED_TOO_SMALL, installed_bytes, available_bytes,
            reserve, child_limit, target,
        )
    if child_limit < MIN_CHILD_LIMIT_BYTES:
        return MemoryVerdict(
            False, MEMORY_AVAILABLE_TOO_SMALL, installed_bytes, available_bytes,
            reserve, child_limit, target,
        )
    return MemoryVerdict(
        True, MEMORY_OK, installed_bytes, available_bytes,
        reserve, child_limit, target,
    )


@dataclass(frozen=True)
class DiskRequirement:
    raw_bytes: int
    cog_bytes: int
    margin_bytes: int

    @property
    def peak_bytes(self) -> int:
        """Maksymalny stan JEDNOCZESNY, nie suma koncowa.

        W szczycie na dysku leza naraz: plik surowy, czesciowy COG i tymczasowy plik
        GDAL-a. VRT jest pomijalny. Liczenie samej sumy koncowej (COG) niedoszacowaloby
        wymagania ponad dwukrotnie.
        """
        return self.raw_bytes + self.cog_bytes + self.margin_bytes

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_bytes": self.raw_bytes,
            "cog_bytes": self.cog_bytes,
            "margin_bytes": self.margin_bytes,
            "peak_bytes": self.peak_bytes,
        }


def estimate_disk_requirement(
    *, width: int, height: int, band_count: int, itemsize: int, source_bytes: int
) -> DiskRequirement:
    raw = int(width) * int(height) * max(1, int(band_count)) * max(1, int(itemsize))
    cog = int(source_bytes * COG_TO_SOURCE_RATIO)
    return DiskRequirement(raw, cog, DISK_SAFETY_MARGIN_BYTES)


def check_disk(requirement: DiskRequirement, free_bytes: int) -> bool:
    return free_bytes >= requirement.peak_bytes


@dataclass
class StripPlanner:
    """Dobiera wysokosc kolejnych pasow na podstawie zmierzonego szczytu RSS.

    Pierwszy pas jest kalibracyjny. Kazdy nastepny skaluje sie tak, zeby trafic w cel
    operacyjny, ale tylko w czesci ZMIENNEJ zuzycia — podloga jest nieusuwalna, wiec
    skalowanie calosci dawaloby wartosci bez sensu.
    """

    width: int
    height: int
    band_count: int = 1
    itemsize: int = 2
    floor_bytes: int = int(2.8 * GIB)
    target_bytes: int = MEMORY_TARGET_BYTES
    hard_limit_bytes: int = MEMORY_HARD_ABORT_BYTES
    observations: list[dict[str, Any]] = field(default_factory=list)
    dangerous_rows: list[int] = field(default_factory=list)
    _next_rows: int | None = None
    _safe_streak: int = 0

    def first_rows(self) -> int:
        reference_row_bytes = 60476 * 2
        actual_row_bytes = max(
            1,
            self.width * max(1, self.band_count) * max(1, self.itemsize),
        )
        scaled = int(CALIBRATION_ROWS * reference_row_bytes / actual_row_bytes)
        return min(max(MIN_STRIP_ROWS, scaled), MAX_STRIP_ROWS, max(1, self.height))

    def next_rows(self) -> int:
        if self._next_rows is None:
            return self.first_rows()
        return self._next_rows

    def observe(self, rows: int, peak_rss_bytes: int) -> int:
        """Zapisz pomiar pasa i zwroc wysokosc kolejnego."""
        self.observations.append({"rows": rows, "peak_rss_bytes": peak_rss_bytes})
        if peak_rss_bytes > self.target_bytes:
            proposed = int(rows * 0.70)
            self._safe_streak = 0
        elif peak_rss_bytes >= self.target_bytes - int(0.25 * GIB):
            proposed = rows
            self._safe_streak = 0
        else:
            self._safe_streak += 1
            proposed = int(rows * 1.125) if self._safe_streak >= 2 else rows
            if self._safe_streak >= 2:
                self._safe_streak = 0
        self._next_rows = max(MIN_STRIP_ROWS, min(MAX_STRIP_ROWS, proposed, self.height))
        return self._next_rows

    def retry_rows(self, failed_rows: int) -> int:
        """Retry the same range at half height and remember the unsafe size."""
        failed_rows = max(1, int(failed_rows))
        self.dangerous_rows.append(failed_rows)
        self._safe_streak = 0
        if failed_rows <= MIN_STRIP_ROWS:
            self._next_rows = max(1, failed_rows // 2)
        else:
            self._next_rows = max(MIN_STRIP_ROWS, failed_rows // 2)
        return self._next_rows

    def exceeded_hard_limit(self, peak_rss_bytes: int) -> bool:
        return peak_rss_bytes >= self.hard_limit_bytes

    def cannot_fit(self, peak_rss_bytes: int, rows: int) -> bool:
        """Czy nawet najmniejszy dopuszczalny pas przekracza twardy limit.

        Wtedy bezpieczenstwo pamieci wygrywa z czasem (§20): przerywamy i zostawiamy
        scene na 2x, zamiast probowac dalej.
        """
        return rows <= MIN_STRIP_ROWS and self.exceeded_hard_limit(peak_rss_bytes)


def plan_all_strips(height: int, rows: int) -> list[tuple[int, int]]:
    """Podziel obraz na pasy o zadanej wysokosci."""
    rows = max(1, int(rows))
    return [(y, min(int(height), y + rows)) for y in range(0, int(height), rows)]


def estimated_total_seconds(observations: list[dict[str, Any]], height: int) -> float | None:
    """Prognoza calkowitego czasu z dotychczasowych pasow — do postepu i decyzji."""
    done_rows = sum(int(item.get("rows") or 0) for item in observations)
    done_seconds = sum(float(item.get("seconds") or 0.0) for item in observations)
    if done_rows <= 0 or done_seconds <= 0:
        return None
    return done_seconds / done_rows * max(1, int(height))
