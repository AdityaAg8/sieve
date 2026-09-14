"""Retention: scans past `expires_at` go, and the purge re-enqueues itself."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sieve.db.models import Job, Scan, Tool, VerdictRow
from sieve.worker import purge
from sieve.worker.main import default_dispatch
from sieve.worker.queue import claim, enqueue

#: Exactly what the plain worker advertises; `claim` takes a capability list (fix round 2, I1).
KINDS = tuple(default_dispatch())


async def make_scan(session: AsyncSession, *, expires_in: timedelta) -> uuid.UUID:
    now = datetime.now(UTC)
    scan = Scan(
        id=uuid.uuid4(),
        source_type="tools_json",
        status="complete",
        tool_count=1,
        expires_at=now + expires_in,
    )
    session.add(scan)
    await session.flush()
    tool = Tool(
        id=uuid.uuid4(),
        scan_id=scan.id,
        name="t",
        input_schema={"type": "object"},
        ordinal=0,
    )
    session.add(tool)
    await session.flush()
    session.add(
        VerdictRow(
            id=uuid.uuid4(),
            tool_id=tool.id,
            verdict="dismissed",
            stage="classifier",
            predicted_class="benign",
            class_probabilities={"benign": 0.9, "imperative_benign": 0.05, "poisoned": 0.05},
            poisoned_probability=0.05,
            confidence=0.9,
            sub_labels=[],
            owasp=[],
            evidence_spans=[],
            model_version="fake@0",
        )
    )
    await session.commit()
    return scan.id


async def test_expired_scans_are_deleted_and_children_cascade(session: AsyncSession) -> None:
    stale = await make_scan(session, expires_in=timedelta(days=-1))
    fresh = await make_scan(session, expires_in=timedelta(days=7))

    assert await purge.purge_expired(session) == 1

    remaining = {s for s in (await session.execute(select(Scan.id))).scalars()}
    assert remaining == {fresh}
    assert stale not in remaining
    assert (await session.execute(select(func.count()).select_from(VerdictRow))).scalar_one() == 1
    assert (await session.execute(select(func.count()).select_from(Tool))).scalar_one() == 1


async def test_purging_nothing_is_not_an_error(session: AsyncSession) -> None:
    await make_scan(session, expires_in=timedelta(days=7))
    assert await purge.purge_expired(session) == 0


async def test_the_purge_job_re_enqueues_itself(session: AsyncSession) -> None:
    await make_scan(session, expires_in=timedelta(days=-1))
    await enqueue(session, purge.KIND, {})
    job = await claim(session, "worker-1", KINDS)
    assert job is not None

    await purge.run(session, job)

    queued = list(
        (
            await session.execute(select(Job).where(Job.kind == purge.KIND, Job.status == "queued"))
        ).scalars()
    )
    assert len(queued) == 1, "the chain never stops"
    assert queued[0].run_after > datetime.now(UTC) + timedelta(hours=23)
    assert (await session.execute(select(func.count()).select_from(Scan))).scalar_one() == 0


async def test_a_future_purge_is_not_claimable_yet(session: AsyncSession) -> None:
    await purge.schedule_next(session)
    assert await claim(session, "worker-1", KINDS) is None


async def test_the_purge_chain_is_bootstrapped_once(session: AsyncSession) -> None:
    """Nothing else enqueues the first purge, so a fresh database would never retain-purge."""
    assert await purge.ensure_scheduled(session) is True
    assert await purge.ensure_scheduled(session) is False

    queued = (
        await session.execute(select(func.count()).select_from(Job).where(Job.kind == purge.KIND))
    ).scalar_one()
    assert queued == 1
    assert await claim(session, "worker-1", KINDS) is not None, "the first one is due immediately"
