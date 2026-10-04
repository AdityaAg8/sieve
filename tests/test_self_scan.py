"""Sieve triages Sieve (PRD §16): the gate that keeps our own tool descriptions honest.

A tool description is the attack surface Sieve exists to judge, so Sieve's own three had
better survive its own classifier. The descriptions are written to be plain and descriptive
first; this test is the check on that, not a target to tune them against. If it ever fails,
the fix is a more honest description -- never a change to the classifier or the threshold.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp.client import Client

from sieve.mcp_server.server import build_server
from sieve.pipeline import PipelineDeps, triage
from sieve.protocols import FakeClassifier, FakeJudge
from sieve.types import ToolDefinition, ToolRecord

REPO = Path(__file__).resolve().parents[1]


async def _own_records(deps: PipelineDeps) -> list[ToolRecord]:
    server = build_server(deps=deps)
    async with Client(server) as client:
        listed = await client.list_tools()
    assert listed.tools, "the server advertised no tools to scan"
    names = [t.name for t in listed.tools]
    return [
        ToolRecord(
            id=tool.name,
            server_name="sieve",
            tool=ToolDefinition.model_validate(tool.model_dump(by_alias=True, exclude_none=True)),
            sibling_tool_names=[n for n in names if n != tool.name],
            server_instructions=server.instructions,
        )
        for tool in listed.tools
    ]


def _published_artefact() -> Path | None:
    """The model `selection.json` published, when it exists on this machine (never in CI)."""
    selection = REPO / "bench" / "results" / "selection.json"
    if not selection.exists():
        return None
    directory = REPO / json.loads(selection.read_text(encoding="utf-8"))["published_dir"]
    return directory if (directory / "sieve_config.json").exists() else None


async def test_sieve_dismisses_its_own_tool_definitions() -> None:
    deps = PipelineDeps(classifier=FakeClassifier(), judge=FakeJudge())
    # The same fakes go into the server, so the gate never depends on `SIEVE_MODEL_ID`; the
    # tool list it advertises does not depend on them either way.
    for record in await _own_records(deps):
        result = triage(record, [], deps)
        tool = record.tool

        assert result.verdict == "dismissed", f"{tool.name}: {result.verdict}"
        assert result.predicted_class == "benign", f"{tool.name}: {result.predicted_class}"
        assert result.stage == "classifier", f"{tool.name}: {result.stage}"


async def test_the_trained_artefact_dismisses_sieves_own_tool_definitions() -> None:
    """PRD §16 against the published v1 model (training plan §13 step 15), at its own frozen
    thresholds: nothing of Sieve's may come back `confirmed` or `needs_review`."""
    artefact = _published_artefact()
    if artefact is None:
        pytest.skip("the published artefact is not on this machine (models/ is gitignored)")
    from sieve.model.infer import load_classifier

    deps = PipelineDeps(classifier=load_classifier(str(artefact)), judge=FakeJudge())
    for record in await _own_records(deps):
        result = triage(record, [], deps)
        assert result.verdict == "dismissed", f"{record.tool.name}: {result.verdict}"
