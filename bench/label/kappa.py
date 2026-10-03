"""Agreement reported three ways, plus blind-vs-generator (PRD §9.6 items 5 and 2).

Three ways because one number hides the thing that matters: overall κ is dominated by the
easy `poisoned` rows, and the boundary the project is actually about lives between `benign`
and `imperative_benign` on *real* text. So the headline is `kappa_real_subset`, and the per
class view is observed agreement plus Gwet's AC1, which does not collapse when one class is
rare.

`--which generator` is **not** an inter-rater κ. It compares the human's blind label against
the generator's recorded intent, and it is published as its own statistic with that caveat.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from bench.label.session import DEFAULT_INTENT_PATH
from sieve.eval.metrics import cohens_kappa, gwet_ac1
from sieve.types import LABELS, CorpusRecord, Label

REAL_SOURCES = frozenset({"public_repo", "wild_fp"})
DEFAULT_OUT = Path("bench/results/agreement.json")

Which = Literal["inter", "retest", "generator"]
Sampler = Literal["simple_random", "stratified"]


class ClassAgreement(BaseModel):
    observed_agreement: float
    ac1: float
    n: int


class KappaReport(BaseModel):
    which: Which
    n: int
    kappa_overall: float
    kappa_real_subset: float  # source in {"public_repo", "wild_fp"} — the headline
    n_real_subset: int
    per_class: dict[Label, ClassAgreement]  # observed agreement + Gwet's AC1 per class
    blind_vs_generator: float | None  # only for which == "generator"
    disagreements: list[tuple[str, Label, Label]]
    sampler: Sampler


def _load_intent(path: Path) -> dict[str, Label]:
    if not path.exists():
        return {}
    out: dict[str, Label] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            # First wins (F40): a later line for the same id can only be a resume or retest.
            out.setdefault(entry["id"], entry["intended_label"])
    return out


def _pairs(
    records: list[CorpusRecord], which: Which, intent_path: Path
) -> list[tuple[CorpusRecord, Label, Label]]:
    """(record, rater A, rater B) triples. A is the reference, B the comparison."""
    if which == "generator":
        intent = _load_intent(intent_path)
        # A row missing from the sidecar was never blind-labelled: skipping it is right,
        # scoring it as a disagreement would invent one.
        return [
            (r, intent[r.id], r.label)
            for r in records
            if r.source == "synthetic_mutation" and r.id in intent
        ]
    return [(r, r.label, r.second_label) for r in records if r.second_label is not None]


def _kappa(pairs: list[tuple[CorpusRecord, Label, Label]]) -> float:
    if not pairs:
        return 0.0
    return cohens_kappa([a for _, a, _ in pairs], [b for _, _, b in pairs])


def _per_class(pairs: list[tuple[CorpusRecord, Label, Label]]) -> dict[Label, ClassAgreement]:
    out: dict[Label, ClassAgreement] = {}
    for label in LABELS:
        if not pairs:
            out[label] = ClassAgreement(observed_agreement=1.0, ac1=1.0, n=0)
            continue
        a = [label if x == label else "other" for _, x, _ in pairs]
        b = [label if y == label else "other" for _, _, y in pairs]
        observed = sum(1 for x, y in zip(a, b, strict=True) if x == y) / len(pairs)
        out[label] = ClassAgreement(
            observed_agreement=observed,
            ac1=gwet_ac1(a, b),
            n=sum(1 for x in a if x == label),
        )
    return out


def report(
    records: list[CorpusRecord],
    *,
    which: Which,
    sampler: Sampler = "simple_random",
    intent_path: Path = DEFAULT_INTENT_PATH,
) -> KappaReport:
    pairs = _pairs(records, which, intent_path)
    real = [p for p in pairs if p[0].source in REAL_SOURCES]
    disagreements = [(r.id, a, b) for r, a, b in pairs if a != b]
    agreement = (len(pairs) - len(disagreements)) / len(pairs) if pairs else 0.0
    return KappaReport(
        which=which,
        n=len(pairs),
        kappa_overall=_kappa(pairs),
        kappa_real_subset=_kappa(real),
        n_real_subset=len(real),
        per_class=_per_class(pairs),
        blind_vs_generator=agreement if which == "generator" else None,
        disagreements=disagreements,
        sampler=sampler,
    )


def _load(path: Path) -> list[CorpusRecord]:
    return [
        CorpusRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bench.label.kappa",
        description="Report agreement three ways, or the blind-vs-generator statistic.",
    )
    parser.add_argument("--which", choices=("inter", "retest", "generator"), required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--sampler", choices=("simple_random", "stratified"), default="simple_random"
    )
    parser.add_argument("--intent-path", type=Path, default=DEFAULT_INTENT_PATH)
    args = parser.parse_args(argv)

    built = report(
        _load(args.corpus),
        which=args.which,
        sampler=args.sampler,
        intent_path=args.intent_path,
    )
    print(built.model_dump_json(indent=2))
    merged = json.loads(args.out.read_text(encoding="utf-8")) if args.out.exists() else {}
    merged[args.which] = built.model_dump()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
