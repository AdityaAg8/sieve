"""Frozen splits (training plan A4): `bench/data/split_map.json` decides every known server."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bench.split import split as split_mod
from bench.split.split import assign, load_split_map, split_map_from, write_split_map
from sieve.types import CorpusRecord

from .test_split import _corpus, _record

LABELLED = Path("bench/data/labelled.jsonl")
DEDUPED = Path("bench/data/deduped.jsonl")


def _sealed_ids(records: list[CorpusRecord]) -> set[str]:
    return {r.id for r in records if r.split != "dev"}


def test_a_map_reproduces_the_assignment_it_was_taken_from() -> None:
    first = assign(_corpus(20))
    again = assign(_corpus(20), seed=7, split_map=split_map_from(first))
    assert [(r.id, r.split, r.fold) for r in again] == [(r.id, r.split, r.fold) for r in first]


def test_new_servers_and_relabels_never_move_a_mapped_server() -> None:
    first = assign(_corpus(20))
    grown = _corpus(20) + [_record(f"new{i}", "poisoned", f"brand-new-{i}") for i in range(30)]
    relabelled = [r.model_copy(update={"label": "benign"}) for r in grown]
    again = assign(relabelled, split_map=split_map_from(first))
    assert _sealed_ids(again) == _sealed_ids(first)
    old = {r.id: (r.split, r.fold) for r in first}
    assert all((r.split, r.fold) == old[r.id] for r in again if r.id in old)


def test_a_new_server_lands_in_dev_on_its_name_hash_fold() -> None:
    mapping = split_map_from(assign(_corpus(20)))
    out = assign([_record("x", "benign", "unseen-server")], split_map=mapping)
    expected = int(hashlib.sha256(b"unseen-server").hexdigest(), 16) % 5
    assert (out[0].split, out[0].fold) == ("dev", expected)


def test_a_new_mutation_follows_its_mapped_host() -> None:
    first = assign(_corpus(20))
    sealed = next(r for r in first if r.split != "dev")
    mutation = _record(
        "m1", "poisoned", sealed.server_name, source="synthetic_mutation", mutation_of=sealed.id
    )
    out = assign([*_corpus(20), mutation], split_map=split_map_from(first))
    assert (out[-1].split, out[-1].fold) == (sealed.split, sealed.fold)


def test_real_poisoned_rows_stay_eval_only_under_a_map() -> None:
    mapping = split_map_from(assign(_corpus(20)))
    out = assign([_record("rp", "poisoned", "server-0", source="real_poisoned")], split_map=mapping)
    assert (out[0].split, out[0].fold) == ("eval_only", None)


def test_the_map_round_trips_through_its_file(tmp_path: Path) -> None:
    mapping = split_map_from(assign(_corpus(20)))
    path = tmp_path / "split_map.json"
    write_split_map(mapping, path)
    assert load_split_map(path) == mapping
    text = path.read_text(encoding="utf-8")
    assert "\r" not in text and text.endswith("\n")
    assert list(json.loads(text)["servers"]) == sorted(mapping)


def test_a_map_entry_with_an_unknown_split_is_refused() -> None:
    with pytest.raises(ValueError, match="split"):
        assign(_corpus(2), split_map={"server-0": ("nowhere", None)})  # type: ignore[dict-item]


def test_the_cli_honours_the_map(tmp_path: Path) -> None:
    corpus = tmp_path / "c.jsonl"
    corpus.write_text("".join(r.model_dump_json() + "\n" for r in _corpus(20)), encoding="utf-8")
    first, second, mapping = tmp_path / "a.jsonl", tmp_path / "b.jsonl", tmp_path / "m.json"
    split_mod.main(
        ["--corpus", str(corpus), "--out", str(first), "--write-split-map", str(mapping)]
    )
    split_mod.main(
        ["--corpus", str(corpus), "--out", str(second), "--seed", "9", "--split-map", str(mapping)]
    )
    assert first.read_bytes() == second.read_bytes()


@pytest.mark.skipif(
    not (LABELLED.exists() and DEDUPED.exists() and split_mod.SPLIT_MAP.exists()),
    reason="needs the gitignored corpus and the committed split map",
)
def test_rebuilding_the_real_corpus_keeps_every_assignment() -> None:
    """`make corpus`'s split step on today's deduped rows reproduces labelled.jsonl exactly."""
    labelled = [
        CorpusRecord.model_validate_json(x)
        for x in LABELLED.read_text(encoding="utf-8").splitlines()
        if x
    ]
    deduped = [
        CorpusRecord.model_validate_json(x)
        for x in DEDUPED.read_text(encoding="utf-8").splitlines()
        if x
    ]
    rebuilt = assign(deduped, split_map=load_split_map(split_mod.SPLIT_MAP))
    assert [r.model_dump_json() for r in rebuilt] == [r.model_dump_json() for r in labelled]
    real: tuple[str, ...] = ("public_repo", "wild_fp")
    assert {r.id for r in rebuilt if r.split != "dev" and r.source in real} == {
        r.id for r in labelled if r.split != "dev" and r.source in real
    }
