"""`snyk_agent_scan` adapter: illustrative-only, hard-capped at 25 invocations (SC-2, PRD §10.2).

There is no Snyk CLI or API token available in this environment. Rather than fabricate an
unverified integration against a third-party API, the adapter's transport is an injectable
`runner` callable (`list[ToolRecord] -> by_tool raw dict`, the same `{tool_name: {"findings":
[...]}}` shape used by `cisco_yara`); tests inject a fake. `scan()` reads the persisted counter
*before* calling the runner and raises `SnykCapExceeded` at the cap without ever invoking it — the
cap is enforced in code, not by hoping the caller behaves (SC-2: "hard-capped at 25 invocations,
enforced in code with a persisted counter").
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from sieve.adapters.base import AdapterSchemaError, SnykCapExceeded
from sieve.config import settings
from sieve.types import ScannerFinding, ToolRecord

SCANNER_NAME = "snyk_agent_scan"

RawByTool = dict[str, Any]
Runner = Callable[[list[ToolRecord]], RawByTool]


class _Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    severity: str | None = None
    message: str | None = None


class _ToolEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[_Finding]


def _unconfigured_runner(records: list[ToolRecord]) -> RawByTool:
    raise RuntimeError(
        "no Snyk CLI or API token is configured in this environment; SnykAdapter.scan() "
        "requires an injected `runner` (SC-2: illustrative comparisons only, never mass-scanned)."
    )


class SnykAdapter:
    """Illustrative-only comparison scanner, hard-capped at `max_invocations` (SC-2)."""

    name = SCANNER_NAME

    def __init__(
        self,
        counter_path: Path = Path("bench/.snyk_invocations"),
        max_invocations: int = settings.snyk_max_invocations,
        runner: Runner = _unconfigured_runner,
        version: str = "illustrative",
    ) -> None:
        self.counter_path = counter_path
        self.max_invocations = max_invocations
        self.version = version
        self._runner = runner

    def _read_count(self) -> int:
        if not self.counter_path.exists():
            return 0
        text = self.counter_path.read_text(encoding="utf-8").strip()
        return int(text) if text else 0

    def _write_count(self, count: int) -> None:
        self.counter_path.write_text(str(count), encoding="utf-8", newline="\n")

    def scan(self, records: list[ToolRecord]) -> list[ScannerFinding]:
        count = self._read_count()
        if count >= self.max_invocations:
            raise SnykCapExceeded(
                f"snyk_agent_scan hard cap reached ({count}/{self.max_invocations}); SC-2"
            )
        raw = self._runner(records)
        self._write_count(count + 1)
        return self.normalise(raw)

    def normalise(self, raw: dict[str, Any]) -> list[ScannerFinding]:
        try:
            parsed = {name: _ToolEntry(**entry) for name, entry in raw.items()}
        except (ValidationError, TypeError) as exc:
            raise AdapterSchemaError(f"unrecognised snyk_agent_scan by_tool schema: {exc}") from exc

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
