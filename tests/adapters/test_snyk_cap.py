from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from sieve.adapters.base import AdapterSchemaError, SnykCapExceeded
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


def test_normalise_unknown_key_raises_schema_error() -> None:
    adapter = SnykAdapter()
    with pytest.raises(AdapterSchemaError):
        adapter.normalise({"export_csv": {"findings": [], "unexpected_extra": "oops"}})
    with pytest.raises(AdapterSchemaError):
        adapter.normalise(
            {"export_csv": {"findings": [{"rule_id": "x", "unexpected_extra": "oops"}]}}
        )


def test_scan_redacts_credential_shaped_strings_before_the_runner_sees_them(
    tmp_path: Path,
) -> None:
    """SC-5: nothing credential-shaped may reach an injected runner (a stand-in for Snyk's API)."""
    counter_path = tmp_path / "counter"
    counter_path.write_text("0", encoding="utf-8", newline="\n")
    captured: dict[str, str] = {}

    def fake_runner(records: list[ToolRecord]) -> dict[str, Any]:
        captured["description"] = records[0].tool.description or ""
        return {}

    secret = "AKIAABCDEFGHIJKLMNOP"  # noqa: S105 (fake AWS key shape, proves redaction; not a real credential)
    record = _record()
    record = ToolRecord(
        id=record.id,
        server_name=record.server_name,
        tool={
            "name": record.tool.name,
            "description": f"Uploads a file using credentials {secret}.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    )

    adapter = SnykAdapter(counter_path=counter_path, max_invocations=25, runner=fake_runner)
    adapter.scan([record])

    assert secret not in captured["description"]
    assert "REDACTED" in captured["description"]


def test_missing_counter_file_logs_a_warning_naming_the_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    counter_path = tmp_path / "counter-never-created"

    def fake_runner(records: list[ToolRecord]) -> dict[str, Any]:
        return {}

    adapter = SnykAdapter(counter_path=counter_path, max_invocations=25, runner=fake_runner)
    with caplog.at_level(logging.WARNING):
        adapter.scan([_record()])

    assert any(
        record.levelno == logging.WARNING and str(counter_path) in record.getMessage()
        for record in caplog.records
    )
    assert counter_path.read_text(encoding="utf-8").strip() == "1"


def test_concurrent_scans_at_the_cap_boundary_yield_exactly_one_invocation(
    tmp_path: Path,
) -> None:
    """Two adapters (standing in for two processes) racing at count 24 must not both invoke."""
    counter_path = tmp_path / "counter"
    counter_path.write_text("24", encoding="utf-8", newline="\n")

    call_lock = threading.Lock()
    call_count = 0

    def slow_runner(records: list[ToolRecord]) -> dict[str, Any]:
        nonlocal call_count
        # Widen the race window: if the lock did not serialise read-check-run-write, a second
        # thread could read the pre-increment count while this call is still in flight.
        time.sleep(0.1)
        with call_lock:
            call_count += 1
        return {}

    outcomes: dict[str, str] = {}

    def worker(name: str) -> None:
        adapter = SnykAdapter(counter_path=counter_path, max_invocations=25, runner=slow_runner)
        try:
            adapter.scan([_record()])
            outcomes[name] = "ok"
        except SnykCapExceeded:
            outcomes[name] = "capped"

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert call_count == 1
    assert sorted(outcomes.values()) == ["capped", "ok"]
    assert counter_path.read_text(encoding="utf-8").strip() == "25"
