"""`action/action.yml`: static checks that don't need `act` or a real GitHub runner, plus
subprocess execution of the install step's own bash logic against a fake `uv`.

The brief's own gate -- "every `${{ inputs.* }}` reference resolves to a declared input" -- is
exactly the v2.0 bug this lane's brief calls out: `version` was interpolated without being
declared, so every run installed `sieve==` with an empty version.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ACTION_YML = Path(__file__).resolve().parents[2] / "action" / "action.yml"
SELF_SCAN_YML = ACTION_YML.parents[1] / ".github" / "workflows" / "self-scan.yml"
INPUT_REF = re.compile(r"\$\{\{\s*inputs\.([A-Za-z0-9_-]+)\s*\}\}")
STEP_OUTPUT_REF = re.compile(r"\$\{\{\s*steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)\s*\}\}")


def _posix_bash() -> str:
    """A POSIX `bash`, not Windows' `System32\\bash.exe` (the WSL launcher).

    `bash` is on PATH three times on this box: Git Bash, WSL's launcher, and a Windows Apps
    shim, and `System32` resolves first for a plain `subprocess.run(["bash", ...])`. WSL runs
    in its own namespace and never sees an `env=` dict passed from the Windows side at all --
    silently, no error, just a script that behaves as if every variable were unset. Git Bash at
    its well-known install path is what actually runs the script under test.
    """
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if git_bash.is_file():
        return str(git_bash)
    return shutil.which("bash") or "bash"  # CI runners have exactly one, real, POSIX bash


def _load() -> dict[str, object]:
    doc = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


def _step(doc: dict[str, object], name: str) -> dict[str, object]:
    steps = doc["runs"]["steps"]  # type: ignore[index]
    return next(s for s in steps if s.get("name") == name)


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


def test_every_steps_output_reference_resolves_to_a_declared_step_and_output() -> None:
    doc = _load()
    steps = doc["runs"]["steps"]
    ids = {s["id"]: s for s in steps if "id" in s}
    text = ACTION_YML.read_text(encoding="utf-8")
    refs = STEP_OUTPUT_REF.findall(text)
    assert refs, "no ${{ steps.*.outputs.* }} reference found -- the fixture may be stale"
    for step_id, output_name in refs:
        assert step_id in ids, f"referenced step id {step_id!r} is not declared"
        script = ids[step_id].get("run", "")
        assert re.search(rf"{re.escape(output_name)}=", script), (
            f"step {step_id!r} never writes an output named {output_name!r} to $GITHUB_OUTPUT"
        )


def test_sieve_version_is_declared_with_a_default() -> None:
    # PRD §15.2: v2.0's bug, named explicitly -- `version` interpolated but never declared,
    # so every run installed `sieve==` with nothing after the `==`.
    doc = _load()
    assert "sieve-version" in doc["inputs"]
    assert doc["inputs"]["sieve-version"]["default"] == "0.1.0"


def test_required_inputs_are_exactly_config() -> None:
    doc = _load()
    required = {name for name, spec in doc["inputs"].items() if spec.get("required")}
    assert required == {"config"}


def test_every_run_step_declares_a_shell() -> None:
    doc = _load()
    for step in doc["runs"]["steps"]:
        if "run" in step:
            assert step.get("shell"), f"step {step.get('name')!r} has `run` but no `shell`"


def test_judge_flag_is_computed_once_and_reused() -> None:
    """Fix round 1, Important 3: no duplicated JUDGE_FLAG block across the two triage steps."""
    doc = _load()
    triage_steps = [s for s in doc["runs"]["steps"] if str(s.get("name", "")).startswith("Triage")]
    assert len(triage_steps) == 2
    for step in triage_steps:
        assert "JUDGE_INPUT" not in step["run"]  # resolved once, upstream, not duplicated here
        # ...and reused from there. Through `env:` since fix round 2's C1: no `run:` body on this
        # action interpolates an expression any more, step outputs included.
        assert step["env"]["JUDGE_FLAG"] == "${{ steps.judge-flag.outputs.flag }}"
        assert "$JUDGE_FLAG" in str(step["run"])
    resolve_step = _step(doc, "Resolve judge flag")
    assert resolve_step["env"]["JUDGE_INPUT"] == "${{ inputs.judge }}"


def test_comment_step_only_runs_on_pull_request() -> None:
    doc = _load()
    comment_step = _step(doc, "Comment on the pull request")
    assert "github.event_name == 'pull_request'" in comment_step["if"]


def test_no_run_body_interpolates_a_github_expression() -> None:
    """Fix round 2, C1: `${{ … }}` is substituted into the script *text* before bash parses it.

    The surrounding double quotes do not help -- `$( … )` and backticks run inside them -- so a
    caller who wires an input to event-derived data gets command execution in their own runner.
    Every value reaches a `run:` body through `env:` instead, which bash expands as data.
    """
    doc = _load()
    offenders = [
        str(step.get("name"))
        for step in doc["runs"]["steps"]
        if "run" in step and "${{" in str(step["run"])
    ]
    assert not offenders, f"steps interpolating an expression into a `run:` body: {offenders}"


def test_tools_input_is_declared_optional_and_reaches_both_triage_steps() -> None:
    """Fix round 2, M5: the realistic CI usage is a committed `tools/list` dump, because SC-1
    forbids spawning the configured server and a config alone carries no tool definitions."""
    doc = _load()
    assert "tools" in doc["inputs"], "action.yml declares no `tools` input"
    assert not doc["inputs"]["tools"].get("required")
    assert doc["inputs"]["tools"]["default"] == ""
    triage_steps = [s for s in doc["runs"]["steps"] if str(s.get("name", "")).startswith("Triage")]
    assert len(triage_steps) == 2
    for step in triage_steps:
        assert step["env"]["TOOLS"] == "${{ inputs.tools }}"
        assert "$TOOLS" in str(step["run"])


def test_the_action_says_out_loud_that_a_config_only_run_reports_zero_tools() -> None:
    """Fix round 2, M5: PRD §15.2's worked example reads as though a config produces findings."""
    doc = _load()
    prose = f"{doc['description']} {doc['inputs']['config']['description']}"
    assert "reports zero tools" in prose, prose
    readme = (ACTION_YML.parents[1] / "README.md").read_text(encoding="utf-8")
    assert "reports zero tools" in readme


