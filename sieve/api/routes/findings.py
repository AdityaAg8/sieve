from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from sieve.api.deps import FixtureStore, get_store
from sieve.types import FindingPage, Label, Stage, SubLabel, Verdict

router = APIRouter(tags=["findings"])


@router.get("/scans/{scan_id}/findings", response_model=FindingPage)
def list_findings(
    scan_id: str,
    verdict: Verdict | None = None,
    predicted_class: Label | None = None,
    sub_label: SubLabel | None = None,
    stage: Stage | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    store: FixtureStore = Depends(get_store),
) -> FindingPage:
    page = store.list_findings(scan_id, verdict, predicted_class, sub_label, stage, cursor, limit)
    if page is None:
        raise HTTPException(status_code=404, detail="scan not found")
    return page
