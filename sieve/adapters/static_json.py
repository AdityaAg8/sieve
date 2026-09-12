"""Static, parse-only loaders for a `tools/list` document and an `mcp.json` config (PRD §10.2).

SC-1 (never execute an untrusted MCP server outside the sandboxed worker) applies here directly:
`load_mcp_config` only reads a JSON mapping and never spawns the configured command or opens the
configured URL, so it always returns zero `ToolRecord`s — tools for a config come only from the
sandboxed worker (out of this lane's scope).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sieve.types import ToolDefinition, ToolRecord

Transport = Literal["stdio", "http"]


@dataclass(frozen=True)
class ConfigEntry:
    name: str
    transport: Transport
    command: str | None
    url: str | None


def _tools_array(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Accept `{"tools": [...]}`, `{"result": {"tools": [...]}}`, or the fixture shape."""
    if "result" in doc and isinstance(doc["result"], dict):
        return list(doc["result"].get("tools", []))
    return list(doc.get("tools", []))


def _servers_index(doc: dict[str, Any]) -> dict[str, str] | None:
    """Map tool name -> server name from a `{"servers": {name: [tool_names]}}` block, if present."""
    servers = doc.get("servers")
    if not isinstance(servers, dict):
        return None
    index: dict[str, str] = {}
    for server_name, tool_names in servers.items():
        for tool_name in tool_names:
            index[tool_name] = server_name
    return index


def load_tools_list(doc: dict[str, Any]) -> list[ToolRecord]:
    """Load a `tools/list` document into `ToolRecord`s.

    Accepts the raw `{"tools": [...]}` shape, the fixture shape that additionally carries a
    `"servers"` map, and a JSON-RPC envelope `{"result": {"tools": [...]}}`. When `servers` is
    present, siblings and `server_name` come from it; otherwise every tool is treated as
    belonging to one unnamed server and siblings are all other tools in the document.
    """
    raw_tools = _tools_array(doc)
    tools = [ToolDefinition(**t) for t in raw_tools]
    servers = _servers_index(doc)
    all_names = [t.name for t in tools]

    records: list[ToolRecord] = []
    for tool in tools:
        if servers is not None:
            server_name = servers.get(tool.name, "")
            siblings = [n for n in all_names if n != tool.name and servers.get(n) == server_name]
        else:
            server_name = ""
            siblings = [n for n in all_names if n != tool.name]
        records.append(
            ToolRecord(
                id=tool.name,
                server_name=server_name,
                tool=tool,
                sibling_tool_names=siblings,
            )
        )
    return records


def load_mcp_config(doc: dict[str, Any]) -> list[ConfigEntry]:
    """Parse-only: never executes a configured command or contacts a configured URL (SC-1)."""
    servers = doc.get("mcpServers", {})
    entries: list[ConfigEntry] = []
    for name, cfg in servers.items():
        command = cfg.get("command")
        url = cfg.get("url")
        transport: Transport = "stdio" if command is not None else "http"
        entries.append(ConfigEntry(name=name, transport=transport, command=command, url=url))
    return entries