def test_self_scan_gates_the_sarif_upload_and_the_comment_on_a_non_fork_pr() -> None:
    """Fix round 2, M4: a fork PR gets a read-only token, so both writes would fail.

    Gated rather than moved to `pull_request_target`, which would check out and run the fork's
    own code with a writable token -- the very thing C1 is about.
    """
    doc = yaml.safe_load(SELF_SCAN_YML.read_text(encoding="utf-8"))
    action_step = next(s for s in doc["jobs"]["sieve"]["steps"] if s.get("uses") == "./action")
    gate = "${{ github.event.pull_request.head.repo.fork == false }}"
    assert action_step["with"]["upload-sarif"] == gate
    assert action_step["with"]["comment"] == gate


def test_self_scan_workflow_parses_and_uses_the_local_action() -> None:
    doc = yaml.safe_load(SELF_SCAN_YML.read_text(encoding="utf-8"))
    steps = doc["jobs"]["sieve"]["steps"]
    action_step = next(s for s in steps if s.get("uses") == "./action")
    assert action_step["with"]["config"] == "fixtures/mcp-config/sample.mcp.json"


def test_self_scan_uses_local_so_it_passes_before_a_pypi_release() -> None:
    doc = yaml.safe_load(SELF_SCAN_YML.read_text(encoding="utf-8"))
    steps = doc["jobs"]["sieve"]["steps"]
    action_step = next(s for s in steps if s.get("uses") == "./action")
    assert action_step["with"]["sieve-version"] == "local"
    # `local` needs the repo already checked out.
    checkout_index = next(i for i, s in enumerate(steps) if s.get("uses") == "actions/checkout@v4")
    action_index = steps.index(action_step)
    assert checkout_index < action_index


# --- the install step's own branching logic, run for real against a fake `uv` -----------------


def _run_install_step(sieve_version_input: str) -> str:
    """Executes the real "Install sieve" step's script with `uv` stubbed to log its argv."""
    doc = _load()
    script = _step(doc, "Install sieve")["run"]
    wrapper = (
        'log="$(mktemp)"\n'
        'uv() { printf "%s" "$*" > "$log"; }\n'
        "export -f uv\n"
        f"{script}\n"
        'cat "$log"\n'
    )
    # Windows carries per-drive pseudo env vars like "=C:" in os.environ; Git Bash chokes on an
    # env entry whose name starts with "=", so those are filtered out here.
    env = {k: v for k, v in os.environ.items() if not k.startswith("=")}
    env["SIEVE_VERSION_INPUT"] = sieve_version_input
    result = subprocess.run(  # noqa: S603 - a fixed argv, the script under test, no shell
        [_posix_bash(), "-c", wrapper], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize(
    ("sieve_version_input", "expected_uv_args"),
    [
        ("0.1.0", "tool install sieve==0.1.0"),
        ("1.2.3", "tool install sieve==1.2.3"),
        ("local", "tool install --from . sieve"),
        ("git+https://github.com/o/r@main", "tool install git+https://github.com/o/r@main"),
        (
            "git+https://github.com/o/r@1fadefa67b2",
            "tool install git+https://github.com/o/r@1fadefa67b2",
        ),
    ],
)
def test_install_step_dispatches_on_sieve_version(
    sieve_version_input: str, expected_uv_args: str
) -> None:
    assert _run_install_step(sieve_version_input) == expected_uv_args
