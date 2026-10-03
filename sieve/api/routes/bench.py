from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from sieve.api.deps import fixture_store
from sieve.types import BenchSummary

router = APIRouter(tags=["bench"])

RESULTS = Path(__file__).resolve().parents[3] / "bench" / "results"

#: The files `make bench` writes that this route serves. Each is optional: a missing one leaves
#: its key out of `tables`, and the web renders `—` ("metric to confirm") in its place.
DEMO_FILE = "demo.json"
#: H5: `bench.ablations.run`, one row per PRD §11.5 configuration (judge rows deferred, D8).
ABLATIONS_FILE = "ablations.json"
#: H3: `bench.report_final`, the sealed report. The REPORT only, never a sealed row.
REPORT_FILE = "holdout.json"
#: F3: `bench.label.audit`, human vs ensemble on the 104-row sealed audit (and dev-50).
LABEL_AUDIT_FILE = "label_audit.json"
#: E5: `bench.label.vote`, agreement between the three labellers.
ENSEMBLE_AGREEMENT_FILE = "ensemble_agreement.json"
#: `bench.eval_final`'s append-only opening log, one JSON object per line.
OPENED_LOG_FILE = "test_opened.log"

#: Report keys too large for a page and read by nothing on it: every model's full report and
#: the per-family aggregates. The headline model's own report travels as `headline_report`.
_REPORT_BULK = ("per_model", "per_family", "models_by_family")


def _read(path: Path) -> dict[str, Any] | None:
    """`bench/results/` is generated only by `make bench`; the API reads, never computes."""
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _read_log(path: Path) -> list[dict[str, Any]] | None:
    if not path.is_file():
        return None
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _report_view(report: dict[str, Any]) -> dict[str, Any]:
    """The sealed report minus its bulk, plus the pre-registered headline model's own report."""
    view = {k: v for k, v in report.items() if k not in _REPORT_BULK}
    per_model = report.get("per_model")
    headline = report.get("headline_model")
    if isinstance(per_model, dict) and isinstance(per_model.get(headline), dict):
        view["headline_report"] = per_model[headline]
    return view


def _results_tables(results: Path, ablations: dict[str, Any] | None) -> dict[str, Any]:
    """Every results file this route serves, under the producer's own keys (H3, H5, F3)."""
    tables: dict[str, Any] = {}
    if ablations is not None and isinstance(ablations.get("rows"), list):
        # `per_seed` repeats every aggregate once per seed; the page reads the aggregates.
        tables["ablations"] = [
            {k: v for k, v in row.items() if k != "per_seed"}
            for row in ablations["rows"]
            if isinstance(row, dict)
        ]
    report = _read(results / REPORT_FILE)
    if report is not None:
        tables["holdout"] = _report_view(report)
    for key, name in (
        ("label_audit", LABEL_AUDIT_FILE),
        ("ensemble_agreement", ENSEMBLE_AGREEMENT_FILE),
    ):
        document = _read(results / name)
        if document is not None:
            tables[key] = document
    opened = _read_log(results / OPENED_LOG_FILE)
    if opened is not None:
        tables["test_opened"] = opened
    return tables


def _generated_at(results: Path) -> datetime:
    """When the newest results file was written: `make bench` records no other timestamp."""
    stamps = [
        (results / name).stat().st_mtime
        for name in (ABLATIONS_FILE, REPORT_FILE, LABEL_AUDIT_FILE, DEMO_FILE)
        if (results / name).is_file()
    ]
    return datetime.fromtimestamp(max(stamps), UTC) if stamps else datetime.now(UTC)


def summary(results: Path = RESULTS) -> BenchSummary:
    demo = _read(results / DEMO_FILE)
    ablations = _read(results / ABLATIONS_FILE) or {}
    tables = _results_tables(results, ablations)
    # The provisional notice is for a deployment with no measured numbers: it shows only while
    # both the ablation matrix and the sealed report are missing.
    published = "ablations" in tables or "holdout" in tables

    if demo is None and not published:
        # No published numbers yet: serve the committed fixture counts, flagged provisional,
        # so the page has something honest to render before `make bench` has ever run.
        return BenchSummary(
            dataset_version="fixture@0",
            model_version="fixture@0",
            headline=fixture_store().template_counts(),
            tables=tables,
            generated_at=datetime.now(UTC),
            provisional=True,
        )

    demo = demo or {}
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

    report: dict[str, Any] = tables.get("holdout", {})
    return BenchSummary(
        dataset_version=str(
            report.get("dataset_version")
            or ablations.get("dataset_version")
            or demo.get("dataset_version")
            or meta.get("dataset_version")
            or "unknown"
        ),
        model_version=str(
            report.get("headline_model")
            or demo.get("model_version")
            or scan.get("model_version")
            or meta.get("model_version")
            or "unknown"
        ),
        headline=headline,
        tables={**dict(demo.get("tables") or {}), **tables},
        generated_at=(
            demo.get("generated_at") or meta.get("generated_at") or _generated_at(results)
        ),
        # Never asserted by the API: measured results on disk, or whatever `make bench`
        # recorded for the demo scan.
        provisional=False
        if published
        else bool(demo.get("provisional", meta.get("provisional", False))),
    )


@router.get("/bench/summary", response_model=BenchSummary)
async def bench_summary() -> BenchSummary:
    return summary()
