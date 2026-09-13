"""Static extraction of MCP tool definitions from Python sources (no code is executed)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from bench.collect.static_py import extract_python

D = Path("tests/bench/data/py")


def _source(name: str) -> str:
    return (D / name).read_text(encoding="utf-8")


def test_fastmcp_decorator_extracts_name_desc_and_approx_schema() -> None:
    tools = extract_python(_source("fastmcp_server.py"), server_name="x")
    t = next(t for t in tools if t.tool.name == "read_file")
    assert "Reads a file" in (t.tool.description or "")
    assert t.tool.inputSchema["properties"]["path"]["type"] == "string"
    assert t.tool.inputSchema["required"] == ["path"]
    assert t.schema_fidelity == "approx"


def test_fastmcp_description_kwarg_beats_the_docstring() -> None:
    tools = extract_python(_source("fastmcp_server.py"), server_name="x")
    t = next(t for t in tools if t.tool.name == "list_dir")
    assert t.tool.description == "Lists a directory."


def test_fastmcp_maps_every_supported_annotation() -> None:
    tools = extract_python(_source("fastmcp_server.py"), server_name="x")
    props = next(t for t in tools if t.tool.name == "search").tool.inputSchema["properties"]
    assert props["query"]["type"] == "string"
    assert props["tags"]["type"] == "array"
    assert props["options"] == {}
    assert props["strict"]["type"] == "boolean"
    other = next(t for t in tools if t.tool.name == "list_dir").tool.inputSchema["properties"]
    assert other["depth"]["type"] == "number"
    assert other["recursive"]["type"] == "boolean"


def test_undecorated_function_is_not_a_tool() -> None:
    tools = extract_python(_source("fastmcp_server.py"), server_name="x")
    assert "not_a_tool" not in {t.tool.name for t in tools}


def test_origin_line_points_at_the_definition() -> None:
    tools = extract_python(_source("fastmcp_server.py"), server_name="x")
    assert all(t.origin_line > 0 for t in tools)
    lines = _source("fastmcp_server.py").splitlines()
    read = next(t for t in tools if t.tool.name == "read_file")
    assert "read_file" in lines[read.origin_line - 1]


def test_lowlevel_tool_literal_is_exact() -> None:
    tools = extract_python(_source("lowlevel_server.py"), server_name="x")
    assert tools[0].schema_fidelity == "exact"
    assert tools[0].tool.inputSchema["type"] == "object"


def test_lowlevel_non_literal_schema_is_skipped_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="bench.collect.static_py"):
        tools = extract_python(_source("lowlevel_server.py"), server_name="x")
    assert {t.tool.name for t in tools} == {"get_current", "get_forecast"}
    assert any("dynamic" in r.message or "inputSchema" in r.message for r in caplog.records)


def test_unparsable_source_yields_nothing() -> None:
    assert extract_python("def broken(:\n", server_name="x") == []


# -- fix round 1: SC-5 redaction before a captured tool reaches a CorpusRecord ----


def test_credential_shaped_strings_are_redacted() -> None:
    source = '''
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("x")


@mcp.tool(description="Authenticate with AKIAIOSFODNN7EXAMPLE before use.")
def leaky(token: str) -> str:
    """Doc."""
    return token
'''
    tools = extract_python(source, server_name="x")
    t = next(t for t in tools if t.tool.name == "leaky")
    assert "AKIAIOSFODNN7EXAMPLE" not in (t.tool.description or "")
    assert "[REDACTED:aws_access_key]" in (t.tool.description or "")
    assert t.tool.name == "leaky"  # the identifier is never redacted


def test_a_credential_in_a_literal_schema_is_redacted() -> None:
    source = """
import mcp.types as types


def list_tools():
    return [
        types.Tool(
            name="leaky",
            description="Fine.",
            inputSchema={
                "type": "object",
                "properties": {
                    "auth": {"type": "string", "description": "api_key=sk-abcdefghij0123456789"}
                },
            },
        )
    ]
"""
    tools = extract_python(source, server_name="x")
    dumped = tools[0].tool.model_dump_json()
    assert "sk-abcdefghij0123456789" not in dumped
    assert "REDACTED" in dumped
