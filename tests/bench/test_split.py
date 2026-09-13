"""Grouped holdout + folds (PRD §9.7). A server is never split across two groups."""

from __future__ import annotations

import collections

from bench.split.split import assign
from sieve.types import CorpusRecord, Label


def _record(
    record_id: str,
    label: Label,
    server: str,
    *,
    source: str = "public_repo",
    mutation_of: str | None = None,
) -> CorpusRecord:
    return CorpusRecord(
        id=record_id,
        source=source,  # type: ignore[arg-type]
        source_repo="https://github.com/example/fixture",
        source_commit="fixture",
        source_license="MIT",
        capture_method="authored",
        schema_fidelity="exact",
        server_name=server,
        tool={"name": "t", "inputSchema": {"type": "object"}},  # type: ignore[arg-type]
        label=label,
        mutation_of=mutation_of,
        labeller="aditya",
        labelled_at="2026-09-11T00:00:00Z",  # type: ignore[arg-type]
    )


def _corpus(servers: int = 20) -> list[CorpusRecord]:
    labels: list[Label] = ["benign", "imperative_benign", "poisoned"]
    out: list[CorpusRecord] = []
    for s in range(servers):
        for t in range(3):
            out.append(_record(f"s{s}-t{t}", labels[t], f"server-{s}"))
    return out


def test_a_server_never_lands_in_two_splits_or_two_folds() -> None:
    assigned = assign(_corpus())
    by_server: dict[str, set[tuple[str | None, int | None]]] = collections.defaultdict(set)
    for record in assigned:
        by_server[record.server_name].add((record.split, record.fold))
    assert all(len(v) == 1 for v in by_server.values())


def test_about_fifteen_percent_of_servers_are_holdout() -> None:
    assigned = assign(_corpus(20))
    holdout = {r.server_name for r in assigned if r.split == "holdout"}
    assert len(holdout) == 3  # round(0.15 * 20)


def test_every_dev_record_has_a_fold_in_range() -> None:
    assigned = assign(_corpus())
    dev = [r for r in assigned if r.split == "dev"]
    assert dev
    assert all(r.fold is not None and 0 <= r.fold <= 4 for r in dev)


def test_holdout_records_carry_no_fold() -> None:
    assigned = assign(_corpus())
    assert all(r.fold is None for r in assigned if r.split == "holdout")


def test_real_poisoned_rows_stay_eval_only() -> None:
    records = _corpus()
    records.append(_record("rp0", "poisoned", "hashed:abcd1234", source="real_poisoned"))
    assigned = assign(records)
    row = next(r for r in assigned if r.id == "rp0")
    assert row.split == "eval_only" and row.fold is None


def test_a_mutation_inherits_its_hosts_split_and_fold() -> None:
    records = _corpus()
    host = records[0]
    records.append(
        _record(
            "mut0",
            "poisoned",
            "a-different-server-name",
            source="synthetic_mutation",
            mutation_of=host.id,
        )
    )
    assigned = {r.id: r for r in assign(records)}
    assert assigned["mut0"].split == assigned[host.id].split
    assert assigned["mut0"].fold == assigned[host.id].fold


def test_the_same_seed_gives_the_same_assignment() -> None:
    a = {r.id: (r.split, r.fold) for r in assign(_corpus(), seed=42)}
    b = {r.id: (r.split, r.fold) for r in assign(_corpus(), seed=42)}
    c = {r.id: (r.split, r.fold) for r in assign(_corpus(), seed=7)}
    assert a == b
    assert a != c


def test_assign_does_not_mutate_the_input() -> None:
    records = _corpus(6)
    before = [r.model_dump_json() for r in records]
    assign(records)
    assert [r.model_dump_json() for r in records] == before


def test_every_fold_is_used_when_there_are_enough_servers() -> None:
    assigned = assign(_corpus(40))
    folds = {r.fold for r in assigned if r.split == "dev"}
    assert folds == {0, 1, 2, 3, 4}
