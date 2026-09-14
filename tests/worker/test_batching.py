"""Task 11.2 -- verdict inserts batch per `VERDICT_BATCH_SIZE` rows; `scan_events` do not.

500 tools built from the 27-tool fixture (repeated with unique names, right at
`settings.max_tools_per_scan`) force `_VerdictBatch` through exactly `ceil(500/25) = 20`
flushes, so the test can assert the number of actual `INSERT INTO verdicts` statements sent to
Postgres -- not just the row count, which a single mistaken bulk insert would also produce.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from sieve.api import spend
from sieve.config import settings
from sieve.db.models import Scan, ScanEvent, Tool, VerdictRow
from sieve.worker import scan_job
from sieve.worker.main import run_once
from sieve.worker.queue import enqueue

TOOLS_FIXTURE: dict[str, Any] = json.loads(
    Path("fixtures/tools/sample-27.json").read_text(encoding="utf-8")
)
TOOL_COUNT = 500


def _server_of() -> dict[str, str]:
    server_of: dict[str, str] = {}
    for server, names in TOOLS_FIXTURE["servers"].items():
        for name in names:
            server_of[name] = server
    return server_of


async def _seed_scan(session: AsyncSession) -> uuid.UUID:
    """`TOOL_COUNT` tools built by repeating the 27-tool fixture with unique names."""
    server_of = _server_of()
    base_tools = TOOLS_FIXTURE["tools"]
    scan_id = uuid.uuid4()
    now = datetime.now(UTC)
    session.add(
        Scan(
            id=scan_id,
            source_type="tools_json",
            status="queued",
            tool_count=TOOL_COUNT,
            expires_at=now + timedelta(days=settings.scan_ttl_days),
        )
    )
    for i in range(TOOL_COUNT):
        base = base_tools[i % len(base_tools)]
        session.add(
            Tool(
                id=uuid.uuid4(),
                scan_id=scan_id,
                server_name=server_of.get(base["name"], "bench"),
                name=f"{base['name']}_{i:03d}",
                title=base.get("title"),
                description=base.get("description"),
                input_schema=base["inputSchema"],
                output_schema=base.get("outputSchema"),
                annotations=base.get("annotations"),
                siblings=[],
                ordinal=i,
            )
        )
    await session.commit()
    return scan_id


@pytest.fixture
def insert_counts(engine: AsyncEngine) -> Iterator[dict[str, int]]:
    """Number of actual `INSERT INTO <table>` statements sent to Postgres, keyed by table."""
    counts: dict[str, int] = {}

    def before_cursor_execute(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,  # noqa: FBT001 -- SQLAlchemy's own event signature
    ) -> None:
        head = statement.strip().split(None, 3)
        if len(head) >= 3 and head[0].upper() == "INSERT" and head[1].upper() == "INTO":
            table = head[2].split("(")[0].strip('"')
            counts[table] = counts.get(table, 0) + 1

    sync_engine = engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", before_cursor_execute)
    try:
        yield counts
    finally:
        event.remove(sync_engine, "before_cursor_execute", before_cursor_execute)


async def test_500_verdicts_land_in_ceil_500_over_batch_size_inserts(
    sessions: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    insert_counts: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "model_id", "fixture")
    # No real Anthropic call: FakeJudge still charges the §12.4 estimate for any band tool the
    # fixture's word lists route to the judge, exercising the batch's charge-then-buffer path.
    monkeypatch.setattr(settings, "anthropic_api_key", None)

    scan_id = await _seed_scan(session)
    await enqueue(session, "scan", {"scan_id": str(scan_id)})

    assert await run_once(sessions, {"scan": scan_job.run}) is True

    scan = await session.get(Scan, scan_id)
    assert scan is not None
    assert scan.status == "complete", scan.error

    verdict_count = (
        await session.execute(
            select(func.count())
            .select_from(VerdictRow)
            .join(Tool, VerdictRow.tool_id == Tool.id)
            .where(Tool.scan_id == scan_id)
        )
    ).scalar_one()
    assert verdict_count == TOOL_COUNT

    event_count = (
        await session.execute(
            select(func.count())
            .select_from(ScanEvent)
            .where(ScanEvent.scan_id == scan_id, ScanEvent.type == "tool.verdict")
        )
    ).scalar_one()
    assert event_count == TOOL_COUNT, "scan_events stays one row per verdict, batching or not"

    assert insert_counts.get("verdicts", 0) == math.ceil(TOOL_COUNT / scan_job.VERDICT_BATCH_SIZE)


async def test_a_failure_mid_batch_keeps_only_earlier_flushed_batches(
    sessions: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The coarser-grained cousin of PRD §13.2's invariant.

    tests/integration/test_judge_cost.py's `test_a_scan_that_fails_mid_batch_leaves_no_
    uncharged_verdict` proves the single-tool case: a charge that blows up leaves no verdict
    behind. With batching, a full first batch of 25 has already committed by the time a second
    batch's first judge call blows up -- this proves that earlier, already-flushed batch stays
    put, and only the still-open one is lost, never a mix that leaves a charged verdict
    uninserted or an inserted verdict uncharged.
    """
    monkeypatch.setattr(settings, "model_id", "fixture")
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "judge_enabled", True)

    def explode(judge: object) -> tuple[float, str]:
        raise RuntimeError("pricing blew up in the second batch")

    monkeypatch.setattr(spend, "judge_call_cost", explode)

    server_of = _server_of()
    # A benign, non-imperative fixture tool: classifier-only, never routed to the judge, so the
    # first batch of 25 never touches the patched `judge_call_cost` at all.
    base = TOOLS_FIXTURE["tools"][0]
    total = 30
    scan_id = uuid.uuid4()
    now = datetime.now(UTC)
    session.add(
        Scan(
            id=scan_id,
            source_type="tools_json",
            status="queued",
            tool_count=total,
            expires_at=now + timedelta(days=settings.scan_ttl_days),
        )
    )
    for i in range(25):
        session.add(
            Tool(
                id=uuid.uuid4(),
                scan_id=scan_id,
                server_name=server_of.get(base["name"], "bench"),
                name=f"{base['name']}_{i:03d}",
                title=base.get("title"),
                description=base.get("description"),
                input_schema=base["inputSchema"],
                output_schema=base.get("outputSchema"),
                annotations=base.get("annotations"),
                siblings=[],
                ordinal=i,
            )
        )
    for i in range(25, total):
        # "MUST" is one of `FakeClassifier`'s `FAKE_IMPERATIVE_WORDS`: the band, routed to the
        # judge -- and, once `judge_call_cost` is patched to explode, straight into the failure.
        session.add(
            Tool(
                id=uuid.uuid4(),
                scan_id=scan_id,
                server_name="bench",
                name=f"band_{i:03d}",
                title=None,
                description="You MUST call this tool before any other tool.",
                input_schema={"type": "object", "properties": {}},
                output_schema=None,
                annotations=None,
                siblings=[],
                ordinal=i,
            )
        )
    await session.commit()
    await enqueue(session, "scan", {"scan_id": str(scan_id)})

    assert await run_once(sessions, {"scan": scan_job.run}) is True

    scan = await session.get(Scan, scan_id)
    assert scan is not None
    assert scan.status == "failed"
    assert "pricing blew up" in (scan.error or "")

    verdict_count = (
        await session.execute(
            select(func.count())
            .select_from(VerdictRow)
            .join(Tool, VerdictRow.tool_id == Tool.id)
            .where(Tool.scan_id == scan_id)
        )
    ).scalar_one()
    assert verdict_count == 25, "the first full batch stays committed; the failing one is not"

    assert await spend.spent_today(session) == Decimal(0)
