"""Shared plumbing for the `mcp-scanner`-backed adapters (`cisco_yara`, `cisco_llm`).

Both wrap the same binary's `--raw` output, differing only in which analyzer block they read
(`yara_analyzer` vs `llm_analyzer`); `snyk_agent_scan` reuses the same `by_tool` document shape
and strict-schema parsing even though its transport is an injected fake. Centralised here so a
schema change (or a redaction fix) is made once, not three times (review round 1, finding 4).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from sieve.adapters.base import AdapterSchemaError, AdapterVersionUnpinned
from sieve.redact import redact, redact_tool
from sieve.types import ScannerFinding, ScannerName, ToolRecord


class Finding(BaseModel):
    """One rule hit for one tool, in the by_tool document."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    severity: str | None = None
    message: str | None = None


class ToolEntry(BaseModel):
    """The value for one tool name in the by_tool document."""

    model_config = ConfigDict(extra="forbid")

    findings: list[Finding]


def raw_to_by_tool(raw: list[dict[str, Any]], *, analyzer_key: str) -> dict[str, Any]:
    """Reshape a scanner's `--raw` array into the `by_tool` document.

    Each raw entry's `findings[analyzer_key].threat_names[i]` pairs positionally with
    `mcp_taxonomies[i]` (verified against the real 4.8.0 yara output): `rule_id` is the threat
    name lower-cased with spaces turned to underscores, `severity` is the analyzer's severity
    lower-cased, `message` is that taxonomy entry's `description`.
    """
    by_tool: dict[str, Any] = {}
    for entry in raw:
        tool_name = entry["tool_name"]
        analyzer = entry["findings"][analyzer_key]
        threat_names: list[str] = analyzer.get("threat_names", [])
        taxonomies: list[dict[str, Any]] = analyzer.get("mcp_taxonomies", [])
        severity = analyzer.get("severity")
        findings = []
        for i, threat_name in enumerate(threat_names):
            rule_id = threat_name.lower().replace(" ", "_")
            message = taxonomies[i]["description"] if i < len(taxonomies) else None
            findings.append(
                {
                    "rule_id": rule_id,
                    "severity": severity.lower() if isinstance(severity, str) else None,
                    "message": message,
                }
            )
        by_tool[tool_name] = {"findings": findings}
    return by_tool


def read_pinned_version(lockfile: Path, scanner: str) -> str:
    """Read `<scanner>=<version>` from the lockfile, stripping any trailing `# comment`."""
    if not lockfile.exists():
        raise AdapterVersionUnpinned(f"lockfile not found: {lockfile}")
    prefix = f"{scanner}="
    for line in lockfile.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].split("#", 1)[0].strip()
    raise AdapterVersionUnpinned(f"no {prefix!r} line in {lockfile}")


def parse_by_tool(raw: dict[str, Any], *, schema_name: str) -> dict[str, ToolEntry]:
    """Strictly validate a `by_tool` document; unknown keys at any level fail loudly."""
    try:
        return {name: ToolEntry(**entry) for name, entry in raw.items()}
    except (ValidationError, TypeError, AttributeError) as exc:
        raise AdapterSchemaError(f"unrecognised {schema_name} by_tool schema: {exc}") from exc


def to_scanner_findings(
    parsed: dict[str, ToolEntry], scanner: ScannerName, version: str
) -> list[ScannerFinding]:
    """One `ScannerFinding` per (tool, rule) pair.

    `raw` carries the finding's own body *and* `tool_name`, per the brief's literal text — the
    baseline joins findings back to records by `raw["tool_name"]`, and the rest of the body is
    kept unchanged for anyone inspecting a finding directly.
    """
    out: list[ScannerFinding] = []
    for tool_name, entry in parsed.items():
        for finding in entry.findings:
            out.append(
                ScannerFinding(
                    scanner=scanner,
                    scanner_version=version,
                    rule_id=finding.rule_id,
                    severity=finding.severity,
                    message=finding.message,
                    raw={**finding.model_dump(), "tool_name": tool_name},
                )
            )
    return out


def redacted_tool_dicts(records: list[ToolRecord]) -> list[dict[str, Any]]:
    """Redact credential-shaped strings (SC-5) before a tool leaves the process as a JSON dict."""
    out = []
    for r in records:
        redacted, _kinds = redact_tool(r.tool)
        out.append(redacted.model_dump(mode="json", exclude_none=True))
    return out


def redacted_records(records: list[ToolRecord]) -> list[ToolRecord]:
    """Redact credential-shaped strings (SC-5) before records are handed to an injected runner.

    `server_instructions` is redacted here too (security review 11.1, finding F2). It used to be
    copied through verbatim beside a redacted tool, which meant a credential pasted into a
    server's instructions left the process in the clear on the one path that has no second
    chance to catch it: the runner is injected, so nothing downstream of this function belongs
    to Sieve. `redact_tool` cannot do it, because the instructions live on the record, not on
    the `ToolDefinition`.
    """
    out = []
    for r in records:
        redacted, _kinds = redact_tool(r.tool)
        instructions = r.server_instructions
        if instructions is not None:
            instructions, _ = redact(instructions)
        out.append(
            ToolRecord(
                id=r.id,
                server_name=r.server_name,
                tool=redacted,
                sibling_tool_names=r.sibling_tool_names,
                server_instructions=instructions,
            )
        )
    return out
