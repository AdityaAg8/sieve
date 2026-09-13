from __future__ import annotations

from pathlib import Path

import pytest

from sieve.adapters._scanner_common import (
    parse_by_tool,
    raw_to_by_tool,
    read_pinned_version,
    to_scanner_findings,
)
from sieve.adapters.base import AdapterSchemaError, AdapterVersionUnpinned


def test_raw_to_by_tool_is_parametrised_by_analyzer_key() -> None:
    raw = [
        {
            "tool_name": "safe_tool",
            "findings": {"llm_analyzer": {"severity": "SAFE", "threat_names": []}},
        },
        {
            "tool_name": "risky_tool",
            "findings": {
                "llm_analyzer": {
                    "severity": "HIGH",
                    "threat_names": ["SECRET SOLICITATION"],
                    "mcp_taxonomies": [{"description": "asks for a secret"}],
                }
            },
        },
    ]
    by_tool = raw_to_by_tool(raw, analyzer_key="llm_analyzer")
    assert by_tool["safe_tool"]["findings"] == []
    assert by_tool["risky_tool"]["findings"] == [
        {"rule_id": "secret_solicitation", "severity": "high", "message": "asks for a secret"}
    ]


def test_read_pinned_version_strips_trailing_comment(tmp_path: Path) -> None:
    lockfile = tmp_path / "versions.lock"
    lockfile.write_text("thing=1.2.3  # a comment\n", encoding="utf-8", newline="\n")
    assert read_pinned_version(lockfile, "thing") == "1.2.3"


def test_read_pinned_version_raises_when_line_missing(tmp_path: Path) -> None:
    lockfile = tmp_path / "versions.lock"
    lockfile.write_text("other=9.9.9\n", encoding="utf-8", newline="\n")
    with pytest.raises(AdapterVersionUnpinned):
        read_pinned_version(lockfile, "thing")


def test_parse_by_tool_rejects_unknown_keys() -> None:
    with pytest.raises(AdapterSchemaError):
        parse_by_tool({"tool": {"findings": [], "extra": "oops"}}, schema_name="test_scanner")
    with pytest.raises(AdapterSchemaError):
        parse_by_tool(
            {"tool": {"findings": [{"rule_id": "x", "extra": "oops"}]}}, schema_name="test_scanner"
        )


def test_to_scanner_findings_carries_the_original_finding_body_in_raw() -> None:
    parsed = parse_by_tool(
        {
            "risky_tool": {
                "findings": [{"rule_id": "secret_solicitation", "severity": "high", "message": "m"}]
            }
        },
        schema_name="test_scanner",
    )
    findings = to_scanner_findings(parsed, "cisco_yara", "4.8.0")
    assert len(findings) == 1
    f = findings[0]
    assert f.scanner == "cisco_yara"
    assert f.scanner_version == "4.8.0"
    assert f.rule_id == "secret_solicitation"
    # Per the brief's literal text: raw carries the finding's own body AND the tool_name join key.
    assert f.raw == {
        "rule_id": "secret_solicitation",
        "severity": "high",
        "message": "m",
        "tool_name": "risky_tool",
    }
