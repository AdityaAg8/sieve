from __future__ import annotations

from fastapi import APIRouter

from sieve.api.deps import get_classifier, get_judge
from sieve.config import settings
from sieve.pipeline import PipelineDeps
from sieve.pipeline import triage as run_triage
from sieve.types import ToolRecord, TriageResult

router = APIRouter(tags=["triage"])


@router.post("/triage", response_model=TriageResult)
async def triage(record: ToolRecord) -> TriageResult:
    """The synchronous hot path for the CLI and the MCP server -- the same `pipeline.triage`."""
    judge = get_judge() if settings.judge_enabled else None
    deps = PipelineDeps(
        classifier=get_classifier(), judge=judge, judge_enabled=settings.judge_enabled
    )
    return run_triage(record, [], deps)
