"""The worker loop: dispatch, failure handling, and the stop flag SIGTERM sets."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sieve.db.models import Job
from sieve.worker.main import default_dispatch, run_forever, run_once
from sieve.worker.queue import enqueue


def test_both_job_kinds_are_dispatchable() -> None:
    """A registry missing `scan` would leave every posted scan queued forever."""
    assert set(default_dispatch()) == {"scan", "purge"}


async def test_an_idle_tick_reports_no_work(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    assert await run_once(sessions, {}) is False


async def test_a_handled_job_is_marked_done(
    sessions: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    seen: list[uuid.UUID] = []

    async def handler(s: AsyncSession, job: Job) -> None:
        seen.append(job.id)

    async with sessions() as setup:
        job_id = await enqueue(setup, "scan", {"scan_id": str(uuid.uuid4())})

    assert await run_once(sessions, {"scan": handler}) is True
    assert seen == [job_id]
    status = (await session.execute(select(Job.status).where(Job.id == job_id))).scalar_one()
    assert status == "done"


async def test_a_raising_handler_requeues_instead_of_killing_the_loop(
    sessions: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    async def handler(s: AsyncSession, job: Job) -> None:
        raise RuntimeError("boom")

    async with sessions() as setup:
        job_id = await enqueue(setup, "scan", {})

    assert await run_once(sessions, {"scan": handler}) is True
    row = (await session.execute(select(Job.status, Job.last_error).where(Job.id == job_id))).one()
    assert row.status == "queued"
    assert "boom" in row.last_error


async def test_an_unknown_kind_fails_the_job_not_the_worker(
    sessions: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    async with sessions() as setup:
        job_id = await enqueue(setup, "not_a_kind", {})

    assert await run_once(sessions, {}) is True
    error = (await session.execute(select(Job.last_error).where(Job.id == job_id))).scalar_one()
    assert "not_a_kind" in error


async def test_the_stop_event_ends_the_loop(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """What SIGTERM sets: the current job finishes and the loop then exits."""
    stop = asyncio.Event()
    stop.set()
    await asyncio.wait_for(run_forever(sessions, {}, stop=stop), timeout=5)
