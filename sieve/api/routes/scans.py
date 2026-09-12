from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from sieve.api.deps import FixtureStore, get_store
from sieve.types import ScanCreate, ScanSummary

router = APIRouter(tags=["scans"])


@router.post("/scans", status_code=202, response_model=ScanSummary)
def create_scan(req: ScanCreate, store: FixtureStore = Depends(get_store)) -> ScanSummary:
    return store.create_scan(req)


@router.get("/scans/{scan_id}", response_model=ScanSummary)
def get_scan(scan_id: str, store: FixtureStore = Depends(get_store)) -> ScanSummary:
    summary = store.get_scan(scan_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="scan not found")
    return summary


def _sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.get("/scans/{scan_id}/events")
async def scan_events(scan_id: str, store: FixtureStore = Depends(get_store)) -> StreamingResponse:
    findings = store.all_findings(scan_id)
    if findings is None:
        raise HTTPException(status_code=404, detail="scan not found")

    async def stream() -> AsyncIterator[str]:
        yield _sse("scan.started", {"scan_id": scan_id})
        for f in findings:
            await asyncio.sleep(0.03)
            yield _sse("tool.verdict", json.loads(f.model_dump_json()))
        yield _sse("scan.completed", {"scan_id": scan_id})

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/scans/{scan_id}/sarif")
def scan_sarif(scan_id: str, store: FixtureStore = Depends(get_store)) -> dict[str, object]:
    if store.get_scan(scan_id) is None:
        raise HTTPException(status_code=404, detail="scan not found")
    return {"version": "2.1.0", "runs": []}
