"""`cisco_yara` adapter: wraps `mcp-scanner --analyzers yara --raw static --tools <file>` (PRD §10).

The installed scanner (cisco-ai-mcp-scanner 4.8.0) emits a human-readable report for
`--format by_tool`, not JSON — the JSON emitter for this version is `--raw`, which prints a bare
JSON array of per-tool scan results. `raw_to_by_tool` reshapes that array into the documented
`by_tool` document (`{tool_name: {"findings": [{"rule_id", "severity", "message"}]}}`) exactly the
way the committed golden (`fixtures/scanner/cisco-yara-sample-27.json`) was produced from
`fixtures/scanner/cisco-yara-sample-27.raw.json` (see `lessons.md`, "Raw Cisco capture committed
beside the reshaped golden"). `normalise` then maps that by_tool document to `ScannerFinding`s.
"""

from __future__ import annotations

import json
import os
import subprocess  # noqa: S404 (subprocess is the adapter's whole job; every call is targeted)
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from sieve.adapters.base import AdapterSchemaError, AdapterVersionUnpinned
from sieve.types import ScannerFinding, ToolRecord

SCANNER_NAME = "cisco_yara"


class _Finding(BaseModel):
    """One rule hit for one tool, in the by_tool document."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    severity: str | None = None
    message: str | None = None


class _ToolEntry(BaseModel):
    """The value for one tool name in the by_tool document."""

    model_config = ConfigDict(extra="forbid")

    findings: list[_Finding]


def raw_to_by_tool(raw: list[dict[str, Any]]) -> dict[str, Any]:
    """Reshape the scanner's `--raw` array into the `by_tool` document.

    Each raw entry's `findings.yara_analyzer.threat_names[i]` pairs positionally with
    `mcp_taxonomies[i]` (verified against the real 4.8.0 output): `rule_id` is the threat name
    lower-cased with spaces turned to underscores, `severity` is the analyzer's severity
    lower-cased, and `message` is that taxonomy entry's `description`.
    """
    by_tool: dict[str, Any] = {}
    for entry in raw:
        tool_name = entry["tool_name"]
        yara = entry["findings"]["yara_analyzer"]
        threat_names: list[str] = yara.get("threat_names", [])
        taxonomies: list[dict[str, Any]] = yara.get("mcp_taxonomies", [])
        severity = yara.get("severity")
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


class CiscoYaraAdapter:
    """Runs Cisco's YARA analyzer in static mode and normalises its output."""

    name = SCANNER_NAME

    def __init__(
        self, binary: str = "mcp-scanner", lockfile: Path = Path("bench/versions.lock")
    ) -> None:
        self.binary = binary
        self.version = _read_pinned_version(lockfile, SCANNER_NAME)

    def scan(self, records: list[ToolRecord]) -> list[ScannerFinding]:
        """Offline mode only: writes a temp tools/list file and runs the scanner locally."""
        tools = [r.tool.model_dump(mode="json", exclude_none=True) for r in records]
        fd, path_str = tempfile.mkstemp(suffix=".json")
        path = Path(path_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump({"tools": tools}, fh)
            env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
            result = subprocess.run(  # noqa: S603 (fixed argv below; no shell, no untrusted input)
                [self.binary, "--analyzers", "yara", "--raw", "static", "--tools", str(path)],
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
        finally:
            path.unlink(missing_ok=True)
        raw = json.loads(result.stdout) if result.stdout.strip() else []
        by_tool = raw_to_by_tool(raw)
        return self.normalise(by_tool)

    def normalise(self, raw: dict[str, Any]) -> list[ScannerFinding]:
        """Map a `by_tool` document to `ScannerFinding`s, one per (tool, rule) pair."""
        try:
            parsed = {name: _ToolEntry(**entry) for name, entry in raw.items()}
        except (ValidationError, TypeError) as exc:
            raise AdapterSchemaError(f"unrecognised cisco_yara by_tool schema: {exc}") from exc

        out: list[ScannerFinding] = []
        for tool_name, entry in parsed.items():
            for finding in entry.findings:
                out.append(
                    ScannerFinding(
                        scanner=SCANNER_NAME,
                        scanner_version=self.version,
                        rule_id=finding.rule_id,
                        severity=finding.severity,
                        message=finding.message,
                        raw={"tool_name": tool_name},
                    )
                )
        return out


def _read_pinned_version(lockfile: Path, scanner: str) -> str:
    """Read `<scanner>=<version>` from the lockfile, stripping any trailing `# comment`."""
    if not lockfile.exists():
        raise AdapterVersionUnpinned(f"lockfile not found: {lockfile}")
    prefix = f"{scanner}="
    for line in lockfile.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if stripped.startswith(prefix):
            value = stripped[len(prefix) :].split("#", 1)[0].strip()
            return value
    raise AdapterVersionUnpinned(f"no {prefix!r} line in {lockfile}")
