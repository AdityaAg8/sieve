"""Turn a `ScanCreate` body into tool records and scanner findings. Parsing only.

SC-1: nothing here starts a process or opens a socket. An `mcp_config` is read for the server
names and any statically declared tools; a `remote_url` is handed to the sandboxed worker,
which is the only code allowed to talk to an untrusted server.

The two static shapes themselves are parsed by `sieve/tools_doc.py`, which the CLI imports too,
so one config ingested by either surface yields the same records (PRD §13.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sieve.redact import redact
from sieve.tools_doc import records_from_mcp_config, records_from_payload, server_names
from sieve.types import ScanCreate, ScannerFinding, ScannerName, ToolRecord


class IngestError(ValueError):
    """The body named a source type but carried nothing usable for it."""


@dataclass
class Ingested:
    source_ref: str | None = None
    records: list[ToolRecord] = field(default_factory=list)
    findings_by_id: dict[str, list[ScannerFinding]] = field(default_factory=dict)


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
        # `source_ref` is stored, returned by `GET /scans/{id}` and exported in SARIF, so it is
        # a writer like any other and gets SC-5's rule -- this was the only one in the codebase
        # that did not (fix round 2, I3). A URL can carry a credential in a query parameter
        # (`?api_key=…` matches `generic_assignment`), and while `ScanCreate` already refuses
        # `https://user:pass@host/` outright, refusing one shape is not redacting every shape.
        #
        # The sandbox later fetches what is stored, so a URL whose query carried a credential is
        # dialled without it and the server answers however it answers. That is the intended
        # trade: Sieve does not keep a credential in order to replay it, and a remote server that
        # needs one is out of scope for a triage tool that promises not to store secrets.
        redacted_url, _kinds = redact(req.remote_url)
        return Ingested(source_ref=redacted_url)

    records: list[ToolRecord] = []
    source_ref: str | None = None

    if req.source_type == "mcp_config":
        if not req.mcp_config:
            raise IngestError("mcp_config is required for source_type 'mcp_config'")
        source_ref = ",".join(server_names(req.mcp_config))
        records = records_from_mcp_config(req.mcp_config)
    elif req.tools_json is not None:
        records = records_from_payload(req.tools_json)
    elif req.source_type == "tools_json":
        raise IngestError("tools_json is required for source_type 'tools_json'")

    findings_by_id: dict[str, list[ScannerFinding]] = {}
    if req.scanner_report is not None:
        records = records or records_from_payload(req.scanner_report)
        findings_by_id = _scanner_findings(
            req.scanner_report, req.scanner or "static_json", records
        )
    elif req.source_type == "scanner_report":
        raise IngestError("scanner_report is required for source_type 'scanner_report'")

    return Ingested(source_ref=source_ref, records=records, findings_by_id=findings_by_id)
