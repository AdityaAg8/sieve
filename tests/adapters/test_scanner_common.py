from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sieve.adapters import _scanner_common as common
from sieve.adapters._scanner_common import (
    parse_by_tool,
    raw_to_by_tool,
    read_pinned_version,
    redacted_records,
    to_scanner_findings,
)
from sieve.adapters.base import AdapterSchemaError, AdapterVersionUnpinned
from sieve.types import ToolDefinition, ToolRecord


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


# ------------------------------------------------------- SC-5 on the injected-runner path (11.1)

SECRET = "AKIA7SIEVEINSTRUCT0"  # noqa: S105 -- a shaped, non-live test string


def _record(instructions: str | None) -> ToolRecord:
    return ToolRecord(
        id="notes/add_note",
        server_name="notes",
        tool=ToolDefinition(
            name="add_note",
            description=f"Append a note. Sync with {SECRET}X.",
            inputSchema={"type": "object", "properties": {}},
        ),
        server_instructions=instructions,
    )


def test_redacted_records_redacts_server_instructions_too() -> None:
    """Review 11.1, finding F2: `server_instructions` used to be copied through verbatim.

    The tool was redacted and the instructions beside it were not, so a credential pasted into a
    server's instructions reached an injected scanner runner in the clear (SC-5 says redact
    before *any* call, not before most of them).
    """
    [out] = redacted_records([_record(f"Authenticate with {SECRET}Y before calling anything.")])

    assert out.server_instructions is not None
    assert SECRET not in out.server_instructions
    assert "[REDACTED:aws_access_key]" in out.server_instructions
    # And the tool is still redacted, and nothing else about the record moved.
    assert out.tool.description is not None and SECRET not in out.tool.description
    assert (out.id, out.server_name, out.tool.name) == ("notes/add_note", "notes", "add_note")


def test_redacted_records_leaves_absent_instructions_absent() -> None:
    """`None` stays `None`; redaction must not invent an empty string on the record."""
    [out] = redacted_records([_record(None)])
    assert out.server_instructions is None


class _BlockedError(OSError):
    """What Windows raises when Smart App Control refuses to start an executable."""

    winerror = 4551


def _tool_env(root: Path) -> Path:
    python = root / "cisco-ai-mcp-scanner" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    return python


def test_a_launcher_windows_refuses_runs_through_its_own_tool_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    python = _tool_env(tmp_path)
    monkeypatch.setattr(common, "_uv_tool_dir", lambda: tmp_path)
    calls: list[list[str]] = []

    def fake_run(
        args: list[str], *, capture_output: bool, text: bool, env: dict[str, str], check: bool
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[0] == "mcp-scanner":
            raise _BlockedError("An Application Control policy has blocked this file")
        return subprocess.CompletedProcess(args, 0, stdout="[]", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = common.run_scanner("mcp-scanner", ["--raw", "static"], env={})

    assert calls == [
        ["mcp-scanner", "--raw", "static"],
        [str(python), "-c", common.SCANNER_ENTRY_POINT, "--raw", "static"],
    ]
    assert result.stdout == "[]"


def test_a_refused_launcher_with_no_tool_environment_still_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(common, "_uv_tool_dir", lambda: tmp_path)

    def fake_run(
        args: list[str], *, capture_output: bool, text: bool, env: dict[str, str], check: bool
    ) -> subprocess.CompletedProcess[str]:
        raise _BlockedError("An Application Control policy has blocked this file")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(_BlockedError):
        common.run_scanner("mcp-scanner", ["--raw"], env={})


def test_any_other_launch_failure_is_raised_as_it_was(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _tool_env(tmp_path)
    monkeypatch.setattr(common, "_uv_tool_dir", lambda: tmp_path)

    def fake_run(
        args: list[str], *, capture_output: bool, text: bool, env: dict[str, str], check: bool
    ) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(args[0])

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(FileNotFoundError):
        common.run_scanner("mcp-scanner", ["--raw"], env={})
