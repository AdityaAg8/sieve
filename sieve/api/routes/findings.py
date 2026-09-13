from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from sieve.api.store import BadCursorError, Store, get_store
from sieve.types import FindingPage, Label, Stage, SubLabel, Verdict

router = APIRouter(tags=["findings"])


@router.get("/scans/{scan_id}/findings", response_model=FindingPage)
async def list_findings(
    scan_id: str,
    verdict: Verdict | None = None,
    predicted_class: Label | None = None,
    sub_label: SubLabel | None = None,
    stage: Stage | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    store: Store = Depends(get_store),
) -> FindingPage:
    try:
        page = await store.list_findings(
            scan_id, verdict, predicted_class, sub_label, stage, cursor, limit
        )
    except BadCursorError as exc:
        # A hand-edited `?cursor=` is a client mistake, not a server fault.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if page is None:
        raise HTTPException(status_code=404, detail="scan not found")
    return page
