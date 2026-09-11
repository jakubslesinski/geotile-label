"""Watchdog pamieci procesu potomnego (R1.3, §20).

Dekod idzie przez `opj_decompress` jako proces POTOMNY, wiec probkowanie wlasnego RSS
niczego nie pilnuje — to bylby watchdog patrzacy w zla strone. Mierzymy drzewo procesu
i przerywamy je przed twardym limitem, zamiast czekac, az system zacznie wymiatac pamiec.

Prochnik i zabijanie sa wstrzykiwane, zeby polityke dalo sie przetestowac bez wywolywania
realnego zuzycia pamieci — testowanie tego przez faktyczna alokacje 4,5 GiB byloby wolne
i zawodne, a sprawdzaloby system operacyjny, nie nasza decyzje.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

ABORT_NONE = "completed"
ABORT_MEMORY = "memory_limit_exceeded"
ABORT_CANCELLED = "cancelled"


@dataclass
class WatchdogResult:
    aborted: bool
    reason: str
    peak_rss_bytes: int
    samples: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "aborted": self.aborted,
            "reason": self.reason,
            "peak_rss_bytes": self.peak_rss_bytes,
            "sample_count": len(self.samples),
        }


class MemoryWatchdog:
    """Pilnuje limitu pamieci i anulowania przez caly czas trwania procesu."""

    def __init__(
        self,
        *,
        limit_bytes: int,
        sample_rss: Callable[[], int],
        terminate: Callable[[], None],
        poll_interval: float = 0.1,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        self._limit = int(limit_bytes)
        self._sample = sample_rss
        self._terminate = terminate
        self._poll = float(poll_interval)
        self._cancel_check = cancel_check
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.result = WatchdogResult(False, ABORT_NONE, 0)

    def _tick(self) -> bool:
        """Jedno sprawdzenie. Zwraca True, gdy proces ma zostac przerwany."""
        if self._cancel_check is not None and self._cancel_check():
            self.result.aborted = True
            self.result.reason = ABORT_CANCELLED
            return True
        try:
            rss = int(self._sample())
        except Exception:
            # Proces mogl wlasnie zniknac — to nie jest powod do przerywania czegokolwiek.
            return False
        self.result.peak_rss_bytes = max(self.result.peak_rss_bytes, rss)
        self.result.samples.append({"t": round(time.monotonic(), 2), "rss_bytes": rss})
        if rss >= self._limit:
            self.result.aborted = True
            self.result.reason = ABORT_MEMORY
            return True
        return False

    def _run(self) -> None:
        while not self._stop.wait(self._poll):
            if self._tick():
                try:
                    self._terminate()
                except Exception:
                    pass
                return

    def __enter__(self) -> "MemoryWatchdog":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)


def sample_process_tree_rss(process: Any) -> int:
    """Suma RSS procesu i jego potomkow.

    `opj_decompress` nie tworzy dzis potomkow, ale liczenie drzewa jest tanie i chroni
    przed cicha zmiana, gdyby kiedys zaczal.
    """
    total = int(process.memory_info().rss)
    try:
        for child in process.children(recursive=True):
            try:
                total += int(child.memory_info().rss)
            except Exception:
                continue
    except Exception:
        pass
    return total
