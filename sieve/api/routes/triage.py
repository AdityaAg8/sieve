from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from sieve.api import ratelimit, spend
from sieve.api.deps import get_classifier, get_judge
from sieve.config import settings
from sieve.db.session import get_session
from sieve.pipeline import PipelineDeps
from sieve.pipeline import triage as run_triage
from sieve.types import ToolRecord, TriageResult

router = APIRouter(tags=["triage"])
log = logging.getLogger("sieve.api.triage")


def _always_allowed() -> bool:
    return True


@router.post("/triage", response_model=TriageResult)
async def triage(
    record: ToolRecord,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TriageResult:
    """The synchronous hot path for the CLI and the MCP server -- the same `pipeline.triage`."""
    await ratelimit.enforce(request, session, "triage_min")

    stub = getattr(request.app.state, "stub", False)
    judge = get_judge() if settings.judge_enabled else None
    deps = PipelineDeps(
        classifier=get_classifier(),
        judge=judge,
        judge_enabled=settings.judge_enabled,
        # Asked at the decision, not before it: the same gate the worker uses, so a request
        # that arrives as the ledger crosses the cap is refused the judge, not handed a
        # snapshot taken a moment earlier (PRD §13.2).
        budget=_always_allowed if stub else spend.budget_gate(session, asyncio.get_running_loop()),
    )
    # Off the event loop: a real DeBERTa forward pass is CPU-bound, and `budget` marshals
    # itself back, so the loop stays free to answer the ledger.
    result = await asyncio.to_thread(run_triage, record, [], deps)

    if result.stage == "judge" and not stub:
        # Same transaction as the answer it paid for (PRD §13.2).
        await spend.charge(session)
        await session.commit()
    # Identifiers only: the tool text stays out of the log above DEBUG (SC-5).
    log.info(
        "triage.done",
        extra={"tool": record.tool.name, "verdict": result.verdict, "stage": result.stage},
    )
    return result
