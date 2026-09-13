"""Async engine and session factory, built from `settings.database_url` (psycopg driver)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sieve.config import settings

_engine: AsyncEngine | None = None
_sessions: async_sessionmaker[AsyncSession] | None = None


def psycopg_dsn(url: str) -> str:
    """`postgresql+psycopg://…` -> the plain libpq URL a raw psycopg connection wants."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1).replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )


def create_engine(url: str | None = None) -> AsyncEngine:
    """A fresh engine. Tests use this so each event loop owns its own pool."""
    return create_async_engine(url or settings.database_url, pool_pre_ping=True, future=True)


def engine() -> AsyncEngine:
    """The process-wide engine used by the API and the worker."""
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    global _sessions
    if _sessions is None:
        _sessions = async_sessionmaker(engine(), expire_on_commit=False)
    return _sessions


async def dispose_engine() -> None:
    global _engine, _sessions
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessions = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, rolled back if the handler raised."""
    async with session_factory()() as session:
        yield session
