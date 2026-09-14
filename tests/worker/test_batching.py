"""Task 11.2 -- verdict inserts batch per `VERDICT_BATCH_SIZE` rows or `verdict_flush_ms`,
whichever comes first; `scan_events` do not.

500 tools built from the 27-tool fixture (repeated with unique names, right at
`settings.max_tools_per_scan`) force `_VerdictBatch` through exactly `ceil(500/25) = 20`
flushes, so the test can assert the number of actual `INSERT INTO verdicts` statements sent to
Postgres -- not just the row count, which a single mistaken bulk insert would also produce. A
second, slower scan proves the time bound: a small scan must not wait for a batch of 25 that a
short scan may never reach (PRD §14.4's live, per-tool results page and sieve animation).
"""

from __future__ import annotations

import json
import math
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from sieve.api import spend
from sieve.config import settings
from sieve.db.models import Scan, ScanEvent, Tool, VerdictRow
from sieve.protocols import FakeClassifier
from sieve.worker import scan_job
from sieve.worker.main import run_once
from sieve.worker.queue import enqueue

TOOLS_FIXTURE: dict[str, Any] = json.loads(
    Path("fixtures/tools/sample-27.json").read_text(encoding="utf-8")
)
TOOL_COUNT = 500

#: "MUST" is one of `FakeClassifier`'s `FAKE_IMPERATIVE_WORDS`: the band, routed to the judge.
BAND_DESCRIPTION = "You MUST call this tool before any other tool."


def _server_of() -> dict[str, str]:
    server_of: dict[str, str] = {}
    for server, names in TOOLS_FIXTURE["servers"].items():
        for name in names:
            server_of[name] = server
    return server_of


def _add_tool(
    session: AsyncSession,
    scan_id: uuid.UUID,
    *,
    ordinal: int,
    name: str,
    description: str | None,
    server_name: str = "bench",
    title: str | None = None,
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    annotations: dict[str, Any] | None = None,
) -> None:
    session.add(
        Tool(
            id=uuid.uuid4(),
            scan_id=scan_id,
            server_name=server_name,
            name=name,
            title=title,
            description=description,
            input_schema=input_schema or {"type": "object", "properties": {}},
            output_schema=output_schema,
            annotations=annotations,
            siblings=[],
            ordinal=ordinal,
        )
    )


def _add_fixture_tool(session: AsyncSession, scan_id: uuid.UUID, *, ordinal: int) -> None:
    """One of the 27 fixture tools, cycling by `ordinal` -- a realistic mix of classifier-only
    and judge-routed stages (some of the 27 score in the band, e.g. `resolve_library_id` at
    0.55), which is exactly the point for the 500-tool test: heterogeneous NULL columns
    (`reasoning`/`remediation`/`judge_model` set on judge-stage rows, `None` elsewhere) across
    one batch is what `render_nulls=True` exists for. A test that needs every tool to land as
    `dismissed`/classifier-only wants `_add_benign_tool`, not this one.
    """
    server_of = _server_of()
    base = TOOLS_FIXTURE["tools"][ordinal % len(TOOLS_FIXTURE["tools"])]
    _add_tool(
        session,
        scan_id,
        ordinal=ordinal,
        name=f"{base['name']}_{ordinal:03d}",
        description=base.get("description"),
        server_name=server_of.get(base["name"], "bench"),
        title=base.get("title"),
        input_schema=base["inputSchema"],
        output_schema=base.get("outputSchema"),
        annotations=base.get("annotations"),
    )


def _add_benign_tool(session: AsyncSession, scan_id: uuid.UUID, *, ordinal: int) -> None:
    """Always fixture tool 0 ("get current weather..."): `fake_scores` puts it at 0.05
    poisoned -- comfortably under `t_low`, so it is `dismissed`/classifier-only every time,
    never routed to the judge. For tests that need that guarantee across many tools at once
    (unlike `_add_fixture_tool`, which cycles through tools that are not all benign).
    """
    base = TOOLS_FIXTURE["tools"][0]
    server_of = _server_of()
    _add_tool(
        session,
        scan_id,
        ordinal=ordinal,
        name=f"{base['name']}_{ordinal:03d}",
        description=base.get("description"),
        server_name=server_of.get(base["name"], "bench"),
        title=base.get("title"),
        input_schema=base["inputSchema"],
        output_schema=base.get("outputSchema"),
        annotations=base.get("annotations"),
    )


