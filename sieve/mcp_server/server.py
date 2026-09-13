"""The three Sieve tools, built on the SDK's `MCPServer` (`mcp` 2.x, formerly `FastMCP`).

The tool descriptions below are plain and descriptive on purpose: they are the first thing a
model reads, they are exactly the surface Sieve exists to judge, and `tests/test_self_scan.py`
triages them. They are written honestly first and checked second -- the gate is never a target
to write toward.

Input and output schemas are generated from the Pydantic models in `sieve/types.py`, so the
wire contract of a verdict is the same object the API and the CLI return. Argument validation
is the SDK's: a submitted "tool" that is not a `ToolDefinition` comes back as `isError: true`
with the offending field names, and never reaches the pipeline.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any
from urllib.parse import quote

import httpx2
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ValidationError

from sieve import __version__
from sieve.api.deps import get_classifier, get_judge
from sieve.config import settings
from sieve.pipeline import PipelineDeps, triage, triage_many
from sieve.types import EvidenceSpan, Finding, ToolDefinition, ToolRecord, TriageResult

log = logging.getLogger("sieve.mcp_server")

SERVER_NAME = "sieve"

#: Sent on `initialize`. Like the tool descriptions, plain prose that says what the server
#: does and what it does not do.
INSTRUCTIONS = (
    "Sieve triages MCP tool definitions. Send it a single tool definition or a whole "
    "tools/list response and it returns a verdict for each tool with the evidence behind it. "
    "It reads definitions as text; it does not start, connect to, or install any MCP server."
)

TRIAGE_TOOL_DEFINITION_DESCRIPTION = (
    "Classifies one MCP tool definition as confirmed, needs review, or dismissed, with evidence."
)
SCAN_TOOLS_LIST_DESCRIPTION = (
    "Classifies every tool in a tools/list response and returns per-tool verdicts and counts."
)
EXPLAIN_VERDICT_DESCRIPTION = "Returns the evidence and reasoning stored for a previous finding id."

#: How `explain_verdict` reaches a Sieve API: `(api_url, finding_id) -> finding document`.
FindingFetcher = Callable[[str, str], Awaitable[Mapping[str, Any]]]


class ScanCounts(BaseModel):
    """The verdict counts for one `scan_tools_list` call."""

    tool_count: int
    confirmed: int
    needs_review: int
    dismissed: int


class ScanToolsListResult(BaseModel):
    summary: ScanCounts
    results: list[TriageResult]


class VerdictExplanation(BaseModel):
    """The stored `TriageResult` narrowed to what a caller asking "why?" needs."""

    evidence_spans: list[EvidenceSpan]
    reasoning: str | None
    remediation: str | None
    owasp: list[str]


def runtime_deps() -> PipelineDeps:
    """The classifier and judge this process serves with, from the shared factories."""
    return PipelineDeps(
        classifier=get_classifier(),
        judge=get_judge(),
        judge_enabled=settings.judge_enabled,
    )


async def _read(client: httpx2.AsyncClient, url: str, finding_id: str) -> Mapping[str, Any]:
    try:
        response = await client.get(url)
    except httpx2.HTTPError as exc:
        raise ToolError(f"could not reach the Sieve API at {url}: {exc}") from exc
    if response.status_code == 404:
        raise ToolError(f"that Sieve API has no finding {finding_id!r}.")
    if response.status_code >= 400:
        raise ToolError(
            f"the Sieve API answered {response.status_code} for finding {finding_id!r}."
        )
    body: Mapping[str, Any] = response.json()
    return body


async def fetch_finding(
    api_url: str, finding_id: str, *, client: httpx2.AsyncClient | None = None
) -> Mapping[str, Any]:
    """`GET {api_url}/api/v1/findings/{finding_id}`.

    `client` is a seam for tests and for a caller that already owns a connection pool; with it
    unset the call owns a client for its own lifetime.
    """
    url = f"{api_url.rstrip('/')}/api/v1/findings/{quote(finding_id, safe='')}"
    if client is not None:
        return await _read(client, url, finding_id)
    async with httpx2.AsyncClient(timeout=float(settings.remote_timeout_seconds)) as owned:
        return await _read(owned, url, finding_id)


def _record(tool: ToolDefinition, sibling_tool_names: Sequence[str]) -> ToolRecord:
    """A submitted definition, with no provenance claimed for it beyond "submitted"."""
    return ToolRecord(
        id=tool.name,
        server_name="submitted",
        tool=tool,
        sibling_tool_names=list(sibling_tool_names),
    )


def _counts(results: Sequence[TriageResult]) -> ScanCounts:
    verdicts = [r.verdict for r in results]
    return ScanCounts(
        tool_count=len(results),
        confirmed=verdicts.count("confirmed"),
        needs_review=verdicts.count("needs_review"),
        dismissed=verdicts.count("dismissed"),
    )


def build_server(
    *,
    deps: PipelineDeps | None = None,
    api_url: str | None = None,
    fetch_finding: FindingFetcher = fetch_finding,
    max_tools: int | None = None,
) -> MCPServer:
    """Build the Sieve MCP server. `deps` defaults to the process's real classifier and judge."""
    pipeline_deps = deps if deps is not None else runtime_deps()
    cap = max_tools if max_tools is not None else settings.max_tools_per_scan
    server: MCPServer = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, version=__version__)

    @server.tool(name="triage_tool_definition", description=TRIAGE_TOOL_DEFINITION_DESCRIPTION)
    def triage_tool_definition(
        tool: ToolDefinition,
        sibling_tool_names: list[str] = [],  # noqa: B006 (the SDK copies it into a model field)
    ) -> TriageResult:
        result = triage(_record(tool, sibling_tool_names), [], pipeline_deps)
        # Counts and verdicts only: a tool description is untrusted text and never logged
        # above DEBUG (SC-5).
        log.info("triage_tool_definition: %s at %s", result.verdict, result.stage)
        return result

    @server.tool(name="scan_tools_list", description=SCAN_TOOLS_LIST_DESCRIPTION)
    def scan_tools_list(tools: list[ToolDefinition]) -> ScanToolsListResult:
        if len(tools) > cap:
            raise ToolError(f"{len(tools)} tools is over the {cap}-tool limit for one scan.")
        names = [t.name for t in tools]
        records = [
            _record(tool, [n for i, n in enumerate(names) if i != index])
            for index, tool in enumerate(tools)
        ]
        results = triage_many(
            records, {}, pipeline_deps, judge_budget=settings.judge_max_calls_per_scan
        )
        summary = _counts(results)
        log.info(
            "scan_tools_list: %d tools, %d confirmed, %d needs_review, %d dismissed",
            summary.tool_count,
            summary.confirmed,
            summary.needs_review,
            summary.dismissed,
        )
        return ScanToolsListResult(summary=summary, results=results)

    @server.tool(name="explain_verdict", description=EXPLAIN_VERDICT_DESCRIPTION)
    async def explain_verdict(finding_id: str) -> VerdictExplanation:
        if api_url is None:
            raise ToolError(
                "explain_verdict reads stored findings from a Sieve API, and this server was "
                "started without one. Restart it with --api-url http://host:port to enable it."
            )
        payload = await fetch_finding(api_url, finding_id)
        try:
            finding = Finding.model_validate(payload)
        except ValidationError as exc:
            raise ToolError(
                f"the Sieve API returned a finding Sieve could not read "
                f"({exc.error_count()} field errors)."
            ) from exc
        stored = finding.result
        return VerdictExplanation(
            evidence_spans=stored.evidence_spans,
            reasoning=stored.reasoning,
            remediation=stored.remediation,
            owasp=stored.owasp,
        )

    return server
