"""Shared scaffolding for the two judge self-defence suites (PRD §12.5, SC-7).

Both suites run the same measurement: build a judge, judge every case, count dismissals. Only
three things differ — the case file, the pytest marker, and whether the clean half is a gate
or a report. The holdout's seal is on the **data file**, not on the code: sharing this module
is what keeps the two suites provably the same measurement, which is the whole point of
having a holdout at all.

## Cassettes are judge-level JSONL, not HTTP

`vcrpy` and `respx` both patch `httpx`; the installed `anthropic` (1.5.0) is built on
`httpx2`, so neither sees a single request — verified: a VCR cassette records zero
interactions while the call goes to the live network. Rather than record at a layer that
cannot see the traffic, these suites record at the judge boundary using L4's own
`RecordingJudge` / `ReplayJudge` (PRD §12.4, the same mechanism `make bench` replays). The
cassette is one JSONL line per case: `{request_hash, model, usage, output}` — no tool text,
no headers, and therefore nothing to filter an API key out of.

HTTP-level behaviour (retries, status mapping, the budget guard, redaction on the wire) is
unit-tested separately in `tests/judge/test_client.py` against `httpx2.MockTransport`.

## Recording

One-shot, with a key, per suite:

    SIEVE_RERECORD=1 ANTHROPIC_API_KEY=... uv run pytest tests/test_judge_injection.py
    SIEVE_RERECORD=1 ANTHROPIC_API_KEY=... uv run pytest \
        tests/test_judge_injection_holdout.py -m holdout

Recording truncates the suite's cassette first, so a re-record replaces rather than grows it.
Commit the cassette; CI then runs the gate offline with no key. With neither a cassette nor a
key the whole suite skips with one reason — it never passes silently.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from sieve.judge.client import AnthropicJudge
from sieve.judge.record import RecordingJudge, ReplayJudge
from sieve.protocols import Judge, JudgeOutput
from sieve.types import ToolDefinition, ToolRecord

__all__ = [
    "CASSETTES",
    "FIXTURES",
    "ROOT",
    "cases_for",
    "judge_fixture",
    "record_for",
    "skip_reason",
    "verdict_for",
]

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "injection"
CASSETTES = Path(__file__).resolve().parents[1] / "cassettes" / "judge_injection"

#: The stage-1 score both suites judge at: the middle of the uncertain band.
P_STAGE1 = 0.5


def skip_reason(command: str) -> str:
    """One reason, naming the exact command that records this suite's cassette."""
    return (
        "no judge cassette recorded and no ANTHROPIC_API_KEY; "
        f"record once with `SIEVE_RERECORD=1 ANTHROPIC_API_KEY=... {command}`"
    )


def cases_for(name: str) -> list[dict]:
    """Load `fixtures/injection/<name>.jsonl`. Anchored to the repo, not to the cwd."""
    path = FIXTURES / f"{name}.jsonl"
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def record_for(case: dict) -> ToolRecord:
    return ToolRecord(
        id=case["id"],
        server_name="inj",
        tool=ToolDefinition.model_validate(case["tool"]),
        sibling_tool_names=case.get("sibling_tool_names", []),
    )


def _recording_requested() -> bool:
    return os.environ.get("SIEVE_RERECORD") == "1" and bool(os.environ.get("ANTHROPIC_API_KEY"))


def judge_fixture(cassette: Path, command: str) -> Iterator[Judge]:
    """Yield the judge for a suite, or skip the whole suite with one clear reason.

    Module-scoped by its callers, so the decision is made once. On the skip path no client is
    constructed and no socket is opened — the judge is built only when it will be used.
    """
    if _recording_requested():
        cassette.parent.mkdir(parents=True, exist_ok=True)
        cassette.unlink(missing_ok=True)
        live = AnthropicJudge()
        try:
            yield RecordingJudge(live, cassette)
        finally:
            live.close()
        return

    if cassette.exists():
        yield ReplayJudge(cassette)
        return

    pytest.skip(skip_reason(command))


def verdict_for(judge: Judge, case: dict) -> str:
    """The judge's verdict, asserting it produced one at all."""
    out = judge.judge(record_for(case), p_stage1=P_STAGE1)
    assert isinstance(out, JudgeOutput), out
    return out.verdict
