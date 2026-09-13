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
from collections.abc import Sequence
from contextlib import AsyncExitStack
from typing import NamedTuple
from urllib.parse import quote

import httpx2
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field, ValidationError

from sieve import __version__
from sieve.api.deps import get_classifier, get_judge
from sieve.config import settings
from sieve.pipeline import PipelineDeps, triage, triage_many
from sieve.types import (
    EvidenceSpan,
    Finding,
    FindingPage,
    Stage,
    SubLabel,
    ToolDefinition,
    ToolRecord,
    TriageResult,
    Verdict,
)

log = logging.getLogger("sieve.mcp_server")

SERVER_NAME = "sieve"

#: Sent on `initialize`. Like the tool descriptions, plain prose that says what the server
#: does and what it does not do.
INSTRUCTIONS = (
    "Sieve triages MCP tool definitions. It classifies a single tool definition, or every "
    "tool in a tools/list response, and returns a verdict for each one with the evidence "
    "behind it. It reads definitions as text; it does not start, connect to, or install any "
    "MCP server."
)

TRIAGE_TOOL_DEFINITION_DESCRIPTION = (
    "Classifies one MCP tool definition as confirmed, needs review, or dismissed, with evidence."
)
SCAN_TOOLS_LIST_DESCRIPTION = (
    "Classifies every tool in a tools/list response and returns per-tool verdicts and counts."
)
EXPLAIN_VERDICT_DESCRIPTION = (
    "Returns the evidence and reasoning stored for a previous finding id. It needs the id of "
    "the scan that finding belongs to, and a Sieve API named by this server's --api-url."
)

#: A stop on the `next_cursor` walk, so a Sieve API that paged forever could not hang a client.
MAX_FINDING_PAGES = 200


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
    """The stored `TriageResult` narrowed to what a caller asking "why?" needs.

    `found = false` with a `message` is the answer for an id the API does not carry: a caller
    asking about a finding that is not there has a fact to learn, not a failure to handle.
    """

    found: bool
    scan_id: str
    finding_id: str
    verdict: Verdict | None = None
    stage: Stage | None = None
    sub_labels: list[SubLabel] = Field(default_factory=list)
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    reasoning: str | None = None
    remediation: str | None = None
    owasp: list[str] = Field(default_factory=list)
    message: str | None = None


class Lookup(NamedTuple):
    """A finding, or the reason there is none. `message` is empty exactly when one was found."""

    finding: Finding | None
    message: str


def runtime_deps() -> PipelineDeps:
    """The classifier and judge this process serves with, from the shared factories."""
    return PipelineDeps(
        classifier=get_classifier(),
        judge=get_judge(),
        judge_enabled=settings.judge_enabled,
    )


async def fetch_findings_page(
    client: httpx2.AsyncClient, api_url: str, scan_id: str, cursor: str | None
) -> FindingPage | None:
    """One page of `GET {api_url}/api/v1/scans/{scan_id}/findings`. `None` means no such scan.

    That route is the only one in `schemas/openapi.json` that serves a stored verdict, and it
    is paged, so a stored explanation is always a cursor walk rather than a single GET.
    """
    url = f"{api_url.rstrip('/')}/api/v1/scans/{quote(scan_id, safe='')}/findings"
    params = {"cursor": cursor} if cursor is not None else None
    try:
        response = await client.get(url, params=params)
    except httpx2.HTTPError as exc:
        raise ToolError(f"could not reach the Sieve API at {url}: {exc}") from exc
    if response.status_code == 404:
        return None
    if response.is_redirect:
        # L5 treats a 3xx as fatal on its own outbound fetches; the same rule applies here. A
        # findings page that moves is a misconfiguration worth naming, not one to chase.
        where = response.headers.get("location", "(no Location header)")
        raise ToolError(
            f"the Sieve API redirected {url} to {where!r}; Sieve does not follow redirects "
            f"when reading a findings page."
        )
    if response.status_code >= 400:
        raise ToolError(f"the Sieve API answered {response.status_code} for that scan's findings.")
    try:
        return FindingPage.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        raise ToolError("the Sieve API returned a findings page Sieve could not read.") from exc


async def find_finding(
    client: httpx2.AsyncClient, api_url: str, scan_id: str, finding_id: str
) -> Lookup:
    """Walk `next_cursor` until the id turns up or the pages run out."""
    cursor: str | None = None
    seen: set[str] = set()
    for _ in range(MAX_FINDING_PAGES):
        page = await fetch_findings_page(client, api_url, scan_id, cursor)
        if page is None:
            # 404 is equally "wrong scan id" and "right id, wrong API": say both, because a
            # caller told only the first will go looking in the wrong place.
            return Lookup(
                None,
                f"no scan {scan_id!r} at {api_url} -- either that scan id is not this API's, "
                f"or --api-url does not point at the Sieve that holds it.",
            )
        for finding in page.items:
            if finding.id == finding_id:
                return Lookup(finding, "")
        cursor = page.next_cursor
        if cursor is None:
            return Lookup(None, f"scan {scan_id!r} has no finding {finding_id!r}.")
        if cursor in seen:
            # A cursor that comes back round would otherwise be an infinite walk.
            return Lookup(
                None,
                f"the findings cursor for scan {scan_id!r} repeated before {finding_id!r} "
                f"was seen.",
            )
        seen.add(cursor)
    return Lookup(
        None,
        f"read {MAX_FINDING_PAGES} pages of scan {scan_id!r} without seeing {finding_id!r}.",
    )


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
    http_client: httpx2.AsyncClient | None = None,
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
    async def explain_verdict(scan_id: str, finding_id: str) -> VerdictExplanation:
        if api_url is None:
            raise ToolError(
                "explain_verdict reads stored findings from a Sieve API, and this server was "
                "started without one. Restart it with --api-url http://host:port to enable it."
            )
        async with AsyncExitStack() as stack:
            # An injected client belongs to the caller and is left open; one opened here is
            # closed by the stack. Either way there is one call to `find_finding`.
            client = http_client
            if client is None:
                timeout = float(settings.remote_timeout_seconds)
                client = await stack.enter_async_context(httpx2.AsyncClient(timeout=timeout))
            lookup = await find_finding(client, api_url, scan_id, finding_id)

        if lookup.finding is None:
            log.info("explain_verdict: not found")
            return VerdictExplanation(
                found=False, scan_id=scan_id, finding_id=finding_id, message=lookup.message
            )
        stored = lookup.finding.result
        log.info("explain_verdict: %s at %s", stored.verdict, stored.stage)
        return VerdictExplanation(
            found=True,
            scan_id=scan_id,
            finding_id=finding_id,
            verdict=stored.verdict,
            stage=stored.stage,
            sub_labels=list(stored.sub_labels),
            evidence_spans=list(stored.evidence_spans),
            reasoning=stored.reasoning,
            remediation=stored.remediation,
            owasp=list(stored.owasp),
        )

    return server
