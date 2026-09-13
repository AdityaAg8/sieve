"""The one function every surface calls (PRD §13.3).

`redact -> serialise -> classifier -> route -> (judge, budget permitting) -> assemble`.
Pure and injectable: the API, CLI, worker, benchmark and MCP server all call `triage`, so a
routing change is impossible to ship to one surface and not another.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from sieve.judge.locate import locate
from sieve.owasp import owasp_for
from sieve.protocols import Classifier, Judge, JudgeFailure, JudgeOutput
from sieve.redact import redact, redact_tool
from sieve.serialise import serialise
from sieve.types import (
    LABELS,
    EvidenceSpan,
    Label,
    ScannerFinding,
    Stage,
    SubLabel,
    ToolRecord,
    TriageResult,
    Verdict,
)

# PRD §12.1: the judge answers on three values and only `uncertain` needs a human.
_JUDGE_VERDICT_TO_VERDICT: dict[str, Verdict] = {
    "confirmed": "confirmed",
    "dismissed": "dismissed",
    "uncertain": "needs_review",
}


def _always_allowed() -> bool:
    return True


@dataclass
class PipelineDeps:
    classifier: Classifier
    judge: Judge | None
    judge_enabled: bool = True
    scanner_features: bool = False
    # False => the daily spend ledger is exhausted. The API passes `sieve.api.spend.allowed`.
    budget: Callable[[], bool] = field(default=_always_allowed)


def _redacted(record: ToolRecord) -> ToolRecord:
    """Redact before the classifier, before the judge and before storage (SC-5)."""
    tool, _ = redact_tool(record.tool)
    instructions = record.server_instructions
    if instructions is not None:
        instructions, _ = redact(instructions)
    return record.model_copy(update={"tool": tool, "server_instructions": instructions})


def _distribution(classifier: Classifier, text: str) -> dict[Label, float]:
    raw = classifier.predict_proba([text])[0]
    # Calibrated output should already sum to 1; normalising costs nothing and keeps a model
    # whose last digit drifts from tripping TriageResult's validator in production.
    total = float(raw.sum())
    return {lab: float(raw[i]) / total for i, lab in enumerate(LABELS)}


def _spans(record: ToolRecord, output: JudgeOutput) -> tuple[list[EvidenceSpan], int]:
    """Quotes, not offsets (PRD §12.2): the judge names text and `locate` finds it here.

    Located against the redacted record -- the text the judge was actually shown -- so the
    offsets index the same string the UI and SARIF render. A quote that will not locate is
    kept with `start = end = None` and counted: a fabricated quote is a quality signal about
    the judge, and dropping it would hide exactly the thing worth seeing.
    """
    return locate(record, output.evidence_quotes)


def triage(record: ToolRecord, findings: list[ScannerFinding], deps: PipelineDeps) -> TriageResult:
    started = time.perf_counter()
    clean = _redacted(record)
    text = serialise(clean, scanner_findings=findings if deps.scanner_features else None)
    probabilities = _distribution(deps.classifier, text)
    poisoned = probabilities["poisoned"]
    predicted_class = max(probabilities, key=lambda lab: probabilities[lab])
    argmax_confidence = probabilities[predicted_class]

    thresholds = deps.classifier.thresholds
    verdict: Verdict
    stage: Stage
    confidence = argmax_confidence
    sub_labels: list[SubLabel] = []
    spans: list[EvidenceSpan] = []
    unlocated = 0
    reasoning: str | None = None
    remediation: str | None = None
    judge_model: str | None = None

    if poisoned >= thresholds.t_high:
        # The classifier is allowed to auto-confirm; that is what t_high buys, and §12.4's
        # cost model assumes the judge is never called here.
        verdict, stage = "confirmed", "classifier"
    elif poisoned < thresholds.t_low:
        verdict, stage = "dismissed", "classifier"
    elif deps.judge is None or not deps.judge_enabled:
        # The public demo default (SIEVE_JUDGE_ENABLED=false): the band is handed to a human.
        verdict, stage = "needs_review", "classifier"
    elif not deps.budget():
        verdict, stage = "needs_review", "judge_budget_exhausted"
    else:
        answer = deps.judge.judge(clean, poisoned)
        if isinstance(answer, JudgeFailure):
            verdict = "needs_review"
            stage = (
                "judge_budget_exhausted" if answer.kind == "budget_exhausted" else "judge_failed"
            )
        else:
            verdict = _JUDGE_VERDICT_TO_VERDICT[answer.verdict]
            stage = "judge"
            confidence = answer.confidence
            sub_labels = list(answer.sub_labels)
            spans, unlocated = _spans(clean, answer)
            reasoning = answer.reasoning
            remediation = answer.remediation
            judge_model = deps.judge.model

    return TriageResult(
        verdict=verdict,
        stage=stage,
        predicted_class=predicted_class,
        class_probabilities=probabilities,
        poisoned_probability=poisoned,
        confidence=confidence,
        sub_labels=sub_labels,
        owasp=owasp_for(sub_labels),
        evidence_spans=spans,
        unlocated_quotes=unlocated,
        reasoning=reasoning,
        remediation=remediation,
        scanner_findings=findings,
        model_version=deps.classifier.model_version,
        judge_model=judge_model,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


class _CountingJudge:
    """Wraps the real judge so a per-scan cap is enforced on calls actually made."""

    def __init__(self, inner: Judge) -> None:
        self._inner = inner
        self.model = inner.model
        self.calls = 0

    def judge(self, record: ToolRecord, p_stage1: float) -> JudgeOutput | JudgeFailure:
        self.calls += 1
        return self._inner.judge(record, p_stage1)


def triage_many(
    records: Iterable[ToolRecord],
    findings_by_id: Mapping[str, list[ScannerFinding]],
    deps: PipelineDeps,
    *,
    on_result: Callable[[str, TriageResult], None] | None = None,
    judge_budget: int | None = None,
) -> list[TriageResult]:
    """Triage a batch, streaming each result to `on_result` in input order as it lands."""
    scoped = deps
    if deps.judge is not None and judge_budget is not None:
        counting = _CountingJudge(deps.judge)
        outer_budget = deps.budget
        scoped = PipelineDeps(
            classifier=deps.classifier,
            judge=counting,
            judge_enabled=deps.judge_enabled,
            scanner_features=deps.scanner_features,
            budget=lambda: outer_budget() and counting.calls < judge_budget,
        )

    results: list[TriageResult] = []
    for record in records:
        result = triage(record, list(findings_by_id.get(record.id, [])), scoped)
        results.append(result)
        if on_result is not None:
            on_result(record.id, result)
    return results
