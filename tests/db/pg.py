"""One Postgres probe per test session, shared by `tests/db`, `tests/api` and `tests/worker`.

The controller's ruling for this lane: when the daemon is not up, every Postgres-dependent
module SKIPS with exactly one reason string -- never fails, never silently passes.
"""

from __future__ import annotations

import functools
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from sieve.config import settings
from sieve.db.migrate import upgrade_head
from sieve.db.session import create_engine, psycopg_dsn

SKIP_REASON = "Postgres not reachable at DATABASE_URL"

# Every table the lane writes, in dependency order, so a TRUNCATE ... CASCADE is deterministic.
ALL_TABLES = (
    "feedback",
    "verdicts",
    "scanner_findings",
    "tools",
    "scan_events",
    "scans",
    "jobs",
    "rate_limits",
    "spend_ledger",
)


@functools.lru_cache(maxsize=1)
def probe() -> str | None:
    """`None` when Postgres answered; otherwise the failure detail. One attempt per session."""
    import psycopg

    try:
        with psycopg.connect(psycopg_dsn(settings.database_url), connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001 -- any failure means "not reachable", and why.
        return f"{type(exc).__name__}: {exc}"
    return None


def require_postgres() -> None:
    if probe() is not None:
        pytest.skip(SKIP_REASON)


@pytest.fixture(scope="session")
def postgres_url() -> str:
    require_postgres()
    return settings.database_url


@pytest.fixture(scope="session")
def migrated(postgres_url: str) -> str:
    """`alembic upgrade head` once per session; every DB test builds on the real migrations."""
    upgrade_head(postgres_url)
    return postgres_url


@pytest_asyncio.fixture
async def engine(migrated: str) -> AsyncIterator[AsyncEngine]:
    eng = create_engine(migrated)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def sessions(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(ALL_TABLES)} RESTART IDENTITY CASCADE"))
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessions() as s:
        yield s


@pytest.fixture
def anyio_backend() -> Iterator[str]:
    yield "asyncio"
