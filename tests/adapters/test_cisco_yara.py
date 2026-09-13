from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from sieve.adapters.base import AdapterSchemaError, AdapterVersionUnpinned, get_adapter
from sieve.adapters.cisco_yara import CiscoYaraAdapter, raw_to_by_tool
from sieve.adapters.static_json import load_tools_list
from sieve.types import ToolRecord

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
GOLDEN_BY_TOOL = FIXTURES / "scanner" / "cisco-yara-sample-27.json"
GOLDEN_RAW = FIXTURES / "scanner" / "cisco-yara-sample-27.raw.json"
LOCKFILE = Path(__file__).resolve().parents[2] / "bench" / "versions.lock"

FLAGGED_TOOLS = {"write_file", "export_csv", "sync_notes", "forward_mail"}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_name_and_version_are_attributes_not_callables() -> None:
    adapter = CiscoYaraAdapter(lockfile=LOCKFILE)
    assert adapter.name == "cisco_yara"
    assert isinstance(adapter.version, str)
    assert adapter.version == "4.8.0"


def test_get_adapter_resolves_cisco_yara() -> None:
    assert isinstance(get_adapter("cisco_yara"), CiscoYaraAdapter)


def test_raw_to_by_tool_matches_reshaped_golden_exactly() -> None:
    raw = _load(GOLDEN_RAW)
    golden = _load(GOLDEN_BY_TOOL)
    assert raw_to_by_tool(raw) == golden


def test_normalise_flags_exactly_the_four_known_tools() -> None:
    adapter = CiscoYaraAdapter(lockfile=LOCKFILE)
    by_tool = _load(GOLDEN_BY_TOOL)
    findings = adapter.normalise(by_tool)

    tool_names = {f.raw["tool_name"] for f in findings}
    assert tool_names == FLAGGED_TOOLS
    for f in findings:
        assert f.scanner == "cisco_yara"
        assert f.scanner_version == "4.8.0"
        assert f.rule_id is not None
        assert f.raw["tool_name"] in FLAGGED_TOOLS

    # write_file and forward_mail each carry two distinct rules in the golden; both survive
    # normalisation so the FP-cause taxonomy (PRD §10.3) can bucket by every triggering rule.
    write_file_rules = {f.rule_id for f in findings if f.raw["tool_name"] == "write_file"}
    assert write_file_rules == {"credential_harvesting", "prompt_injection"}


def test_normalise_unknown_key_inside_a_tool_entry_raises_schema_error() -> None:
    adapter = CiscoYaraAdapter(lockfile=LOCKFILE)
    by_tool = _load(GOLDEN_BY_TOOL)
    by_tool["write_file"]["unexpected_extra"] = "oops"
    with pytest.raises(AdapterSchemaError):
        adapter.normalise(by_tool)


def test_normalise_unknown_key_inside_a_finding_raises_schema_error() -> None:
    adapter = CiscoYaraAdapter(lockfile=LOCKFILE)
    by_tool = _load(GOLDEN_BY_TOOL)
    by_tool["write_file"]["findings"][0]["unexpected_extra"] = "oops"
    with pytest.raises(AdapterSchemaError):
        adapter.normalise(by_tool)


def test_missing_lockfile_line_raises_adapter_version_unpinned(tmp_path: Path) -> None:
    lockfile = tmp_path / "versions.lock"
    lockfile.write_text("cisco_llm=4.8.0\n", encoding="utf-8", newline="\n")
    with pytest.raises(AdapterVersionUnpinned):
        CiscoYaraAdapter(lockfile=lockfile)


def test_scan_forwards_exact_argv_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(
        args: list[str], *, capture_output: bool, text: bool, env: dict[str, str], check: bool
    ) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["env"] = env
        return subprocess.CompletedProcess(args, 0, stdout="[]", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    adapter = CiscoYaraAdapter(binary="mcp-scanner", lockfile=LOCKFILE)
    doc = _load(FIXTURES / "tools" / "sample-27.json")
    records = load_tools_list(doc)
    result = adapter.scan(records)

    args = captured["args"]
    assert args[:6] == ["mcp-scanner", "--analyzers", "yara", "--raw", "static", "--tools"]
    assert len(args) == 7
    tools_path = Path(args[6])
    assert tools_path.suffix == ".json"
    # scan() must clean up its temp file after running
    assert not tools_path.exists()

    env = captured["env"]
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert result == []


def test_scan_redacts_credential_shaped_strings_before_writing_the_temp_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SC-5: nothing credential-shaped may reach the temp file handed to the scanner binary."""
    captured_content: dict[str, str] = {}

    def fake_run(
        args: list[str], *, capture_output: bool, text: bool, env: dict[str, str], check: bool
    ) -> subprocess.CompletedProcess[str]:
        # Read the temp file's bytes now, before scan()'s `finally` deletes it.
        captured_content["tools_json"] = Path(args[6]).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="[]", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    secret = "AKIAABCDEFGHIJKLMNOP"  # noqa: S105 (fake AWS key shape, proves redaction; not a real credential)
    record = ToolRecord(
        id="leaky",
        server_name="leaky-server",
        tool={
            "name": "leaky_tool",
            "description": f"Uploads a file using credentials {secret}.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    )
    adapter = CiscoYaraAdapter(binary="mcp-scanner", lockfile=LOCKFILE)
    adapter.scan([record])

    assert secret not in captured_content["tools_json"]
    assert "REDACTED" in captured_content["tools_json"]


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("mcp-scanner") is None, reason="mcp-scanner not on PATH")
def test_scan_end_to_end_with_real_scanner() -> None:
    adapter = CiscoYaraAdapter(lockfile=LOCKFILE)
    doc = _load(FIXTURES / "tools" / "sample-27.json")
    records = load_tools_list(doc)
    findings = adapter.scan(records)
    tool_names = {f.raw["tool_name"] for f in findings}
    assert tool_names == FLAGGED_TOOLS
