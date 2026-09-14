"""The Postgres queue: SKIP LOCKED exclusivity, the reaper, backoff and the dead letter."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sieve.db.models import Job
from sieve.worker.queue import (
    REMOTE_SCAN_KIND,
    SCAN_KIND,
    claim,
    complete,
    enqueue,
    fail,
    heartbeat,
    reap,
)

#: What a plain worker can run. `claim` takes a capability declaration, not a convenience
#: filter, so every call site has to say what it is able to handle (fix round 2, I1).
KINDS = (SCAN_KIND, "purge")


async def test_enqueue_then_claim_round_trip(session: AsyncSession) -> None:
    job_id = await enqueue(session, "scan", {"scan_id": "abc"})
    claimed = await claim(session, "worker-1", KINDS)
    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.status == "running"
    assert claimed.locked_by == "worker-1"
    assert claimed.attempts == 1
    assert claimed.heartbeat_at is not None
    assert claimed.payload == {"scan_id": "abc"}


async def test_an_empty_queue_claims_nothing(session: AsyncSession) -> None:
    assert await claim(session, "worker-1", KINDS) is None


async def test_a_future_run_after_is_not_claimed(session: AsyncSession) -> None:
    later = datetime.now(UTC) + timedelta(minutes=5)
    await enqueue(session, "purge", {}, run_after=later)
    assert await claim(session, "worker-1", KINDS) is None


async def test_two_concurrent_claims_never_return_the_same_job(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """50 rounds of two workers racing on one job. SKIP LOCKED is the whole point."""
    for _ in range(50):
        async with sessions() as setup:
            await enqueue(setup, "scan", {})

        async def one(worker: str) -> Job | None:
            async with sessions() as s:
                return await claim(s, worker, KINDS)

        first, second = await asyncio.gather(one("worker-a"), one("worker-b"))
        claimed = [j for j in (first, second) if j is not None]
        assert len(claimed) == 1, "two workers claimed the same job"

        async with sessions() as cleanup:
            await cleanup.execute(text("DELETE FROM jobs"))
            await cleanup.commit()


async def test_heartbeat_moves_the_visibility_deadline(session: AsyncSession) -> None:
    await enqueue(session, "scan", {})
    job = await claim(session, "worker-1", KINDS)
    assert job is not None
    before = job.heartbeat_at
    await session.execute(
        update(Job).where(Job.id == job.id).values(heartbeat_at=text("now() - interval '30 s'"))
    )
    await session.commit()
    await heartbeat(session, job.id, worker_id="worker-1")
    after = (await session.execute(select(Job.heartbeat_at).where(Job.id == job.id))).scalar_one()
    assert before is not None and after is not None and after > before - timedelta(seconds=30)


async def test_a_stale_job_is_reaped_and_can_be_reclaimed(session: AsyncSession) -> None:
    await enqueue(session, "scan", {})
    job = await claim(session, "worker-gone", KINDS)
    assert job is not None
    await session.execute(
        update(Job).where(Job.id == job.id).values(heartbeat_at=text("now() - interval '10 min'"))
    )
    await session.commit()

    assert await reap(session, visibility_s=120) == 1
    again = await claim(session, "worker-2", KINDS)
    assert again is not None and again.id == job.id
    assert again.attempts == 2, "a reaped job keeps its attempt history"


async def test_a_live_job_is_not_reaped(session: AsyncSession) -> None:
    await enqueue(session, "scan", {})
    assert await claim(session, "worker-1", KINDS) is not None
    assert await reap(session, visibility_s=120) == 0


async def test_complete_marks_the_job_done(session: AsyncSession) -> None:
    await enqueue(session, "scan", {})
    job = await claim(session, "worker-1", KINDS)
    assert job is not None
    await complete(session, job.id, worker_id="worker-1")
    status = (await session.execute(select(Job.status).where(Job.id == job.id))).scalar_one()
    assert status == "done"
    assert await claim(session, "worker-1", KINDS) is None


async def test_fail_requeues_with_exponential_backoff(session: AsyncSession) -> None:
    await enqueue(session, "scan", {})
    job = await claim(session, "worker-1", KINDS)
    assert job is not None
    await fail(session, job.id, "boom", worker_id="worker-1", backoff_base_s=10)
    row = (
        await session.execute(
            select(Job.status, Job.last_error, Job.run_after, Job.locked_by).where(Job.id == job.id)
        )
    ).one()
    assert row.status == "queued"
    assert row.last_error == "boom"
    assert row.locked_by is None
    assert row.run_after > datetime.now(UTC) + timedelta(seconds=5)

    # Second failure backs off further than the first (10 * 2^1 vs 10 * 2^0).
    await session.execute(update(Job).where(Job.id == job.id).values(run_after=text("now()")))
    await session.commit()
    job2 = await claim(session, "worker-1", KINDS)
    assert job2 is not None and job2.attempts == 2
    await fail(session, job2.id, "boom again", worker_id="worker-1", backoff_base_s=10)
    run_after2 = (await session.execute(select(Job.run_after).where(Job.id == job.id))).scalar_one()
    assert run_after2 > datetime.now(UTC) + timedelta(seconds=15)


async def test_fail_at_max_attempts_marks_the_job_dead(session: AsyncSession) -> None:
    await enqueue(session, "scan", {})
    job_id = (await session.execute(select(Job.id))).scalar_one()
    await session.execute(update(Job).where(Job.id == job_id).values(max_attempts=1))
    await session.commit()

    job = await claim(session, "worker-1", KINDS)
    assert job is not None
    await fail(session, job.id, "fatal", worker_id="worker-1")
    row = (await session.execute(select(Job.status, Job.last_error).where(Job.id == job_id))).one()
    assert row.status == "dead"
    assert row.last_error == "fatal"
    assert await claim(session, "worker-1", KINDS) is None


async def test_fail_on_an_unknown_job_is_a_no_op(session: AsyncSession) -> None:
    assert await fail(session, uuid.uuid4(), "nothing to see", worker_id="w") is False
    assert await complete(session, uuid.uuid4(), worker_id="w") is False
    assert await heartbeat(session, uuid.uuid4(), worker_id="w") is False


async def test_jobs_are_claimed_oldest_first(session: AsyncSession) -> None:
    first = await enqueue(session, "scan", {"n": 1})
    await session.execute(
        update(Job).where(Job.id == first).values(created_at=text("now() - interval '1 min'"))
    )
    await session.commit()
    await enqueue(session, "scan", {"n": 2})
    claimed = await claim(session, "worker-1", KINDS)
    assert claimed is not None and claimed.id == first


async def test_a_reaped_worker_can_no_longer_close_its_old_job(session: AsyncSession) -> None:
    """`locked_by` is the fence token: without it two workers run one job and A closes B's.

    Claim by A, reap, claim by B; A's `complete` and `heartbeat` must be no-ops, B's must work.
    """
    await enqueue(session, "scan", {})
    first = await claim(session, "worker-a", KINDS)
    assert first is not None

    await session.execute(
        update(Job).where(Job.id == first.id).values(heartbeat_at=text("now() - interval '10 min'"))
    )
    await session.commit()
    assert await reap(session, visibility_s=120) == 1

    second = await claim(session, "worker-b", KINDS)
    assert second is not None and second.id == first.id

    assert await heartbeat(session, first.id, worker_id="worker-a") is False
    assert await complete(session, first.id, worker_id="worker-a") is False
    status = (await session.execute(select(Job.status).where(Job.id == first.id))).scalar_one()
    assert status == "running", "A must not close a job B is still executing"

    assert await heartbeat(session, second.id, worker_id="worker-b") is True
    assert await complete(session, second.id, worker_id="worker-b") is True
    status = (await session.execute(select(Job.status).where(Job.id == first.id))).scalar_one()
    assert status == "done"


async def test_a_reaped_worker_can_no_longer_fail_its_old_job(session: AsyncSession) -> None:
    await enqueue(session, "scan", {})
    first = await claim(session, "worker-a", KINDS)
    assert first is not None
    await session.execute(
        update(Job).where(Job.id == first.id).values(heartbeat_at=text("now() - interval '10 min'"))
    )
    await session.commit()
    await reap(session, visibility_s=120)
    second = await claim(session, "worker-b", KINDS)
    assert second is not None

    assert await fail(session, first.id, "A gave up", worker_id="worker-a") is False
    row = (
        await session.execute(select(Job.status, Job.last_error).where(Job.id == first.id))
    ).one()
    assert row.status == "running"
    assert row.last_error is None, "A's error must not overwrite B's in-flight job"

    assert await fail(session, second.id, "B gave up", worker_id="worker-b") is True


# --- kind routing (fix round 2, I1) ------------------------------------------------------------


async def test_a_claim_never_takes_a_kind_the_worker_did_not_ask_for(
    session: AsyncSession,
) -> None:
    await enqueue(session, REMOTE_SCAN_KIND, {"scan_id": "abc"})
    assert await claim(session, "worker-1", KINDS) is None
    claimed = await claim(session, "worker-sandbox", [REMOTE_SCAN_KIND])
    assert claimed is not None
    assert claimed.kind == REMOTE_SCAN_KIND


async def test_a_claim_with_no_kinds_at_all_takes_nothing(session: AsyncSession) -> None:
    """An empty capability list is a worker that can run nothing, not a wildcard."""
    await enqueue(session, SCAN_KIND, {"scan_id": "abc"})
    assert await claim(session, "worker-1", []) is None


async def test_an_unwanted_kind_does_not_hide_a_wanted_one_behind_it(
    session: AsyncSession,
) -> None:
    """`ORDER BY created_at` picks the oldest *claimable* row, not the oldest row."""
    await enqueue(session, REMOTE_SCAN_KIND, {"scan_id": "older"})
    await enqueue(session, SCAN_KIND, {"scan_id": "newer"})
    claimed = await claim(session, "worker-1", KINDS)
    assert claimed is not None
    assert claimed.payload == {"scan_id": "newer"}
