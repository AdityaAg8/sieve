from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from sieve.api.deps import FixtureStore, get_store
from sieve.types import BenchSummary

router = APIRouter(tags=["bench"])


@router.get("/bench/summary", response_model=BenchSummary)
def bench_summary(store: FixtureStore = Depends(get_store)) -> BenchSummary:
    return BenchSummary(
        dataset_version="fixture@0",
        model_version="fixture@0",
        headline=store.template_counts(),
        tables={},
        generated_at=datetime.now(UTC),
        provisional=True,
    )
