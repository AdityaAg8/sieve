from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from sieve.api import ratelimit
from sieve.db.models import FeedbackRow, VerdictRow
from sieve.db.session import get_session
from sieve.types import FeedbackCreate

router = APIRouter(tags=["feedback"])
log = logging.getLogger("sieve.api.feedback")


@router.post("/findings/{finding_id}/feedback", status_code=204)
async def submit_feedback(
    finding_id: str,
    body: FeedbackCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Agree/disagree on a verdict. Rate-limited, with a honeypot field (PRD §13.1)."""
    await ratelimit.enforce(request, session, "triage_min")

    if body.website:
        # A bot filled the hidden field. Answer exactly as for a real submission and store
        # nothing, so the bot learns neither that it was caught nor that it was accepted.
        log.info("feedback.honeypot")
        return Response(status_code=204)

    if getattr(request.app.state, "stub", False):
        return Response(status_code=204)

    try:
        verdict_id = uuid.UUID(finding_id)
    except ValueError:
        return Response(status_code=204)  # an unknown id is not worth an error oracle
    if await session.get(VerdictRow, verdict_id) is None:
        return Response(status_code=204)

    session.add(
        FeedbackRow(
            id=uuid.uuid4(),
            verdict_id=verdict_id,
            agrees=body.agrees,
            note=body.note,
            ip_hash=ratelimit.ip_hash(ratelimit.client_ip(request)),
        )
    )
    await session.commit()
    log.info("feedback.stored", extra={"finding_id": finding_id, "agrees": body.agrees})
    return Response(status_code=204)
