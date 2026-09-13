"""Sieve triages Sieve (PRD §16): the gate that keeps our own tool descriptions honest.

A tool description is the attack surface Sieve exists to judge, so Sieve's own three had
better survive its own classifier. The descriptions are written to be plain and descriptive
first; this test is the check on that, not a target to tune them against. If it ever fails,
the fix is a more honest description -- never a change to the classifier or the threshold.
"""

from __future__ import annotations

from mcp.client import Client

from sieve.mcp_server.server import build_server
from sieve.pipeline import PipelineDeps, triage
from sieve.protocols import FakeClassifier, FakeJudge
from sieve.types import ToolDefinition, ToolRecord


async def test_sieve_dismisses_its_own_tool_definitions() -> None:
    server = build_server()
    async with Client(server) as client:
        listed = await client.list_tools()

    assert listed.tools, "the server advertised no tools to scan"
    names = [t.name for t in listed.tools]
    deps = PipelineDeps(classifier=FakeClassifier(), judge=FakeJudge())

    for tool in listed.tools:
        wire = tool.model_dump(by_alias=True, exclude_none=True)
        record = ToolRecord(
            id=tool.name,
            server_name="sieve",
            tool=ToolDefinition.model_validate(wire),
            sibling_tool_names=[n for n in names if n != tool.name],
            server_instructions=server.instructions,
        )
        result = triage(record, [], deps)

        assert result.verdict == "dismissed", f"{tool.name}: {result.verdict}"
        assert result.predicted_class == "benign", f"{tool.name}: {result.predicted_class}"
        assert result.stage == "classifier", f"{tool.name}: {result.stage}"
