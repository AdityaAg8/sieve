from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sieve.adapters.base import SnykCapExceeded
from sieve.adapters.snyk_agent_scan import SnykAdapter
from sieve.types import ToolRecord


def _record(name: str = "export_csv") -> ToolRecord:
    return ToolRecord(
        id=name,
        server_name="db-query",
        tool={
            "name": name,
            "description": "Exports a query result.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    )


def test_cap_exceeded_at_25_never_calls_the_runner(tmp_path: Path) -> None:
    counter_path = tmp_path / "counter"
    counter_path.write_text("25", encoding="utf-8", newline="\n")
    called = False

    def fake_runner(records: list[ToolRecord]) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    adapter = SnykAdapter(counter_path=counter_path, max_invocations=25, runner=fake_runner)
    with pytest.raises(SnykCapExceeded):
        adapter.scan([_record()])

    assert called is False
    assert counter_path.read_text(encoding="utf-8").strip() == "25"


def test_at_24_runs_once_and_counter_advances_to_25(tmp_path: Path) -> None:
    counter_path = tmp_path / "counter"
    counter_path.write_text("24", encoding="utf-8", newline="\n")
    calls: list[list[ToolRecord]] = []

    def fake_runner(records: list[ToolRecord]) -> dict[str, Any]:
        calls.append(records)
        return {
            "export_csv": {
                "findings": [
                    {"rule_id": "leaked_secret", "severity": "high", "message": "found a token"}
                ]
            }
        }

    adapter = SnykAdapter(counter_path=counter_path, max_invocations=25, runner=fake_runner)
    findings = adapter.scan([_record()])

    assert len(calls) == 1
    assert counter_path.read_text(encoding="utf-8").strip() == "25"
    assert len(findings) == 1
    assert findings[0].scanner == "snyk_agent_scan"
    assert findings[0].rule_id == "leaked_secret"
    assert findings[0].raw["tool_name"] == "export_csv"


def test_missing_counter_file_is_treated_as_zero(tmp_path: Path) -> None:
    counter_path = tmp_path / "does-not-exist-yet"

    def fake_runner(records: list[ToolRecord]) -> dict[str, Any]:
        return {}

    adapter = SnykAdapter(counter_path=counter_path, max_invocations=25, runner=fake_runner)
    adapter.scan([_record()])
    assert counter_path.read_text(encoding="utf-8").strip() == "1"


def test_committed_counter_file_starts_at_zero() -> None:
    committed = Path(__file__).resolve().parents[2] / "bench" / ".snyk_invocations"
    assert committed.read_text(encoding="utf-8").strip() == "0"


def test_unconfigured_runner_raises_clear_error(tmp_path: Path) -> None:
    counter_path = tmp_path / "counter"
    counter_path.write_text("0", encoding="utf-8", newline="\n")
    adapter = SnykAdapter(counter_path=counter_path, max_invocations=25)
    with pytest.raises(RuntimeError, match="SC-2"):
        adapter.scan([_record()])
