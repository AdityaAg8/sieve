from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import sieve.db.session as db_session
from sieve.api.main import create_app
from tests.db.pg import (  # noqa: F401 -- re-exported as pytest fixtures
    anyio_backend,
    engine,
    migrated,
    postgres_url,
    session,
    sessions,
)


@pytest_asyncio.fixture
async def api(
    engine: AsyncEngine,  # noqa: F811 -- the fixture, not the function
    sessions: async_sessionmaker[AsyncSession],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    """The real app over ASGI, in this test's event loop, on this test's engine.

    ASGITransport rather than `TestClient`: the SSE route and `readyz` reach for the
    process-wide `session_factory()`, and a `TestClient` would run them on a second event
    loop with a pool built on the first -- which psycopg will not tolerate.
    """
    monkeypatch.setattr(db_session, "_engine", engine)
    monkeypatch.setattr(db_session, "_sessions", sessions)
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://sieve.test") as client:
        yield client
