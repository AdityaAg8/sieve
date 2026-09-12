"""Sealed holdout half of the judge self-defence suite (PRD §12.5, §9.7).

Same two tests as `tests/test_judge_injection.py`, over `judge-cases-holdout.jsonl`, with
one deliberate difference: **the clean threshold here is a reported ratio, not a gate.** The
holdout exists to say what the prompt does on cases it was never tuned against, and a number
you are allowed to fail against is a number you will eventually tune against. So this module
asserts only that the file was fully evaluated, and writes both halves to
`bench/results/judge_injection_holdout.json`.

Every test carries `@pytest.mark.holdout`, so `make test` and CI's fast job deselect it
(`-m "not ... and not holdout"`). It is run **once**, in Phase 4 Task 11.3. No prompt change
may follow a holdout run for that version — if the result is bad, that is the result.

Recording, once, with a key:

    ANTHROPIC_API_KEY=... uv run pytest tests/test_judge_injection_holdout.py -m holdout \
        --record-mode=once
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sieve.judge.client import AnthropicJudge
from sieve.judge.schema import JudgeOutput
from sieve.types import ToolDefinition, ToolRecord

ROOT = Path(__file__).resolve().parents[1]
CASSETTE_DIR = Path(__file__).parent / "cassettes" / "judge_injection_holdout"
RESULTS = ROOT / "bench" / "results" / "judge_injection_holdout.json"
SKIP_REASON = (
    "no cassette recorded and no ANTHROPIC_API_KEY; run "
    "`uv run pytest tests/test_judge_injection.py --record-mode=once` with a key"
)

CASES = [
    json.loads(line)
    for line in (ROOT / "fixtures/injection/judge-cases-holdout.jsonl")
    .read_text(encoding="utf-8")
    .splitlines()
    if line.strip()
]
ATTACKS = [c for c in CASES if c["kind"] == "attack"]
CLEAN = [c for c in CASES if c["kind"] == "clean"]

ATTACKS_DISMISSED: list[str] = []
CLEAN_DISMISSED: list[str] = []
EVALUATED: list[str] = []


@pytest.fixture(scope="module")
def vcr_cassette_dir() -> str:
    return str(CASSETTE_DIR)


@pytest.fixture(scope="module")
def vcr_config() -> dict[str, object]:
    return {"filter_headers": ["x-api-key", "authorization"], "record_mode": "once"}


@pytest.fixture(autouse=True)
def _cassette_or_skip(request: pytest.FixtureRequest) -> None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    if not (CASSETTE_DIR / f"{request.node.name}.yaml").exists():
        pytest.skip(SKIP_REASON)


def _record(case: dict) -> ToolRecord:
    return ToolRecord(
        id=case["id"],
        server_name="inj",
        tool=ToolDefinition.model_validate(case["tool"]),
        sibling_tool_names=case.get("sibling_tool_names", []),
    )


@pytest.mark.holdout
@pytest.mark.vcr(filter_headers=["x-api-key", "authorization"])
@pytest.mark.parametrize("case", ATTACKS, ids=[c["id"] for c in ATTACKS])
def test_holdout_attacks_are_judged(case: dict) -> None:
    out = AnthropicJudge().judge(_record(case), p_stage1=0.5)
    assert isinstance(out, JudgeOutput), out
    EVALUATED.append(case["id"])
    if out.verdict == "dismissed":
        ATTACKS_DISMISSED.append(case["id"])


@pytest.mark.holdout
@pytest.mark.vcr(filter_headers=["x-api-key", "authorization"])
@pytest.mark.parametrize("case", CLEAN, ids=[c["id"] for c in CLEAN])
def test_holdout_clean_imperative_cases_are_judged(case: dict) -> None:
    out = AnthropicJudge().judge(_record(case), p_stage1=0.5)
    assert isinstance(out, JudgeOutput), out
    EVALUATED.append(case["id"])
    if out.verdict == "dismissed":
        CLEAN_DISMISSED.append(case["id"])


@pytest.mark.holdout
def test_zz_holdout_is_reported_not_gated() -> None:
    """The only assertion is that the whole file was evaluated. The numbers are reported."""
    if len(EVALUATED) < len(CASES):
        pytest.skip(
            f"only {len(EVALUATED)}/{len(CASES)} holdout cases ran; the slice is not "
            f"measured, so nothing is written. {SKIP_REASON}"
        )
    payload = {
        "attacks_total": len(ATTACKS),
        "attacks_dismissed": len(ATTACKS_DISMISSED),
        "attacks_dismissed_ids": sorted(ATTACKS_DISMISSED),
        "clean_total": len(CLEAN),
        "clean_dismissed": len(CLEAN_DISMISSED),
        "clean_dismissed_ids": sorted(CLEAN_DISMISSED),
        "note": "this is the suite's result, not general injection resistance",
    }
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
