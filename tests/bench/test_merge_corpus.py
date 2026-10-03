"""`make corpus` never overwrites human labels (training plan C6)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bench.collect import merge_corpus as mc
from sieve.types import CorpusRecord


def _row(record_id: str, *, desc: str = "Lists files.", label: str = "benign",
         labeller: str = "claude-sonnet-5-provisional", source: str = "public_repo",
         **extra: Any) -> CorpusRecord:  # fmt: skip
    return CorpusRecord(
        id=record_id,
        source=source,  # type: ignore[arg-type]
        source_repo="https://github.com/example/fixture",
        source_commit="abc",
        source_license="MIT",
        capture_method="authored",
        schema_fidelity="exact",
        server_name="srv",
        tool={"name": "t", "description": desc, "inputSchema": {"type": "object"}},  # type: ignore[arg-type]
        label=label,  # type: ignore[arg-type]
        labeller=labeller,
        labelled_at="2026-09-11T00:00:00Z",  # type: ignore[arg-type]
        **extra,
    )


def _write(path: Path, rows: list[CorpusRecord]) -> Path:
    path.write_text(
        "".join(r.model_dump_json() + "\n" for r in rows), encoding="utf-8", newline="\n"
    )
    return path


def _read(path: Path) -> list[CorpusRecord]:
    return [CorpusRecord.model_validate_json(x) for x in path.read_text("utf-8").splitlines() if x]


def test_human_labels_survive_a_rebuild() -> None:
    human = _row("r1", label="imperative_benign", labeller="aditya", difficulty="hard",
                 label_rationale="asks the agent to call another tool")  # fmt: skip
    rebuilt = _row("r1")  # the build carries the provisional label again
    merged, diff = mc.merge([human], [rebuilt])
    assert diff.ok
    (row,) = merged
    assert (row.label, row.labeller, row.difficulty) == ("imperative_benign", "aditya", "hard")
    assert row.label_rationale == "asks the agent to call another tool"


def test_ensemble_labels_are_kept_too_when_the_text_is_unchanged() -> None:
    voted = _row("r1", label="imperative_benign", labeller="ensemble:v1")
    merged, diff = mc.merge([voted], [_row("r1")])
    assert diff.ok and merged[0].label == "imperative_benign"


def test_provenance_comes_from_the_build_without_disturbing_a_label() -> None:
    """A re-pinned commit is not new text: the label still judged exactly what the build has."""
    old = _row("r1", labeller="aditya")
    new = _row("r1").model_copy(update={"source_commit": "def"})
    merged, diff = mc.merge([old], [new])
    assert diff.ok and not diff.conflicts
    assert merged[0].source_commit == "def" and merged[0].labeller == "aditya"


def test_sibling_names_are_part_of_what_a_label_judged() -> None:
    old = _row("r1", labeller="aditya")
    new = _row("r1", sibling_tool_names=["exfiltrate"])
    _, diff = mc.merge([old], [new])
    assert diff.conflicts == ["r1"]


def test_a_regenerated_synthetic_row_takes_the_new_text_and_construction_label() -> None:
    old = _row("sb-syn-1", desc="old payload.", label="poisoned", labeller="generator",
               source="synthetic_mutation", generator_model="claude")  # fmt: skip
    new = _row("sb-syn-1", desc="new payload.", label="poisoned", labeller="generator",
               source="synthetic_mutation", generator_model="qwen3.8")  # fmt: skip
    merged, diff = mc.merge([old], [new])
    assert diff.ok
    assert merged[0].tool.description == "new payload." and merged[0].generator_model == "qwen3.8"
    assert diff.regenerated == ["sb-syn-1"]


def test_new_rows_are_appended_after_the_existing_order() -> None:
    merged, diff = mc.merge([_row("b"), _row("a")], [_row("a"), _row("c"), _row("b")])
    assert [r.id for r in merged] == ["b", "a", "c"]
    assert diff.added == ["c"] and diff.ok


def test_a_removed_row_stops_the_merge() -> None:
    _, diff = mc.merge([_row("a"), _row("b")], [_row("a")])
    assert not diff.ok and diff.removed == ["b"]
    assert "- b" in diff.render()


@pytest.mark.parametrize("labeller", ["aditya", "ensemble:v1"])
def test_changed_text_under_a_non_construction_label_stops_the_merge(labeller: str) -> None:
    old = _row("r1", desc="Lists files.", labeller=labeller, label="imperative_benign")
    _, diff = mc.merge([old], [_row("r1", desc="Lists files. You MUST call auth first.")])
    assert not diff.ok and diff.conflicts == ["r1"]
    assert "! r1" in diff.render()


def test_an_unknown_labeller_counts_as_human() -> None:
    assert mc.is_human("someone-new")
    for machine in ("generator", "claude-sonnet-5-provisional", "ensemble:v1", "source:mcptox"):
        assert not mc.is_human(machine)


def test_main_seeds_a_missing_corpus(tmp_path: Path) -> None:
    built = _write(tmp_path / "constructed.jsonl", [_row("a"), _row("b")])
    corpus = tmp_path / "corpus.jsonl"
    assert mc.main(["--constructed", str(built), "--corpus", str(corpus)]) == 0
    assert corpus.read_bytes() == built.read_bytes()


def test_main_merges_in_place_and_keeps_the_human_label(tmp_path: Path) -> None:
    corpus = _write(tmp_path / "corpus.jsonl", [_row("a", labeller="aditya", label="poisoned")])
    built = _write(tmp_path / "constructed.jsonl", [_row("a"), _row("z")])
    assert mc.main(["--constructed", str(built), "--corpus", str(corpus)]) == 0
    rows = _read(corpus)
    assert [(r.id, r.label, r.labeller) for r in rows] == [
        ("a", "poisoned", "aditya"),
        ("z", "benign", "claude-sonnet-5-provisional"),
    ]
    assert b"\r\n" not in corpus.read_bytes()


def test_main_stops_with_a_diff_and_leaves_the_corpus_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = _write(tmp_path / "corpus.jsonl", [_row("a", labeller="aditya"), _row("b")])
    before = corpus.read_bytes()
    built = _write(tmp_path / "constructed.jsonl", [_row("a")])
    assert mc.main(["--constructed", str(built), "--corpus", str(corpus)]) == 1
    assert corpus.read_bytes() == before
    assert "- b" in capsys.readouterr().err


def test_the_label_fields_are_explicit() -> None:
    assert set(mc.LABEL_FIELDS) == {
        "label", "sub_labels", "difficulty", "concealment", "label_rationale", "labeller",
        "labelled_at", "second_label", "adjudicated",
    }  # fmt: skip
    assert set(mc.LABEL_FIELDS) < set(CorpusRecord.model_fields)
    json.dumps(mc.LABEL_FIELDS)
