from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette import EventSourceResponse
from starlette.background import BackgroundTask

from sieve.api import ratelimit, sse
from sieve.api.ingest import IngestError, ingest
from sieve.api.store import Store, StubStore, get_store
from sieve.config import settings
from sieve.db.session import get_session, session_factory
from sieve.sarif import to_sarif
from sieve.types import ScanCreate, ScanSummary

router = APIRouter(tags=["scans"])

#: `scans.idempotency_key` is a unique-indexed `Text` column, so an unbounded header is free
#: write volume against an index. 255 bytes is well past any real client's key.
MAX_IDEMPOTENCY_KEY_BYTES = 255


def _scoped_idempotency_key(request: Request) -> str | None:
    """The `Idempotency-Key` header, namespaced by the caller's own address hash.

    Security review 11.1, finding F3 (OWASP API1, CWE-639). There is no authentication here by
    design, which means an unguessable scan id is the only thing standing between a scan and
    anyone who asks for it. A globally unique, client-chosen `Idempotency-Key` was a *second*
    name for the same row: POSTing a guessed key (`1`, `test`, a build number) returned whoever
    had claimed it first, id and counts included, and the id is the whole findings page.

    Namespacing by `ip_hash` keeps the property idempotency is for -- the client that made a
    scan replays its own -- and removes the one it was never meant to have.

    **The namespace is exactly as strong as `client_ip`**, so both of its limits are this
    route's limits too (review round 1, finding 2):

    * With `SIEVE_TRUST_PROXY=false` behind a proxy, every caller hashes to the proxy's own
      address, so the namespace *collapses* to one and the guessed-key read is back. That is a
      misconfiguration, not a design: the setting exists because trusting the header when
      nothing appends it is the worse failure, and the same collapse already applies to the
      per-IP quotas.
    * With `SIEVE_TRUST_PROXY=true` the address is `X-Forwarded-For`'s **right-most** entry,
      which is sound for **exactly one** appending hop -- the `docs/deploy.md` topology. The
      left-most entry is attacker-written and must never be used here: it would let anyone who
      knows a victim's address name it and read the victim's scan. Behind two or more hops the
      namespace collapses as in the first case -- fewer namespaces, never someone else's.
    """
    raw = request.headers.get("Idempotency-Key")
    if not raw:
        return None
    if len(raw.encode("utf-8")) > MAX_IDEMPOTENCY_KEY_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Idempotency-Key is at most {MAX_IDEMPOTENCY_KEY_BYTES} bytes",
        )
    return f"{ratelimit.ip_hash(ratelimit.client_ip(request))}:{raw}"


@router.post("/scans", status_code=202, response_model=ScanSummary)
async def create_scan(
    req: ScanCreate,
    request: Request,
    response: Response,
    store: Store = Depends(get_store),
    session: AsyncSession = Depends(get_session),
) -> ScanSummary:
    await ratelimit.enforce(request, session, "scans_min", "scans_day")
    # Read before the body is parsed: a malformed header is answered without paying for ingest.
    key = _scoped_idempotency_key(request)
    try:
        data = ingest(req)
    except IngestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:  # a malformed tool object inside an otherwise valid body
        raise HTTPException(status_code=422, detail="tool definition failed validation") from exc

    if len(data.records) > settings.max_tools_per_scan:
        raise HTTPException(
            status_code=413,
            detail=f"a scan carries at most {settings.max_tools_per_scan} tools",
        )

    if key:
        existing = await store.get_by_idempotency_key(key)
        if existing is not None:
            # Same key, same scan: a replay is not a second scan, so it is 200, not 202.
            response.status_code = 200
            return existing
    return await store.create_scan(req, data, idempotency_key=key)


@router.get("/scans/{scan_id}", response_model=ScanSummary)
async def get_scan(scan_id: str, store: Store = Depends(get_store)) -> ScanSummary:
    summary = await store.get_scan(scan_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="scan not found")
    return summary


def _sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _stub_stream(store: StubStore, scan_id: str) -> StreamingResponse:
    findings = await store.all_findings(scan_id)
    if findings is None:
        raise HTTPException(status_code=404, detail="scan not found")

    async def stream() -> AsyncIterator[str]:
        yield _sse("scan.started", {"scan_id": scan_id})
        for f in findings:
            await asyncio.sleep(0.03)
            # `model_dump(mode="json")` -- not a dump-then-parse round trip (Phase 0b note).
            yield _sse("tool.verdict", f.model_dump(mode="json"))
        yield _sse("scan.completed", {"scan_id": scan_id})

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/scans/{scan_id}/events")
async def scan_events(
    scan_id: str, request: Request, store: Store = Depends(get_store)
) -> Response:
    """SSE. `Last-Event-ID` replays only what the client missed (PRD §13.1)."""
    if isinstance(store, StubStore):
        return await _stub_stream(store, scan_id)
    if await store.get_scan(scan_id) is None:
        raise HTTPException(status_code=404, detail="scan not found")
    last = sse.parse_last_event_id(request.headers.get("Last-Event-ID"))
    # A live stream holds a dedicated Postgres LISTEN connection, so the number of them is
    # capped process-wide (security review 11.1, finding F4). Reserved here, before the response
    # starts, so the refusal is a 503 a client can retry rather than a half-opened stream.
    try:
        slot = sse.acquire_slot()
    except sse.StreamsBusyError as exc:
        raise HTTPException(
            status_code=503,
            detail="too many live event streams; retry shortly or poll /findings",
            headers={"Retry-After": "5"},
        ) from exc
    return EventSourceResponse(
        sse.stream(session_factory(), scan_id, last_event_id=last, slot=slot),
        media_type="text/event-stream",
        # Two releases, because the generator's own `finally` is not reachable in every case
        # (review round 1, finding 1). `EventSourceResponse.__call__` awaits `http.response.
        # start` inside a task group that also runs `_listen_for_disconnect`; a cancellation at
        # that first await -- a client that aborts immediately, a shutdown grace period --
        # leaves the generator **never started**, and closing an async generator that has not
        # run to its first `yield` runs no `finally` at all. The count is process-global and
        # monotonic, so each such race would burn a slot permanently and eventually 503 every
        # client until a restart. `__call__` awaits `self.background` after the task group
        # exits, cancelled or not; `StreamSlot.release` is idempotent, so whichever path gets
        # there first is the one that counts.
        background=BackgroundTask(slot.release),
    )


@router.get("/scans/{scan_id}/sarif")
async def scan_sarif(scan_id: str, store: Store = Depends(get_store)) -> dict[str, object]:
    """SARIF 2.1.0 for CI. The scanner's original verdict rides along in `properties`."""
    summary = await store.get_scan(scan_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="scan not found")
    findings = await store.all_findings(scan_id) or []
    return to_sarif(summary, findings, source_path=await store.source_path(scan_id))
