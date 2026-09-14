"""`scan_job.run`'s failure envelope: no window leaves a scan stranded at `queued`.

A scan row is the only thing a client can see. If the job behind it dies without moving the row
off `queued`, the client polls forever and the SSE stream never terminates -- which is strictly
worse than a `failed` scan with a reason, because a reason can be read and acted on.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sieve.config import settings
from sieve.db.models import Scan, ScanEvent
from sieve.worker import scan_job
from sieve.worker.main import run_once
from sieve.worker.queue import SCAN_KIND, enqueue

SECRET = "AKIAIOSFODNN7EXAMPLE"  # noqa: S105 -- a shaped, non-live test string


async def _queued_scan(session: AsyncSession) -> uuid.UUID:
    scan_id = uuid.uuid4()
    session.add(
        Scan(
            id=scan_id,
            source_type="tools_json",
            status="queued",
            tool_count=0,
            expires_at=datetime.now(UTC) + timedelta(days=settings.scan_ttl_days),
        )
    )
    await enqueue(session, SCAN_KIND, {"scan_id": str(scan_id)})
    await session.commit()
    return scan_id


async def test_a_failure_in_reset_fails_the_scan_instead_of_stranding_it_at_queued(
    sessions: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix round 2, I2: `_reset`, the `running` commit and the `scan.started` event used to sit
    outside `run()`'s `try:`, so a failure in any of them requeued the job and left the row at
    `queued` with a NULL error and no terminating event."""
    scan_id = await _queued_scan(session)

    async def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError(f"reset blew up while holding {SECRET}")

    monkeypatch.setattr(scan_job, "_reset", boom)

    assert await run_once(sessions, {SCAN_KIND: scan_job.run}) is True

    scan = await session.get(Scan, scan_id)
    assert scan is not None
    assert scan.status == "failed", "a scan whose job died must not stay claimable-looking"
    assert "reset blew up" in (scan.error or "")
    # SC-5 applies to `scans.error` here exactly as it does on every other failure path.
    assert SECRET not in (scan.error or "")

    types = list(
        (
            await session.execute(
                select(ScanEvent.type).where(ScanEvent.scan_id == scan_id).order_by(ScanEvent.id)
            )
        ).scalars()
    )
    assert "scan.failed" in types, "the stream has to terminate, or a viewer waits forever"


async def test_a_failure_in_the_started_event_also_fails_the_scan(
    sessions: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the same window: `_event` is the last of the four statements, and the
    `failed` path has to survive an aborted transaction left behind by a failed statement."""
    scan_id = await _queued_scan(session)
    calls: list[str] = []
    real_event = scan_job._event

    async def flaky(
        s: AsyncSession, sid: uuid.UUID, type_: str, payload: dict[str, object]
    ) -> None:
        calls.append(type_)
        if type_ == "scan.started":
            raise RuntimeError("the started event blew up")
        await real_event(s, sid, type_, payload)

    monkeypatch.setattr(scan_job, "_event", flaky)

    assert await run_once(sessions, {SCAN_KIND: scan_job.run}) is True

    scan = await session.get(Scan, scan_id)
    assert scan is not None
    assert scan.status == "failed"
    assert "started event blew up" in (scan.error or "")
    assert calls == ["scan.started", "scan.failed"]
