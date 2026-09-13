"""The three tools, exercised through the SDK's in-memory client against the real server.

Nothing here mocks `sieve.pipeline`: the server runs the real `triage`/`triage_many` over the
shared `FakeClassifier` and `FakeJudge`, so a routing change shows up here the way it shows up
in the API and the CLI.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from mcp.client import Client
from mcp.server.mcpserver.exceptions import ToolError

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
        result = await client.call_tool("explain_verdict", {"finding_id": "abc"})

    assert result.is_error is True
    assert "--api-url" in result.content[0].text  # type: ignore[union-attr]


async def test_explain_verdict_returns_the_stored_evidence(
    deps: PipelineDeps, write_file_tool: dict[str, Any]
) -> None:
    stored = {
        "id": "f-1",
        "scan_id": "s-1",
        "tool": write_file_tool,
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
    seen: list[tuple[str, str]] = []

    async def fetch(api_url: str, finding_id: str) -> Mapping[str, Any]:
        seen.append((api_url, finding_id))
        return stored

    server = build_server(deps=deps, api_url="http://api.test", fetch_finding=fetch)
    async with Client(server) as client:
        result = await client.call_tool("explain_verdict", {"finding_id": "f-1"})

    assert seen == [("http://api.test", "f-1")]
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["owasp"] == ["LLM01"]
    assert result.structured_content["reasoning"] == stored["result"]["reasoning"]  # type: ignore[index]
    assert result.structured_content["evidence_spans"][0]["quote"] == "read ~/.ssh"


async def test_explain_verdict_reports_a_finding_the_api_does_not_have(
    deps: PipelineDeps,
) -> None:
    async def fetch(api_url: str, finding_id: str) -> Mapping[str, Any]:
        raise ToolError(f"no finding {finding_id!r} at {api_url}")

    server = build_server(deps=deps, api_url="http://api.test", fetch_finding=fetch)
    async with Client(server) as client:
        result = await client.call_tool("explain_verdict", {"finding_id": "nope"})

    assert result.is_error is True


@pytest.mark.parametrize("status", [404, 500])
async def test_fetch_finding_turns_an_http_failure_into_a_tool_error(status: int) -> None:
    import httpx2

    from sieve.mcp_server.server import fetch_finding

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/v1/findings/f-1"
        return httpx2.Response(status)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        with pytest.raises(ToolError):
            await fetch_finding("http://api.test", "f-1", client=http)
