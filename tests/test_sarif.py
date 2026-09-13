"""SARIF 2.1.0 export, validated against the committed OASIS schema."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft7Validator

from sieve.owasp import SUB_LABEL_TO_OWASP
from sieve.sarif import to_sarif
from sieve.types import (
    EvidenceSpan,
    Finding,
    ScannerFinding,
    ScanSummary,
    SubLabel,
    ToolDefinition,
    TriageResult,
    Verdict,
)

SCHEMA = json.loads(Path("tests/data/sarif-schema-2.1.0.json").read_text(encoding="utf-8"))
FIXTURE = json.loads(Path("fixtures/verdicts/sample-27.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def validator() -> Draft7Validator:
    return Draft7Validator(SCHEMA)


def summary(**kw: Any) -> ScanSummary:
    return ScanSummary.model_validate({**FIXTURE["summary"], **kw})


def result(
    verdict: Verdict = "confirmed",
    *,
    sub_labels: list[SubLabel] | None = None,
    reasoning: str | None = "Reads a credentials file the tool has no reason to touch.",
    spans: list[EvidenceSpan] | None = None,
    scanner_findings: list[ScannerFinding] | None = None,
    stage: str = "judge",
) -> TriageResult:
    labels: list[SubLabel] = ["exfiltration"] if sub_labels is None else sub_labels
    return TriageResult(
        verdict=verdict,
        stage=stage,  # type: ignore[arg-type]
        predicted_class="poisoned",
        class_probabilities={"benign": 0.05, "imperative_benign": 0.05, "poisoned": 0.90},
        poisoned_probability=0.90,
        confidence=0.90,
        sub_labels=labels,
        owasp=sorted({SUB_LABEL_TO_OWASP[s] for s in labels}),
        evidence_spans=spans or [],
        reasoning=reasoning,
        remediation="Remove the out-of-scope instruction.",
        scanner_findings=scanner_findings or [],
        model_version="fake@0",
        judge_model="claude-haiku-4-5",
        latency_ms=12,
    )


def finding(res: TriageResult, *, name: str = "read_file", server: str = "fs") -> Finding:
    return Finding(
        id=f"{server}-{name}",
        scan_id=FIXTURE["summary"]["id"],
        tool=ToolDefinition(
            name=name,
            title=None,
            description="Reads a file.",
            inputSchema={"type": "object", "properties": {}},
            outputSchema=None,
            annotations=None,
        ),
        server_name=server,
        sibling_tool_names=["write_file"],
        result=res,
    )


def fixture_findings() -> list[Finding]:
    return [Finding.model_validate(f) for f in FIXTURE["findings"]]


def test_the_whole_sample_scan_validates(validator: Draft7Validator) -> None:
    doc = to_sarif(summary(), fixture_findings(), source_path="mcp.json")
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    assert not errors, errors[0].message if errors else ""
    assert doc["version"] == "2.1.0"
    assert doc["$schema"].endswith("sarif-schema-2.1.0.json")
    assert len(doc["runs"][0]["results"]) == 27


def test_an_empty_scan_still_validates(validator: Draft7Validator) -> None:
    doc = to_sarif(summary(tool_count=0), [], source_path=None)
    assert not list(validator.iter_errors(doc))
    assert doc["runs"][0]["results"] == []


def test_the_driver_carries_one_rule_per_sub_label() -> None:
    doc = to_sarif(summary(), [], source_path=None)
    driver = doc["runs"][0]["tool"]["driver"]
    assert driver["name"] == "Sieve"
    assert driver["version"]
    assert driver["informationUri"]
    ids = [r["id"] for r in driver["rules"]]
    assert set(SUB_LABEL_TO_OWASP) <= set(ids)
    assert "triage" in ids
    for rule in driver["rules"]:
        assert rule["helpUri"].startswith("https://owasp.org/")


@pytest.mark.parametrize(
    ("verdict", "level"),
    [("confirmed", "error"), ("needs_review", "warning"), ("dismissed", "note")],
)
def test_level_follows_the_verdict(
    verdict: Verdict, level: str, validator: Draft7Validator
) -> None:
    doc = to_sarif(summary(), [finding(result(verdict))], source_path="mcp.json")
    assert not list(validator.iter_errors(doc))
    assert doc["runs"][0]["results"][0]["level"] == level


def test_rule_id_is_the_first_sub_label() -> None:
    doc = to_sarif(
        summary(),
        [finding(result(sub_labels=["tool_shadowing", "exfiltration"]))],
        source_path=None,
    )
    assert doc["runs"][0]["results"][0]["ruleId"] == "tool_shadowing"


def test_rule_id_falls_back_to_triage_without_sub_labels() -> None:
    doc = to_sarif(summary(), [finding(result("dismissed", sub_labels=[]))], source_path=None)
    assert doc["runs"][0]["results"][0]["ruleId"] == "triage"


def test_scanner_original_verdict_is_preserved_both_ways() -> None:
    flagged = ScannerFinding(
        scanner="cisco_yara",
        scanner_version="1.0",
        rule_id="credential_harvesting",
        severity="high",
        message="m",
        raw={},
    )
    doc = to_sarif(
        summary(),
        [
            finding(result(scanner_findings=[flagged]), name="export_csv"),
            finding(result("dismissed", sub_labels=[]), name="get_current"),
        ],
        source_path=None,
    )
    verdicts = [r["properties"]["scanner_original_verdict"] for r in doc["runs"][0]["results"]]
    assert verdicts == ["flagged", "not_flagged"]


def test_properties_carry_the_triage_explanation() -> None:
    doc = to_sarif(summary(), [finding(result())], source_path=None)
    props = doc["runs"][0]["results"][0]["properties"]
    assert props["poisoned_probability"] == pytest.approx(0.90)
    assert props["predicted_class"] == "poisoned"
    assert props["stage"] == "judge"
    assert props["model_version"] == "fake@0"
    assert props["tool_name"] == "read_file"
    assert props["server_name"] == "fs"


def test_a_located_span_becomes_a_region(validator: Draft7Validator) -> None:
    span = EvidenceSpan(field="description", path=None, quote="Reads a file.", start=4, end=17)
    doc = to_sarif(summary(), [finding(result(spans=[span]))], source_path="mcp.json")
    assert not list(validator.iter_errors(doc))
    location = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
    assert location["artifactLocation"]["uri"] == "mcp.json"
    assert location["region"] == {"charOffset": 4, "charLength": 13}


def test_an_unlocated_span_produces_no_region() -> None:
    span = EvidenceSpan(field="description", path=None, quote="nowhere", start=None, end=None)
    doc = to_sarif(summary(), [finding(result(spans=[span]))], source_path="mcp.json")
    assert "region" not in doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]


def test_without_a_source_path_a_result_has_no_locations(validator: Draft7Validator) -> None:
    doc = to_sarif(summary(), [finding(result())], source_path=None)
    assert not list(validator.iter_errors(doc))
    assert doc["runs"][0]["results"][0].get("locations") in (None, [])


def test_message_text_falls_back_to_a_generated_sentence() -> None:
    doc = to_sarif(
        summary(),
        [finding(result("needs_review", reasoning=None, sub_labels=[], stage="classifier"))],
        source_path=None,
    )
    text = doc["runs"][0]["results"][0]["message"]["text"]
    assert "read_file" in text
    assert "needs review" in text.lower()
    assert text.endswith(".")


def test_run_properties_carry_the_scan_counters() -> None:
    doc = to_sarif(summary(), fixture_findings(), source_path=None)
    props = doc["runs"][0]["properties"]
    assert props["scan_id"] == FIXTURE["summary"]["id"]
    assert props["tool_count"] == 27
    assert props["confirmed"] + props["needs_review"] + props["dismissed"] == 27
