"""Small, explicit bridge between async HTTP handlers and blocking workers.

GeoTile Label still uses synchronous generators for raster processing and model
inference.  Advancing those generators from an ``async def`` handler blocks the
event loop.  This module keeps the generators synchronous (important for GDAL and
Ultralytics) while advancing them in Starlette's worker pool.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Generator, TypeVar

from starlette.concurrency import run_in_threadpool


T = TypeVar("T")


class WorkerCancelled(RuntimeError):
    """Cooperative cancellation observed between two generator steps."""


@dataclass(frozen=True, slots=True)
class GeneratorStep:
    """A generator yield or its final return value.

    ``StopIteration`` must never cross a Future boundary: asyncio rejects it and
    can leave a request hanging.  ``has_value=False`` carries the value returned by
    the generator instead.
    """

    has_value: bool
    value: Any


async def run_blocking(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run one blocking callable without occupying the application event loop."""

    return await run_in_threadpool(function, *args, **kwargs)


def _advance_generator(
    generator: Generator[Any, None, T],
    min_interval_s: float,
    should_cancel: Callable[[], bool] | None,
) -> GeneratorStep:
    """Advance a generator until a progress update is due or it completes."""

    started = time.monotonic()
    latest: Any = None
    while True:
        if should_cancel is not None and should_cancel():
            raise WorkerCancelled("Worker cancelled")
        try:
            latest = next(generator)
        except StopIteration as stop:
            return GeneratorStep(False, stop.value)
        if min_interval_s <= 0 or time.monotonic() - started >= min_interval_s:
            return GeneratorStep(True, latest)


async def next_generator_in_threadpool(
    generator: Generator[Any, None, T],
    *,
    max_progress_hz: float = 4.0,
    should_cancel: Callable[[], bool] | None = None,
) -> GeneratorStep:
    """Advance a blocking generator in a worker and throttle progress events.

    The worker coalesces fast consecutive yields and returns the newest progress
    value at most ``max_progress_hz`` times per second.  Slow steps are returned as
    soon as they finish.  Cancellation is cooperative and checked between yields.
    """

    interval = 0.0 if max_progress_hz <= 0 else 1.0 / max_progress_hz
    return await run_in_threadpool(
        _advance_generator,
        generator,
        interval,
        should_cancel,
    )
