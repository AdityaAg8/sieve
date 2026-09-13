from __future__ import annotations

from pathlib import Path

import pytest
from transformers import AutoTokenizer

from sieve.model.data import ID2LABEL, LABEL2ID, add_markers, build_splits, load_corpus
from sieve.serialise import MARKERS
from sieve.types import LABELS, CorpusRecord

TINY = "prajjwal1/bert-tiny"


def _servers(ds) -> set[str]:  # type: ignore[no-untyped-def]
    return set(ds["server_name"])


def _ids(ds) -> list[str]:  # type: ignore[no-untyped-def]
    return list(ds["id"])


def _populated_folds(records: list[CorpusRecord]) -> list[int]:
    return sorted({r.fold for r in records if r.fold is not None})


def test_label2id_order_equals_labels_order() -> None:
    assert tuple(LABEL2ID) == LABELS
    assert list(LABEL2ID.values()) == [0, 1, 2]
    assert ID2LABEL == {0: "benign", 1: "imperative_benign", 2: "poisoned"}


def test_load_corpus_reads_the_committed_fixture() -> None:
    records = load_corpus(Path("fixtures/corpus/mini-50.jsonl"))
    assert len(records) == 50
    assert all(isinstance(r, CorpusRecord) for r in records)


def test_every_fold_has_three_non_empty_disjoint_splits(
    balanced_dev_records: list[CorpusRecord],
) -> None:
    assert _populated_folds(balanced_dev_records) == [0, 1, 2, 3, 4]
    for fold in range(5):
        d = build_splits(balanced_dev_records, fold=fold)
        train, early, oof = d["train"], d["early_stop"], d["oof"]
        assert len(train) > 0 and len(early) > 0 and len(oof) > 0
        sets = [set(_ids(train)), set(_ids(early)), set(_ids(oof))]
        assert sets[0] & sets[1] == set()
        assert sets[0] & sets[2] == set()
        assert sets[1] & sets[2] == set()


def test_no_server_appears_in_both_train_and_oof(
    balanced_dev_records: list[CorpusRecord],
) -> None:
    for fold in range(5):
        d = build_splits(balanced_dev_records, fold=fold)
        assert _servers(d["train"]) & _servers(d["oof"]) == set()
        assert _servers(d["early_stop"]) & _servers(d["oof"]) == set()


def test_early_stop_servers_are_a_subset_of_the_folds_training_servers(
    balanced_dev_records: list[CorpusRecord],
) -> None:
    for fold in range(5):
        d = build_splits(balanced_dev_records, fold=fold)
        held_out = {r.server_name for r in balanced_dev_records if r.fold == fold}
        training_servers = {r.server_name for r in balanced_dev_records if r.fold != fold}
        assert _servers(d["early_stop"]) <= training_servers
        assert _servers(d["early_stop"]) & held_out == set()


def test_pooled_oof_covers_every_dev_record_exactly_once(
    balanced_dev_records: list[CorpusRecord],
) -> None:
    pooled: list[str] = []
    for fold in range(5):
        pooled.extend(_ids(build_splits(balanced_dev_records, fold=fold)["oof"]))
    assert sorted(pooled) == sorted(r.id for r in balanced_dev_records)


def test_the_brief_fixture_folds_are_usable_where_they_are_populated(
    dev_records: list[CorpusRecord],
) -> None:
    """`_fold_of` leaves fold 0 empty on mini-50; every populated fold still splits cleanly."""
    for fold in _populated_folds(dev_records):
        d = build_splits(dev_records, fold=fold)
        assert len(d["oof"]) > 0
        assert _servers(d["train"]) & _servers(d["oof"]) == set()


def test_holdout_and_eval_only_rows_never_reach_any_split(
    balanced_dev_records: list[CorpusRecord],
) -> None:
    sealed = [
        balanced_dev_records[0].model_copy(
            update={"id": "sealed-holdout", "server_name": "sealed-a", "split": "holdout"}
        ),
        balanced_dev_records[1].model_copy(
            update={"id": "sealed-eval", "server_name": "sealed-b", "split": "eval_only"}
        ),
    ]
    for fold in range(5):
        d = build_splits([*balanced_dev_records, *sealed], fold=fold)
        for name in ("train", "early_stop", "oof"):
            assert "sealed-holdout" not in _ids(d[name])
            assert "sealed-eval" not in _ids(d[name])


def test_columns_and_label_ids(balanced_dev_records: list[CorpusRecord]) -> None:
    d = build_splits(balanced_dev_records, fold=0)
    assert set(d["train"].column_names) == {
        "id",
        "text",
        "label",
        "difficulty",
        "concealment",
        "source",
        "server_name",
        "fold",
    }
    by_id = {r.id: r for r in balanced_dev_records}
    for row in d["train"]:
        assert row["label"] == LABEL2ID[by_id[row["id"]].label]


def test_description_only_mode_serialises_only_the_description(
    balanced_dev_records: list[CorpusRecord],
) -> None:
    d = build_splits(balanced_dev_records, fold=0, mode="description_only")
    for text in d["train"]["text"]:
        assert text.startswith("[DESC]")
        assert "[NAME]" not in text


def test_scanner_findings_are_appended_when_supplied(
    balanced_dev_records: list[CorpusRecord],
) -> None:
    from sieve.types import ScannerFinding

    target = balanced_dev_records[0]
    finding = ScannerFinding(
        scanner="cisco_yara",
        scanner_version="1.0",
        rule_id="R1",
        severity="high",
        message=None,
        raw={},
    )
    d = build_splits(
        balanced_dev_records,
        fold=1 if target.fold == 0 else 0,
        scanner_findings_by_id={target.id: [finding]},
    )
    texts = {i: t for i, t in zip(d["train"]["id"], d["train"]["text"], strict=True)}
    assert "[SCANNER] cisco_yara:R1" in texts[target.id]


def test_build_splits_is_deterministic(balanced_dev_records: list[CorpusRecord]) -> None:
    a = build_splits(balanced_dev_records, fold=2)
    b = build_splits(balanced_dev_records, fold=2)
    assert _ids(a["early_stop"]) == _ids(b["early_stop"])
    assert _ids(a["train"]) == _ids(b["train"])


def test_add_markers_grows_the_vocabulary_by_eight() -> None:
    tokenizer = AutoTokenizer.from_pretrained(TINY)
    before = len(tokenizer)
    after = add_markers(tokenizer)
    assert after == len(tokenizer)
    assert after - before == len(MARKERS) == 8
    assert tokenizer.tokenize("[DESC] x")[0] == "[DESC]"


def test_add_markers_is_idempotent() -> None:
    tokenizer = AutoTokenizer.from_pretrained(TINY)
    first = add_markers(tokenizer)
    assert add_markers(tokenizer) == first


def test_build_splits_rejects_a_fold_with_no_training_servers() -> None:
    records = load_corpus(Path("fixtures/corpus/mini-50.jsonl"))
    one_server = [
        r.model_copy(update={"split": "dev", "fold": 0, "server_name": "only"}) for r in records
    ]
    with pytest.raises(ValueError, match="training servers"):
        build_splits(one_server, fold=1)