def _add_band_tool(session: AsyncSession, scan_id: uuid.UUID, *, ordinal: int) -> None:
    _add_tool(
        session, scan_id, ordinal=ordinal, name=f"band_{ordinal:03d}", description=BAND_DESCRIPTION
    )


async def _new_scan(session: AsyncSession, *, tool_count: int) -> uuid.UUID:
    scan_id = uuid.uuid4()
    now = datetime.now(UTC)
    session.add(
        Scan(
            id=scan_id,
            source_type="tools_json",
            status="queued",
            tool_count=tool_count,
            expires_at=now + timedelta(days=settings.scan_ttl_days),
        )
    )
    return scan_id


async def _seed_scan(session: AsyncSession) -> uuid.UUID:
    """`TOOL_COUNT` tools built by repeating the 27-tool fixture with unique names."""
    scan_id = await _new_scan(session, tool_count=TOOL_COUNT)
    for i in range(TOOL_COUNT):
        _add_fixture_tool(session, scan_id, ordinal=i)
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

    total = 30
    scan_id = await _new_scan(session, tool_count=total)
    for i in range(25):
        _add_benign_tool(session, scan_id, ordinal=i)
    for i in range(25, total):
        # Once `judge_call_cost` is patched to explode, this batch's first judge call fails.
        _add_band_tool(session, scan_id, ordinal=i)
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


class _SlowFixtureClassifier:
    """`FakeClassifier`, but `predict_proba` takes `delay_s` -- a stand-in for a slow real
    forward pass, so a scan's tools land far enough apart in wall time to prove the batch's
    *time* bound fires independently of its size bound (which a handful of tools never reaches).
    """

    model_version = FakeClassifier.model_version
    thresholds = FakeClassifier.thresholds

    def __init__(self, delay_s: float) -> None:
        self._inner = FakeClassifier()
        self._delay_s = delay_s

    def predict_proba(self, texts: list[str]) -> Any:
        time.sleep(self._delay_s)
        return self._inner.predict_proba(texts)


