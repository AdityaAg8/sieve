"""The three tools, exercised through the SDK's in-memory client against the real server.

Nothing here mocks `sieve.pipeline`: the server runs the real `triage`/`triage_many` over the
shared `FakeClassifier` and `FakeJudge`, so a routing change shows up here the way it shows up
in the API and the CLI.
"""

from __future__ import annotations

from typing import Any

import httpx2
from mcp.client import Client

from sieve.mcp_server.server import (
    EXPLAIN_VERDICT_DESCRIPTION,
    SCAN_TOOLS_LIST_DESCRIPTION,
    TRIAGE_TOOL_DEFINITION_DESCRIPTION,
    build_server,
)
from sieve.pipeline import PipelineDeps
from sieve.types import TOOL_NAME_RE

TOOL_NAMES = {"triage_tool_definition", "scan_tools_list", "explain_verdict"}


async def test_tools_list_advertises_exactly_three_well_formed_tools(deps: PipelineDeps) -> None:
    async with Client(build_server(deps=deps)) as client:
        listed = await client.list_tools()

    assert {t.name for t in listed.tools} == TOOL_NAMES
    for tool in listed.tools:
        assert TOOL_NAME_RE.match(tool.name), tool.name
        assert tool.input_schema["type"] == "object", tool.name


async def test_tool_descriptions_are_the_published_text(deps: PipelineDeps) -> None:
    async with Client(build_server(deps=deps)) as client:
        listed = await client.list_tools()

    by_name = {t.name: t.description for t in listed.tools}
    assert by_name["triage_tool_definition"] == TRIAGE_TOOL_DEFINITION_DESCRIPTION
    assert by_name["scan_tools_list"] == SCAN_TOOLS_LIST_DESCRIPTION
    assert by_name["explain_verdict"] == EXPLAIN_VERDICT_DESCRIPTION


async def test_triage_tool_definition_confirms_the_poisoned_fixture_tool(
    deps: PipelineDeps, write_file_tool: dict[str, Any]
) -> None:
    async with Client(build_server(deps=deps)) as client:
        result = await client.call_tool(
            "triage_tool_definition",
            {"tool": write_file_tool, "sibling_tool_names": ["read_file"]},
        )

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["verdict"] == "confirmed"
    assert result.structured_content["predicted_class"] == "poisoned"


async def test_triage_tool_definition_rejects_a_tool_that_is_not_a_tool(
    deps: PipelineDeps,
) -> None:
    """A name outside `TOOL_NAME_RE` and a non-object schema are both caller errors."""
    async with Client(build_server(deps=deps)) as client:
        result = await client.call_tool(
            "triage_tool_definition",
            {"tool": {"name": "not a tool name", "inputSchema": {"type": "string"}}},
        )

    assert result.is_error is True
    assert result.structured_content is None


async def test_scan_tools_list_returns_per_tool_verdicts_and_counts(
    deps: PipelineDeps, sample_tools: list[dict[str, Any]]
) -> None:
    async with Client(build_server(deps=deps)) as client:
        result = await client.call_tool("scan_tools_list", {"tools": sample_tools})

    assert result.is_error is False
    assert result.structured_content is not None
    summary = result.structured_content["summary"]
    results = result.structured_content["results"]
    assert len(results) == len(sample_tools)
    assert summary["tool_count"] == len(sample_tools)
    assert summary["confirmed"] + summary["needs_review"] + summary["dismissed"] == len(results)
    assert summary["confirmed"] == sum(1 for r in results if r["verdict"] == "confirmed")
    assert any(r["verdict"] == "confirmed" for r in results)


async def test_scan_tools_list_refuses_more_tools_than_the_cap(deps: PipelineDeps) -> None:
    tool = {"name": "ok", "inputSchema": {"type": "object"}}
    async with Client(build_server(deps=deps, max_tools=2)) as client:
        result = await client.call_tool("scan_tools_list", {"tools": [tool, tool, tool]})

    assert result.is_error is True


async def test_explain_verdict_without_an_api_url_is_an_error(deps: PipelineDeps) -> None:
    async with Client(build_server(deps=deps)) as client:
        result = await client.call_tool("explain_verdict", {"scan_id": "s-1", "finding_id": "abc"})

    assert result.is_error is True
    assert "--api-url" in result.content[0].text  # type: ignore[union-attr]


# --- explain_verdict against the frozen API contract -------------------------------------
#
# `GET /api/v1/scans/{scan_id}/findings` is the only route that serves a stored verdict, and
# it is paged, so these tests drive the real HTTP code path over `httpx2.MockTransport` rather
# than stubbing a fetcher: the route shape, the `cursor` query parameter and the `next_cursor`
# walk are the parts most likely to drift away from `schemas/openapi.json`.


