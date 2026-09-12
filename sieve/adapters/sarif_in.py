"""`sarif_in` adapter: ingests SARIF 2.1.0 from any SARIF-emitting scanner (PRD §10.2).

This is an *ingest* adapter (no binary to run) so it exposes only `normalise`; there is nothing to
`scan()` offline. `sieve/sarif.py` (a different lane) is Sieve's own SARIF *output* — unrelated.
"""

from __future__ import annotations

from typing import Any

from sieve.types import ScannerFinding, Severity

_LEVEL_TO_SEVERITY: dict[str, Severity] = {"error": "high", "warning": "medium", "note": "low"}
_DEFAULT_SEVERITY: Severity = "medium"


def _tool_name(result: dict[str, Any]) -> str | None:
    locations = result.get("locations") or []
    if locations:
        logical = locations[0].get("logicalLocations") or []
        if logical:
            name = logical[0].get("name")
            if isinstance(name, str):
                return name
    props = result.get("properties") or {}
    tool = props.get("tool")
    return tool if isinstance(tool, str) else None


def normalise(sarif: dict[str, Any]) -> list[ScannerFinding]:
    """Map `results[]` in every run to `ScannerFinding`s.

    `ruleId` -> `rule_id`; `level` -> `severity` (error/warning/note -> high/medium/low, unknown
    or missing -> medium); `message.text` -> `message`; tool name from
    `locations[0].logicalLocations[0].name`, falling back to `properties.tool`.
    """
    out: list[ScannerFinding] = []
    for run in sarif.get("runs", []):
        version = run.get("tool", {}).get("driver", {}).get("version", "unknown")
        for result in run.get("results", []):
            level = result.get("level")
            severity = _LEVEL_TO_SEVERITY.get(level, _DEFAULT_SEVERITY)
            out.append(
                ScannerFinding(
                    scanner="sarif_in",
                    scanner_version=version,
                    rule_id=result.get("ruleId"),
                    severity=severity,
                    message=result.get("message", {}).get("text"),
                    raw={"tool_name": _tool_name(result)},
                )
            )
    return out
