"""Daily retention purge: scans older than `SIEVE_SCAN_TTL_DAYS` go, children cascade."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession

from sieve.db.models import Job
from sieve.worker.queue import enqueue

log = logging.getLogger("sieve.worker.purge")

KIND = "purge"


async def purge_expired(session: AsyncSession) -> int:
    """Delete every scan past `expires_at`. `tools`, `verdicts` and events cascade (§18)."""
    result = await session.execute(text("DELETE FROM scans WHERE expires_at < now()"))
    await session.commit()
    deleted = int(cast("CursorResult[Any]", result).rowcount)
    if deleted:
        log.info("purge.deleted", extra={"scans": deleted})
    return deleted


async def schedule_next(session: AsyncSession, *, after: timedelta = timedelta(days=1)) -> None:
    await enqueue(session, KIND, {}, run_after=datetime.now(UTC) + after)


async def run(session: AsyncSession, job: Job) -> None:
    """Job handler: purge, then enqueue tomorrow's purge so the chain never stops."""
    await purge_expired(session)
    await schedule_next(session)
