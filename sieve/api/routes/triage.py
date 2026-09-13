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
    # Asked at the decision, not before it: the same gate the worker uses, so a request that
    # arrives as the ledger crosses the cap is refused the judge, not handed a snapshot taken
    # a moment earlier (PRD §13.2). The judge holds the same callback, so the cap survives a
    # path that reaches it without the pipeline.
    budget = _always_allowed if stub else spend.budget_gate(session, asyncio.get_running_loop())
    judge = get_judge(budget) if settings.judge_enabled else None
    deps = PipelineDeps(
        classifier=get_classifier(),
        judge=judge,
        judge_enabled=settings.judge_enabled,
        budget=budget,
    )
    # Off the event loop: a real DeBERTa forward pass is CPU-bound, and `budget` marshals
    # itself back, so the loop stays free to answer the ledger.
    result = await asyncio.to_thread(run_triage, record, [], deps)

    charged: float | None = None
    cost_source: str | None = None
    if result.stage == "judge" and not stub:
        # What the call reported, not what it was expected to cost. Same transaction as the
        # answer it paid for (PRD §13.2).
        charged, cost_source = spend.judge_call_cost(judge)
        await spend.charge(session, charged)
        await session.commit()
    # Identifiers only: the tool text stays out of the log above DEBUG (SC-5). The cost and
    # where the figure came from are logged here because this path has no scan row to record
    # them on, unlike the worker's `scans.judge_cost_source`.
    log.info(
        "triage.done",
        extra={
            "tool": record.tool.name,
            "verdict": result.verdict,
            "stage": result.stage,
            "judge_cost_usd": charged,
            "judge_cost_source": cost_source,
        },
    )
    return result
