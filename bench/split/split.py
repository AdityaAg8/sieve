"""Grouped holdout and 5-fold assignment (PRD §9.7).

Grouping is by `server_name`, not by record: rows from one server share an author, a
register and often whole sentences, so a record-level split leaks the training set into the
evaluation set and every number comes out flattering. Stratification is over the *servers*,
by the label mix each server carries, so the folds stay comparable.

Mutations never get an assignment of their own: a mutation and its host differ by one
sentence, so putting them in different folds is the same leak in a smaller package.
`real_poisoned` is `eval_only` and never carries a fold — it is opened once, by
`bench/eval_final.py`.
"""

from __future__ import annotations

import argparse
import collections
from pathlib import Path

import numpy as np

from sieve.types import CorpusRecord

DEFAULT_SEED = 42
DEFAULT_HOLDOUT = 0.15
DEFAULT_FOLDS = 5


def _server_signature(records: list[CorpusRecord]) -> str:
    """A server's stratum: the label it carries most, ties broken by label order."""
    counts = collections.Counter(r.label for r in records)
    return max(sorted(counts), key=lambda label: counts[label])


def _resolve_host(record: CorpusRecord, by_id: dict[str, CorpusRecord]) -> CorpusRecord:
    """Follow `mutation_of` to the record whose group decides this one's split and fold."""
    seen: set[str] = set()
    current = record
    while current.mutation_of and current.mutation_of in by_id and current.id not in seen:
        seen.add(current.id)
        current = by_id[current.mutation_of]
    return current


def assign(
    records: list[CorpusRecord],
    *,
    seed: int = DEFAULT_SEED,
    holdout: float = DEFAULT_HOLDOUT,
    folds: int = DEFAULT_FOLDS,
) -> list[CorpusRecord]:
    """Return copies of `records` carrying `split` and `fold`. The input is not mutated."""
    by_id = {r.id: r for r in records}
    groupable = [r for r in records if _resolve_host(r, by_id).source != "real_poisoned"]

    by_server: dict[str, list[CorpusRecord]] = collections.defaultdict(list)
    for record in groupable:
        by_server[_resolve_host(record, by_id).server_name].append(record)

    by_signature: dict[str, list[str]] = collections.defaultdict(list)
    for server in sorted(by_server):
        by_signature[_server_signature(by_server[server])].append(server)

    rng = np.random.default_rng(seed)
    holdout_servers: set[str] = set()
    dev_servers: list[str] = []
    for signature in sorted(by_signature):
        servers = by_signature[signature]
        order = rng.permutation(len(servers))
        shuffled = [servers[int(i)] for i in order]
        take = int(round(holdout * len(shuffled)))
        holdout_servers.update(shuffled[:take])
        dev_servers.extend(shuffled[take:])

    # Round-robin within each signature keeps every fold's label mix comparable.
    fold_of: dict[str, int] = {}
    cursor = 0
    for signature in sorted(by_signature):
        for server in by_signature[signature]:
            if server in holdout_servers:
                continue
            fold_of[server] = cursor % folds
            cursor += 1

    out: list[CorpusRecord] = []
    for record in records:
        host = _resolve_host(record, by_id)
        if host.source == "real_poisoned":
            out.append(record.model_copy(update={"split": "eval_only", "fold": None}))
            continue
        server = host.server_name
        if server in holdout_servers:
            out.append(record.model_copy(update={"split": "holdout", "fold": None}))
        else:
            out.append(record.model_copy(update={"split": "dev", "fold": fold_of[server]}))
    return out


def _load(path: Path) -> list[CorpusRecord]:
    return [
        CorpusRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bench.split.split",
        description="Assign grouped holdout and 5-fold indices by server (PRD §9.7).",
    )
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--holdout", type=float, default=DEFAULT_HOLDOUT)
    parser.add_argument("--folds", type=int, default=DEFAULT_FOLDS)
    args = parser.parse_args(argv)
    assigned = assign(_load(args.corpus), seed=args.seed, holdout=args.holdout, folds=args.folds)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(r.model_dump_json() + "\n" for r in assigned), encoding="utf-8", newline="\n"
    )
    counts = collections.Counter(r.split for r in assigned)
    print(f"wrote {len(assigned)} records: {dict(counts)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
