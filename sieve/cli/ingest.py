"""Turns CLI input into `ToolRecord`s and `ScannerFinding`s (PRD §15.1).

Three shapes of input, three loaders:

- A `tools/list` dump (the default) -- `sieve.adapters.static_json.load_tools_list`.
- A scanner's own native report (`--from cisco-yara|sarif`) -- `load_from_scanner` below, which
  has no tool descriptions to work with (a scanner report never carries one), only tool names
  and whatever findings the scanner attached to them.
- An `mcp.json` config (`--config PATH --static`) -- its shape validated by
  `sieve.adapters.static_json.load_mcp_config`, its statically declared tools lifted by
  `sieve.tools_doc.records_from_mcp_config`. SC-1 forbids spawning the configured server, and
  listing a server's tools requires exactly that, so this path yields **only** what the config
  declares for itself: for a config that declares nothing, zero `ToolRecord`s (PRD §15.1,
  "parse only"). The API lifts the same array from the same helper, so the two surfaces ingest
  one config identically (PRD §13.3).
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from sieve.adapters import sarif_in
from sieve.adapters.base import get_adapter
from sieve.adapters.static_json import ConfigEntry, load_mcp_config, load_tools_list
from sieve.tools_doc import records_from_mcp_config
from sieve.types import ScannerFinding, ToolDefinition, ToolRecord

ScannerFrom = Literal["cisco-yara", "sarif"]


class IngestError(ValueError):
    """Bad or unreadable CLI input. The caller maps this to exit code 2."""


def read_text(path: str) -> str:
    """`path` is a file, or `"-"` for stdin."""
    if path == "-":
        return sys.stdin.read()
    p = Path(path)
    if not p.is_file():
        raise IngestError(f"input file not found: {path}")
    try:
        return p.read_text(encoding="utf-8")
    except OSError as exc:
        raise IngestError(f"could not read {path}: {exc}") from exc


def load_json(text: str, *, what: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise IngestError(f"{what} is not valid JSON: {exc}") from exc


def load_from_scanner(
    fmt: ScannerFrom, doc: Any
) -> tuple[list[ToolRecord], dict[str, list[ScannerFinding]]]:
    """Build `ToolRecord`s (name only -- no description, no schema) plus findings per tool.

    `--from cisco-yara` expects the reshaped `by_tool` document (`{tool_name: {"findings": [...]}
    }`), the one `mcp-scanner --format by_tool` (or the adapter's own `raw_to_by_tool`) produces
    -- its keys are every tool the scan covered, flagged or not. `--from sarif` expects a SARIF
    log; SARIF only names tools it flagged, so a clean tool in a SARIF-only run is invisible to
    Sieve (a documented limitation of ingesting someone else's report instead of a tools/list).
    """
    if fmt == "cisco-yara":
        if not isinstance(doc, dict):
            raise IngestError("--from cisco-yara expects a by_tool JSON object")
        tool_names = sorted(doc.keys())
        findings = get_adapter("cisco_yara").normalise(doc)
    elif fmt == "sarif":
        if not isinstance(doc, dict):
            raise IngestError("--from sarif expects a SARIF JSON object")
        findings = sarif_in.normalise(doc)
        tool_names = sorted({n for f in findings if isinstance(n := f.raw.get("tool_name"), str)})
    else:  # pragma: no cover - Typer's own choice validation rejects this first
        raise IngestError(f"unknown --from scanner: {fmt!r}")

    by_tool: dict[str, list[ScannerFinding]] = defaultdict(list)
    for finding in findings:
        name = finding.raw.get("tool_name")
        if isinstance(name, str):
            by_tool[name].append(finding)

    records = [
        ToolRecord(
            id=name,
            server_name="",
            tool=ToolDefinition(name=name, inputSchema={}),
            sibling_tool_names=[n for n in tool_names if n != name],
        )
        for name in tool_names
    ]
    return records, dict(by_tool)


def load_tools(doc: Any) -> list[ToolRecord]:
    if not isinstance(doc, dict):
        raise IngestError("input must be a tools/list JSON object")
    return load_tools_list(doc)


def parse_static_config(doc: Any) -> list[ConfigEntry]:
    """Parse-only (SC-1): validates the config and never spawns or connects to anything."""
    if not isinstance(doc, dict):
        raise IngestError("--config input must be a JSON object")
    return load_mcp_config(doc)


def declared_tools(doc: Any) -> list[ToolRecord]:
    """The tools an `mcp.json` declares statically -- the only ones a parse can know about.

    Shares `sieve.tools_doc` with `sieve/api/ingest.py` rather than re-deriving the lift, so a
    config posted to the API and the same config handed to `--config --static` cannot disagree.
    """
    if not isinstance(doc, dict):
        raise IngestError("--config input must be a JSON object")
    return records_from_mcp_config(doc)