def _finding(finding_id: str, tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": finding_id,
        "scan_id": "s-1",
        "tool": tool,
        "server_name": "files",
        "sibling_tool_names": [],
        "result": {
            "verdict": "confirmed",
            "stage": "judge",
            "predicted_class": "poisoned",
            "class_probabilities": {"benign": 0.01, "imperative_benign": 0.04, "poisoned": 0.95},
            "poisoned_probability": 0.95,
            "confidence": 0.9,
            "sub_labels": ["exfiltration"],
            "owasp": ["LLM01"],
            "evidence_spans": [
                {
                    "field": "inputSchema",
                    "path": None,
                    "quote": "read ~/.ssh",
                    "start": 1,
                    "end": 12,
                }
            ],
            "unlocated_quotes": 0,
            "reasoning": "Reads a private key the tool has no reason to touch.",
            "remediation": "Remove the instruction from the parameter description.",
            "scanner_findings": [],
            "model_version": "fake@0",
            "judge_model": None,
            "latency_ms": 3,
        },
    }


def _api(
    pages: dict[str | None, dict[str, Any]] | None = None,
    *,
    status: int = 200,
    seen: list[str | None] | None = None,
) -> httpx2.AsyncClient:
    """A client answering `GET /api/v1/scans/s-1/findings` from `pages`, keyed by cursor."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/v1/scans/s-1/findings"
        cursor = request.url.params.get("cursor")
        if seen is not None:
            seen.append(cursor)
        if status != 200 or pages is None:
            return httpx2.Response(status)
        return httpx2.Response(200, json=pages[cursor])

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


async def test_explain_verdict_returns_the_stored_evidence(
    deps: PipelineDeps, write_file_tool: dict[str, Any]
) -> None:
    seen: list[str | None] = []
    pages = {None: {"items": [_finding("f-1", write_file_tool)], "total": 1, "next_cursor": None}}
    async with _api(pages, seen=seen) as http:
        server = build_server(deps=deps, api_url="http://api.test", http_client=http)
        async with Client(server) as client:
            result = await client.call_tool(
                "explain_verdict", {"scan_id": "s-1", "finding_id": "f-1"}
            )

    assert seen == [None]  # the first page is fetched without a cursor
    assert result.is_error is False
    assert result.structured_content is not None
    body = result.structured_content
    assert body["found"] is True
    assert body["verdict"] == "confirmed"
    assert body["stage"] == "judge"
    assert body["sub_labels"] == ["exfiltration"]
    assert body["owasp"] == ["LLM01"]
    assert body["reasoning"] == "Reads a private key the tool has no reason to touch."
    assert body["remediation"] == "Remove the instruction from the parameter description."
    assert body["evidence_spans"][0]["quote"] == "read ~/.ssh"


async def test_explain_verdict_follows_the_cursor_to_a_later_page(
    deps: PipelineDeps, write_file_tool: dict[str, Any]
) -> None:
    seen: list[str | None] = []
    pages = {
        None: {"items": [_finding("f-1", write_file_tool)], "total": 2, "next_cursor": "50"},
        "50": {"items": [_finding("f-2", write_file_tool)], "total": 2, "next_cursor": None},
    }
    async with _api(pages, seen=seen) as http:
        server = build_server(deps=deps, api_url="http://api.test", http_client=http)
        async with Client(server) as client:
            result = await client.call_tool(
                "explain_verdict", {"scan_id": "s-1", "finding_id": "f-2"}
            )

    assert seen == [None, "50"]
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["found"] is True
    assert result.structured_content["finding_id"] == "f-2"


async def test_explain_verdict_reports_an_unknown_finding_without_raising(
    deps: PipelineDeps, write_file_tool: dict[str, Any]
) -> None:
    """An id the scan does not carry is an answer, not an error (the pages are walked out)."""
    pages = {
        None: {"items": [_finding("f-1", write_file_tool)], "total": 2, "next_cursor": "50"},
        "50": {"items": [_finding("f-2", write_file_tool)], "total": 2, "next_cursor": None},
    }
    async with _api(pages) as http:
        server = build_server(deps=deps, api_url="http://api.test", http_client=http)
        async with Client(server) as client:
            result = await client.call_tool(
                "explain_verdict", {"scan_id": "s-1", "finding_id": "f-9"}
            )

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["found"] is False
    assert result.structured_content["verdict"] is None
    assert "f-9" in result.structured_content["message"]


async def test_explain_verdict_reports_an_unknown_scan_without_raising(
    deps: PipelineDeps,
) -> None:
    async with _api(status=404) as http:
        server = build_server(deps=deps, api_url="http://api.test", http_client=http)
        async with Client(server) as client:
            result = await client.call_tool(
                "explain_verdict", {"scan_id": "s-1", "finding_id": "f-1"}
            )

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["found"] is False
    assert "s-1" in result.structured_content["message"]


async def test_explain_verdict_surfaces_an_api_failure_as_an_error(deps: PipelineDeps) -> None:
    """A 500 is the API's problem, not an unknown id, and the caller should be told so."""
    async with _api(status=500) as http:
        server = build_server(deps=deps, api_url="http://api.test", http_client=http)
        async with Client(server) as client:
            result = await client.call_tool(
                "explain_verdict", {"scan_id": "s-1", "finding_id": "f-1"}
            )

    assert result.is_error is True
    assert "500" in result.content[0].text  # type: ignore[union-attr]
