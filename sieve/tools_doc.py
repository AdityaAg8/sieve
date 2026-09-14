"""The two static JSON shapes Sieve ingests, parsed once for every surface (PRD §13.3).

A `tools/list` payload and an `mcp.json` config are read by both the API (`sieve/api/ingest.py`)
and the CLI (`sieve/cli/ingest.py`). They used to be read by only one of them: the API lifted a
`tools` array declared inside an `mcp_config` entry and the CLI discarded it, so one config
ingested two ways gave two different answers. The helpers live here, in a module neither surface
owns, so that divergence cannot come back.

Parse-only, like everything on this path: SC-1 forbids spawning the configured server, and a
config that declares no tools therefore yields no `ToolRecord`s at all. What a config *does*
declare statically is triaged; enumerating the rest needs the sandboxed worker.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sieve.types import ToolDefinition, ToolRecord


def _tool_objects(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [t for t in payload if isinstance(t, dict)]
    if isinstance(payload, dict):
        tools = payload.get("tools")
        if isinstance(tools, list):
            return [t for t in tools if isinstance(t, dict)]
    return []


def _servers(payload: Any) -> dict[str, list[str]]:
    if isinstance(payload, dict):
        servers = payload.get("servers")
        if isinstance(servers, dict):
            return {
                str(name): [str(t) for t in tools]
                for name, tools in servers.items()
                if isinstance(tools, list)
            }
    return {}


def records_from_payload(payload: Any, *, instructions: str | None = None) -> list[ToolRecord]:
    """`ToolRecord`s from a `tools/list`-shaped payload, ids namespaced by server name."""
    servers = _servers(payload)
    owner = {tool: server for server, tools in servers.items() for tool in tools}
    records: list[ToolRecord] = []
    for raw in _tool_objects(payload):
        tool = ToolDefinition.model_validate(raw)
        server = owner.get(tool.name, str(raw.get("server_name") or "unknown"))
        siblings = [t for t in servers.get(server, []) if t != tool.name]
        records.append(
            ToolRecord(
                id=f"{server}/{tool.name}",
                server_name=server,
                tool=tool,
                sibling_tool_names=siblings,
                server_instructions=instructions,
            )
        )
    return records


def server_names(config: Mapping[str, Any]) -> list[str]:
    """The servers an `mcp.json` names, sorted. Empty for a config with no `mcpServers` map."""
    servers = config.get("mcpServers")
    return sorted(servers) if isinstance(servers, dict) else []


def records_from_mcp_config(config: Mapping[str, Any]) -> list[ToolRecord]:
    """Every tool an `mcp.json` declares statically, in sorted server order.

    A config names servers; it does not usually describe their tools, and SC-1 forbids starting
    one to ask. An entry that carries its own `tools` array is the exception, and it is the only
    thing this returns -- a config without one yields nothing, which is not a clean bill of
    health but an empty scan, and both the Action's description and the README say so.
    """
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        return []
    records: list[ToolRecord] = []
    for name in server_names(config):
        entry = servers[name]
        if isinstance(entry, dict) and isinstance(entry.get("tools"), list):
            records.extend(records_from_payload({"servers": {name: []}, "tools": entry["tools"]}))
    return records


__all__ = ["records_from_mcp_config", "records_from_payload", "server_names"]
