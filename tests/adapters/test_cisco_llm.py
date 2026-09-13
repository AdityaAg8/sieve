from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from sieve.adapters.base import AdapterCacheMiss, AdapterSchemaError
from sieve.adapters.cisco_llm import CiscoLlmAdapter, raw_to_by_tool
from sieve.adapters.static_json import load_tools_list
from sieve.types import ToolRecord

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "tests" / "adapters" / "data" / "cisco-llm-sample.AUTHORED.json"
LOCKFILE = ROOT / "bench" / "versions.lock"
TOOLS_27 = ROOT / "fixtures" / "tools" / "sample-27.json"

FLAGGED_TOOLS = {"set_location", "fill_form"}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_name_and_version_are_attributes(tmp_path: Path) -> None:
    adapter = CiscoLlmAdapter(lockfile=LOCKFILE, cache_dir=tmp_path)
    assert adapter.name == "cisco_llm"
    assert isinstance(adapter.version, str)
    assert adapter.version == "4.8.0"


def test_raw_to_by_tool_shapes_the_sample() -> None:
    raw = _load(SAMPLE)
    by_tool = raw_to_by_tool(raw)
    assert set(by_tool) == {
        "get_current",
        "list_directory",
        "set_location",
        "fill_form",
        "read_file",
    }
    assert by_tool["set_location"]["findings"][0]["rule_id"] == "secret_solicitation"
    assert by_tool["get_current"]["findings"] == []


def test_normalise_on_committed_sample_yields_findings_for_flagged_tools(tmp_path: Path) -> None:
    adapter = CiscoLlmAdapter(lockfile=LOCKFILE, cache_dir=tmp_path)
    by_tool = raw_to_by_tool(_load(SAMPLE))
    findings = adapter.normalise(by_tool)

    tool_names = {f.raw["tool_name"] for f in findings}
    assert tool_names == FLAGGED_TOOLS
    for f in findings:
        assert f.scanner == "cisco_llm"
        assert f.scanner_version == "4.8.0"
        assert f.rule_id is not None


def test_normalise_unknown_key_raises_schema_error(tmp_path: Path) -> None:
    adapter = CiscoLlmAdapter(lockfile=LOCKFILE, cache_dir=tmp_path)
    by_tool = raw_to_by_tool(_load(SAMPLE))
    by_tool["set_location"]["unexpected_extra"] = "oops"
    with pytest.raises(AdapterSchemaError):
        adapter.normalise(by_tool)


def test_second_scan_with_same_records_hits_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fake_run(
        args: list[str], *, capture_output: bool, text: bool, env: dict[str, str], check: bool
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(args, 0, stdout=SAMPLE.read_text(encoding="utf-8"))

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.delenv("SIEVE_OFFLINE", raising=False)

    adapter = CiscoLlmAdapter(lockfile=LOCKFILE, cache_dir=tmp_path)
    records = load_tools_list(_load(TOOLS_27))[:5]

    first = adapter.scan(records)
    second = adapter.scan(records)

    assert calls == 1
    assert {f.raw["tool_name"] for f in first} == {f.raw["tool_name"] for f in second}


def test_scan_redacts_credential_shaped_strings_before_writing_the_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC-5: nothing credential-shaped may reach the temp file handed to the scanner binary."""
    captured_content: dict[str, str] = {}

    def fake_run(
        args: list[str], *, capture_output: bool, text: bool, env: dict[str, str], check: bool
    ) -> subprocess.CompletedProcess[str]:
        captured_content["tools_json"] = Path(args[6]).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="[]")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.delenv("SIEVE_OFFLINE", raising=False)

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
    adapter = CiscoLlmAdapter(lockfile=LOCKFILE, cache_dir=tmp_path)
    adapter.scan([record])

    assert secret not in captured_content["tools_json"]
    assert "REDACTED" in captured_content["tools_json"]


def test_cache_miss_under_sieve_offline_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def fail_run(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("must not shell out to the scanner while SIEVE_OFFLINE=1")

    monkeypatch.setattr(subprocess, "run", fail_run)
    monkeypatch.setenv("SIEVE_OFFLINE", "1")

    adapter = CiscoLlmAdapter(lockfile=LOCKFILE, cache_dir=tmp_path)
    records = load_tools_list(_load(TOOLS_27))[:5]

    with pytest.raises(AdapterCacheMiss):
        adapter.scan(records)
    assert called is False
