"""Watchdog pamięci procesu potomnego — R1.3, §20."""

from __future__ import annotations

from services.scene_packages.process_watchdog import (
    ABORT_CANCELLED,
    ABORT_MEMORY,
    ABORT_NONE,
    MemoryWatchdog,
    sample_process_tree_rss,
)

GIB = 1024 ** 3


def _watchdog(samples, *, limit=int(4.5 * GIB), cancel=None):
    """Watchdog z ustalonym ciągiem odczytów RSS; zapisuje wywołania przerwania."""
    killed: list[bool] = []
    queue = list(samples)

    def sampler() -> int:
        return queue.pop(0) if queue else (samples[-1] if samples else 0)

    watchdog = MemoryWatchdog(
        limit_bytes=limit,
        sample_rss=sampler,
        terminate=lambda: killed.append(True),
        cancel_check=cancel,
    )
    return watchdog, killed


def test_process_within_limit_is_not_touched():
    watchdog, killed = _watchdog([int(3.0 * GIB), int(3.4 * GIB), int(3.6 * GIB)])
    for _ in range(3):
        assert watchdog._tick() is False
    assert killed == []
    assert watchdog.result.aborted is False
    assert watchdog.result.reason == ABORT_NONE
    assert watchdog.result.peak_rss_bytes == int(3.6 * GIB)


def test_process_is_terminated_at_the_hard_limit():
    """Przerwanie ma nastąpić zanim system zacznie wymiatać pamięć."""
    watchdog, _ = _watchdog([int(3.0 * GIB), int(4.6 * GIB)])
    assert watchdog._tick() is False
    assert watchdog._tick() is True
    assert watchdog.result.aborted is True
    assert watchdog.result.reason == ABORT_MEMORY
    assert watchdog.result.peak_rss_bytes == int(4.6 * GIB)


def test_limit_is_inclusive():
    watchdog, _ = _watchdog([int(4.5 * GIB)])
    assert watchdog._tick() is True


def test_cancellation_wins_over_memory_check():
    """Anulowanie nie może czekać na kolejny pomiar pamięci."""
    watchdog, _ = _watchdog([int(1.0 * GIB)], cancel=lambda: True)
    assert watchdog._tick() is True
    assert watchdog.result.reason == ABORT_CANCELLED


def test_sampler_failure_does_not_abort_the_build():
    """Znikający proces to nie powód do przerywania czegokolwiek."""

    def boom() -> int:
        raise OSError("proces zniknął")

    watchdog = MemoryWatchdog(
        limit_bytes=int(4.5 * GIB), sample_rss=boom, terminate=lambda: None,
    )
    assert watchdog._tick() is False
    assert watchdog.result.aborted is False


def test_context_manager_starts_and_stops_cleanly():
    watchdog, _ = _watchdog([int(1.0 * GIB)])
    with watchdog:
        pass
    assert watchdog.result.aborted is False


def test_tree_sampling_includes_children():
    class _Info:
        def __init__(self, rss):
            self.rss = rss

    class _Proc:
        def __init__(self, rss, children=()):
            self._rss = rss
            self._children = list(children)

        def memory_info(self):
            return _Info(self._rss)

        def children(self, recursive=False):
            return self._children

    parent = _Proc(1000, [_Proc(2000), _Proc(3000)])
    assert sample_process_tree_rss(parent) == 6000


def test_tree_sampling_survives_a_dead_child():
    class _Info:
        def __init__(self, rss):
            self.rss = rss

    class _Dead:
        def memory_info(self):
            raise OSError("zniknął")

    class _Proc:
        def memory_info(self):
            return _Info(500)

        def children(self, recursive=False):
            return [_Dead()]

    assert sample_process_tree_rss(_Proc()) == 500
