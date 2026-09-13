"""`action/comment.py`: pure-function tests, no GitHub API calls (Task 7.3, Step 3)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_comment_module() -> ModuleType:
    """`action/` is not a Python package (it ships alongside `action.yml`, not under `sieve/`),
    so it is loaded by file path rather than imported by name."""
    path = Path(__file__).resolve().parents[2] / "action" / "comment.py"
    spec = importlib.util.spec_from_file_location("sieve_action_comment", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


comment = _load_comment_module()


def _sarif(results: list[dict[str, object]]) -> dict[str, object]:
    return {
        "version": "2.1.0",
        "runs": [{"tool": {"driver": {"name": "Sieve"}}, "results": results}],
    }


def _result(
    *, level: str, tool_name: str, rule_id: str, message: str, owasp: list[str]
) -> dict[str, object]:
    return {
        "ruleId": rule_id,
        "level": level,
        "message": {"text": message},
        "properties": {"tool_name": tool_name, "owasp": owasp},
    }


def test_only_warnings_produces_no_body() -> None:
    sarif = _sarif(
        [
            _result(
                level="warning",
                tool_name="update_note",
                rule_id="triage",
                message="Needs review.",
                owasp=[],
            ),
            _result(
                level="note", tool_name="click", rule_id="triage", message="Dismissed.", owasp=[]
            ),
        ]
    )
    assert comment.render_comment(sarif) is None


def test_two_errors_produce_a_body_with_two_rows_and_the_marker() -> None:
    sarif = _sarif(
        [
            _result(
                level="error",
                tool_name="export_csv",
                rule_id="exfiltration",
                message="Exports credentials via a compliance-logging pretext.",
                owasp=["MCP10"],
            ),
            _result(
                level="warning",
                tool_name="update_note",
                rule_id="triage",
                message="Needs review.",
                owasp=[],
            ),
            _result(
                level="error",
                tool_name="forward_mail",
                rule_id="instruction_override",
                message="BCCs an attacker-controlled address on every send.",
                owasp=["MCP06"],
            ),
        ]
    )
    body = comment.render_comment(sarif)
    assert body is not None
    assert body.startswith(comment.MARKER)
    assert body.count("| `") == 2  # exactly the two error rows, not the warning
    assert "export_csv" in body
    assert "forward_mail" in body
    assert "MCP10" in body
    assert "MCP06" in body
    assert "owasp.org" in body


def test_no_results_at_all_produces_no_body() -> None:
    assert comment.render_comment(_sarif([])) is None


def test_reasoning_with_a_pipe_and_newline_does_not_break_the_table() -> None:
    sarif = _sarif(
        [
            _result(
                level="error",
                tool_name="run_query",
                rule_id="command_execution",
                message="Runs `a | b`\nacross two lines.",
                owasp=[],
            )
        ]
    )
    body = comment.render_comment(sarif)
    assert body is not None
    # exactly one row: the header separator row plus this one row, no stray pipes splitting cells
    rows = [line for line in body.splitlines() if line.startswith("| `")]
    assert len(rows) == 1
    assert "\n" not in rows[0]


def test_find_existing_comment_matches_by_marker() -> None:
    comments = [
        {"id": 1, "body": "unrelated comment"},
        {"id": 2, "body": f"{comment.MARKER}\nold sieve comment"},
    ]
    assert comment.find_existing_comment(comments) == 2


def test_find_existing_comment_returns_none_when_absent() -> None:
    assert comment.find_existing_comment([{"id": 1, "body": "unrelated"}]) is None
