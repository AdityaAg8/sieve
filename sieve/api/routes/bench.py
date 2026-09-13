from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from sieve.api.deps import fixture_store
from sieve.types import BenchSummary

router = APIRouter(tags=["bench"])


@router.get("/bench/summary", response_model=BenchSummary)
async def bench_summary() -> BenchSummary:
    return BenchSummary(
        dataset_version="fixture@0",
        model_version="fixture@0",
        headline=fixture_store().template_counts(),
        tables={},
        generated_at=datetime.now(UTC),
        provisional=True,
    )
