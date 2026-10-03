"""`make gate`: the pre-registered synthetic-style gate (training plan E4, §5.3).

Training may start only when all of these hold on the DEV rows of the corpus to be trained:
the four style probes of `bench.analysis.probes` (source leakage on the benign and imperative
strata, surface, transfer) and its length check (as operationalised in the I1 ADR), 0 payload
survival failures, and at least as many minimal pairs as the pre-registered baseline.

If it fails, exactly ONE tightening pass runs: for each failing probe, the 20 top
synthetic-side n-grams of its stratum join the generator lint's banned list, that stratum is
regenerated (seed 0, no seed shopping), the corpus is rebuilt, and the gate re-checks. A second
failure exits `STOP` (3): stop and ask the owner. The pass is recorded in `gate.json`, so a
later run can never spend it again. `--check-only` (`NO_REGENERATE=1`, §13 step 5) only checks.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from bench.analysis.probes import SYNTHETIC, _tfidf_model, run_all
from bench.split.dedup import text_of
from bench.split.pairs import build as build_pairs
from sieve.types import CorpusRecord

STOP = 3
#: The dev minimal-pair count of the corpus on 2026-10-03, before the v1 regeneration.
BASELINE_PAIRS = 480
TOP_NGRAMS = 20
#: The stratum a failing probe regenerates.
STRATUM_OF = {
    "imperative": "imperative_benign",
    "benign": "benign",
    "surface": "imperative_benign",
    "length": "imperative_benign",
    "transfer": "poisoned",
}
EXTRA_BANNED = Path("bench/generate/lint_banned_extra.json")

Loader = Callable[[], list[CorpusRecord]]
Regenerate = Callable[[list[str], list[str]], None]


def evaluate(
    records: Sequence[CorpusRecord],
    *,
    lengths_fn: Callable[[Sequence[CorpusRecord]], Any],
    survives: Callable[[CorpusRecord], bool],
    baseline_pairs: int,
) -> dict[str, Any]:
    """Every gate condition on the dev rows of `records`."""
    dev = [r for r in records if r.split == "dev"]
    probes = run_all(dev, lengths=lengths_fn(dev))
    failures = sorted(r.id for r in dev if r.payload_fields and not survives(r))
    pairs = len(build_pairs(dev))
    failing = [
        name
        for name in STRATUM_OF
        if isinstance(probes.get(name), dict) and not probes[name]["passed"]
    ]
    passed = bool(probes["passed"]) and not failures and pairs >= baseline_pairs
    return {
        "probes": probes,
        "failing_probes": failing,
        "survival_failures": failures,
        "minimal_pairs": {
            "count": pairs,
            "baseline": baseline_pairs,
            "passed": pairs >= baseline_pairs,
        },
        "passed": passed,
    }


def top_ngrams(records: Sequence[CorpusRecord], stratum: str, *, k: int = TOP_NGRAMS) -> list[str]:
    """The `k` n-grams that most say "synthetic" in `stratum`'s dev rows: real vs synthetic
    within the stratum, or, for `poisoned`, synthetic poisoned vs real imperative_benign (the
    transfer probe's comparison). Same TF-IDF + logistic model as the probes."""
    dev = [r for r in records if r.split == "dev"]
    if stratum == "poisoned":
        rows = [r for r in dev if (r.label == "poisoned" and r.source == SYNTHETIC)
                or (r.label == "imperative_benign" and r.source != SYNTHETIC)]  # fmt: skip
    else:
        rows = [r for r in dev if r.label == stratum]
    y = np.array([int(r.source == SYNTHETIC) for r in rows])
    model = _tfidf_model().fit([text_of(r) for r in rows], y)
    names = model.steps[0][1].get_feature_names_out()
    coef = model.steps[1][1].coef_[0]
    order = np.argsort(-coef)
    return [str(names[i]) for i in order[:k] if coef[i] > 0]


def _read(out: Path) -> dict[str, Any]:
    return dict(json.loads(out.read_text(encoding="utf-8"))) if out.exists() else {}


def _write(out: Path, report: dict[str, Any]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")


def run_gate(
    load: Loader,
    regenerate: Regenerate,
    *,
    out: Path,
    check_only: bool,
    lengths_fn: Callable[[Sequence[CorpusRecord]], Any],
    survives: Callable[[CorpusRecord], bool],
    baseline_pairs: int = BASELINE_PAIRS,
) -> int:
    """Check; on failure tighten once (unless check-only or already spent); check again."""
    used = bool(_read(out).get("tightening_used", False))

    def check() -> dict[str, Any]:
        return evaluate(
            load(), lengths_fn=lengths_fn, survives=survives, baseline_pairs=baseline_pairs
        )

    attempts = [check()]
    report: dict[str, Any] = {
        "tightening_used": used,
        "check_only": check_only,
        "attempts": attempts,
    }
    if not attempts[0]["passed"] and not check_only and not used:
        strata = sorted({STRATUM_OF[p] for p in attempts[0]["failing_probes"]})
        records = load()
        banned = sorted({g for s in strata for g in top_ngrams(records, s)})
        report["tightening"] = {"strata": strata, "banned_ngrams": banned}
        report["tightening_used"] = True
        _write(out, {**report, "passed": False, "stop": False, "in_progress": True})
        if strata:
            regenerate(strata, banned)
        attempts.append(check())
    passed = bool(attempts[-1]["passed"])
    report.update({"passed": passed, "stop": not passed})
    _write(out, report)
    return 0 if passed else STOP


# ----------------------------------------------------------------------------------- CLI


def _load(path: Path) -> Loader:
    def load() -> list[CorpusRecord]:
        return [
            CorpusRecord.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    return load


def _regenerate(strata: list[str], banned: list[str]) -> None:
    """The real pass: banned n-grams into the lint, the strata regenerated, the corpus rebuilt."""
    EXTRA_BANNED.write_text(
        json.dumps({"strata": strata, "ngrams": banned}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    python = sys.executable
    subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            python,
            "-m",
            "bench.generate.generate_local",
            "--all",
            "--only-labels",
            *strata,
            "--raw-log",
            "bench/data/synthetic/raw/generate_v1_tightened.jsonl",
        ],  # fmt: skip
        check=True,
    )
    subprocess.run(["make", "corpus"], check=True)  # noqa: S603, S607 - the repo's own target


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.gate")
    parser.add_argument("--corpus", type=Path, default=Path("bench/data/labelled.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("bench/results/gate.json"))
    parser.add_argument("--config", type=Path, default=Path("bench/train.yaml"))
    parser.add_argument(
        "--check-only", action="store_true", help="NO_REGENERATE=1: never regenerate"
    )
    ns = parser.parse_args(argv)
    from bench.analysis.probes import _token_lengths
    from bench.generate.plan import _survival_check

    code = run_gate(
        _load(ns.corpus),
        _regenerate,
        out=ns.out,
        check_only=ns.check_only,
        lengths_fn=lambda records: _token_lengths(records, ns.config),
        survives=_survival_check(ns.config),
    )
    report = _read(ns.out)
    last = report["attempts"][-1]
    print(json.dumps({"passed": report["passed"], "failing_probes": last["failing_probes"],
                      "survival_failures": len(last["survival_failures"]),
                      "minimal_pairs": last["minimal_pairs"]}))  # fmt: skip
    if code == STOP:
        print(
            "gate: STOP. The pre-registered tightening pass is spent or not allowed; ask the owner."
        )
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
