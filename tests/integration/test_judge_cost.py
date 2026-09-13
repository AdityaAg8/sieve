"""I1.3 -- what a judge call actually cost.

The ledger is a spend cap, so the figure it accumulates has to be the one the API reported,
not the §12.4 planning estimate. The estimate survives exactly one case -- a judge that
reports no usage at all (the `FakeJudge` every demo and test runs on) -- and a scan says
which of the two paid for it, so a published cost figure is never ambiguous.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from anthropic.types import Usage
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sieve.api import spend
from sieve.config import settings
from sieve.db.models import Scan, Tool, VerdictRow
from sieve.judge.cost import estimate
from sieve.protocols import FakeJudge
from tests.integration.conftest import BAND_TOOLS, Transport

USAGE = Usage(
    input_tokens=600,
    output_tokens=250,
    cache_read_input_tokens=1800,
    cache_creation_input_tokens=0,
)


class _JudgeWithUsage:
    model = "claude-haiku-4-5"
    last_usage = USAGE


# ------------------------------------------------------------------ which figure is charged


def test_a_judge_that_reports_usage_is_charged_what_it_reported() -> None:
    usd, source = spend.judge_call_cost(_JudgeWithUsage())
    assert usd == estimate(USAGE, "claude-haiku-4-5")
    assert source == "usage"


def test_a_judge_without_usage_falls_back_to_the_estimate() -> None:
    usd, source = spend.judge_call_cost(FakeJudge())
    assert usd == spend.ESTIMATED_JUDGE_CALL_USD
    assert source == "estimate"


# ------------------------------------------------------------------------- the worker path


async def _scan(
    api: AsyncClient, sessions: async_sessionmaker[AsyncSession], session: AsyncSession
) -> Scan:
    from sieve.worker import scan_job
    from sieve.worker.main import run_once

    created = await api.post(
        "/api/v1/scans", json={"source_type": "tools_json", "tools_json": BAND_TOOLS}
    )
    await run_once(sessions, {"scan": scan_job.run})
    scan_id = created.json()["id"]
    return (await session.execute(select(Scan).where(Scan.id == scan_id))).scalar_one()


async def test_a_real_judge_call_charges_the_reported_usage(
    api: AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    canned: Transport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "judge_enabled", True)
    scan = await _scan(api, sessions, session)

    expected = Decimal(str(estimate(USAGE, settings.judge_model)))
    assert scan.judge_calls == 1
    assert scan.judge_cost_source == "usage"
    assert scan.judge_cost_usd == expected.quantize(Decimal("0.00001"))
    assert await spend.spent_today(session) == expected.quantize(Decimal("0.00001"))
    # The point of the change: the estimate and the truth are not the same number.
    assert scan.judge_cost_usd != Decimal(str(spend.ESTIMATED_JUDGE_CALL_USD))


async def test_the_fake_judge_still_charges_the_estimate(
    api: AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "judge_enabled", True)
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    scan = await _scan(api, sessions, session)

    assert scan.judge_calls == 1
    assert scan.judge_cost_source == "estimate"
    assert scan.judge_cost_usd == Decimal(str(spend.ESTIMATED_JUDGE_CALL_USD))


async def test_a_scan_that_never_judged_names_no_cost_source(
    api: AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "judge_enabled", False)
    scan = await _scan(api, sessions, session)

    assert scan.judge_calls == 0
    assert scan.judge_cost_source is None
    assert scan.judge_cost_usd == Decimal(0)


async def test_the_api_reports_the_cost_source(
    api: AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    canned: Transport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "judge_enabled", True)
    scan = await _scan(api, sessions, session)
    body = (await api.get(f"/api/v1/scans/{scan.id}")).json()
    assert body["judge_cost_source"] == "usage"
    assert body["judge_cost_usd"] == pytest.approx(estimate(USAGE, settings.judge_model), abs=1e-5)


# ----------------------------------------------------- a failure never banks an unpaid verdict


async def test_a_scan_that_fails_mid_batch_leaves_no_uncharged_verdict(
    api: AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    canned: Transport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRD §13.2 holds in both directions: no charge without a verdict, no verdict unpaid.

    The verdict row is added to the session and the ledger is charged in one transaction. A
    raise between the two -- pricing, in the case that prompted this test -- used to leave the
    pending row to be flushed by the failure handler's own commit, banking a judged verdict
    nothing had paid for.
    """
    monkeypatch.setattr(settings, "judge_enabled", True)

    def explode(judge: object) -> tuple[float, str]:
        raise RuntimeError("pricing blew up between the verdict and the ledger")

    monkeypatch.setattr(spend, "judge_call_cost", explode)

    created = await api.post(
        "/api/v1/scans", json={"source_type": "tools_json", "tools_json": BAND_TOOLS}
    )
    scan_id = created.json()["id"]
    from sieve.worker import scan_job
    from sieve.worker.main import run_once

    await run_once(sessions, {"scan": scan_job.run})

    scan = (await session.execute(select(Scan).where(Scan.id == scan_id))).scalar_one()
    verdicts = (
        (
            await session.execute(
                select(VerdictRow)
                .join(Tool, VerdictRow.tool_id == Tool.id)
                .where(Tool.scan_id == scan.id)
            )
        )
        .scalars()
        .all()
    )

    assert scan.status == "failed"
    assert "pricing blew up" in (scan.error or "")
    assert list(verdicts) == []
    assert await spend.spent_today(session) == Decimal(0)
