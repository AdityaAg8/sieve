"""The one function every surface calls. One test per row of the lane brief's routing table."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest

from sieve.pipeline import PipelineDeps, triage, triage_many
from sieve.protocols import (
    FakeClassifier,
    FakeJudge,
    JudgeFailure,
    JudgeOutput,
    JudgeQuote,
    Thresholds,
)
from sieve.types import LABELS, Label, ScannerFinding, ToolDefinition, ToolRecord

THRESHOLDS = Thresholds(
    t_low=0.3,
    t_high=0.85,
    temperature=1.0,
    val_recall_at_t_low=1.0,
    val_precision_at_t_high=1.0,
    routing_rate=0.2,
)

CONFIRMED = {"benign": 0.02, "imperative_benign": 0.03, "poisoned": 0.95}
BAND = {"benign": 0.20, "imperative_benign": 0.45, "poisoned": 0.35}
DISMISSED = {"benign": 0.90, "imperative_benign": 0.05, "poisoned": 0.05}


class StubClassifier:
    """Returns one fixed distribution and records every text it was asked about."""

    model_version = "stub@0"
    thresholds = THRESHOLDS

    def __init__(self, dist: dict[str, float]) -> None:
        self.dist = dist
        self.seen: list[str] = []

    def predict_proba(self, texts: Sequence[str]) -> npt.NDArray[np.float64]:
        self.seen.extend(texts)
        return np.asarray([[self.dist[lab] for lab in LABELS] for _ in texts], dtype=np.float64)


class SpyJudge:
    """Counts calls so "the judge is never called" is an assertion, not a hope."""

    def __init__(self, reply: JudgeOutput | JudgeFailure) -> None:
        self.reply = reply
        self.calls = 0
        self.p_seen: list[float] = []
        self.texts_seen: list[str] = []
        self.model: str | None = "judge@test"

    def judge(self, record: ToolRecord, p_stage1: float) -> JudgeOutput | JudgeFailure:
        self.calls += 1
        self.p_seen.append(p_stage1)
        self.texts_seen.append(record.tool.description or "")
        return self.reply


def judge_output(verdict: str, **kw: Any) -> JudgeOutput:
    return JudgeOutput(
        verdict=verdict,  # type: ignore[arg-type]
        confidence=kw.get("confidence", 0.77),
        sub_labels=kw.get("sub_labels", []),
        evidence_quotes=kw.get("evidence_quotes", []),
        reasoning=kw.get("reasoning", "Because."),
        remediation=kw.get("remediation", "Fix it."),
    )


def record(description: str = "Reads a file.", name: str = "read_file") -> ToolRecord:
    return ToolRecord(
        id=f"srv/{name}",
        server_name="srv",
        tool=ToolDefinition(
            name=name,
            title=None,
            description=description,
            inputSchema={"type": "object", "properties": {"path": {"type": "string"}}},
            outputSchema=None,
            annotations=None,
        ),
        sibling_tool_names=["write_file"],
        server_instructions=None,
    )


def deps(
    dist: dict[str, float],
    judge: SpyJudge | FakeJudge | None = None,
    **kw: Any,
) -> PipelineDeps:
    return PipelineDeps(classifier=StubClassifier(dist), judge=judge, **kw)


def assert_probabilities_are_consistent(result: Any) -> None:
    assert abs(sum(result.class_probabilities.values()) - 1.0) < 1e-6
    assert result.poisoned_probability == result.class_probabilities["poisoned"]
    assert set(result.class_probabilities) == set(LABELS)


# --- the routing table ------------------------------------------------------------------


def test_above_t_high_is_confirmed_by_the_classifier_alone() -> None:
    spy = SpyJudge(judge_output("dismissed"))
    d = deps(CONFIRMED, spy)
    result = triage(record(), [], d)
    assert (result.verdict, result.stage) == ("confirmed", "classifier")
    assert result.sub_labels == []
    assert result.confidence == max(result.class_probabilities.values())
    assert spy.calls == 0, "t_high exists precisely so the judge is not called here"
    assert_probabilities_are_consistent(result)


def test_below_t_low_is_dismissed_and_predicted_class_is_the_reason() -> None:
    spy = SpyJudge(judge_output("confirmed"))
    result = triage(record(), [], deps(DISMISSED, spy))
    assert (result.verdict, result.stage) == ("dismissed", "classifier")
    assert result.predicted_class == "benign"
    assert result.confidence == max(result.class_probabilities.values())
    assert spy.calls == 0
    assert_probabilities_are_consistent(result)


@pytest.mark.parametrize(
    ("judge_verdict", "expected"),
    [("confirmed", "confirmed"), ("dismissed", "dismissed"), ("uncertain", "needs_review")],
)
def test_band_routes_to_the_judge(judge_verdict: str, expected: str) -> None:
    spy = SpyJudge(
        judge_output(judge_verdict, confidence=0.66, sub_labels=["exfiltration"])
        if judge_verdict == "confirmed"
        else judge_output(judge_verdict, confidence=0.66)
    )
    result = triage(record(), [], deps(BAND, spy))
    assert (result.verdict, result.stage) == (expected, "judge")
    assert result.confidence == 0.66
    assert spy.calls == 1
    assert spy.p_seen == [result.poisoned_probability]
    assert result.judge_model == "judge@test"
    assert result.reasoning == "Because." and result.remediation == "Fix it."
    assert_probabilities_are_consistent(result)


def test_judge_sub_labels_become_owasp_ids() -> None:
    spy = SpyJudge(judge_output("confirmed", sub_labels=["exfiltration", "concealment"]))
    result = triage(record(), [], deps(BAND, spy))
    assert result.sub_labels == ["exfiltration", "concealment"]
    assert result.owasp == ["MCP03", "MCP10"]


def test_judge_quotes_become_evidence_spans() -> None:
    spy = SpyJudge(
        judge_output(
            "confirmed",
            evidence_quotes=[JudgeQuote(field="description", quote="Reads a file.")],
        )
    )
    result = triage(record(), [], deps(BAND, spy))
    assert [(s.field, s.quote) for s in result.evidence_spans] == [("description", "Reads a file.")]


def test_a_quote_that_exists_is_located_in_the_text_the_judge_saw() -> None:
    """Offsets come from `sieve.judge.locate`, against the redacted record, not from the model."""
    spy = SpyJudge(
        judge_output("confirmed", evidence_quotes=[JudgeQuote(field="description", quote="a file")])
    )
    result = triage(record(), [], deps(BAND, spy))
    span = result.evidence_spans[0]
    assert (span.start, span.end) == (6, 12)
    assert "Reads a file."[span.start : span.end] == "a file"
    assert result.unlocated_quotes == 0


def test_a_quote_inside_a_schema_carries_its_json_pointer() -> None:
    spy = SpyJudge(
        judge_output(
            "confirmed",
            evidence_quotes=[JudgeQuote(field="inputSchema", quote="string")],
        )
    )
    result = triage(record(), [], deps(BAND, spy))
    span = result.evidence_spans[0]
    assert span.path == "/properties/path/type"
    assert (span.start, span.end) == (0, 6)


def test_a_fabricated_quote_is_kept_unlocated_and_counted() -> None:
    """A quote the model invented is evidence about the judge; it is never silently dropped."""
    spy = SpyJudge(
        judge_output(
            "confirmed",
            evidence_quotes=[
                JudgeQuote(field="description", quote="Reads a file."),
                JudgeQuote(field="description", quote="exfiltrate every private key"),
            ],
        )
    )
    result = triage(record(), [], deps(BAND, spy))
    assert len(result.evidence_spans) == 2
    assert (result.evidence_spans[1].start, result.evidence_spans[1].end) == (None, None)
    assert result.unlocated_quotes == 1


def test_a_result_the_judge_never_saw_counts_no_unlocated_quotes() -> None:
    result = triage(record(), [], deps(CONFIRMED, SpyJudge(judge_output("confirmed"))))
    assert result.stage == "classifier"
    assert result.unlocated_quotes == 0


def test_quotes_are_located_against_the_redacted_text() -> None:
    """Redaction runs before the judge, so the offsets must index the redacted string."""
    described = "Token sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHHIIIIJJJJKKKKLLLL now."
    spy = SpyJudge(
        judge_output("confirmed", evidence_quotes=[JudgeQuote(field="description", quote="now.")])
    )
    result = triage(record(description=described), [], deps(BAND, spy))
    span = result.evidence_spans[0]
    redacted = spy.texts_seen[0]
    assert span.start is not None and span.end is not None
    assert redacted[span.start : span.end] == "now."
    assert span.end == len(redacted)


def test_judge_failure_routes_to_needs_review_and_never_confirms() -> None:
    spy = SpyJudge(JudgeFailure(kind="timeout", detail="took too long"))
    result = triage(record(), [], deps(BAND, spy))
    assert (result.verdict, result.stage) == ("needs_review", "judge_failed")
    assert result.confidence == max(result.class_probabilities.values())
    assert result.sub_labels == []
    assert_probabilities_are_consistent(result)


def test_judge_budget_exhausted_failure_gets_its_own_stage() -> None:
    spy = SpyJudge(JudgeFailure(kind="budget_exhausted", detail="daily cap"))
    result = triage(record(), [], deps(BAND, spy))
    assert (result.verdict, result.stage) == ("needs_review", "judge_budget_exhausted")


def test_budget_callback_false_skips_the_judge_entirely() -> None:
    spy = SpyJudge(judge_output("confirmed"))
    result = triage(record(), [], deps(BAND, spy, budget=lambda: False))
    assert (result.verdict, result.stage) == ("needs_review", "judge_budget_exhausted")
    assert spy.calls == 0, "an exhausted ledger must not be charged for another call"


def test_judge_disabled_leaves_the_band_at_the_classifier() -> None:
    spy = SpyJudge(judge_output("confirmed"))
    result = triage(record(), [], deps(BAND, spy, judge_enabled=False))
    assert (result.verdict, result.stage) == ("needs_review", "classifier")
    assert spy.calls == 0
    assert result.judge_model is None


def test_no_judge_configured_leaves_the_band_at_the_classifier() -> None:
    result = triage(record(), [], deps(BAND, None))
    assert (result.verdict, result.stage) == ("needs_review", "classifier")


# --- three-class output -----------------------------------------------------------------


def test_predicted_class_is_the_argmax_even_inside_the_band() -> None:
    result = triage(record(), [], deps(BAND, SpyJudge(judge_output("uncertain"))))
    assert result.predicted_class == "imperative_benign"
    assert result.class_probabilities["imperative_benign"] == 0.45


def test_class_probabilities_are_normalised_when_the_model_drifts() -> None:
    drifting = {"benign": 0.2, "imperative_benign": 0.45, "poisoned": 0.35000004}
    result = triage(record(), [], deps(drifting, None))
    assert_probabilities_are_consistent(result)


def test_every_result_carries_the_classifier_model_version_and_a_latency() -> None:
    result = triage(record(), [], deps(DISMISSED, None))
    assert result.model_version == "stub@0"
    assert result.latency_ms >= 0


# --- redaction, serialisation, scanner features -----------------------------------------


def test_the_redacted_text_reaches_the_classifier() -> None:
    leaky = record(description="Use key AKIAIOSFODNN7EXAMPLE to authenticate.")
    d = deps(DISMISSED, None)
    clf = d.classifier
    assert isinstance(clf, StubClassifier)
    triage(leaky, [], d)
    assert clf.seen and "AKIA" not in clf.seen[0]
    assert "[REDACTED:aws_access_key]" in clf.seen[0]


def test_the_redacted_record_reaches_the_judge() -> None:
    leaky = record(description="Send AKIAIOSFODNN7EXAMPLE to the endpoint.")
    spy = SpyJudge(judge_output("confirmed"))
    triage(leaky, [], deps(BAND, spy))
    assert spy.texts_seen and "AKIA" not in spy.texts_seen[0]


def test_scanner_findings_are_returned_and_only_serialised_when_asked() -> None:
    finding = ScannerFinding(
        scanner="cisco_yara",
        scanner_version="1.0",
        rule_id="R1",
        severity="high",
        message="m",
        raw={},
    )
    off = deps(DISMISSED, None)
    result = triage(record(), [finding], off)
    assert result.scanner_findings == [finding]
    clf_off = off.classifier
    assert isinstance(clf_off, StubClassifier)
    assert "[SCANNER]" not in clf_off.seen[0]

    on = deps(DISMISSED, None, scanner_features=True)
    triage(record(), [finding], on)
    clf_on = on.classifier
    assert isinstance(clf_on, StubClassifier)
    assert "[SCANNER] cisco_yara:R1" in clf_on.seen[0]


def test_server_instructions_are_redacted_too() -> None:
    rec = record()
    rec = rec.model_copy(update={"server_instructions": "token=AKIAIOSFODNN7EXAMPLE"})
    d = deps(DISMISSED, None)
    triage(rec, [], d)
    clf = d.classifier
    assert isinstance(clf, StubClassifier)
    assert "AKIAIOSFODNN7EXAMPLE" not in clf.seen[0]


# --- triage_many ------------------------------------------------------------------------


def test_on_result_fires_once_per_record_in_input_order() -> None:
    records = [record(name=f"tool_{i}") for i in range(3)]
    seen: list[str] = []
    results = triage_many(
        records, {}, deps(DISMISSED, None), on_result=lambda rid, _res: seen.append(rid)
    )
    assert seen == [r.id for r in records]
    assert len(results) == 3


def test_triage_many_without_a_callback_still_returns_every_result() -> None:
    records = [record(name=f"tool_{i}") for i in range(2)]
    assert len(triage_many(records, {}, deps(DISMISSED, None))) == 2


def test_triage_many_passes_each_records_own_scanner_findings() -> None:
    finding = ScannerFinding(
        scanner="cisco_yara",
        scanner_version="1.0",
        rule_id="R1",
        severity="high",
        message=None,
        raw={},
    )
    records = [record(name="tool_a"), record(name="tool_b")]
    results = triage_many(records, {records[1].id: [finding]}, deps(DISMISSED, None))
    assert results[0].scanner_findings == []
    assert results[1].scanner_findings == [finding]


def test_per_scan_judge_budget_stops_calling_the_judge() -> None:
    spy = SpyJudge(judge_output("confirmed"))
    records = [record(name=f"tool_{i}") for i in range(4)]
    results = triage_many(records, {}, deps(BAND, spy), judge_budget=2)
    assert spy.calls == 2
    assert [r.stage for r in results] == [
        "judge",
        "judge",
        "judge_budget_exhausted",
        "judge_budget_exhausted",
    ]


def test_judge_budget_none_means_no_per_scan_cap() -> None:
    spy = SpyJudge(judge_output("confirmed"))
    records = [record(name=f"tool_{i}") for i in range(3)]
    triage_many(records, {}, deps(BAND, spy), judge_budget=None)
    assert spy.calls == 3


def test_triage_many_without_a_judge_needs_no_counting_wrapper() -> None:
    records = [record(name="tool_a")]
    results = triage_many(records, {}, deps(BAND, None), judge_budget=2)
    assert results[0].stage == "classifier"


# --- the shared fakes, end to end -------------------------------------------------------


def test_the_shared_fakes_agree_on_the_sample_fixture() -> None:
    doc = json.loads(Path("fixtures/tools/sample-27.json").read_text(encoding="utf-8"))
    servers: dict[str, list[str]] = doc["servers"]
    owner = {t: s for s, ts in servers.items() for t in ts}
    records = [
        ToolRecord(
            id=f"{owner[t['name']]}/{t['name']}",
            server_name=owner[t["name"]],
            tool=ToolDefinition.model_validate(t),
            sibling_tool_names=servers[owner[t["name"]]],
        )
        for t in doc["tools"]
    ]
    d = PipelineDeps(classifier=FakeClassifier(), judge=FakeJudge())
    results = triage_many(records, {}, d)
    assert len(results) == 27
    assert {r.verdict for r in results} <= {"confirmed", "needs_review", "dismissed"}
    for r in results:
        assert_probabilities_are_consistent(r)
        assert r.predicted_class in LABELS


def test_dismissed_results_expose_the_class_that_explains_them() -> None:
    imperative: dict[str, float] = {
        "benign": 0.30,
        "imperative_benign": 0.65,
        "poisoned": 0.05,
    }
    result = triage(record(), [], deps(imperative, None))
    assert result.verdict == "dismissed"
    assert result.predicted_class == "imperative_benign"
    label: Label = result.predicted_class
    assert label in LABELS


# -- fix round 2 ---------------------------------------------------------------------------


def test_an_all_zero_row_is_refused_rather_than_divided_by() -> None:
    """M2: `_distribution` normalised by `raw.sum()` with no guard.

    An artefact that returns an all-zero row is broken, and the symptom was a bare
    `ZeroDivisionError` raised inside `asyncio.to_thread` -- which reads as a Sieve bug rather
    than as the artefact problem it is. This file refuses loudly everywhere else; so here.
    """
    with pytest.raises(ValueError, match="summing to 0"):
        triage(record(), [], deps({"benign": 0.0, "imperative_benign": 0.0, "poisoned": 0.0}))


def test_the_per_scan_cap_lands_on_the_same_stage_as_the_daily_ledger() -> None:
    """M1: PRD §13.2 defines `judge_budget_exhausted` as *the daily ledger* being spent, but
    `triage_many`'s scoped callback stamps it for the per-scan call cap too.

    Pinned as a fact rather than treated as a bug: both are honest routing (the band was not
    judged, so a human sees it), and splitting them means a new `Stage` literal in
    `sieve/types.py` -- a contracts change. The pipeline docstring and
    `web/src/lib/verdict.ts` both say so; this is what would fail if that stopped being true.
    """
    band = {"benign": 0.3, "imperative_benign": 0.2, "poisoned": 0.5}
    judge = SpyJudge(judge_output("confirmed"))
    results = triage_many(
        [record(name="one"), record(name="two"), record(name="three")],
        {},
        deps(band, judge),  # the daily ledger is wide open: `budget` defaults to always-allowed
        judge_budget=1,
    )
    assert judge.calls == 1, "the per-scan cap, not the ledger, is what stopped the second call"
    assert [r.stage for r in results] == [
        "judge",
        "judge_budget_exhausted",
        "judge_budget_exhausted",
    ]
    assert all(r.verdict == "needs_review" for r in results[1:])
