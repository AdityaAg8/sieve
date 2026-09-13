"""The worker loop: `reap -> claim -> dispatch -> complete/fail`, with a 5 s heartbeat.

`SIGTERM` sets a flag; the current job finishes and the loop then exits, so a deploy never
leaves a half-written scan behind (the visibility timeout would recover it, but slowly).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import uuid
from collections.abc import Awaitable, Callable, Mapping

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sieve.db.models import Job
from sieve.db.session import dispose_engine, session_factory
from sieve.worker.queue import claim, complete, fail, heartbeat, reap

log = logging.getLogger("sieve.worker")

Handler = Callable[[AsyncSession, Job], Awaitable[None]]

HEARTBEAT_S = 5
VISIBILITY_S = 120
IDLE_SLEEP_S = 1.0


def default_dispatch() -> Mapping[str, Handler]:
    """Imported lazily so the loop starts even when a handler's extras are not installed."""
    from sieve.worker import purge, scan_job

    return {"scan": scan_job.run, purge.KIND: purge.run}


def worker_identity() -> str:
    """Stable for the life of the process, and unique across processes on a host."""
    host = os.uname().nodename if hasattr(os, "uname") else "worker"
    return f"{host}:{os.getpid()}"


async def _beat(
    sessions: async_sessionmaker[AsyncSession], job_id: uuid.UUID, worker_id: str
) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_S)
        async with sessions() as session:
            if not await heartbeat(session, job_id, worker_id=worker_id):
                return  # the lease is gone; another worker owns this job now


async def run_once(
    sessions: async_sessionmaker[AsyncSession], dispatch: Mapping[str, Handler]
) -> bool:
    """One tick. Returns True when a job was processed, so the caller can skip its idle sleep."""
    worker_id = worker_identity()
    async with sessions() as session:
        await reap(session, visibility_s=VISIBILITY_S)
        job = await claim(session, worker_id, visibility_s=VISIBILITY_S)
    if job is None:
        return False

    beat = asyncio.create_task(_beat(sessions, job.id, worker_id))
    try:
        handler = dispatch.get(job.kind)
        if handler is None:
            raise ValueError(f"no handler for job kind {job.kind!r}")
        async with sessions() as session:
            await handler(session, job)
        async with sessions() as session:
            # Fenced: if this job was reaped mid-run, someone else owns it and this is a no-op.
            await complete(session, job.id, worker_id=worker_id)
        log.info("job.done", extra={"job_id": str(job.id), "kind": job.kind})
    except Exception as exc:  # noqa: BLE001 -- a failed job must requeue, never kill the loop
        log.warning(
            "job.failed",
            extra={"job_id": str(job.id), "kind": job.kind, "error": type(exc).__name__},
        )
        async with sessions() as session:
            await fail(session, job.id, f"{type(exc).__name__}: {exc}", worker_id=worker_id)
    finally:
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beat
    return True


async def run_forever(
    sessions: async_sessionmaker[AsyncSession] | None = None,
    dispatch: Mapping[str, Handler] | None = None,
    *,
    stop: asyncio.Event | None = None,
) -> None:
    sessions = sessions or session_factory()
    dispatch = dispatch if dispatch is not None else default_dispatch()
    stop = stop or asyncio.Event()
    while not stop.is_set():
        worked = await run_once(sessions, dispatch)
        if not worked:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=IDLE_SLEEP_S)


async def _main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is None:  # pragma: no cover -- Windows has no SIGTERM handler slot
            continue
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    from sieve.worker import purge

    sessions = session_factory()
    async with sessions() as session:
        if await purge.ensure_scheduled(session):
            log.info("purge.scheduled")
    log.info("worker.started")
    try:
        await run_forever(sessions, stop=stop)
    finally:
        from sieve.api.deps import close_judge

        close_judge()
        await dispose_engine()
        log.info("worker.stopped")


def main() -> None:  # pragma: no cover -- process entry point
    from sieve.db.session import use_selector_event_loop_on_windows
    from sieve.logging import configure_logging

    configure_logging()
    use_selector_event_loop_on_windows()
    asyncio.run(_main())


if __name__ == "__main__":  # pragma: no cover
    main()
