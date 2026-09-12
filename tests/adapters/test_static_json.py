from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from sieve.adapters.static_json import ConfigEntry, load_mcp_config, load_tools_list

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_fixture_loads_to_27_records_with_correct_siblings() -> None:
    doc = _load(FIXTURES / "tools" / "sample-27.json")
    records = load_tools_list(doc)
    assert len(records) == 27

    by_name = {r.tool.name: r for r in records}
    weather = by_name["get_current"]
    assert weather.server_name == "weather"
    assert sorted(weather.sibling_tool_names) == ["get_forecast", "search_city", "set_location"]

    mail = by_name["forward_mail"]
    assert mail.server_name == "mail-relay"
    assert sorted(mail.sibling_tool_names) == ["list_folders", "send_mail"]


def test_json_rpc_envelope_loads() -> None:
    tool = {
        "name": "do_thing",
        "description": "Does a thing.",
        "inputSchema": {"type": "object", "properties": {}},
    }
    other = {
        "name": "do_other",
        "description": "Does another thing.",
        "inputSchema": {"type": "object", "properties": {}},
    }
    doc = {"result": {"tools": [tool, other]}}
    records = load_tools_list(doc)
    assert len(records) == 2
    by_name = {r.tool.name: r for r in records}
    assert by_name["do_thing"].sibling_tool_names == ["do_other"]
    assert by_name["do_other"].sibling_tool_names == ["do_thing"]


def test_plain_tools_list_loads() -> None:
    doc = {
        "tools": [
            {"name": "a", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "b", "inputSchema": {"type": "object", "properties": {}}},
        ]
    }
    records = load_tools_list(doc)
    assert len(records) == 2


def test_mcp_config_yields_two_entries_and_no_tool_records() -> None:
    doc = _load(FIXTURES / "mcp-config" / "sample.mcp.json")
    entries = load_mcp_config(doc)
    assert len(entries) == 2
    assert all(isinstance(e, ConfigEntry) for e in entries)

    by_name = {e.name: e for e in entries}
    assert by_name["weather"].transport == "stdio"
    assert by_name["weather"].command == "npx"
    assert by_name["weather"].url is None
    assert by_name["docs-index"].transport == "http"
    assert by_name["docs-index"].url == "https://docs.example.invalid/mcp"
    assert by_name["docs-index"].command is None


def test_mcp_config_never_executes_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    """SC-1: parsing a config must never touch subprocess or network."""
    import subprocess

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("load_mcp_config must never execute anything")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    doc = _load(FIXTURES / "mcp-config" / "sample.mcp.json")
    load_mcp_config(doc)


def test_tool_missing_input_schema_raises_validation_error_with_tool_name() -> None:
    doc = {"tools": [{"name": "broken_tool", "description": "no schema"}]}
    with pytest.raises(ValidationError, match="broken_tool"):
        load_tools_list(doc)
