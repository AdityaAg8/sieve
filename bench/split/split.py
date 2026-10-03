"""Grouped holdout and 5-fold assignment (PRD §9.7).

Grouping is by `server_name`, not by record: rows from one server share an author, a
register and often whole sentences, so a record-level split leaks the training set into the
evaluation set and every number comes out flattering. Stratification is over the *servers*,
by the label mix each server carries, so the folds stay comparable.

Mutations never get an assignment of their own: a mutation and its host differ by one
sentence, so putting them in different folds is the same leak in a smaller package.
`real_poisoned` is `eval_only` and never carries a fold — it is opened once, by
`bench/eval_final.py`.

**Frozen splits (training plan A4).** Once a corpus is published its assignment is frozen in
`bench/data/split_map.json` (host server -> split, fold). With a map, every mapped server keeps
its split and fold whatever the seed, the corpus size or the labels; a server the map has never
seen goes to dev, on the fold `sha256(server_name) % folds`, and never to the holdout. So adding
servers or relabelling rows can no longer reshuffle the strata and move the holdout (F25).
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path

import numpy as np

from sieve.types import CorpusRecord

DEFAULT_SEED = 42
DEFAULT_HOLDOUT = 0.15
DEFAULT_FOLDS = 5
SPLIT_MAP = Path("bench/data/split_map.json")

#: host server -> (split, fold); fold is None outside dev.
SplitMap = dict[str, tuple[str, int | None]]
_MAPPABLE = {"dev", "holdout"}


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


def new_server_fold(server: str, folds: int = DEFAULT_FOLDS) -> int:
    """The dev fold of a server the split map has never seen: stable, seed-independent."""
    return int(hashlib.sha256(server.encode("utf-8")).hexdigest(), 16) % folds


def split_map_from(records: list[CorpusRecord]) -> SplitMap:
    """The frozen map of an assigned corpus: each host server's split and fold."""
    by_id = {r.id: r for r in records}
    out: SplitMap = {}
    for record in records:
        host = _resolve_host(record, by_id)
        if host.source == "real_poisoned":
            continue
        entry = (record.split, record.fold)
        if out.setdefault(host.server_name, entry) != entry:
            raise ValueError(f"server {host.server_name!r} carries two assignments")
    return out


