"""P0.3 regressions: blocking workers must not stall the ASGI event loop."""

from __future__ import annotations

import asyncio
import pathlib
import statistics
import sys
import time

import httpx
from fastapi import FastAPI

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.async_bridge import next_generator_in_threadpool, run_blocking


def test_generator_return_value_does_not_cross_future_as_stop_iteration():
    def worker():
        yield {"done": 1}
        return {"result": 42}

    async def consume():
        generator = worker()
        first = await next_generator_in_threadpool(generator, max_progress_hz=0)
        last = await next_generator_in_threadpool(generator, max_progress_hz=0)
        return first, last

    first, last = asyncio.run(consume())
    assert first.has_value is True
    assert first.value == {"done": 1}
    assert last.has_value is False
    assert last.value == {"result": 42}


def test_health_p95_stays_below_250_ms_during_blocking_worker():
    app = FastAPI()

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.post("/work")
    async def work():
        await run_blocking(time.sleep, 0.5)
        return {"status": "done"}

    async def measure() -> list[float]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            work_task = asyncio.create_task(client.post("/work"))
            await asyncio.sleep(0.03)
            latencies = []
            while not work_task.done():
                started = time.perf_counter()
                response = await client.get("/api/health")
                latencies.append(time.perf_counter() - started)
                assert response.json() == {"status": "ok"}
                await asyncio.sleep(0.01)
            assert (await work_task).status_code == 200
            return latencies

    latencies = asyncio.run(measure())
    assert len(latencies) >= 10
    p95 = statistics.quantiles(latencies, n=100, method="inclusive")[94]
    assert p95 < 0.250, f"health p95 was {p95:.3f}s"


def test_fast_generator_progress_is_coalesced_to_four_hz():
    def worker():
        for index in range(80):
            time.sleep(0.01)
            yield {"done": index + 1}
        return "complete"

    async def consume():
        generator = worker()
        updates = []
        while True:
            step = await next_generator_in_threadpool(generator, max_progress_hz=4.0)
            if not step.has_value:
                return updates, step.value
            updates.append(step.value)

    updates, result = asyncio.run(consume())
    assert result == "complete"
    assert 2 <= len(updates) <= 4
    assert updates == sorted(updates, key=lambda item: item["done"])


if __name__ == "__main__":
    for name, function in list(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
            print(f"OK {name}")
    print("all async bridge tests passed")
