"""Fixture: a low-level MCP server returning literal `types.Tool` objects."""

import mcp.types as types
from mcp.server import Server

server = Server("weather")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_current",
            description="Get current weather conditions for a location.",
            inputSchema={
                "type": "object",
                "properties": {"location": {"type": "string", "description": "City name."}},
                "required": ["location"],
            },
        ),
        types.Tool(
            name="get_forecast",
            description="Get a multi-day forecast.",
            inputSchema={
                "type": "object",
                "properties": {"location": {"type": "string"}, "days": {"type": "integer"}},
                "required": ["location"],
            },
        ),
        types.Tool(
            name="dynamic",
            description="Schema built at runtime, so it cannot be read statically.",
            inputSchema=build_schema(),  # noqa: F821 (fixture: deliberately non-literal)
        ),
    ]
