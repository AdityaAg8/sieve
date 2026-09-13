from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from sieve.api.deps import fixture_store
from sieve.types import BenchSummary

router = APIRouter(tags=["bench"])

RESULTS = Path("bench/results")
DEMO = RESULTS / "demo.json"
ABLATIONS = RESULTS / "ablations.json"


def _read(path: Path) -> dict[str, Any] | None:
    """`bench/results/` is generated only by `make bench`; the API reads, never computes."""
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _ablation_headline(document: dict[str, Any]) -> dict[str, Any]:
    headline = document.get("headline")
    if isinstance(headline, dict):
        return dict(headline)
    rows = document.get("rows")
    if isinstance(rows, list):
        return {str(r.get("name")): r.get("headline", r) for r in rows if isinstance(r, dict)}
    return {}


def summary() -> BenchSummary:
    demo = _read(DEMO)
    if demo is None:
        # No published numbers yet: serve the committed fixture counts, flagged provisional,
        # so the page has something honest to render before `make bench` has ever run.
        return BenchSummary(
            dataset_version="fixture@0",
            model_version="fixture@0",
            headline=fixture_store().template_counts(),
            tables={},
            generated_at=datetime.now(UTC),
            provisional=True,
        )

    # `make bench` writes either a headline document or a demo scan (`meta` + `summary` +
    # `findings`); read both shapes rather than making the benchmark lane match the API.
    raw_meta = demo.get("meta")
    raw_scan = demo.get("summary")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    scan: dict[str, Any] = raw_scan if isinstance(raw_scan, dict) else {}
    headline = dict(demo.get("headline") or {})
    if not headline and scan:
        headline = {
            key: scan[key]
            for key in (
                "tool_count",
                "scanner_flagged",
                "confirmed",
                "needs_review",
                "dismissed",
                "judge_calls",
            )
            if key in scan
        }

    ablations = _read(ABLATIONS)
    if ablations is not None:
        headline["ablations"] = _ablation_headline(ablations)

    return BenchSummary(
        dataset_version=str(
            demo.get("dataset_version") or meta.get("dataset_version") or "unknown"
        ),
        model_version=str(
            demo.get("model_version")
            or scan.get("model_version")
            or meta.get("model_version")
            or "unknown"
        ),
        headline=headline,
        tables=dict(demo.get("tables") or {}),
        generated_at=demo.get("generated_at") or meta.get("generated_at") or datetime.now(UTC),
        # Never asserted by the API: `provisional` is whatever `make bench` recorded.
        provisional=bool(demo.get("provisional", meta.get("provisional", False))),
    )


@router.get("/bench/summary", response_model=BenchSummary)
async def bench_summary() -> BenchSummary:
    return summary()
