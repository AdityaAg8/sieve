"""The dependencies every surface shares: the fixture store and the two model factories.

`FixtureStore` loads `fixtures/verdicts/sample-27.json` and serves it back for every scan
created in stub mode. L5 replaces it with a DB-backed store behind the same three methods
(`create_scan`, `get_scan`, `list_findings`); nothing above the store boundary changes.

`stub_classifier()` / `stub_judge()` return `FakeClassifier()` / `FakeJudge()` imported from
`sieve.protocols` -- never redefined here -- so the stub API and every lane's tests exercise
the same two fakes.

`get_classifier()` and `get_judge()` are the seams to L3 and L4. Both are configuration-
driven and cached per process: the classifier because loading an ONNX session is expensive,
the judge because one HTTP connection pool per process is one more than a judge per request.
Both say at INFO which implementation is live, so "the fake was running in production" is
answerable from a log rather than from a guess.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sieve.config import settings
from sieve.model.infer import FIXTURE_ID, load_classifier
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

if TYPE_CHECKING:  # `anthropic` is the optional `judge` extra; only the annotation needs it.
    from sieve.judge.client import AnthropicJudge

log = logging.getLogger("sieve.api.deps")

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

    def all_findings(self, scan_id: str) -> list[Finding] | None:
        """Every finding for a scan, unfiltered and unpaginated (the SSE `/events` route)."""
        return self._findings.get(scan_id)


# Process-wide, in-memory, and never evicted: correct for Phase 0 stub routes and tests, which
# never outlive one process. L5's DB-backed store enforces `settings.scan_ttl_days` for real.
_STORE = FixtureStore()


def fixture_store() -> FixtureStore:
    return _STORE


def stub_classifier() -> Classifier:
    return FakeClassifier()


def stub_judge() -> Judge:
    return FakeJudge()


#: `(model_id, model_revision, model_cache) -> classifier`, one entry deep. Loading an ONNX
#: session and a tokenizer costs seconds; every surface in this process shares the instance,
#: and a settings change (tests, a reload) rebuilds it because the key moved.
_CLASSIFIER: tuple[tuple[str, str | None, str], Classifier] | None = None


def get_classifier() -> Classifier:
    """The active classifier for the API, the worker and the CLI.

    `SIEVE_MODEL_ID=fixture` (the default) is the shared `FakeClassifier`; anything else is an
    artefact directory or a Hub id, resolved by L3's `sieve.model.infer.load_classifier`. A
    configured artefact that will not load raises -- `ThresholdsMissing` for an uncalibrated
    one -- rather than falling back to the fake, because a fake serving production traffic
    under a real model id is the one failure nothing downstream can detect.
    """
    global _CLASSIFIER
    key = (settings.model_id, settings.model_revision, settings.model_cache)
    if _CLASSIFIER is not None and _CLASSIFIER[0] == key:
        return _CLASSIFIER[1]

    if settings.model_id == FIXTURE_ID:
        log.info("classifier: the shared FakeClassifier (SIEVE_MODEL_ID=%s)", FIXTURE_ID)
        classifier: Classifier = stub_classifier()
    else:
        log.info(
            "classifier: loading %s (revision=%s, onnx=%s)",
            settings.model_id,
            settings.model_revision,
            settings.use_onnx,
        )
        classifier = load_classifier(
            settings.model_id,
            revision=settings.model_revision,
            use_onnx=settings.use_onnx,
            cache_dir=Path(settings.model_cache).expanduser(),
        )
        log.info(
            "classifier: %s active, t_low=%s t_high=%s from the artefact",
            classifier.model_version,
            classifier.thresholds.t_low,
            classifier.thresholds.t_high,
        )
    _CLASSIFIER = (key, classifier)
    return classifier


#: The process's Anthropic judge. It owns the one HTTP connection pool; the per-call judges
#: `get_judge(budget=...)` hands out borrow that client and own nothing, so closing this one
#: at shutdown closes everything.
_JUDGE: AnthropicJudge | None = None


def _require_a_priced_model(model: str) -> None:
    """Refuse a judge whose calls could be made but not billed.

    Every judge call is charged to the daily ledger from `sieve.judge.cost.PRICES`, and an
    unpriced model raises there rather than billing zero. Discovering that *after* a call has
    been made and answered is too late -- the money is spent and the scan dies carrying a
    verdict nothing paid for -- so the check happens where the judge is built.
    """
    from sieve.judge.cost import PRICES, UnpricedModelError

    if model not in PRICES:
        raise UnpricedModelError(
            f"SIEVE_JUDGE_MODEL={model!r} has no published price, so its calls could not be "
            f"charged to the daily ledger; priced models are {', '.join(sorted(PRICES))} "
            f"(add one to sieve.judge.cost.PRICES)"
        )


def _anthropic_judge() -> AnthropicJudge:
    global _JUDGE
    if _JUDGE is None or _JUDGE.model != settings.judge_model:
        close_judge()
        _require_a_priced_model(settings.judge_model)
        # Imported here, not at module scope: `sieve.judge.client` needs `anthropic`, the
        # optional `judge` extra, and the fixture path must stay importable without it.
        from sieve.judge.client import AnthropicJudge as _AnthropicJudge

        log.info("judge: AnthropicJudge on %s", settings.judge_model)
        _JUDGE = _AnthropicJudge(model=settings.judge_model)
    return _JUDGE


def get_judge(budget: Callable[[], bool] | None = None) -> Judge:
    """The active judge for the API, the worker and the CLI.

    With `ANTHROPIC_API_KEY` set this is `AnthropicJudge` on `settings.judge_model`; without
    it, the shared `FakeJudge`, said out loud at INFO so a deployment that lost its key is
    visible rather than merely cheap.

    `budget` is the daily-ledger gate, and it is a callback rather than a bool on purpose: it
    is asked at each decision, so a scan that crosses the cap mid-batch stops there (PRD
    §13.2). The pipeline asks it too; passing it into the judge as well is what makes the cap
    hold for any caller that reaches the judge directly.
    """
    if not settings.anthropic_api_key:
        log.info("judge: ANTHROPIC_API_KEY is unset; the shared FakeJudge is active")
        return stub_judge()
    base = _anthropic_judge()
    if budget is None:
        return base
    from sieve.judge.client import AnthropicJudge as _AnthropicJudge

    return _AnthropicJudge(model=settings.judge_model, client=base.client, budget=budget)


def close_judge() -> None:
    """Close the process's judge client. Called at API shutdown and at worker shutdown.

    Deterministic rather than left to the garbage collector: a pooled TLS socket finalised at
    an arbitrary moment surfaces as an unraisable `ResourceWarning` inside whichever request
    or test happens to be running.
    """
    global _JUDGE
    judge, _JUDGE = _JUDGE, None
    if judge is not None:
        judge.close()
