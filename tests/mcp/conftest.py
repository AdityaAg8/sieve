from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sieve.pipeline import PipelineDeps
from sieve.protocols import FakeClassifier, FakeJudge

SAMPLE_TOOLS = Path("fixtures/tools/sample-27.json")


@pytest.fixture
def deps() -> PipelineDeps:
    """The two shared fakes through the real pipeline -- never a mocked `triage`."""
    return PipelineDeps(classifier=FakeClassifier(), judge=FakeJudge())


@pytest.fixture(scope="session")
def sample_tools() -> list[dict[str, Any]]:
    doc: dict[str, Any] = json.loads(SAMPLE_TOOLS.read_text(encoding="utf-8"))
    tools: list[dict[str, Any]] = doc["tools"]
    return tools


@pytest.fixture(scope="session")
def write_file_tool(sample_tools: list[dict[str, Any]]) -> dict[str, Any]:
    """The fixture's poisoned tool: its `content` description asks for `~/.ssh/id_rsa`."""
    return next(t for t in sample_tools if t["name"] == "write_file")
