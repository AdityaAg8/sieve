"""`cisco_yara` adapter: wraps `mcp-scanner --analyzers yara --raw static --tools <file>` (PRD §10).

The installed scanner (cisco-ai-mcp-scanner 4.8.0) emits a human-readable report for
`--format by_tool`, not JSON — the JSON emitter for this version is `--raw`, which prints a bare
JSON array of per-tool scan results. `raw_to_by_tool` reshapes that array into the documented
`by_tool` document (`{tool_name: {"findings": [{"rule_id", "severity", "message"}]}}`) exactly the
way the committed golden (`fixtures/scanner/cisco-yara-sample-27.json`) was produced from
`fixtures/scanner/cisco-yara-sample-27.raw.json`, which is committed beside the reshaped
golden so the transform stays auditable. `normalise` then maps that by_tool document to
`ScannerFinding`s.

The by_tool reshape, strict-schema parsing and version-pinning are shared with `cisco_llm` via
`sieve.adapters._scanner_common` (review round 1, finding 4) — this module only supplies the
`yara_analyzer` key and the scanner's arguments; `_scanner_common.run_scanner` runs it.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from sieve.adapters import _scanner_common as common
from sieve.types import ScannerFinding, ScannerName, ToolRecord

SCANNER_NAME: ScannerName = "cisco_yara"


def raw_to_by_tool(raw: list[dict[str, Any]]) -> dict[str, Any]:
    """Reshape the scanner's `--raw` array into the `by_tool` document (see module docstring)."""
    return common.raw_to_by_tool(raw, analyzer_key="yara_analyzer")


class CiscoYaraAdapter:
    """Runs Cisco's YARA analyzer in static mode and normalises its output."""

    name: str = SCANNER_NAME

    def __init__(
        self, binary: str = "mcp-scanner", lockfile: Path = Path("bench/versions.lock")
    ) -> None:
        self.binary = binary
        self.version = common.read_pinned_version(lockfile, SCANNER_NAME)

    def scan(self, records: list[ToolRecord]) -> list[ScannerFinding]:
        """Offline mode only: writes a temp tools/list file and runs the scanner locally."""
        # SC-5: redact credential-shaped strings before anything is written to disk for the
        # scanner to read.
        tools = common.redacted_tool_dicts(records)
        fd, path_str = tempfile.mkstemp(suffix=".json")
        path = Path(path_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump({"tools": tools}, fh)
            env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
            result = common.run_scanner(
                self.binary,
                ["--analyzers", "yara", "--raw", "static", "--tools", str(path)],
                env=env,
            )
        finally:
            path.unlink(missing_ok=True)
        raw = json.loads(result.stdout) if result.stdout.strip() else []
        by_tool = raw_to_by_tool(raw)
        return self.normalise(by_tool)

    def normalise(self, raw: dict[str, Any]) -> list[ScannerFinding]:
        """Map a `by_tool` document to `ScannerFinding`s, one per (tool, rule) pair."""
        parsed = common.parse_by_tool(raw, schema_name=SCANNER_NAME)
        return common.to_scanner_findings(parsed, SCANNER_NAME, self.version)
