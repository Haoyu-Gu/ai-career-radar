from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from ..db import connect
from ..settings import settings
from .refresh import create_refresh, run_refresh


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def refresh_is_due() -> bool:
    with connect() as conn:
        active = conn.execute(
            "SELECT 1 FROM refresh_runs WHERE status IN ('queued','running') LIMIT 1"
        ).fetchone()
        if active:
            return False
        latest_jobs = conn.execute(
            "SELECT MAX(finished_at) FROM scan_batches WHERE kind='jobs' AND status IN ('complete','partial')"
        ).fetchone()[0]
        latest_papers = conn.execute(
            """SELECT MAX(finished_at) FROM scan_batches
            WHERE kind='papers' AND status IN ('complete','partial')
              AND source_slug IN ('openalex','crossref')"""
        ).fetchone()[0]
    cutoff = datetime.now(UTC) - timedelta(hours=settings.auto_refresh_hours)
    job_time = _parse_timestamp(latest_jobs)
    paper_time = _parse_timestamp(latest_papers)
    return job_time is None or paper_time is None or job_time < cutoff or paper_time < cutoff


async def scheduler_loop() -> None:
    """Persistent, idempotent scheduler tied to the local app process."""
    await asyncio.sleep(8)
    while True:
        if settings.auto_refresh_enabled and refresh_is_due():
            run_id = create_refresh()
            await run_refresh(run_id)
        await asyncio.sleep(max(60, settings.scheduler_check_minutes * 60))


async def stop_scheduler(task: asyncio.Task | None) -> None:
    if not task:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
