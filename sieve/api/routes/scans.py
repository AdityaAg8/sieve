from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sse_starlette import EventSourceResponse

from sieve.api import sse
from sieve.api.ingest import IngestError, ingest
from sieve.api.store import Store, StubStore, get_store
from sieve.config import settings
from sieve.db.session import session_factory
from sieve.types import ScanCreate, ScanSummary

router = APIRouter(tags=["scans"])


@router.post("/scans", status_code=202, response_model=ScanSummary)
async def create_scan(
    req: ScanCreate,
    request: Request,
    response: Response,
    store: Store = Depends(get_store),
) -> ScanSummary:
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

    key = request.headers.get("Idempotency-Key")
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
    return EventSourceResponse(
        sse.stream(session_factory(), scan_id, last_event_id=last),
        media_type="text/event-stream",
    )


@router.get("/scans/{scan_id}/sarif")
async def scan_sarif(scan_id: str, store: Store = Depends(get_store)) -> dict[str, object]:
    if await store.get_scan(scan_id) is None:
        raise HTTPException(status_code=404, detail="scan not found")
    return {"version": "2.1.0", "runs": []}
