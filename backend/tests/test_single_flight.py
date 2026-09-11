"""Deduplikacja rownoczesnych identycznych obliczen — R0.5."""

from __future__ import annotations

import threading
import time

import pytest

from services.single_flight import SingleFlight


def test_concurrent_identical_calls_run_the_work_once():
    """Sedno R0.5: przy JP2 powtorzony dekad bloku to sekundy, nie milisekundy."""
    flight = SingleFlight()
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow():
        calls.append(1)
        started.set()
        release.wait(5)
        return "wynik"

    results: list[str] = []
    threads = [
        threading.Thread(target=lambda: results.append(flight.do("k", slow)))
        for _ in range(6)
    ]
    threads[0].start()
    assert started.wait(5)
    for thread in threads[1:]:
        thread.start()
    # Oczekujacy muszą już czekać, zanim lider skończy.
    time.sleep(0.05)
    release.set()
    for thread in threads:
        thread.join(5)

    assert len(calls) == 1
    assert results == ["wynik"] * 6
    assert flight.stats()["deduplicated_total"] == 5


def test_different_keys_do_not_block_each_other():
    flight = SingleFlight()
    calls: list[str] = []

    def work(name: str) -> str:
        calls.append(name)
        return name

    assert flight.do("a", lambda: work("a")) == "a"
    assert flight.do("b", lambda: work("b")) == "b"
    assert calls == ["a", "b"]


def test_leader_error_reaches_every_waiter():
    """Zadania sa identyczne, wiec i blad jest ten sam — powtarzanie go nie ma sensu."""
    flight = SingleFlight()
    started = threading.Event()
    release = threading.Event()

    def boom():
        started.set()
        release.wait(5)
        raise ValueError("dekod nieudany")

    errors: list[BaseException] = []

    def run():
        try:
            flight.do("k", boom)
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=run) for _ in range(4)]
    threads[0].start()
    assert started.wait(5)
    for thread in threads[1:]:
        thread.start()
    time.sleep(0.05)
    release.set()
    for thread in threads:
        thread.join(5)

    assert len(errors) == 4
    assert all(isinstance(error, ValueError) for error in errors)


def test_key_is_released_after_completion():
    """Po zakonczeniu klucz musi zniknac, inaczej kolejne zadanie dostaloby stary wynik."""
    flight = SingleFlight()
    assert flight.do("k", lambda: 1) == 1
    assert flight.stats()["in_flight"] == 0
    assert flight.do("k", lambda: 2) == 2


def test_key_is_released_after_failure():
    flight = SingleFlight()
    with pytest.raises(RuntimeError):
        flight.do("k", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert flight.stats()["in_flight"] == 0
    assert flight.do("k", lambda: "po bledzie") == "po bledzie"


def test_sequential_calls_are_not_counted_as_deduplicated():
    flight = SingleFlight()
    for _ in range(3):
        flight.do("k", lambda: 1)
    assert flight.stats()["deduplicated_total"] == 0
