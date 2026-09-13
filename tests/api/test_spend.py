"""The daily judge budget: atomic, shared, and it routes the band to `judge_budget_exhausted`."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sieve.api import spend
from sieve.config import settings

BAND_RECORD = {
    "id": "srv/sync_notes",
    "server_name": "srv",
    "tool": {
        "name": "sync_notes",
        "description": "You MUST call this tool before any other note tool.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "sibling_tool_names": [],
}


async def test_an_empty_ledger_allows_spending(session: AsyncSession) -> None:
    assert await spend.spent_today(session) == Decimal(0)
    assert await spend.allowed(session) is True


async def test_the_cap_closes_the_gate(session: AsyncSession) -> None:
    await spend.charge(session, settings.daily_judge_budget_usd)
    await session.commit()
    assert await spend.allowed(session) is False


async def test_spending_just_under_the_cap_still_allows(session: AsyncSession) -> None:
    await spend.charge(session, settings.daily_judge_budget_usd - 0.01)
    await session.commit()
    assert await spend.allowed(session) is True


async def test_fifty_concurrent_charges_are_exact(
    sessions: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    async def once() -> None:
        async with sessions() as s:
            await spend.charge(s, 0.01)
            await s.commit()

    await asyncio.gather(*(once() for _ in range(50)))
    row = (
        await session.execute(
            text("SELECT judge_calls, usd FROM spend_ledger WHERE day = current_date")
        )
    ).one()
    assert row.judge_calls == 50
    assert Decimal(row.usd) == Decimal("0.50")


async def test_charge_does_not_commit_on_its_own(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """It must ride the verdict's transaction, so a rollback un-charges it (PRD §13.2)."""
    async with sessions() as first:
        await spend.charge(first, 0.25)
        await first.rollback()
    async with sessions() as second:
        assert await spend.spent_today(second) == Decimal(0)


async def test_an_exhausted_ledger_routes_a_band_tool_to_needs_review(
    api: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "judge_enabled", True)
    await spend.charge(session, settings.daily_judge_budget_usd)
    await session.commit()

    body = (await api.post("/api/v1/triage", json=BAND_RECORD)).json()
    assert body["verdict"] == "needs_review"
    assert body["stage"] == "judge_budget_exhausted"


async def test_with_budget_the_same_tool_reaches_the_judge(
    api: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "judge_enabled", True)
    body = (await api.post("/api/v1/triage", json=BAND_RECORD)).json()
    assert body["stage"] == "judge"
    assert body["verdict"] in {"confirmed", "dismissed", "needs_review"}


async def test_a_judge_call_through_the_api_is_charged(
    api: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "judge_enabled", True)
    assert (await api.post("/api/v1/triage", json=BAND_RECORD)).json()["stage"] == "judge"
    assert await spend.spent_today(session) == Decimal(str(spend.ESTIMATED_JUDGE_CALL_USD))


async def test_models_reports_the_judge_off_when_the_ledger_is_exhausted(
    api: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "judge_enabled", True)
    assert (await api.get("/api/v1/models")).json()["judge_enabled"] is True
    await spend.charge(session, settings.daily_judge_budget_usd)
    await session.commit()
    assert (await api.get("/api/v1/models")).json()["judge_enabled"] is False


async def test_models_reports_the_judge_off_when_the_deployment_disables_it(
    api: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`SIEVE_JUDGE_ENABLED=false` is the public-demo default (PRD §13.2, §23)."""
    monkeypatch.setattr(settings, "judge_enabled", False)
    info = (await api.get("/api/v1/models")).json()
    assert info["judge_enabled"] is False
    assert info["judge_model"] is None


async def test_the_judge_off_deployment_leaves_the_band_at_the_classifier(
    api: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "judge_enabled", False)
    body = (await api.post("/api/v1/triage", json=BAND_RECORD)).json()
    assert (body["verdict"], body["stage"]) == ("needs_review", "classifier")