async def test_a_slow_scan_flushes_on_the_time_bound_before_the_batch_fills(
    sessions: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRD §14.4: the results page and the sieve animation depend on live, per-tool filling.

    3 tools at 100ms apart (a stand-in for a slow real classifier), `verdict_flush_ms=50`: the
    second tool arrives ~100ms after the first was buffered, well past the 50ms bound, so the
    first flush fires there -- with 2 rows pending, not 25 and not all 3. A batch bounded by
    size alone would never flush until every tool was in, which is exactly the regression this
    setting exists to prevent.
    """
    monkeypatch.setattr(settings, "model_id", "fixture")
    monkeypatch.setattr(settings, "judge_enabled", False)  # keep this test to the time bound
    monkeypatch.setattr(settings, "verdict_flush_ms", 50)
    monkeypatch.setattr(scan_job, "get_classifier", lambda: _SlowFixtureClassifier(0.1))

    flush_sizes: list[int] = []
    original_flush = scan_job._VerdictBatch.flush

    async def traced_flush(self: scan_job._VerdictBatch) -> None:
        flush_sizes.append(len(self._rows))
        await original_flush(self)

    monkeypatch.setattr(scan_job._VerdictBatch, "flush", traced_flush)

    n = 3
    scan_id = await _new_scan(session, tool_count=n)
    for i in range(n):
        _add_benign_tool(session, scan_id, ordinal=i)
    await session.commit()
    await enqueue(session, "scan", {"scan_id": str(scan_id)})

    assert await run_once(sessions, {"scan": scan_job.run}) is True

    scan = await session.get(Scan, scan_id)
    assert scan is not None
    assert scan.status == "complete", scan.error

    assert len(flush_sizes) >= 2, "the time bound should flush at least once before the final one"
    assert flush_sizes[0] < scan_job.VERDICT_BATCH_SIZE, "the first flush must not wait for 25"

    verdict_count = (
        await session.execute(
            select(func.count())
            .select_from(VerdictRow)
            .join(Tool, VerdictRow.tool_id == Tool.id)
            .where(Tool.scan_id == scan_id)
        )
    ).scalar_one()
    assert verdict_count == n


async def test_a_failure_still_flushes_whatever_was_safely_pending(
    sessions: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scan that fails mid-way must still show the real progress it made (PRD §14.4).

    3 classifier-only tools (never charged, so nothing about them could be "uncharged") sit
    pending -- too few to hit the size bound, too fast to hit the default time bound -- when a
    4th, judge-routed tool's charge blows up. The 3 already-computed verdicts are still real
    work; discarding them along with the failure would be needlessly worse than what the worker
    did before batching existed at all.
    """
    monkeypatch.setattr(settings, "model_id", "fixture")
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "judge_enabled", True)

    def explode(judge: object) -> tuple[float, str]:
        raise RuntimeError("pricing blew up on the fourth tool")

    monkeypatch.setattr(spend, "judge_call_cost", explode)

    total = 4
    scan_id = await _new_scan(session, tool_count=total)
    for i in range(3):
        _add_benign_tool(session, scan_id, ordinal=i)
    _add_band_tool(session, scan_id, ordinal=3)
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
    assert verdict_count == 3, "the 3 safely-computed verdicts survive; the failing 4th does not"

    event_count = (
        await session.execute(
            select(func.count())
            .select_from(ScanEvent)
            .where(ScanEvent.scan_id == scan_id, ScanEvent.type == "tool.verdict")
        )
    ).scalar_one()
    assert event_count == 3

    assert await spend.spent_today(session) == Decimal(0)


async def test_bulk_insert_still_applies_a_python_side_default_for_an_omitted_column(
    session: AsyncSession,
) -> None:
    """Guards the exact risk `_verdict_values()` exists to avoid.

    A future `VerdictRow` column with a Python-side default (like `id`'s `uuid.uuid4`, via
    `_uuid_pk()`) must still get that default applied through the bulk path when a row
    genuinely never sets it -- not a literal `NULL` because `render_nulls=True` is active.
    `_verdict_values()` reads `vars(row)`, which only holds attributes actually assigned on a
    transient, unflushed instance, so a column nobody touched is omitted from the insert dict
    entirely (exactly like `created_at` already is) and SQLAlchemy supplies its own default for
    any column missing from the dict -- `render_nulls` only changes how a column that IS
    present, with a `None` value, is treated.
    """
    scan_id = await _new_scan(session, tool_count=1)
    tool_id = uuid.uuid4()
    session.add(
        Tool(
            id=tool_id,
            scan_id=scan_id,
            server_name="bench",
            name="probe",
            title=None,
            description="d",
            input_schema={"type": "object", "properties": {}},
            output_schema=None,
            annotations=None,
            siblings=[],
            ordinal=0,
        )
    )
    await session.commit()

    # Deliberately never sets `id` -- the one `VerdictRow` column with a Python-side default.
    bare = VerdictRow(
        tool_id=tool_id,
        verdict="dismissed",
        stage="classifier",
        predicted_class="benign",
        class_probabilities={"benign": 0.9, "imperative_benign": 0.05, "poisoned": 0.05},
        poisoned_probability=0.05,
        confidence=0.9,
        sub_labels=[],
        owasp=[],
        evidence_spans=[],
        unlocated_quotes=0,
        reasoning=None,
        remediation=None,
        model_version="fake@0",
        judge_model=None,
        latency_ms=1,
    )
    values = scan_job._verdict_values(bare)
    assert "id" not in values, "a column nobody set must be omitted, never inserted as NULL"

    await session.execute(insert(VerdictRow).execution_options(render_nulls=True), [values])
    await session.commit()

    landed = (
        await session.execute(select(VerdictRow).where(VerdictRow.tool_id == tool_id))
    ).scalar_one()
    assert landed.id is not None
