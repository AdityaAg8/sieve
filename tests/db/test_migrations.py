"""`alembic upgrade head` against a real Postgres, checked against PRD §18 column for column."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

EXPECTED_TABLES = {
    "scans",
    "tools",
    "scanner_findings",
    "verdicts",
    "feedback",
    "jobs",
    "scan_events",
    "rate_limits",
    "spend_ledger",
}


async def _columns(session: AsyncSession, table: str) -> dict[str, tuple[str, str]]:
    rows = await session.execute(
        text(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns WHERE table_name = :t"
        ),
        {"t": table},
    )
    return {r[0]: (r[1], r[2]) for r in rows}


async def test_all_nine_tables_exist(session: AsyncSession) -> None:
    rows = await session.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    )
    names = {r[0] for r in rows}
    assert names >= EXPECTED_TABLES, EXPECTED_TABLES - names


async def test_verdicts_carries_the_three_class_explanation(session: AsyncSession) -> None:
    cols = await _columns(session, "verdicts")
    assert cols["predicted_class"] == ("text", "NO")
    assert cols["class_probabilities"] == ("jsonb", "NO")


async def test_verdicts_count_the_quotes_that_would_not_locate(session: AsyncSession) -> None:
    """0004, additive: a fabricated quote is counted, never dropped (PRD §12.2)."""
    cols = await _columns(session, "verdicts")
    assert cols["unlocated_quotes"] == ("integer", "NO")


async def test_rate_limits_composite_primary_key(session: AsyncSession) -> None:
    rows = await session.execute(
        text(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = 'rate_limits'::regclass AND i.indisprimary"
        )
    )
    assert {r[0] for r in rows} == {"ip_hash", "bucket", "window_start"}


async def test_jobs_ready_index_is_partial(session: AsyncSession) -> None:
    row = await session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_jobs_ready'")
    )
    indexdef = row.scalar_one()
    assert "WHERE" in indexdef and "'queued'" in indexdef
    assert "status" in indexdef and "run_after" in indexdef


@pytest.mark.parametrize(
    "index_name",
    ["idx_tools_scan", "idx_verdicts_tool", "idx_scans_expires", "idx_rate_limits_win"],
)
async def test_every_prd_index_exists(session: AsyncSession, index_name: str) -> None:
    row = await session.execute(
        text("SELECT count(*) FROM pg_indexes WHERE indexname = :n"), {"n": index_name}
    )
    assert row.scalar_one() == 1


async def test_scan_events_index_covers_scan_and_id(session: AsyncSession) -> None:
    row = await session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_scan_events_scan'")
    )
    assert "scan_id" in row.scalar_one()


async def test_spend_ledger_shape(session: AsyncSession) -> None:
    cols = await _columns(session, "spend_ledger")
    assert cols["day"][0] == "date"
    assert cols["judge_calls"] == ("integer", "NO")
    assert cols["usd"] == ("numeric", "NO")


async def test_scans_error_column_is_nullable_text(session: AsyncSession) -> None:
    cols = await _columns(session, "scans")
    assert cols["error"] == ("text", "YES")


async def test_scans_record_where_their_cost_figure_came_from(session: AsyncSession) -> None:
    """0003, additive: nullable because a scan that never judged has no cost source."""
    cols = await _columns(session, "scans")
    assert cols["judge_cost_source"] == ("text", "YES")


async def test_verdict_stage_round_trips_judge_budget_exhausted(session: AsyncSession) -> None:
    """`stage` is free text in Postgres; the Literal in `sieve.types` is the validation."""
    from sieve.db.models import Scan, Tool, VerdictRow
    from sieve.types import TriageResult

    now = datetime.now(UTC)
    scan = Scan(
        id=uuid.uuid4(),
        source_type="tools_json",
        status="complete",
        expires_at=now + timedelta(days=7),
    )
    tool = Tool(
        id=uuid.uuid4(), scan_id=scan.id, name="t", input_schema={"type": "object"}, ordinal=0
    )
    verdict = VerdictRow(
        id=uuid.uuid4(),
        tool_id=tool.id,
        verdict="needs_review",
        stage="judge_budget_exhausted",
        predicted_class="imperative_benign",
        class_probabilities={"benign": 0.2, "imperative_benign": 0.5, "poisoned": 0.3},
        poisoned_probability=0.3,
        confidence=0.5,
        sub_labels=[],
        owasp=[],
        evidence_spans=[],
        model_version="fake@0",
    )
    session.add_all([scan, tool, verdict])
    await session.commit()

    stored = (
        await session.execute(text("SELECT stage FROM verdicts WHERE id = :i"), {"i": verdict.id})
    ).scalar_one()
    assert stored == "judge_budget_exhausted"
    result = TriageResult(
        verdict="needs_review",
        stage=stored,
        predicted_class="imperative_benign",
        class_probabilities={"benign": 0.2, "imperative_benign": 0.5, "poisoned": 0.3},
        poisoned_probability=0.3,
        confidence=0.5,
        sub_labels=[],
        owasp=[],
        evidence_spans=[],
        reasoning=None,
        remediation=None,
        scanner_findings=[],
        model_version="fake@0",
        judge_model=None,
        latency_ms=1,
    )
    assert result.stage == "judge_budget_exhausted"


async def test_scan_events_notify_trigger_is_installed(session: AsyncSession) -> None:
    """Migration 0002: the trigger the SSE stream's LISTEN depends on."""
    row = await session.execute(
        text("SELECT count(*) FROM pg_trigger WHERE tgname = 'scan_events_notify'")
    )
    assert row.scalar_one() == 1
