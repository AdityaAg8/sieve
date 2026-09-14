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

#: How many live streams this process will hold at once (security review 11.1, finding F4).
#:
#: Reads are unlimited by design (see THREAT_MODEL.md row 13), but a stream is not a read. Each
#: one holds a **dedicated** psycopg connection for `LISTEN scan_events` for its whole lifetime
#: -- outside the SQLAlchemy pool, and outside every per-IP quota, because the quotas are on the
#: three POST routes. Unbounded, one known scan id (the demo's, which is linked from the landing
#: page) was enough to open connections from a single host until Postgres hit `max_connections`
#: and *every* route started failing, including the ones that are rate-limited.
#:
#: 64 is chosen against Postgres's default `max_connections = 100`: 64 listeners plus the
#: engine's own pool (5 + 10 overflow) plus the worker's leaves room to connect with `psql` and
#: find out what is going on. It is a code constant rather than a setting because the PRD does
#: not pin a number and a deployment that needs a different one is a deployment whose database
#: sizing changed too.
#:
#: Two limits of the cap, both deliberate, both recorded in THREAT_MODEL.md's residual risks:
#:
#: * It is **per process**, because the counter is. A deployment running uvicorn with
#:   `--workers N` permits `64 * N` listeners and has to divide this number by N, or raise
#:   `max_connections` to match. The demo runs one worker process.
#: * It is **global, not per IP**. One host can hold all 64 slots and deny the stream to
#:   everyone else -- the quotas that would stop that are on the POST routes, and this is a GET.
#:   What that host can no longer do is take the *database* with it, which was the finding: the
#:   cap converts an outage of every route into a degradation of one, and `GET /findings` still
#:   answers, which is the fallback `web/src/components/findings/useScanFeed.ts` already takes
#:   when a stream fails. A per-IP cap needs `client_ip`, and `client_ip` is only as honest as
#:   `SIEVE_TRUST_PROXY` -- so it would read a spoofable header to decide a denial, which trades
#:   this bounded problem for an unbounded one.
MAX_CONCURRENT_STREAMS = 64

_live_streams = 0


class StreamsBusyError(RuntimeError):
    """This process is already holding `MAX_CONCURRENT_STREAMS`. Answered as 503, never queued."""


class StreamSlot:
    """One reservation. Released exactly once, whether the stream ended, failed or was dropped."""

    __slots__ = ("_held",)

    def __init__(self) -> None:
        self._held = True

    def release(self) -> None:
        global _live_streams
        if self._held:
            self._held = False
            _live_streams -= 1


def acquire_slot() -> StreamSlot:
    """Reserve a stream, or raise `StreamsBusyError`.

    Taken by the route *before* the response starts, not by `stream()` on its first `__anext__`:
    an async generator's body does not run until it is iterated, so a check-then-construct in
    the route would leave a window in which every concurrent request passes the check and none
    of them has incremented yet. There is no `await` between the test and the increment here, so
    on a single-threaded event loop this is atomic.
    """
    global _live_streams
    if _live_streams >= MAX_CONCURRENT_STREAMS:
        raise StreamsBusyError(f"already streaming {_live_streams} scans")
    _live_streams += 1
    return StreamSlot()


def live_streams() -> int:
    """How many slots are held right now. For the 503 path and for tests."""
    return _live_streams


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
    slot: StreamSlot | None = None,
) -> AsyncIterator[ServerSentEvent]:
    cursor = last_event_id or 0
    idle = 0.0
    # `finally` rather than a context manager: this generator is closed three different ways --
    # it returns on a terminal event, it is thrown a `GeneratorExit` when the browser tab shuts,
    # and it raises if the database goes away. All three have to give the slot back (F4). The
    # `UUID` parse is inside it for the same reason: the route has already refused an unparseable
    # id with a 404, but a slot held by a generator that raised on its first line would be a slot
    # nothing ever returns.
    try:
        key = uuid.UUID(scan_id)
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
    finally:
        if slot is not None:
            slot.release()
