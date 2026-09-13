"""A Postgres job queue (PRD §13.4).

`SELECT … FOR UPDATE SKIP LOCKED` is what makes this safe with more than one worker: the row
is locked inside the same statement that claims it, so two workers racing on one job see one
winner and one empty result, with no advisory locks and no external broker.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from sieve.db.models import Job

# `FOR UPDATE SKIP LOCKED` inside a CTE, then an UPDATE joined to it: one statement, one
# round trip, and the losing worker's CTE simply selects nothing.
_CLAIM_SQL = """
WITH next AS (
  SELECT id FROM jobs
  WHERE status = 'queued' AND run_after <= now()
  ORDER BY created_at
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
UPDATE jobs j SET status = 'running', locked_at = now(), locked_by = :worker_id,
                  heartbeat_at = now(), attempts = attempts + 1
FROM next WHERE j.id = next.id
RETURNING j.*
"""


async def enqueue(
    session: AsyncSession,
    kind: str,
    payload: dict[str, Any],
    *,
    run_after: datetime | None = None,
) -> uuid.UUID:
    job = Job(id=uuid.uuid4(), kind=kind, payload=payload)
    if run_after is not None:
        job.run_after = run_after
    session.add(job)
    await session.commit()
    return job.id


async def claim(session: AsyncSession, worker_id: str, *, visibility_s: int = 120) -> Job | None:
    """Claim one ready job, or `None`. `visibility_s` is the deadline `reap` enforces."""
    statement = (
        text(_CLAIM_SQL)
        .bindparams(bindparam("worker_id", worker_id))
        .columns(*Job.__table__.columns)
    )
    # `populate_existing`: RETURNING gives the post-UPDATE row, but a session that already
    # loaded this job would otherwise hand back the stale identity-map copy (`attempts` one
    # behind), which is exactly the value the reaper and the backoff depend on.
    result = await session.execute(
        select(Job).from_statement(statement).execution_options(populate_existing=True)
    )
    job = result.scalars().first()
    await session.commit()
    return job


async def heartbeat(session: AsyncSession, job_id: uuid.UUID) -> None:
    await session.execute(
        text("UPDATE jobs SET heartbeat_at = now() WHERE id = :id AND status = 'running'"),
        {"id": job_id},
    )
    await session.commit()


async def complete(session: AsyncSession, job_id: uuid.UUID) -> None:
    await session.execute(
        text(
            "UPDATE jobs SET status = 'done', locked_by = NULL, locked_at = NULL, "
            "heartbeat_at = NULL WHERE id = :id"
        ),
        {"id": job_id},
    )
    await session.commit()


async def fail(
    session: AsyncSession, job_id: uuid.UUID, error: str, *, backoff_base_s: int = 10
) -> None:
    """Requeue with exponential backoff, or mark `dead` once `max_attempts` is spent."""
    await session.execute(
        text(
            """
            UPDATE jobs SET
              status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'queued' END,
              run_after = CASE
                  WHEN attempts >= max_attempts THEN run_after
                  ELSE now() + make_interval(
                      secs => :base * power(2, greatest(attempts - 1, 0))::double precision)
              END,
              last_error = :error,
              locked_by = NULL,
              locked_at = NULL,
              heartbeat_at = NULL
            WHERE id = :id
            """
        ),
        {"id": job_id, "error": error, "base": backoff_base_s},
    )
    await session.commit()


async def reap(session: AsyncSession, *, visibility_s: int = 120) -> int:
    """Requeue running jobs whose heartbeat went stale (a worker died mid-job)."""
    result = await session.execute(
        text(
            """
            UPDATE jobs SET status = 'queued', locked_by = NULL, locked_at = NULL,
                            heartbeat_at = NULL
            WHERE status = 'running'
              AND heartbeat_at < now() - make_interval(secs => :visibility)
            """
        ),
        {"visibility": visibility_s},
    )
    await session.commit()
    # `execute(text(...))` is typed as Result; a DML statement always returns a CursorResult.
    return int(cast("CursorResult[Any]", result).rowcount)
