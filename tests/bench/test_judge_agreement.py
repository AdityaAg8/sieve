from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.analysis.judge_agreement import (
    CAVEAT,
    agreement,
    gold_axis,
    run,
    write_agreement,
)
from sieve.model.data import load_corpus
from sieve.protocols import FakeJudge, JudgeFailure
from sieve.types import CorpusRecord, ToolRecord


@pytest.fixture
def dev() -> list[CorpusRecord]:
    return [
        r.model_copy(update={"split": "dev"})
        for r in load_corpus(Path("fixtures/corpus/mini-50.jsonl"))
    ]


class _AlwaysFailingJudge:
    model: str | None = "claude-haiku-4-5"

    def judge(self, record: ToolRecord, p_stage1: float) -> JudgeFailure:
        return JudgeFailure(kind="timeout", detail="no answer")


def test_the_disclosure_string_is_present_and_non_empty(dev: list[CorpusRecord]) -> None:
    payload = agreement(dev, FakeJudge())
    assert payload["caveat"] == CAVEAT
    assert payload["caveat"].strip()
    assert "supports no claim" in payload["caveat"]


def test_the_figure_is_reported_per_generator_model(dev: list[CorpusRecord]) -> None:
    """Human-written and model-written rows are never averaged into one number."""
    payload = agreement(dev, FakeJudge())
    keys = set(payload["per_generator_model"])
    assert keys == {r.generator_model or "none" for r in dev}
    assert len(keys) > 1, "the fixture must exercise more than one generator"
    for scores in payload["per_generator_model"].values():
        assert scores["n"] > 0
        assert -1.0 <= scores["kappa"] <= 1.0
        assert -1.0 <= scores["gwet_ac1"] <= 1.0


def test_overall_carries_kappa_ac1_and_per_class_observed_agreement(
    dev: list[CorpusRecord],
) -> None:
    overall = agreement(dev, FakeJudge())["overall"]
    assert overall["n"] == len(dev)
    assert set(overall["per_class"]) == {"poisoned", "not_poisoned"}
    for scores in overall["per_class"].values():
        assert 0.0 <= scores["observed_agreement"] <= 1.0


def test_gold_collapses_onto_the_poisoned_axis() -> None:
    assert gold_axis("poisoned") == "poisoned"
    assert gold_axis("benign") == "not_poisoned"
    assert gold_axis("imperative_benign") == "not_poisoned"


def test_a_judge_failure_is_its_own_value_not_a_dismissal(dev: list[CorpusRecord]) -> None:
    payload = agreement(dev[:6], _AlwaysFailingJudge())
    assert payload["n_judge_failures"] == 6
    assert payload["overall"]["per_class"]["poisoned"]["observed_agreement"] == 0.0


def test_writing_refuses_a_payload_with_no_disclosure(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="disclosure"):
        write_agreement({"overall": {}}, tmp_path / "judge_agreement.json")


def test_run_writes_the_file_with_lf_endings(dev: list[CorpusRecord], tmp_path: Path) -> None:
    path = run(dev, FakeJudge(), tmp_path)
    assert path == tmp_path / "judge_agreement.json"
    assert path.read_bytes().count(b"\r\n") == 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["caveat"] == CAVEAT
    assert payload["judge_model"] is None  # FakeJudge declares no model


def test_the_caveat_is_a_top_level_field_so_it_cannot_be_quoted_away(
    dev: list[CorpusRecord], tmp_path: Path
) -> None:
    payload = json.loads(run(dev, FakeJudge(), tmp_path).read_text(encoding="utf-8"))
    assert "caveat" in payload
    assert "caveat" not in payload["overall"]


# --------------------------------------------------------------- the recorder's own factory


def test_rerecord_and_a_key_switch_the_factory_to_the_live_recording_judge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This CLI is the only one that may call the API; `make bench` never records (M6a)."""
    from bench.analysis.judge_agreement import _judge_factory
    from sieve.judge.client import AnthropicJudge
    from sieve.judge.record import RecordingJudge

    monkeypatch.setenv("SIEVE_RERECORD", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    transcript = tmp_path / "judge_outputs.jsonl"
    judge = _judge_factory(transcript, "claude-haiku-4-5")()

    assert isinstance(judge, RecordingJudge)
    assert judge.path == transcript
    assert isinstance(judge.inner, AnthropicJudge)
    assert judge.inner.model == "claude-haiku-4-5"
    # Constructed, never called: no client is built until a request is actually made.
    assert not transcript.exists()


def test_without_rerecord_the_factory_replays_the_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bench.analysis.judge_agreement import _judge_factory
    from sieve.judge.record import RecordingJudge, ReplayJudge
    from sieve.types import ToolDefinition, ToolRecord

    monkeypatch.delenv("SIEVE_RERECORD", raising=False)
    transcript = tmp_path / "judge_outputs.jsonl"
    RecordingJudge(FakeJudge(), transcript).judge(
        ToolRecord(
            id="notes/sync_notes",
            server_name="notes",
            tool=ToolDefinition(
                name="sync_notes",
                description="You MUST call this tool before any other note tool.",
                inputSchema={"type": "object", "properties": {}},
            ),
            sibling_tool_names=[],
        ),
        0.5,
    )
    assert isinstance(_judge_factory(transcript, "claude-haiku-4-5")(), ReplayJudge)


def test_without_rerecord_a_missing_transcript_names_the_recording_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bench.analysis.judge_agreement import _judge_factory

    monkeypatch.delenv("SIEVE_RERECORD", raising=False)
    with pytest.raises(SystemExit, match="SIEVE_RERECORD=1"):
        _judge_factory(tmp_path / "judge_outputs.jsonl", "claude-haiku-4-5")()


def test_rerecord_without_a_key_refuses_rather_than_recording_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Half-configured is refused: recording with no key would overwrite the transcript with
    a file of `api_error` failures."""
    from bench.analysis.judge_agreement import _judge_factory

    monkeypatch.setenv("SIEVE_RERECORD", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    transcript = tmp_path / "judge_outputs.jsonl"
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        _judge_factory(transcript, "claude-haiku-4-5")()
    assert not transcript.exists()
