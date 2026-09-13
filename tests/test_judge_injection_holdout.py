"""Sealed holdout half of the judge self-defence suite (PRD §12.5, §9.7).

The same measurement as `tests/test_judge_injection.py`, over `judge-cases-holdout.jsonl`,
with one deliberate difference: **the clean threshold here is a reported ratio, not a gate.**
The holdout exists to say what the prompt does on cases it was never tuned against, and a
number you are allowed to fail against is a number you will eventually tune against. So this
module asserts only that the file was fully evaluated, and writes both halves to
`bench/results/judge_injection_holdout.json`.

Every test carries `@pytest.mark.holdout`, so `make test` and CI's fast job deselect it
(`-m "not ... and not holdout"`). It is run **once**, in Phase 4 Task 11.3. No prompt change
may follow a holdout run for that version — if the result is bad, that is the result.

The shared scaffolding lives in `tests/judge/injection_support.py`. Sharing it is safe: the
seal is on the data file, which nothing outside this module opens.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from sieve.protocols import Judge
from tests.judge.injection_support import (
    CASSETTES,
    ROOT,
    cases_for,
    judge_fixture,
    verdict_for,
)

CASSETTE = CASSETTES / "holdout.jsonl"
RECORD_COMMAND = "uv run pytest tests/test_judge_injection_holdout.py -m holdout"
RESULTS = ROOT / "bench" / "results" / "judge_injection_holdout.json"

CASES = cases_for("judge-cases-holdout")
ATTACKS = [c for c in CASES if c["kind"] == "attack"]
CLEAN = [c for c in CASES if c["kind"] == "clean"]

ATTACKS_DISMISSED: list[str] = []
CLEAN_DISMISSED: list[str] = []
EVALUATED: list[str] = []


@pytest.fixture(scope="module")
def judge() -> Iterator[Judge]:
    yield from judge_fixture(Path(CASSETTE), RECORD_COMMAND)


@pytest.mark.holdout
@pytest.mark.parametrize("case", ATTACKS, ids=[c["id"] for c in ATTACKS])
def test_holdout_attacks_are_judged(judge: Judge, case: dict) -> None:
    verdict = verdict_for(judge, case)
    EVALUATED.append(case["id"])
    if verdict == "dismissed":
        ATTACKS_DISMISSED.append(case["id"])


@pytest.mark.holdout
@pytest.mark.parametrize("case", CLEAN, ids=[c["id"] for c in CLEAN])
def test_holdout_clean_imperative_cases_are_judged(judge: Judge, case: dict) -> None:
    verdict = verdict_for(judge, case)
    EVALUATED.append(case["id"])
    if verdict == "dismissed":
        CLEAN_DISMISSED.append(case["id"])


@pytest.mark.holdout
def test_zz_holdout_is_reported_not_gated() -> None:
    """The only assertion is that the whole file was evaluated. The numbers are reported."""
    if len(EVALUATED) < len(CASES):
        pytest.skip(
            f"only {len(EVALUATED)}/{len(CASES)} holdout cases ran; the slice is not "
            f"measured, so nothing is written"
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
