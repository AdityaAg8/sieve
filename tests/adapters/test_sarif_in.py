from __future__ import annotations

import json
from pathlib import Path

from sieve.adapters.sarif_in import normalise

SAMPLE = Path(__file__).resolve().parent / "data" / "sample.sarif"


def _load() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def test_normalise_maps_level_to_severity_and_resolves_tool_name() -> None:
    findings = normalise(_load())
    assert len(findings) == 3
    by_rule = {f.rule_id: f for f in findings}

    error_finding = by_rule["prompt_injection"]
    assert error_finding.severity == "high"
    assert error_finding.message == "Description attempts to override system instructions."
    assert error_finding.raw["tool_name"] == "forward_mail"
    assert error_finding.scanner == "sarif_in"
    assert error_finding.scanner_version == "1.2.3"

    warning_finding = by_rule["credential_harvesting"]
    assert warning_finding.severity == "medium"
    # No `locations`; falls back to `properties.tool`.
    assert warning_finding.raw["tool_name"] == "set_location"

    note_finding = by_rule["loose_permissions"]
    assert note_finding.severity == "low"
    assert note_finding.raw["tool_name"] == "write_file"


def test_normalise_defaults_unknown_level_to_medium() -> None:
    doc = {
        "runs": [
            {
                "tool": {"driver": {"name": "x", "version": "0.0.1"}},
                "results": [
                    {
                        "ruleId": "mystery",
                        "message": {"text": "no level field"},
                    }
                ],
            }
        ]
    }
    findings = normalise(doc)
    assert len(findings) == 1
    assert findings[0].severity == "medium"
    assert findings[0].raw["tool_name"] is None
