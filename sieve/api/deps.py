"""Phase 0 stub-backed dependencies.

`FixtureStore` loads `fixtures/verdicts/sample-27.json` and serves it back for every scan
created in stub mode. L5 replaces it with a DB-backed store behind the same three methods
(`create_scan`, `get_scan`, `list_findings`); nothing above the store boundary changes.

`stub_classifier()` / `stub_judge()` return `FakeClassifier()` / `FakeJudge()` imported from
`sieve.protocols` -- never redefined here -- so the stub API and every lane's tests exercise
the same two fakes.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sieve.config import settings
from sieve.protocols import Classifier, FakeClassifier, FakeJudge, Judge
from sieve.types import (
    Finding,
    FindingPage,
    Label,
    ScanCreate,
    ScanSummary,
    Stage,
    SubLabel,
    TriageResult,
    Verdict,
)

FIXTURE_PATH = Path("fixtures/verdicts/sample-27.json")


class FixtureStore:
    """Serves `fixtures/verdicts/sample-27.json` back for every scan created in stub mode."""

    def __init__(self, fixture_path: Path = FIXTURE_PATH) -> None:
        doc = json.loads(fixture_path.read_text(encoding="utf-8"))
        self._template_summary: dict[str, Any] = doc["summary"]
        self._template_findings: list[dict[str, Any]] = doc["findings"]
        self._summaries: dict[str, ScanSummary] = {}
        self._findings: dict[str, list[Finding]] = {}

    def create_scan(self, req: ScanCreate) -> ScanSummary:
        scan_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        summary = ScanSummary.model_validate(
            {
                **self._template_summary,
                "id": scan_id,
                "source_type": req.source_type,
                "created_at": now,
                "expires_at": now + timedelta(days=settings.scan_ttl_days),
            }
        )
        findings = [
            Finding.model_validate({**f, "id": f"{scan_id}:{f['id']}", "scan_id": scan_id})
            for f in self._template_findings
        ]
        self._summaries[scan_id] = summary
        self._findings[scan_id] = findings
        return summary

    def get_scan(self, scan_id: str) -> ScanSummary | None:
        return self._summaries.get(scan_id)

    def list_findings(
        self,
        scan_id: str,
        verdict: Verdict | None = None,
        predicted_class: Label | None = None,
        sub_label: SubLabel | None = None,
        stage: Stage | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> FindingPage | None:
        items = self._findings.get(scan_id)
        if items is None:
            return None
        if verdict is not None:
            items = [f for f in items if f.result.verdict == verdict]
        if predicted_class is not None:
            items = [f for f in items if f.result.predicted_class == predicted_class]
        if sub_label is not None:
            items = [f for f in items if sub_label in f.result.sub_labels]
        if stage is not None:
            items = [f for f in items if f.result.stage == stage]
        start = int(cursor) if cursor else 0
        page = items[start : start + limit]
        next_cursor = str(start + limit) if start + limit < len(items) else None
        return FindingPage(items=page, total=len(items), next_cursor=next_cursor)

    def template_result(self, tool_name: str) -> TriageResult | None:
        """The fixture's canned verdict for a tool by name, used by the stub `/triage` route."""
        for f in self._template_findings:
            if f["tool"]["name"] == tool_name:
                return TriageResult.model_validate(f["result"])
        return None

    def template_counts(self) -> dict[str, Any]:
        """The fixture's summary counts, used by the stub `/bench/summary` route."""
        return {
            "tool_count": self._template_summary["tool_count"],
            "scanner_flagged": self._template_summary["scanner_flagged"],
            "confirmed": self._template_summary["confirmed"],
            "needs_review": self._template_summary["needs_review"],
            "dismissed": self._template_summary["dismissed"],
        }


_STORE = FixtureStore()


def get_store() -> FixtureStore:
    return _STORE


def stub_classifier() -> Classifier:
    return FakeClassifier()


def stub_judge() -> Judge:
    return FakeJudge()
