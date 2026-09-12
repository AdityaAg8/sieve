import json

import pytest
from pydantic import ValidationError

from sieve.types import (
    CorpusRecord,
    EvidenceSpan,
    ScannerFinding,
    ToolDefinition,
    ToolRecord,
    TriageResult,
)


def test_tool_definition_requires_object_schema() -> None:
    with pytest.raises(ValidationError):
        ToolDefinition(name="x", inputSchema={"type": "string"})


def test_tool_definition_rejects_bad_name() -> None:
    with pytest.raises(ValidationError):
        ToolDefinition(name="has space", inputSchema={"type": "object"})


def test_tool_definition_keeps_unknown_mcp_fields() -> None:
    td = ToolDefinition.model_validate(
        {"name": "t", "inputSchema": {"type": "object"}, "icons": [{"src": "x.png"}]}
    )
    assert td.model_dump()["icons"] == [{"src": "x.png"}]


def test_triage_result_roundtrip() -> None:
    r = TriageResult(
        verdict="confirmed",
        stage="classifier",
        predicted_class="poisoned",
        class_probabilities={"benign": 0.01, "imperative_benign": 0.02, "poisoned": 0.97},
        poisoned_probability=0.97,
        confidence=0.97,
        sub_labels=["exfiltration"],
        owasp=["MCP10"],
        evidence_spans=[EvidenceSpan(field="description", path=None, quote="read ~/.aws", start=10, end=21)],
        reasoning="x",
        remediation="y",
        scanner_findings=[],
        model_version="test@0",
        judge_model=None,
        latency_ms=12,
    )
    assert TriageResult.model_validate_json(r.model_dump_json()) == r


def test_class_probabilities_must_sum_to_one() -> None:
    with pytest.raises(ValidationError):
        TriageResult(
            verdict="dismissed", stage="classifier", predicted_class="benign",
            class_probabilities={"benign": 0.5, "imperative_benign": 0.2, "poisoned": 0.1},
            poisoned_probability=0.1, confidence=0.5, sub_labels=[], owasp=[], evidence_spans=[],
            reasoning=None, remediation=None, scanner_findings=[], model_version="t@0",
            judge_model=None, latency_ms=1,
        )


def test_class_probabilities_keys_are_exactly_the_three_labels() -> None:
    with pytest.raises(ValidationError):
        TriageResult(
            verdict="dismissed", stage="classifier", predicted_class="benign",
            class_probabilities={"benign": 0.5, "poisoned": 0.5},
            poisoned_probability=0.5, confidence=0.5, sub_labels=[], owasp=[], evidence_spans=[],
            reasoning=None, remediation=None, scanner_findings=[], model_version="t@0",
            judge_model=None, latency_ms=1,
        )


def test_poisoned_probability_must_match_class_probabilities() -> None:
    with pytest.raises(ValidationError):
        TriageResult(
            verdict="dismissed", stage="classifier", predicted_class="benign",
            class_probabilities={"benign": 0.8, "imperative_benign": 0.1, "poisoned": 0.1},
            poisoned_probability=0.42, confidence=0.8, sub_labels=[], owasp=[], evidence_spans=[],
            reasoning=None, remediation=None, scanner_findings=[], model_version="t@0",
            judge_model=None, latency_ms=1,
        )


def test_corpus_record_defaults_concealment_and_accepts_eval_only_split() -> None:
    rec = CorpusRecord.model_validate(
        {
            "id": "sb-1", "source": "real_poisoned", "source_repo": "https://example.invalid/a",
            "source_commit": "abc1234", "source_license": "MIT", "capture_method": "published_artifact",
            "schema_fidelity": "approx", "server_name": "hashed:9f2c", "label": "poisoned",
            "tool": {"name": "t", "inputSchema": {"type": "object"}},
            "labeller": "aditya", "labelled_at": "2026-09-20T10:00:00Z", "split": "eval_only",
        }
    )
    assert rec.concealment == "none" and rec.generator_model is None and rec.fold is None


def test_corpus_record_requires_provenance() -> None:
    with pytest.raises(ValidationError):
        CorpusRecord.model_validate({"id": "sb-1", "label": "benign"})


def test_scanner_finding_raw_is_free_form() -> None:
    f = ScannerFinding(scanner="cisco_yara", scanner_version="4.3.0", rule_id="r", severity="high", message=None, raw={"any": 1})
    assert json.loads(f.model_dump_json())["raw"] == {"any": 1}


def test_tool_record_sibling_default() -> None:
    rec = ToolRecord(id="r1", server_name="s", tool=ToolDefinition(name="a", inputSchema={"type": "object"}))
    assert rec.sibling_tool_names == []
