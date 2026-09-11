"""Deduplikacja rownoczesnych, identycznych obliczen (R0.5).

Do tej pory dwa identyczne zadania kafla mogly renderowac sie rownolegle. Chronily przed
tym wylacznie blokady cache dyskowego w `_produce_geo_tile`, wiec dedupliakcji NIE bylo:

* przy wariantach wyswietlania z wylaczonym cache (jasnosc, kontrast, gamma, rozciagniecie),
* w calym trybie pikselowym.

Przy JP2 kosztuje to podwojny dekod bloku, czyli sekundy — a przy panoramowaniu tam
i z powrotem zdarza sie regularnie, bo przegladarka potrafi zazadac tego samego kafla,
zanim poprzednie zadanie sie skonczy.

Model: pierwszy watek dla danego klucza LICZY, pozostale CZEKAJA i dostaja jego wynik.
Wyjatek lidera jest podnoszony u wszystkich — zadania sa identyczne, wiec i wynik bledu
jest ten sam; inaczej kazdy oczekujacy powtarzalby te sama nieudana prace.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Hashable


class _Call:
    __slots__ = ("done", "value", "error", "waiters")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.value: Any = None
        self.error: BaseException | None = None
        self.waiters = 0


class SingleFlight:
    """Jedno wykonanie na klucz, wspoldzielone przez wszystkich rownoczesnych."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: dict[Hashable, _Call] = {}
        self._deduplicated = 0

    def do(self, key: Hashable, fn: Callable[[], Any]) -> Any:
        with self._lock:
            call = self._calls.get(key)
            leader = call is None
            if leader:
                call = _Call()
                self._calls[key] = call
            else:
                call.waiters += 1
                self._deduplicated += 1

        assert call is not None
        if not leader:
            # Oczekujacy blokuje swojego workera, ale lider ma juz wlasnego, wiec nie ma
            # tu zakleszczenia — jest tylko czekanie zamiast powtorzonej pracy.
            call.done.wait()
            if call.error is not None:
                raise call.error
            return call.value

        try:
            call.value = fn()
        except BaseException as error:  # noqa: BLE001 - przekazywany oczekujacym
            call.error = error
            raise
        finally:
            with self._lock:
                self._calls.pop(key, None)
            call.done.set()
        return call.value

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"in_flight": len(self._calls), "deduplicated_total": self._deduplicated}


#: Wspolna instancja dla serwowania kafli. Klucz musi zawierac WSZYSTKO, co wplywa na
#: wynik: projekt, scene, wspolrzedne kafla, parametry wyswietlania i rewizje assetu.
tile_single_flight = SingleFlight()