def write_split_map(mapping: SplitMap, path: Path) -> None:
    servers = {s: {"split": mapping[s][0], "fold": mapping[s][1]} for s in sorted(mapping)}
    payload = {
        "about": "Frozen split assignment per host server (training plan A4). Never edit by hand.",
        "servers": servers,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8", newline="\n")


def load_split_map(path: Path = SPLIT_MAP) -> SplitMap:
    servers = json.loads(path.read_text(encoding="utf-8"))["servers"]
    return {s: (v["split"], v["fold"]) for s, v in servers.items()}


def _assign_frozen(
    records: list[CorpusRecord], mapping: SplitMap, folds: int
) -> list[CorpusRecord]:
    bad = {s for s, (split, _) in mapping.items() if split not in _MAPPABLE}
    if bad:
        raise ValueError(f"split map entries with an unknown split: {sorted(bad)[:5]}")
    by_id = {r.id: r for r in records}
    out: list[CorpusRecord] = []
    for record in records:
        host = _resolve_host(record, by_id)
        if host.source == "real_poisoned":
            update: dict[str, object] = {"split": "eval_only", "fold": None}
        elif host.server_name in mapping:
            split, fold = mapping[host.server_name]
            update = {"split": split, "fold": fold}
        else:
            update = {"split": "dev", "fold": new_server_fold(host.server_name, folds)}
        out.append(record.model_copy(update=update))
    return out


def assign(
    records: list[CorpusRecord],
    *,
    seed: int = DEFAULT_SEED,
    holdout: float = DEFAULT_HOLDOUT,
    folds: int = DEFAULT_FOLDS,
    split_map: SplitMap | None = None,
) -> list[CorpusRecord]:
    """Return copies of `records` carrying `split` and `fold`. The input is not mutated.

    With `split_map`, the map decides (see the module docstring) and `seed`/`holdout` are unused.
    """
    if split_map is not None:
        return _assign_frozen(records, split_map, folds)
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
    dev_by_signature: dict[str, list[str]] = {}
    for signature in sorted(by_signature):
        servers = by_signature[signature]
        order = rng.permutation(len(servers))
        shuffled = [servers[int(i)] for i in order]
        # Per-stratum rounding can give a small stratum zero representation in the sealed
        # split. Forcing one server instead overshoots the global 15% badly on a small corpus
        # (seven servers across three strata would seal 43% of them), so this stays as it is
        # and the gap is recorded as a known limitation rather than traded for a worse one.
        take = int(round(holdout * len(shuffled)))
        holdout_servers.update(shuffled[:take])
        dev_by_signature[signature] = shuffled[take:]

    # Round-robin over the SHUFFLED remainder within each signature: this keeps every fold's
    # label mix comparable *and* is the only place the seed can reach fold membership. Walking
    # the unshuffled sorted list here made `seed` inert except through the holdout draw.
    fold_of: dict[str, int] = {}
    cursor = 0
    for signature in sorted(dev_by_signature):
        for server in dev_by_signature[signature]:
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


Twin = tuple[str, str, float]


def group_and_separate(
    records: list[CorpusRecord], twins: list[Twin]
) -> tuple[list[CorpusRecord], dict[str, object]]:
    """Keep every near-twin pair (from `bench.split.dedup`) in one fold and out of the holdout.

    Training plan C8, finding F34. On an assigned corpus:

    * a dev row twinned with a holdout or eval_only row is dropped, with its mutations; the
      sealed row is never touched, so the holdout ids stay exactly what A4 froze;
    * dev servers joined by a twin pair become one component, and the whole component moves to
      the fold of its alphabetically first server. Dev folds may be re-balanced only for this.
    """
    by_id = {r.id: r for r in records}
    pairs = [(a, b) for a, b, _ in twins if a in by_id and b in by_id]

    children: dict[str, list[str]] = collections.defaultdict(list)
    for r in records:
        if r.mutation_of:
            children[r.mutation_of].append(r.id)
    doomed: set[str] = set()
    for a, b in pairs:
        sides = {by_id[a].split, by_id[b].split}
        if "dev" in sides and sides != {"dev"}:
            doomed.add(a if by_id[a].split == "dev" else b)
    frontier = list(doomed)
    while frontier:
        for child in children.get(frontier.pop(), []):
            if child not in doomed:
                doomed.add(child)
                frontier.append(child)

    server_of = {r.id: _resolve_host(r, by_id).server_name for r in records}
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        if a in doomed or b in doomed or by_id[a].split != "dev" or by_id[b].split != "dev":
            continue
        ra, rb = find(server_of[a]), find(server_of[b])
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    fold_of_server = {server_of[r.id]: r.fold for r in records if r.split == "dev"}
    target = {s: fold_of_server[find(s)] for s in list(parent)}
    regrouped = {
        s: [fold_of_server[s], target[s]] for s in sorted(target) if target[s] != fold_of_server[s]
    }
    out = []
    for r in records:
        if r.id in doomed:
            continue
        server = server_of[r.id]
        if r.split == "dev" and server in regrouped:
            r = r.model_copy(update={"fold": target[server]})
        out.append(r)
    report: dict[str, object] = {
        "dropped_dev_copies": sorted(doomed),
        "regrouped_servers": regrouped,
        "twin_pairs": len(pairs),
    }
    return out, report


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
    parser.add_argument(
        "--split-map", type=Path, default=None, help="honour this frozen map (training plan A4)"
    )
    parser.add_argument(
        "--write-split-map", type=Path, default=None, help="write the resulting assignment's map"
    )
    parser.add_argument(
        "--near-twins",
        type=Path,
        default=None,
        help="bench.split.dedup's twin pairs: one fold per twin component, none across splits",
    )
    parser.add_argument("--report", type=Path, default=Path("bench/results/split_grouping.json"))
    args = parser.parse_args(argv)
    mapping = load_split_map(args.split_map) if args.split_map else None
    assigned = assign(
        _load(args.corpus),
        seed=args.seed,
        holdout=args.holdout,
        folds=args.folds,
        split_map=mapping,
    )
    if args.near_twins:
        pairs = json.loads(args.near_twins.read_text(encoding="utf-8"))["pairs"]
        assigned, report = group_and_separate(assigned, [(a, b, c) for a, b, c in pairs])
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8", newline="\n")
    if args.write_split_map:
        write_split_map(split_map_from(assigned), args.write_split_map)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(r.model_dump_json() + "\n" for r in assigned), encoding="utf-8", newline="\n"
    )
    counts = collections.Counter(r.split for r in assigned)
    print(f"wrote {len(assigned)} records: {dict(counts)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
