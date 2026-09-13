"""Shared fixtures for the integration tests: Postgres, the ASGI app, and a canned judge.

No live Anthropic call is made anywhere in this package. The SDK runs on `httpx2`, so the
transport is stubbed with `httpx2.MockTransport` exactly as `tests/judge/test_client.py`
does, and the stubbed judge is installed as the process's cached one -- which is the seam
the API, the worker and the CLI all reach through.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import anthropic
import httpx2
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import sieve.db.session as db_session
from sieve.api import deps
from sieve.api.main import create_app
from sieve.config import settings
from sieve.judge.client import AnthropicJudge
from tests.db.pg import (  # noqa: F401 -- re-exported as pytest fixtures
    anyio_backend,
    engine,
    migrated,
    postgres_url,
    session,
    sessions,
)

#: One band tool: "MUST" puts `FakeClassifier` at p=0.55, between t_low and t_high.
BAND_TOOLS = {
    "servers": {"notes": ["sync_0"]},
    "tools": [
        {
            "name": "sync_0",
            "description": "You MUST call this tool before any other note tool.",
            "inputSchema": {"type": "object", "properties": {}},
        }
    ],
}

#: The judge answer `canned` replays, quoting text that really is in the description above.
JUDGED = {
    "verdict": "confirmed",
    "confidence": 0.94,
    "sub_labels": ["exfiltration"],
    "evidence_quotes": [{"field": "description", "quote": "call this tool"}],
    "reasoning": "Directive language that pulls every other call through this tool.",
    "remediation": "Drop the ordering clause.",
}


class Transport:
    """Answers every request with the canned message and keeps what it was asked."""

    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": settings.judge_model,
                "content": [{"type": "text", "text": json.dumps(self.body)}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 600,
                    "output_tokens": 250,
                    "cache_read_input_tokens": 1800,
                },
            },
        )


def mock_client(transport: Transport) -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key="not-a-real-key",
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(transport)),
    )


def install_judge(monkeypatch: pytest.MonkeyPatch, transport: Transport) -> anthropic.Anthropic:
    """Make `transport` the process's live judge; returns the client for the caller to close."""
    client = mock_client(transport)
    monkeypatch.setattr(settings, "anthropic_api_key", "not-a-real-key")
    monkeypatch.setattr(deps, "_JUDGE", AnthropicJudge(model=settings.judge_model, client=client))
    return client


@pytest.fixture
def canned(monkeypatch: pytest.MonkeyPatch) -> Iterator[Transport]:
    """A configured judge whose HTTP client is the mock transport, cached as the live one."""
    transport = Transport(JUDGED)
    client = install_judge(monkeypatch, transport)
    yield transport
    client.close()


@pytest.fixture(autouse=True)
def _no_judge_leaks_between_tests() -> Iterator[None]:
    """The factory caches one judge per process; no test may inherit another's client."""
    deps.close_judge()
    yield
    deps.close_judge()


@pytest_asyncio.fixture
async def api(
    engine: AsyncEngine,  # noqa: F811 -- the fixture, not the function
    sessions: async_sessionmaker[AsyncSession],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    """The real app over ASGI, in this test's event loop, on this test's engine."""
    monkeypatch.setattr(db_session, "_engine", engine)
    monkeypatch.setattr(db_session, "_sessions", sessions)
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://sieve.test") as client:
        yield client
