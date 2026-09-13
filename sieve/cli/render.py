"""Output formats for `sieve triage`: pretty (rich table), JSON, SARIF (PRD §15.1, §13.5).

Verdict words are the web's own (`web/src/lib/verdict.ts`'s `VERDICT_LABEL`), not re-invented
here, and dismissed is always rendered dim and never green (PRD §14.2, §14.6): a dismissal is a
"not this finding", not a green checkmark.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from rich.console import Console
from rich.table import Table

from sieve.config import settings
from sieve.sarif import to_sarif
from sieve.types import (
    Finding,
    Label,
    ScanSummary,
    SourceType,
    ToolRecord,
    TriageResult,
    Verdict,
)

VERDICT_LABEL: dict[Verdict, str] = {
    "confirmed": "Confirmed",
    "needs_review": "Needs review",
    "dismissed": "Dismissed",
}
VERDICT_ORDER: tuple[Verdict, ...] = ("confirmed", "needs_review", "dismissed")
VERDICT_STYLE: dict[Verdict, str] = {
    "confirmed": "bold red",
    "needs_review": "yellow",
    "dismissed": "dim",  # never green (PRD §14.2)
}
CLASS_LABEL: dict[Label, str] = {
    "poisoned": "Poisoned",
    "imperative_benign": "Imperative benign",
    "benign": "Benign",
}

CLEAN_SCAN_MESSAGE = "No findings to triage. The scanner flagged nothing in these {n} tools."


def build_scan_summary(
    records: list[ToolRecord],
    results: list[TriageResult],
    *,
    source_type: SourceType,
) -> ScanSummary:
    """A `ScanSummary` synthesised from one CLI run, for `--format sarif`'s run-level properties."""
    now = datetime.now(UTC)
    model_version = next((r.model_version for r in results if r.model_version), None)
    judge_model = next((r.judge_model for r in results if r.judge_model), None)
    return ScanSummary(
        id=str(uuid.uuid4()),
        status="complete",
        source_type=source_type,
        tool_count=len(records),
        scanner_flagged=sum(1 for r in results if r.scanner_findings),
        confirmed=sum(1 for r in results if r.verdict == "confirmed"),
        needs_review=sum(1 for r in results if r.verdict == "needs_review"),
        dismissed=sum(1 for r in results if r.verdict == "dismissed"),
        judge_calls=sum(1 for r in results if r.stage == "judge"),
        judge_cost_usd=0.0,
        judge_cost_source=None,
        model_version=model_version,
        judge_model=judge_model,
        created_at=now,
        expires_at=now + timedelta(days=settings.scan_ttl_days),
        error=None,
    )


def build_findings(
    records: list[ToolRecord], results: list[TriageResult], *, scan_id: str
) -> list[Finding]:
    return [
        Finding(
            id=f"{scan_id}:{record.id}",
            scan_id=scan_id,
            tool=record.tool,
            server_name=record.server_name or None,
            sibling_tool_names=record.sibling_tool_names,
            result=result,
        )
        for record, result in zip(records, results, strict=True)
    ]


def _dismissed_reason(result: TriageResult) -> str:
    p = result.class_probabilities.get(result.predicted_class)
    p_text = f"{p:.2f}" if p is not None else "—"
    return f"{CLASS_LABEL[result.predicted_class]} (p={p_text})"


def render_pretty(
    findings: list[Finding], *, tool_count: int, console: Console | None = None
) -> None:
    console = console or Console(no_color=False)
    if not findings:
        console.print(CLEAN_SCAN_MESSAGE.format(n=tool_count))
        return

    table = Table(show_lines=False)
    table.add_column("Verdict")
    table.add_column("Tool")
    table.add_column("Class")
    table.add_column("p(poisoned)", justify="right")
    table.add_column("Reason")

    for verdict in VERDICT_ORDER:
        style = VERDICT_STYLE[verdict]
        for finding in (f for f in findings if f.result.verdict == verdict):
            result = finding.result
            reason = (
                _dismissed_reason(result) if verdict == "dismissed" else (result.reasoning or "")
            )
            table.add_row(
                VERDICT_LABEL[verdict],
                finding.tool.name,
                CLASS_LABEL[result.predicted_class],
                f"{result.poisoned_probability:.2f}",
                reason,
                style=style,
            )
    console.print(table)


def render_json(findings: list[Finding]) -> str:
    return (
        json.dumps([f.model_dump(mode="json") for f in findings], indent=2, sort_keys=True) + "\n"
    )


def render_sarif(scan: ScanSummary, findings: list[Finding], *, source_path: str | None) -> str:
    doc = to_sarif(scan, findings, source_path=source_path)
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"
