"""`action/action.yml`: static checks that don't need `act` or a real GitHub runner.

The brief's own gate -- "every `${{ inputs.* }}` reference resolves to a declared input" -- is
exactly the v2.0 bug this lane's brief calls out: `version` was interpolated without being
declared, so every run installed `sieve==` with an empty version.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ACTION_YML = Path(__file__).resolve().parents[2] / "action" / "action.yml"
INPUT_REF = re.compile(r"\$\{\{\s*inputs\.([A-Za-z0-9_-]+)\s*\}\}")


def _load() -> dict[str, object]:
    doc = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


def test_action_yml_parses_as_yaml() -> None:
    doc = _load()
    assert doc["runs"]["using"] == "composite"


def test_every_inputs_reference_is_declared() -> None:
    doc = _load()
    declared = set(doc.get("inputs", {}).keys())
    assert declared, "action.yml declares no inputs at all"
    text = ACTION_YML.read_text(encoding="utf-8")
    referenced = set(INPUT_REF.findall(text))
    assert referenced, "no ${{ inputs.* }} reference found -- the regex or the fixture is stale"
    undeclared = referenced - declared
    assert not undeclared, f"referenced but never declared: {sorted(undeclared)}"


def test_version_is_declared_with_a_default() -> None:
    # PRD §15.2: v2.0's bug, named explicitly -- `version` interpolated but never declared,
    # so every run installed `sieve==` with nothing after the `==`.
    doc = _load()
    assert "version" in doc["inputs"]
    assert doc["inputs"]["version"]["default"] == "0.1.0"


def test_required_inputs_are_exactly_config() -> None:
    doc = _load()
    required = {name for name, spec in doc["inputs"].items() if spec.get("required")}
    assert required == {"config"}


def test_every_run_step_declares_a_shell() -> None:
    doc = _load()
    for step in doc["runs"]["steps"]:
        if "run" in step:
            assert step.get("shell"), f"step {step.get('name')!r} has `run` but no `shell`"


def test_comment_step_only_runs_on_pull_request() -> None:
    doc = _load()
    comment_step = next(
        s for s in doc["runs"]["steps"] if s.get("name") == "Comment on the pull request"
    )
    assert "github.event_name == 'pull_request'" in comment_step["if"]


def test_self_scan_workflow_parses_and_uses_the_local_action() -> None:
    workflow_path = ACTION_YML.parents[1] / ".github" / "workflows" / "self-scan.yml"
    doc = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    steps = doc["jobs"]["sieve"]["steps"]
    action_step = next(s for s in steps if s.get("uses") == "./action")
    assert action_step["with"]["config"] == "fixtures/mcp-config/sample.mcp.json"
