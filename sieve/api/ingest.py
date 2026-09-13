"""Turn a `ScanCreate` body into tool records and scanner findings. Parsing only.

SC-1: nothing here starts a process or opens a socket. An `mcp_config` is read for the server
names and any statically declared tools; a `remote_url` is handed to the sandboxed worker,
which is the only code allowed to talk to an untrusted server.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sieve.types import ScanCreate, ScannerFinding, ScannerName, ToolDefinition, ToolRecord


class IngestError(ValueError):
    """The body named a source type but carried nothing usable for it."""


@dataclass
class Ingested:
    source_ref: str | None = None
    records: list[ToolRecord] = field(default_factory=list)
    findings_by_id: dict[str, list[ScannerFinding]] = field(default_factory=dict)


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


def _records(payload: Any, *, instructions: str | None = None) -> list[ToolRecord]:
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


def _scanner_findings(
    report: dict[str, Any], scanner: ScannerName, records: list[ToolRecord]
) -> dict[str, list[ScannerFinding]]:
    """The Phase-0 report shape: `{tool_name: {"findings": [...]}}`. L2's adapters own the rest."""
    by_name = {r.tool.name: r.id for r in records}
    version = str(report.get("scanner_version", "unknown"))
    out: dict[str, list[ScannerFinding]] = {}
    for name, entry in report.items():
        if not isinstance(entry, dict):
            continue
        raw_findings = entry.get("findings")
        if not isinstance(raw_findings, list):
            continue
        record_id = by_name.get(name, name)
        parsed = [
            ScannerFinding(
                scanner=scanner,
                scanner_version=version,
                rule_id=(str(f["rule_id"]) if f.get("rule_id") is not None else None),
                severity=f.get("severity"),
                message=(str(f["message"]) if f.get("message") is not None else None),
                raw=dict(f),
            )
            for f in raw_findings
            if isinstance(f, dict)
        ]
        if parsed:
            out[record_id] = parsed
    return out


def ingest(req: ScanCreate) -> Ingested:
    if req.source_type == "remote_url":
        if not req.remote_url:
            raise IngestError("remote_url is required for source_type 'remote_url'")
        return Ingested(source_ref=req.remote_url)

    records: list[ToolRecord] = []
    source_ref: str | None = None

    if req.source_type == "mcp_config":
        if not req.mcp_config:
            raise IngestError("mcp_config is required for source_type 'mcp_config'")
        servers = req.mcp_config.get("mcpServers")
        names = sorted(servers) if isinstance(servers, dict) else []
        source_ref = ",".join(names)
        # Parse only: a config names servers, it does not describe their tools. Anything the
        # config declares statically is triaged; enumerating the rest needs the sandbox.
        for name in names:
            entry = servers[name] if isinstance(servers, dict) else None
            if isinstance(entry, dict) and isinstance(entry.get("tools"), list):
                records.extend(_records({"servers": {name: []}, "tools": entry["tools"]}))
    elif req.tools_json is not None:
        records = _records(req.tools_json)
    elif req.source_type == "tools_json":
        raise IngestError("tools_json is required for source_type 'tools_json'")

    findings_by_id: dict[str, list[ScannerFinding]] = {}
    if req.scanner_report is not None:
        records = records or _records(req.scanner_report)
        findings_by_id = _scanner_findings(
            req.scanner_report, req.scanner or "static_json", records
        )
    elif req.source_type == "scanner_report":
        raise IngestError("scanner_report is required for source_type 'scanner_report'")

    return Ingested(source_ref=source_ref, records=records, findings_by_id=findings_by_id)
