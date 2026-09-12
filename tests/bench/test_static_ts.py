"""Static extraction from TypeScript sources. Zod shapes can only ever be `approx`."""

from __future__ import annotations

from pathlib import Path

from bench.collect.static_ts import extract_typescript

D = Path("tests/bench/data/ts")


def _source(name: str) -> str:
    return (D / name).read_text(encoding="utf-8")


def test_positional_tool_call_gives_name_description_and_zod_properties() -> None:
    tools = extract_typescript(_source("mcp_sdk_server.ts"), server_name="files")
    t = next(t for t in tools if t.tool.name == "read_file")
    assert t.tool.description == "Reads a file."
    assert t.schema_fidelity == "approx"
    props = t.tool.inputSchema["properties"]
    assert props["path"]["type"] == "string"
    assert props["path"]["description"] == "Absolute path"
    assert props["limit"]["type"] == "number"
    assert props["verbose"]["type"] == "boolean"
    assert props["filters"]["type"] == "array"


def test_optional_properties_drop_out_of_required() -> None:
    tools = extract_typescript(_source("mcp_sdk_server.ts"), server_name="files")
    t = next(t for t in tools if t.tool.name == "read_file")
    assert t.tool.inputSchema["required"] == ["path", "filters"]


def test_tool_call_without_a_description_still_extracts() -> None:
    tools = extract_typescript(_source("mcp_sdk_server.ts"), server_name="files")
    t = next(t for t in tools if t.tool.name == "ping")
    assert t.tool.description is None
    assert t.tool.inputSchema["properties"]["note"]["type"] == "string"


def test_a_call_on_another_receiver_is_not_a_tool() -> None:
    names = {t.tool.name for t in extract_typescript(_source("mcp_sdk_server.ts"), server_name="x")}
    assert "decoy" not in names


def test_register_tool_reads_the_config_object() -> None:
    tools = extract_typescript(_source("registerTool_server.ts"), server_name="weather")
    t = next(t for t in tools if t.tool.name == "get_current")
    assert t.tool.title == "Current weather"
    assert t.tool.description == "Get current weather conditions for a location."
    assert t.schema_fidelity == "approx"
    props = t.tool.inputSchema["properties"]
    assert props["location"]["description"] == "City name or 'lat,lon'"
    assert t.tool.inputSchema["required"] == ["location"]


def test_non_literal_shape_yields_an_empty_schema_not_a_guess() -> None:
    tools = extract_typescript(_source("registerTool_server.ts"), server_name="weather")
    t = next(t for t in tools if t.tool.name == "dynamic")
    assert t.tool.inputSchema == {"type": "object", "properties": {}, "required": []}


def test_origin_line_is_recorded() -> None:
    tools = extract_typescript(_source("registerTool_server.ts"), server_name="weather")
    assert all(t.origin_line > 0 for t in tools)


def test_source_without_tools_yields_nothing() -> None:
    assert extract_typescript("const x = 1;\n", server_name="x") == []
