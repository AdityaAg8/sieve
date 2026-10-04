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


def _opened(tmp_path: Path, corpus: Path, version: str | None = None) -> Path:
    """An opening of `corpus`'s own dataset version, as eval_final logs it."""
    from bench.ablations.run import dataset_version

    log = tmp_path / "test_opened.log"
    entry = {
        "reason": "v1 release",
        "rows_evaluated": 9,
        "dataset_version": version or dataset_version(corpus),
    }
    log.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    return log


def _run(tmp_path: Path, records: list[CorpusRecord], *extra: str) -> tuple[int, Path]:
    """A run AFTER an opening: the sealed splits may be published."""
    hub = tmp_path / "hub"
    corpus = _corpus(tmp_path, records)
    if "--test-opened-log" not in extra:
        extra = (*extra, "--test-opened-log", str(_opened(tmp_path, corpus)))
    code = publish.main(
        [
            "--dry-run",
            "--corpus",
            str(corpus),
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


def test_before_an_opening_the_sealed_splits_are_never_written(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    code, hub = _run(
        tmp_path, _split(mini_records), "--test-opened-log", str(tmp_path / "none.log")
    )
    assert code == 0
    assert {p.name for p in hub.iterdir()} == {"README.md", "dev.jsonl", "NOTICE"}
    assert "withheld" in (hub / "README.md").read_text(encoding="utf-8")


def test_an_opening_of_another_dataset_version_keeps_the_splits_sealed(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    """Z2: v1 opened, corpus regenerated -> the new holdout is not public yet."""
    corpus = _corpus(tmp_path, _split(mini_records))
    stale = _opened(tmp_path, corpus, version="labelled.jsonl@000000000000")
    code, hub = _run(tmp_path, _split(mini_records), "--test-opened-log", str(stale))
    assert code == 0
    assert {p.name for p in hub.iterdir()} == {"README.md", "dev.jsonl", "NOTICE"}


def test_the_card_quotes_the_opening_log(tmp_path: Path, mini_records: list[CorpusRecord]) -> None:
    _, hub = _run(tmp_path, _split(mini_records))
    assert '"reason": "v1 release"' in (hub / "README.md").read_text(encoding="utf-8")


def test_the_default_corpus_is_the_split_one() -> None:
    assert Path("bench/data/labelled.jsonl") == publish.DEFAULT_CORPUS


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


# -- fix round 1: the licence gate is an allow-list, not a deny-list of one ----


@pytest.mark.parametrize("licence", ["GPL-3.0", "AGPL-3.0", "All rights reserved", "MPL-2.0", ""])
def test_any_licence_outside_the_permissive_set_is_refused(
    tmp_path: Path, mini_records: list[CorpusRecord], licence: str
) -> None:
    records = _split(mini_records)
    records[3] = records[3].model_copy(update={"source_license": licence})
    code, _ = _run(tmp_path, records)
    assert code != 0


@pytest.mark.parametrize("licence", ["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"])
def test_the_permissive_set_is_accepted(
    tmp_path: Path, mini_records: list[CorpusRecord], licence: str
) -> None:
    records = _split(mini_records)
    records[3] = records[3].model_copy(update={"source_license": licence})
    code, _ = _run(tmp_path, records)
    assert code == 0


def test_a_pointer_derived_record_is_refused_even_with_a_permissive_licence(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    # `reconstruct()` stamps the pointer's URL into source_repo. A permissive-looking licence
    # string on a row whose bytes may not be redistributed must not be enough to publish it.
    url = "https://example.invalid/labs/2025-04-tool-poisoning.md"
    records = _split(mini_records)
    records[6] = records[6].model_copy(
        update={"source_repo": url, "source_license": "MIT", "split": "eval_only", "fold": None}
    )
    pointers = tmp_path / "pointers.json"
    pointers.write_text(
        json.dumps(
            [
                {
                    "url": url,
                    "sha256": "0" * 64,
                    "licence": "All rights reserved",
                    "extract": {"path": "x.md", "json_pointer": ""},
                }
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    code, _ = _run(tmp_path, records, "--pointers", str(pointers))
    assert code != 0


def test_an_empty_pointers_file_blocks_nothing(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    pointers = tmp_path / "pointers.json"
    pointers.write_text("[]\n", encoding="utf-8", newline="\n")
    code, _ = _run(tmp_path, _split(mini_records), "--pointers", str(pointers))
    assert code == 0


def test_partial_agreement_and_leakage_json_do_not_crash_the_card(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    agreement = tmp_path / "agreement.json"
    agreement.write_text(json.dumps({"inter": {"n": 3}}), encoding="utf-8", newline="\n")
    leakage = tmp_path / "leakage.json"
    leakage.write_text(json.dumps({"passed": True}), encoding="utf-8", newline="\n")
    code, hub = _run(
        tmp_path,
        _split(mini_records),
        "--agreement",
        str(agreement),
        "--leakage",
        str(leakage),
    )
    assert code == 0
    assert (hub / "README.md").read_text(encoding="utf-8")


def test_the_card_leaves_no_placeholder_behind(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    _, hub = _run(tmp_path, _split(mini_records))
    card = (hub / "README.md").read_text(encoding="utf-8")
    for placeholder in (
        "<!-- COMPOSITION -->",
        "<!-- AGREEMENT -->",
        "<!-- LEAKAGE -->",
        "<!-- TEST_OPENED -->",
    ):
        assert placeholder not in card


def test_the_card_discloses_the_cross_boundary_near_duplicate_count(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    stats = tmp_path / "corpus_stats.json"
    stats.write_text(
        json.dumps({"n": 50, "cross_boundary_near_duplicate_pairs": 3}),
        encoding="utf-8",
        newline="\n",
    )
    hub = tmp_path / "hub"
    code = publish.main(
        [
            "--dry-run",
            "--corpus",
            str(_corpus(tmp_path, _split(mini_records))),
            "--stats",
            str(stats),
            "--out",
            str(hub),
        ]
    )
    assert code == 0
    card = (hub / "README.md").read_text(encoding="utf-8")
    assert "cross-boundary near-duplicate" in card.lower()
    assert "3" in card
