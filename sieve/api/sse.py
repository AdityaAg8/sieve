"""Tail `scan_events` over SSE: `LISTEN/NOTIFY` when the database allows it, polling when not.

Every message carries `id: <scan_events.id>`, so a browser reconnect sends `Last-Event-ID` and
gets only what it missed. The NOTIFY payload is an id pair, never event content -- the row is
read back through the normal session.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sse_starlette import ServerSentEvent

from sieve.config import settings
from sieve.db.models import ScanEvent
from sieve.db.session import psycopg_dsn

log = logging.getLogger("sieve.api.sse")

CHANNEL = "scan_events"
TERMINAL = frozenset({"scan.completed", "scan.failed"})
POLL_INTERVAL_S = 1.0
MAX_IDLE_S = 300.0


def parse_last_event_id(raw: str | None) -> int | None:
    """`Last-Event-ID` is attacker-controlled: a non-integer replays from the start, not a 500."""
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


class _Listener:
    """A dedicated psycopg connection in autocommit, LISTENing on the events channel."""

    def __init__(self) -> None:
        self._conn: object | None = None

    async def __aenter__(self) -> _Listener:
        try:
            import psycopg

            conn = await psycopg.AsyncConnection.connect(
                psycopg_dsn(settings.database_url), autocommit=True
            )
            await conn.execute(f"LISTEN {CHANNEL}")
            self._conn = conn
        except Exception as exc:  # noqa: BLE001 -- LISTEN is an optimisation; polling still works
            log.info("sse.listen_unavailable", extra={"error": type(exc).__name__})
            self._conn = None
        return self

    async def __aexit__(self, *exc: object) -> None:
        conn = self._conn
        self._conn = None
        if conn is not None:
            with contextlib.suppress(Exception):
                await conn.close()  # type: ignore[attr-defined]

    async def wait(self, timeout: float) -> None:
        """Return as soon as any scan_events NOTIFY lands, or after `timeout` seconds."""
        conn = self._conn
        if conn is None:
            await asyncio.sleep(timeout)
            return
        try:
            async for _ in conn.notifies(timeout=timeout):  # type: ignore[attr-defined]
                return
        except Exception as exc:  # noqa: BLE001 -- fall back to polling for the rest of the stream
            log.info("sse.listen_failed", extra={"error": type(exc).__name__})
            self._conn = None
            await asyncio.sleep(timeout)


async def _fetch(session: AsyncSession, scan_id: uuid.UUID, after: int) -> list[ScanEvent]:
    rows = await session.execute(
        select(ScanEvent)
        .where(ScanEvent.scan_id == scan_id, ScanEvent.id > after)
        .order_by(ScanEvent.id)
    )
    return list(rows.scalars())


async def stream(
    sessions: async_sessionmaker[AsyncSession],
    scan_id: str,
    *,
    last_event_id: int | None = None,
    poll_interval_s: float = POLL_INTERVAL_S,
    max_idle_s: float = MAX_IDLE_S,
) -> AsyncIterator[ServerSentEvent]:
    key = uuid.UUID(scan_id)
    cursor = last_event_id or 0
    idle = 0.0
    async with _Listener() as listener:
        while True:
            async with sessions() as session:
                rows = await _fetch(session, key, cursor)
            for row in rows:
                cursor = row.id
                idle = 0.0
                yield ServerSentEvent(
                    id=str(row.id),
                    event=row.type,
                    data=json.dumps(row.payload, ensure_ascii=False),
                )
                if row.type in TERMINAL:
                    return
            if idle >= max_idle_s:
                return
            await listener.wait(poll_interval_s)
            idle += poll_interval_s
