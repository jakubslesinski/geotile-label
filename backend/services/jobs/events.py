"""Persistent JSONL event log and reconnectable polling stream."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator

from models.job import TERMINAL_JOB_STATUSES
from services.jobs.store import interprocess_lock, job_dir, mutate_state, read_state, utc_now


def append_event(
    project_id: str,
    job_id: str,
    event: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sequence = 0

    def increment(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal sequence
        sequence = int(state.get("event_seq") or 0) + 1
        state["event_seq"] = sequence
        return state

    directory = job_dir(project_id, job_id)
    path = directory / "events.jsonl"
    with interprocess_lock(directory / ".events.lock") as acquired:
        if not acquired:
            raise TimeoutError(f"Timed out locking events for job {job_id}")
        mutate_state(project_id, job_id, increment)
        record = {
            "seq": sequence,
            "timestamp": utc_now(),
            "event": str(event),
            "data": data or {},
        }
        line = (json.dumps(record, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        with path.open("ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return record


def read_events(project_id: str, job_id: str, after: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
    path = job_dir(project_id, job_id) / "events.jsonl"
    if not path.is_file():
        return []
    result: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if int(item.get("seq") or 0) <= after:
                    continue
                result.append(item)
                if len(result) >= limit:
                    break
    except OSError:
        return result
    return result


async def stream_job_events(
    project_id: str,
    job_id: str,
    *,
    after: int = 0,
    legacy_only: bool = False,
    poll_interval_s: float = 0.2,
) -> AsyncIterator[dict[str, str]]:
    """Tail events independently of the worker and of any individual SSE client."""

    cursor = max(0, int(after))
    terminal_seen = False
    while True:
        records = read_events(project_id, job_id, after=cursor)
        for record in records:
            cursor = max(cursor, int(record.get("seq") or 0))
            event_name = str(record.get("event") or "message")
            if legacy_only and event_name not in {"progress", "complete", "cancelled", "error"}:
                continue
            if event_name in {"complete", "cancelled", "error"}:
                terminal_seen = True
            yield {
                "event": event_name,
                "data": json.dumps(record.get("data") or {}, ensure_ascii=False, default=str),
                "id": str(cursor),
            }
        state = read_state(project_id, job_id)
        if state.get("status") in TERMINAL_JOB_STATUSES and not records:
            if legacy_only and not terminal_seen:
                status = str(state.get("status"))
                if status == "completed":
                    event_name, data = "complete", state.get("artifacts") or {"status": "completed"}
                elif status == "cancelled":
                    event_name, data = "cancelled", {"status": "cancelled"}
                else:
                    event_name, data = "error", {"error": state.get("error") or status}
                yield {"event": event_name, "data": json.dumps(data, ensure_ascii=False)}
            return
        await asyncio.sleep(poll_interval_s)
