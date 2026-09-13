"""The injection suites' plumbing, tested without a key and without a live cassette.

The gate itself is unmeasured until someone records against the model, but the machinery that
will carry it — case loading, the cassette-or-skip decision, replay through `ReplayJudge` —
is testable today, and is exactly the part that would otherwise fail silently on the day
someone finally has a key.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from sieve.judge.record import RecordingJudge
from sieve.protocols import FakeJudge, Judge
from tests.judge import injection_support as support


def _drain(gen: Iterator[Judge]) -> Judge:
    judge = next(gen)
    gen.close()
    return judge


def test_the_dev_file_carries_the_shape_the_gate_assumes() -> None:
    cases = support.cases_for("judge-cases-dev")
    attacks = [c for c in cases if c["kind"] == "attack"]
    clean = [c for c in cases if c["kind"] == "clean"]
    assert len(attacks) >= 25, "PRD §12.5 requires at least 25 attack cases"
    assert len(clean) >= 15, "PRD §12.5 requires at least 15 clean cases"


def test_record_for_builds_a_tool_record_from_a_case() -> None:
    case = support.cases_for("judge-cases-dev")[0]
    record = support.record_for(case)
    assert record.id == case["id"]
    assert record.server_name == "inj"


def test_without_a_cassette_or_a_key_the_suite_skips_and_names_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SIEVE_RERECORD", raising=False)

    with pytest.raises(pytest.skip.Exception) as excinfo:
        _drain(support.judge_fixture(tmp_path / "absent.jsonl", "uv run pytest tests/x.py"))

    reason = str(excinfo.value)
    assert "no judge cassette recorded and no ANTHROPIC_API_KEY" in reason
    assert "SIEVE_RERECORD=1" in reason
    assert "uv run pytest tests/x.py" in reason


def test_a_recorded_cassette_replays_the_suite_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end proof of the record-then-replay path, with FakeJudge standing in for the API."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SIEVE_RERECORD", raising=False)

    cassette = tmp_path / "dev.jsonl"
    cases = support.cases_for("judge-cases-dev")[:3]
    recorder = RecordingJudge(FakeJudge(), cassette)
    for case in cases:
        recorder.judge(support.record_for(case), support.P_STAGE1)

    judge = _drain(support.judge_fixture(cassette, "uv run pytest tests/x.py"))
    for case in cases:
        assert support.verdict_for(judge, case) in {"confirmed", "dismissed", "uncertain"}


def test_recording_requires_both_the_key_and_the_opt_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A key alone must not start spending: recording is an explicit, one-shot decision.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.delenv("SIEVE_RERECORD", raising=False)
    with pytest.raises(pytest.skip.Exception):
        _drain(support.judge_fixture(tmp_path / "absent.jsonl", "uv run pytest tests/x.py"))
