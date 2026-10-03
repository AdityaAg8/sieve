"""Agreement samplers (PRD §9.6 item 3).

`simple_random` is the primary sampler and the one whose κ is published. `stratified` is
kept and reported *second*, because a stratified subset distorts prevalence and κ is a
prevalence-sensitive statistic: a stratified κ answers a different question than the one
the reader thinks it answers.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

import numpy as np

from sieve.types import CorpusRecord, Label

DEV_AUDIT_SEED = 20261002
DEV_AUDIT_N = 50
REAL_SOURCES = ("public_repo", "wild_fp")


def simple_random(records: list[CorpusRecord], n: int, seed: int) -> list[str]:
    """Draw `n` record ids uniformly without replacement. Reproducible for a seed."""
    ids = [r.id for r in records]
    rng = np.random.default_rng(seed)
    size = min(n, len(ids))
    chosen = rng.choice(len(ids), size=size, replace=False)
    return [ids[int(i)] for i in chosen]


def stratified(records: list[CorpusRecord], n: int, seed: int) -> list[str]:
    """Draw `n` ids proportionally by label. Reported second; never the headline."""
    rng = np.random.default_rng(seed)
    by_label: dict[Label, list[str]] = collections.defaultdict(list)
    for record in records:
        by_label[record.label].append(record.id)
    total = len(records)
    if total == 0:
        return []
    n = min(n, total)
    quotas = {label: n * len(ids) / total for label, ids in by_label.items()}
    allocated = {label: int(np.floor(q)) for label, q in quotas.items()}
    # Hand out the leftover places to the largest fractional remainders.
    remainder = n - sum(allocated.values())
    order = sorted(quotas, key=lambda lab: quotas[lab] - allocated[lab], reverse=True)
    for label in order[:remainder]:
        allocated[label] += 1
    out: list[str] = []
    for label, ids in by_label.items():
        take = min(allocated[label], len(ids))
        chosen = rng.choice(len(ids), size=take, replace=False)
        out.extend(ids[int(i)] for i in chosen)
    return out


def dev_audit_ids(
    records: list[CorpusRecord], n: int = DEV_AUDIT_N, seed: int = DEV_AUDIT_SEED
) -> list[str]:
    """The dev-50 audit (training plan §4.3): a seeded simple random sample of real dev rows.

    Dev only, so the audit that may stop training never touches a sealed row.
    """
    pool = [r for r in records if r.split == "dev" and r.source in REAL_SOURCES]
    return simple_random(pool, n, seed)


def retest(
    records: list[CorpusRecord], fraction: float, seed: int, exclude_ids: set[str]
) -> list[str]:
    """Draw a fresh `fraction` of the records the inter-rater pass did not already use."""
    remaining = [r for r in records if r.id not in exclude_ids]
    return simple_random(remaining, int(len(remaining) * fraction), seed)


def _load(path: Path) -> list[CorpusRecord]:
    return [
        CorpusRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bench.label.sample",
        description="Draw the agreement subsets: simple random (primary), stratified, retest.",
    )
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--which", choices=("simple_random", "stratified", "retest"))
    parser.add_argument("--n", type=int, default=150)
    parser.add_argument("--fraction", type=float, default=0.15, help="retest only")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--exclude",
        type=Path,
        default=None,
        help="retest only: a JSON list of ids already used for inter-rater agreement",
    )
    args = parser.parse_args(argv)
    records = _load(args.corpus)
    if args.which == "simple_random":
        ids = simple_random(records, args.n, args.seed)
    elif args.which == "stratified":
        ids = stratified(records, args.n, args.seed)
    else:
        exclude = (
            set(json.loads(args.exclude.read_text(encoding="utf-8"))) if args.exclude else set()
        )
        ids = retest(records, args.fraction, args.seed, exclude)
    print(json.dumps(ids, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
