"""SARIF 2.1.0 export (PRD §13.5).

The original scanner verdict is never dropped: every result records whether the scanner had
flagged that tool, so a SARIF consumer can see what Sieve *changed* rather than only what
Sieve concluded. Levels follow the triage verdict, not the scanner's severity.
"""

from __future__ import annotations

from typing import Any

from sieve import __version__
from sieve.config import settings
from sieve.owasp import OWASP_TITLES, SUB_LABEL_TO_OWASP
from sieve.types import Finding, ScanSummary, TriageResult, Verdict

SCHEMA_URI = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"
)
SARIF_VERSION = "2.1.0"
TOOL_NAME = "Sieve"
OWASP_PROJECT = "https://owasp.org/www-project-mcp-top-10/"
FALLBACK_RULE = "triage"

LEVELS: dict[Verdict, str] = {
    "confirmed": "error",
    "needs_review": "warning",
    "dismissed": "note",
}
VERDICT_PHRASE: dict[Verdict, str] = {
    "confirmed": "is confirmed as tool poisoning",
    "needs_review": "needs review",
    "dismissed": "was dismissed",
}


def _help_uri(owasp_id: str) -> str:
    return f"{OWASP_PROJECT}#{owasp_id}"


def _rules() -> list[dict[str, Any]]:
    """One rule per sub-label plus the catch-all, each pointing at its OWASP entry."""
    rules: list[dict[str, Any]] = []
    for sub_label, owasp_id in sorted(SUB_LABEL_TO_OWASP.items()):
        title = OWASP_TITLES[owasp_id]
        rules.append(
            {
                "id": sub_label,
                "name": sub_label,
                "shortDescription": {"text": sub_label.replace("_", " ").capitalize()},
                "fullDescription": {"text": f"OWASP MCP Top 10 {owasp_id}: {title}."},
                "helpUri": _help_uri(owasp_id),
                "properties": {"owasp": owasp_id, "tags": ["mcp", "tool-poisoning", owasp_id]},
            }
        )
    rules.append(
        {
            "id": FALLBACK_RULE,
            "name": FALLBACK_RULE,
            "shortDescription": {"text": "Triage verdict without a specific sub-label"},
            "fullDescription": {
                "text": "A Sieve verdict the judge did not attribute to a named sub-label."
            },
            "helpUri": OWASP_PROJECT,
            "properties": {"tags": ["mcp", "triage"]},
        }
    )
    return rules


def _message(finding: Finding) -> str:
    result = finding.result
    if result.reasoning:
        return result.reasoning
    phrase = VERDICT_PHRASE[result.verdict]
    return (
        f"Tool {finding.tool.name!r} {phrase} "
        f"(p_poisoned={result.poisoned_probability:.2f}, class={result.predicted_class}, "
        f"stage={result.stage})."
    )


def _location(finding: Finding, source_path: str | None) -> list[dict[str, Any]]:
    if source_path is None:
        return []
    physical: dict[str, Any] = {"artifactLocation": {"uri": source_path}}
    span = next(
        (s for s in finding.result.evidence_spans if s.start is not None and s.end is not None),
        None,
    )
    if span is not None and span.start is not None and span.end is not None:
        physical["region"] = {
            "charOffset": span.start,
            "charLength": span.end - span.start,
        }
    return [{"physicalLocation": physical}]


def _properties(finding: Finding) -> dict[str, Any]:
    result: TriageResult = finding.result
    return {
        "poisoned_probability": result.poisoned_probability,
        "predicted_class": result.predicted_class,
        "class_probabilities": dict(result.class_probabilities),
        "stage": result.stage,
        "confidence": result.confidence,
        "model_version": result.model_version,
        "judge_model": result.judge_model,
        "owasp": list(result.owasp),
        "sub_labels": list(result.sub_labels),
        # The scanner's own call, kept beside Sieve's so the delta is legible.
        "scanner_original_verdict": "flagged" if result.scanner_findings else "not_flagged",
        "scanner_rule_ids": [f.rule_id for f in result.scanner_findings if f.rule_id],
        "tool_name": finding.tool.name,
        "server_name": finding.server_name,
        "finding_id": finding.id,
    }


def _result(finding: Finding, source_path: str | None) -> dict[str, Any]:
    sub_labels = finding.result.sub_labels
    out: dict[str, Any] = {
        "ruleId": sub_labels[0] if sub_labels else FALLBACK_RULE,
        "level": LEVELS[finding.result.verdict],
        "message": {"text": _message(finding)},
        "properties": _properties(finding),
    }
    locations = _location(finding, source_path)
    if locations:
        out["locations"] = locations
    return out


def to_sarif(
    scan: ScanSummary, findings: list[Finding], *, source_path: str | None
) -> dict[str, Any]:
    """A SARIF 2.1.0 log for one scan. `source_path` is the config file the tools came from."""
    return {
        "$schema": SCHEMA_URI,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": TOOL_NAME,
                        "version": __version__,
                        "semanticVersion": __version__,
                        "informationUri": settings.web_origin,
                        "rules": _rules(),
                    }
                },
                "results": [_result(f, source_path) for f in findings],
                "properties": {
                    "scan_id": scan.id,
                    "source_type": scan.source_type,
                    "tool_count": scan.tool_count,
                    "scanner_flagged": scan.scanner_flagged,
                    "confirmed": scan.confirmed,
                    "needs_review": scan.needs_review,
                    "dismissed": scan.dismissed,
                    "judge_calls": scan.judge_calls,
                    "model_version": scan.model_version,
                    "judge_model": scan.judge_model,
                },
            }
        ],
    }
