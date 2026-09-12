"""The labelling session: the longest human task in the project, so it is tested hardest."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from bench.label.session import IncompleteLabel, LabelSession
from sieve.types import CorpusRecord


def _write(path: Path, records: list[CorpusRecord]) -> Path:
    path.write_text(
        "".join(r.model_dump_json() + "\n" for r in records), encoding="utf-8", newline="\n"
    )
    return path


@pytest.fixture
def corpus(tmp_path: Path, mini_records: list[CorpusRecord]) -> Path:
    return _write(tmp_path / "corpus.jsonl", mini_records[:6])


def _reload(path: Path) -> list[CorpusRecord]:
    return [
        CorpusRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def test_set_label_and_save_round_trips(corpus: Path) -> None:
    session = LabelSession(corpus, labeller="aditya")
    session.set_label("poisoned")
    session.set_rationale("Reads a file outside the tool's stated purpose.")
    session.save()
    assert _reload(corpus)[0].label == "poisoned"
    assert _reload(corpus)[0].labeller == "aditya"


def test_rationale_is_required_for_the_two_directive_classes(corpus: Path) -> None:
    session = LabelSession(corpus, labeller="aditya")
    session.set_label("poisoned")
    session.set_rationale("")
    with pytest.raises(IncompleteLabel) as excinfo:
        session.save()
    assert "mini-000" in str(excinfo.value)


def test_benign_needs_no_rationale(corpus: Path) -> None:
    session = LabelSession(corpus, labeller="aditya")
    session.set_label("benign")
    session.set_rationale("")
    session.save()
    assert _reload(corpus)[0].label == "benign"


def test_save_is_atomic_and_leaves_no_temp_file(corpus: Path) -> None:
    session = LabelSession(corpus, labeller="aditya")
    session.set_label("benign")
    session.save()
    siblings = {p.name for p in corpus.parent.iterdir()}
    assert siblings == {corpus.name}
    assert len(_reload(corpus)) == 6


def test_a_crash_mid_save_leaves_the_original_intact(
    corpus: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = corpus.read_text(encoding="utf-8")
    session = LabelSession(corpus, labeller="aditya")
    session.set_label("benign")

    def boom(src: Any, dst: Any) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk gone"):
        session.save()
    assert corpus.read_text(encoding="utf-8") == before
    assert {p.name for p in corpus.parent.iterdir()} == {corpus.name}


def test_navigation_and_progress(corpus: Path) -> None:
    session = LabelSession(corpus, labeller="aditya")
    assert session.progress() == (1, 6)
    assert session.current().id == "mini-000"
    session.next()
    assert session.progress() == (2, 6)
    assert session.current().id == "mini-001"
    session.prev()
    session.prev()
    assert session.progress() == (1, 6)


def test_sub_label_toggle_and_difficulty_and_concealment(corpus: Path) -> None:
    session = LabelSession(corpus, labeller="aditya")
    session.toggle_sub_label("exfiltration")
    assert session.current().sub_labels == ["exfiltration"]
    session.toggle_sub_label("exfiltration")
    assert session.current().sub_labels == []
    session.set_difficulty("hard")
    session.set_concealment("zero_width")
    assert session.current().difficulty == "hard"
    assert session.current().concealment == "zero_width"


def test_blind_render_hides_every_anchoring_field(corpus: Path) -> None:
    session = LabelSession(corpus, labeller="aditya", blind=True)
    pane = session.render()
    for hidden in (
        "label:",
        "second_label",
        "sub_labels",
        "difficulty",
        "concealment",
        "label_rationale",
        "generator_model",
        "mutation_of",
        "payload_fields",
    ):
        assert hidden not in pane
    assert "get_current" in pane


def test_sighted_render_shows_the_label_and_tier(corpus: Path) -> None:
    pane = LabelSession(corpus, labeller="aditya").render()
    assert "label:" in pane and "difficulty" in pane and "sub_labels" in pane


def _synthetic(records: list[CorpusRecord]) -> CorpusRecord:
    return next(r for r in records if r.source == "synthetic_mutation")


def test_blind_primary_pass_records_generator_intent_before_overwriting(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    row = _synthetic(mini_records)
    intended_label = row.label
    corpus = _write(tmp_path / "c.jsonl", [row])
    intent = tmp_path / "generator_intent.jsonl"
    session = LabelSession(corpus, labeller="aditya", blind=True, intent_path=intent)
    new_label = "benign" if intended_label != "benign" else "poisoned"
    session.set_label(new_label)
    session.set_rationale("Blind read: within the tool's stated purpose.")
    session.save()

    lines = intent.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["id"] == row.id
    assert entry["intended_label"] == intended_label
    assert entry["intended_sub_labels"] == list(row.sub_labels)
    assert entry["intended_difficulty"] == row.difficulty
    assert entry["generator_model"] == row.generator_model
    assert _reload(corpus)[0].label == new_label


def test_blind_primary_pass_records_intent_once_per_record(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    row = _synthetic(mini_records)
    corpus = _write(tmp_path / "c.jsonl", [row])
    intent = tmp_path / "generator_intent.jsonl"
    session = LabelSession(corpus, labeller="aditya", blind=True, intent_path=intent)
    session.set_label("benign")
    session.set_label("poisoned")
    assert len(intent.read_text(encoding="utf-8").splitlines()) == 1


def test_blind_primary_pass_on_a_real_row_writes_no_intent(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    row = next(r for r in mini_records if r.source == "public_repo")
    corpus = _write(tmp_path / "c.jsonl", [row])
    intent = tmp_path / "generator_intent.jsonl"
    session = LabelSession(corpus, labeller="aditya", blind=True, intent_path=intent)
    session.set_label("benign")
    assert not intent.exists()


def test_second_mode_writes_second_label_and_never_label(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    row = _synthetic(mini_records)
    corpus = _write(tmp_path / "c.jsonl", [row])
    intent = tmp_path / "generator_intent.jsonl"
    original_label = row.label
    session = LabelSession(corpus, labeller="second", blind=True, second=True, intent_path=intent)
    session.set_label("benign")
    session.set_rationale("Second annotator read.")
    session.save()
    out = _reload(corpus)[0]
    assert out.second_label == "benign"
    assert out.label == original_label
    assert out.labeller == row.labeller
    assert not intent.exists()
