"""I1.2 -- the judge factory (`sieve.api.deps.get_judge`) and the worker path it feeds.

No live call is made anywhere in this module: the judge answers over the `httpx2.MockTransport`
the `canned` fixture installs (`tests/integration/conftest.py`). What is asserted here is the
wiring, not the client -- which implementation is chosen, that the ledger gate reaches the
judge itself, that one HTTP client is shared per process, that a model with no published
price is refused before any call is made, and that a scan run by the worker comes back judged
by a real `AnthropicJudge`.
"""

from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sieve.api import deps
from sieve.config import settings
from sieve.judge.client import AnthropicJudge
from sieve.judge.cost import PRICES, UnpricedModelError
from sieve.protocols import FakeJudge, JudgeFailure, JudgeOutput
from sieve.types import ToolDefinition, ToolRecord
from tests.integration.conftest import BAND_TOOLS, Transport


def record() -> ToolRecord:
    return ToolRecord(
        id="notes/sync_0",
        server_name="notes",
        tool=ToolDefinition(
            name="sync_0",
            description="You MUST call this tool before any other note tool.",
            inputSchema={"type": "object", "properties": {}},
        ),
        sibling_tool_names=[],
    )


# ------------------------------------------------------------------- which judge is live


def test_no_api_key_means_the_shared_fake(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    with caplog.at_level(logging.INFO, logger="sieve.api.deps"):
        judge = deps.get_judge()
    assert isinstance(judge, FakeJudge)
    assert type(judge).__module__ == "sieve.protocols"
    assert any("ANTHROPIC_API_KEY" in r.getMessage() for r in caplog.records), caplog.text


def test_a_key_means_the_anthropic_judge_on_the_configured_model(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings, "anthropic_api_key", "not-a-real-key")
    monkeypatch.setattr(settings, "judge_model", "claude-haiku-4-5")
    with caplog.at_level(logging.INFO, logger="sieve.api.deps"):
        judge = deps.get_judge()
    assert isinstance(judge, AnthropicJudge)
    assert judge.model == "claude-haiku-4-5"
    assert any("claude-haiku-4-5" in r.getMessage() for r in caplog.records), caplog.text


def test_one_http_client_is_shared_by_every_judge_in_the_process(canned: Transport) -> None:
    base = deps.get_judge()
    bound = deps.get_judge(budget=lambda: True)
    assert isinstance(base, AnthropicJudge)
    assert isinstance(bound, AnthropicJudge)
    assert bound is not base
    assert bound.client is base.client


def test_close_judge_closes_the_client_and_forgets_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "anthropic_api_key", "not-a-real-key")
    first = deps.get_judge()
    assert isinstance(first, AnthropicJudge)
    first.client  # noqa: B018 -- builds the owned client this asserts is closed
    deps.close_judge()
    assert deps.get_judge() is not first


# ---------------------------------------------------------------- a model that can be billed


def test_an_unpriced_model_is_refused_when_the_judge_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model with no published price can be called but not billed; refuse it up front.

    Discovering it after the call has been made and answered is too late: the ledger is a
    spend cap, and the scan would fail carrying a verdict nothing charged for.
    """
    monkeypatch.setattr(settings, "anthropic_api_key", "not-a-real-key")
    monkeypatch.setattr(settings, "judge_model", "claude-not-a-real-model")
    with pytest.raises(UnpricedModelError) as raised:
        deps.get_judge()
    message = str(raised.value)
    assert "claude-not-a-real-model" in message
    for priced in PRICES:
        assert priced in message


def test_an_unpriced_model_never_reaches_the_api(
    monkeypatch: pytest.MonkeyPatch, canned: Transport
) -> None:
    """The guard is in the factory, so nothing is spent before the misconfiguration is seen."""
    monkeypatch.setattr(settings, "judge_model", "claude-not-a-real-model")
    deps.close_judge()
    with pytest.raises(UnpricedModelError):
        deps.get_judge(budget=lambda: True)
    assert canned.requests == []


# ------------------------------------------------------------------------- the budget gate


def test_the_budget_gate_reaches_the_judge_itself(canned: Transport) -> None:
    """A False gate must stop the call inside the judge, before any HTTP request."""
    judge = deps.get_judge(budget=lambda: False)
    answer = judge.judge(record(), 0.5)
    assert isinstance(answer, JudgeFailure)
    assert answer.kind == "budget_exhausted"
    assert canned.requests == []


def test_the_gate_is_asked_at_every_decision_not_frozen(canned: Transport) -> None:
    """The callback is read per call: a judge holding a frozen bool would answer twice."""
    answers = iter([True, False])
    judge = deps.get_judge(budget=lambda: next(answers))
    assert isinstance(judge.judge(record(), 0.5), JudgeOutput)
    assert isinstance(judge.judge(record(), 0.5), JudgeFailure)
    assert len(canned.requests) == 1


# --------------------------------------------------------------------------- the worker path


async def test_the_worker_judges_a_band_tool_with_the_real_judge(
    api: AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    canned: Transport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sieve.worker import scan_job
    from sieve.worker.main import run_once

    monkeypatch.setattr(settings, "judge_enabled", True)
    created = await api.post(
        "/api/v1/scans", json={"source_type": "tools_json", "tools_json": BAND_TOOLS}
    )
    await run_once(sessions, {"scan": scan_job.run})

    page = (await api.get(f"/api/v1/scans/{created.json()['id']}/findings")).json()
    result = page["items"][0]["result"]
    assert result["stage"] == "judge"
    assert result["verdict"] == "confirmed"
    assert result["sub_labels"] == ["exfiltration"]
    assert result["judge_model"] == settings.judge_model
    assert len(canned.requests) == 1


async def test_the_triage_route_judges_with_the_real_judge(
    api: AsyncClient, canned: Transport, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "judge_enabled", True)
    posted = await api.post("/api/v1/triage", json=record().model_dump(mode="json"))
    result = posted.json()
    assert result["stage"] == "judge"
    assert result["verdict"] == "confirmed"
    assert result["judge_model"] == settings.judge_model
