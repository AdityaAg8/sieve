"""The dry run builds the dataset card and the split files without uploading anything."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench import publish
from sieve.types import CorpusRecord


def _corpus(tmp_path: Path, records: list[CorpusRecord]) -> Path:
    path = tmp_path / "corpus.jsonl"
    path.write_text(
        "".join(r.model_dump_json() + "\n" for r in records), encoding="utf-8", newline="\n"
    )
    return path


def _stats(tmp_path: Path) -> Path:
    path = tmp_path / "corpus_stats.json"
    path.write_text(
        json.dumps(
            {
                "n": 50,
                "counts": {"label": {"benign": 13, "imperative_benign": 20, "poisoned": 17}},
                "exact_share": 1.0,
                "hand_authored_poisoned_share": 0.31,
            }
        ),
        encoding="utf-8",
        newline="\n",
    )
    return path


def _split(records: list[CorpusRecord]) -> list[CorpusRecord]:
    """Give the mini corpus one of each split so all three files have content."""
    out: list[CorpusRecord] = []
    for index, record in enumerate(records):
        if index % 10 == 0:
            out.append(record.model_copy(update={"split": "holdout", "fold": None}))
        elif index % 10 == 1:
            out.append(
                record.model_copy(
                    update={"split": "eval_only", "fold": None, "source": "real_poisoned"}
                )
            )
        else:
            out.append(record.model_copy(update={"split": "dev", "fold": index % 5}))
    return out


def _run(tmp_path: Path, records: list[CorpusRecord], *extra: str) -> tuple[int, Path]:
    hub = tmp_path / "hub"
    code = publish.main(
        [
            "--dry-run",
            "--corpus",
            str(_corpus(tmp_path, records)),
            "--stats",
            str(_stats(tmp_path)),
            "--out",
            str(hub),
            *extra,
        ]
    )
    return code, hub


def test_dry_run_writes_all_five_files(tmp_path: Path, mini_records: list[CorpusRecord]) -> None:
    code, hub = _run(tmp_path, _split(mini_records))
    assert code == 0
    names = {p.name for p in hub.iterdir()}
    assert names == {"README.md", "dev.jsonl", "holdout.jsonl", "eval_only.jsonl", "NOTICE"}
    for path in hub.iterdir():
        assert b"\r\n" not in path.read_bytes()
        path.read_text(encoding="utf-8")


def test_rows_are_written_to_the_file_their_split_names(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    records = _split(mini_records)
    _, hub = _run(tmp_path, records)
    for name, split in (("dev", "dev"), ("holdout", "holdout"), ("eval_only", "eval_only")):
        lines = (hub / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
        loaded = [CorpusRecord.model_validate_json(line) for line in lines]
        assert loaded, f"{name}.jsonl is empty"
        assert {r.split for r in loaded} == {split}
        assert len(loaded) == sum(1 for r in records if r.split == split)


def test_the_card_never_quotes_a_holdout_metric(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    _, hub = _run(tmp_path, _split(mini_records))
    card = (hub / "README.md").read_text(encoding="utf-8")
    assert (hub / "holdout.jsonl").exists()
    lower = card.lower()
    assert "holdout" in lower  # it is described...
    # ...but never scored: the holdout is opened once, by bench/eval_final.py.
    for forbidden in ("holdout auroc", "holdout recall", "holdout precision", "holdout f1"):
        assert forbidden not in lower


def test_the_card_carries_the_two_licence_statement_and_the_takedown_commitment(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    _, hub = _run(tmp_path, _split(mini_records))
    card = (hub / "README.md").read_text(encoding="utf-8")
    assert "CC BY 4.0" in card
    assert "source_license" in card
    assert "7 days" in card
    assert "eval-only" in card.lower() or "eval_only" in card


def test_the_card_carries_the_llm_annotator_disclaimer(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    _, hub = _run(tmp_path, _split(mini_records))
    card = (hub / "README.md").read_text(encoding="utf-8")
    assert (
        "the same model family authored the synthetic payloads; this figure supports no claim."
        in card
    )


def test_the_notice_lists_every_source_repo_commit_and_licence(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    _, hub = _run(tmp_path, _split(mini_records))
    notice = (hub / "NOTICE").read_text(encoding="utf-8")
    for record in mini_records:
        assert record.source_repo in notice
        assert record.source_commit in notice
        assert record.source_license in notice


def test_an_apache_row_pulls_in_the_required_attribution(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    records = _split(mini_records)
    records[2] = records[2].model_copy(update={"source_license": "Apache-2.0"})
    _, hub = _run(tmp_path, records)
    notice = (hub / "NOTICE").read_text(encoding="utf-8")
    assert "Apache License, Version 2.0" in notice


def test_a_planted_mpl_record_makes_publish_exit_non_zero(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    records = _split(mini_records)
    records[3] = records[3].model_copy(update={"source_license": "MPL-2.0"})
    code, _ = _run(tmp_path, records)
    assert code != 0


def test_a_record_missing_its_provenance_makes_publish_exit_non_zero(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    records = _split(mini_records)
    records[4] = records[4].model_copy(update={"source_commit": ""})
    code, _ = _run(tmp_path, records)
    assert code != 0


def test_a_record_with_no_split_makes_publish_exit_non_zero(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    records = _split(mini_records)
    records[5] = records[5].model_copy(update={"split": None})
    code, _ = _run(tmp_path, records)
    assert code != 0


def test_the_committed_datasheet_has_every_required_section() -> None:
    text = Path("bench/datasheet.md").read_text(encoding="utf-8")
    for heading in (
        "## Motivation",
        "## Composition",
        "## Collection process",
        "## Synthesis protocol",
        "## Labelling protocol",
        "## Source-leakage probe",
        "## LLM annotator",
        "## Limitations",
        "## Licensing",
        "## Citation",
    ):
        assert heading in text
    assert (
        "the same model family authored the synthetic payloads; this figure supports no claim."
        in text
    )
    assert "7 days" in text


def test_a_real_upload_needs_a_repo_id(tmp_path: Path, mini_records: list[CorpusRecord]) -> None:
    with pytest.raises(SystemExit):
        publish.main(
            [
                "--corpus",
                str(_corpus(tmp_path, _split(mini_records))),
                "--stats",
                str(_stats(tmp_path)),
                "--out",
                str(tmp_path / "hub"),
            ]
        )
