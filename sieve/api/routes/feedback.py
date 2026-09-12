from __future__ import annotations

from fastapi import APIRouter, Response

from sieve.types import FeedbackCreate

router = APIRouter(tags=["feedback"])


@router.post("/findings/{finding_id}/feedback", status_code=204)
def submit_feedback(finding_id: str, body: FeedbackCreate) -> Response:
    # Phase 0 stub: accepted and discarded. L5 persists it and checks the honeypot field.
    return Response(status_code=204)
