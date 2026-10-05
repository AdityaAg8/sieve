"""The label audits of §4.3 (training plan F3).

* **dev-50 (decision-driving).** Cohen's κ between the human labels on the 50 seeded real dev
  rows and the ensemble's labels. κ ≥ 0.70 → exit 0; below → exit `STOP` (3): stop and ask the
  owner, there is no automatic relabelling path. Until all 50 rows carry a human label it exits
  `NOT_READY` (2). The CI is a server-cluster bootstrap, published beside the point.
* **test-ID (report-only).** Human gold vs ensemble on the real sealed rows: the error rate with
  a Clopper–Pearson and a server-cluster bootstrap 95% CI, and κ with a CI. A row the ensemble
  left undecided counts as an error. It never changes a label, a threshold or a model choice.
  The gold labels are read only through `bench/label/holdout_gold.py` (F4).

Both write into `bench/results/label_audit.json`, keeping the other audit's block.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import beta

from bench.collect.merge_corpus import is_human
from bench.label.sample import dev_audit_ids
from sieve.eval.metrics import cluster_bootstrap, cohens_kappa
from sieve.types import LABELS, CorpusRecord

STOP = 3
NOT_READY = 2
KAPPA_GATE = 0.70
_NONE = "undecided"
_CODES = [*LABELS, _NONE]


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float, float]:
    """(k / n, exact lower, exact upper) for k events in n trials."""
    lo = 0.0 if k == 0 else float(beta.ppf(alpha / 2, k, n - k + 1))
    hi = 1.0 if k == n else float(beta.ppf(1 - alpha / 2, k + 1, n - k))
    return k / n, lo, hi


def _encode(labels: Sequence[str | None]) -> list[float]:
    return [float(_CODES.index(x if x is not None else _NONE)) for x in labels]


def _decode(codes: Any) -> list[str]:
    return [_CODES[int(c)] for c in codes]


def _kappa_stat(human: Any, ensemble_codes: Any) -> float:
    if len(set(human.tolist())) < 2 and len(set(_decode(ensemble_codes))) < 2:
        return float("nan")
    return cohens_kappa(list(human), _decode(ensemble_codes))


def _error_stat(human: Any, ensemble_codes: Any) -> float:
    return float(np.mean([h != e for h, e in zip(human, _decode(ensemble_codes), strict=True)]))


def _agreement(
    human: Sequence[str], ensemble: Sequence[str | None], servers: Sequence[str], n_boot: int
) -> dict[str, Any]:
    codes = _encode(ensemble)
    kappa = cluster_bootstrap(_kappa_stat, human, codes, servers, n_boot=n_boot)
    errors = cluster_bootstrap(_error_stat, human, codes, servers, n_boot=n_boot)
    return {
        "kappa": round(kappa.point, 4),
        "kappa_lo": round(kappa.lo, 4),
        "kappa_hi": round(kappa.hi, 4),
        "kappa_n_undefined": kappa.n_undefined,
        "error_rate": errors.point,
        "cluster_lo": errors.lo,
        "cluster_hi": errors.hi,
    }


def dev50_report(
    corpus: Sequence[CorpusRecord],
    split_corpus: Sequence[CorpusRecord],
    majority: Mapping[str, str | None],
    *,
    n_boot: int,
) -> dict[str, Any]:
    ids = dev_audit_ids(list(split_corpus))
    by_id = {r.id: r for r in corpus}
    done = [i for i in ids if i in by_id and is_human(by_id[i].labeller)]
    report: dict[str, Any] = {"n": len(done), "of": len(ids), "gate": KAPPA_GATE}
    if len(done) < len(ids):
        return {**report, "ready": False, "passed": False}
    human = [by_id[i].label for i in ids]
    stats = _agreement(
        human, [majority.get(i) for i in ids], [by_id[i].server_name for i in ids], n_boot
    )
    return {**report, "ready": True, **stats, "passed": stats["kappa"] >= KAPPA_GATE}


def test_id_report(
    gold: Mapping[str, str],
    majority: Mapping[str, str | None],
    servers: Mapping[str, str],
    *,
    n_boot: int,
) -> dict[str, Any]:
    ids = sorted(gold)
    ensemble = [majority.get(i) for i in ids]
    errors = sum(gold[i] != e for i, e in zip(ids, ensemble, strict=True))
    point, lo, hi = clopper_pearson(errors, len(ids))
    stats = _agreement([gold[i] for i in ids], ensemble, [servers[i] for i in ids], n_boot)
    return {
        "n": len(ids),
        "errors": errors,
        "undecided": sum(e is None for e in ensemble),
        **stats,
        "error_rate": point,
        "cp_lo": lo,
        "cp_hi": hi,
        "report_only": True,
    }


def _load(path: Path) -> list[CorpusRecord]:
    return [
        CorpusRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.label.audit")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--gate", choices=["dev50"])
    which.add_argument("--report", choices=["test-id"])
    parser.add_argument("--corpus", type=Path, default=Path("bench/data/corpus.jsonl"))
    parser.add_argument("--split-corpus", type=Path, default=Path("bench/data/labelled.jsonl"))
    parser.add_argument(
        "--majority", type=Path, default=Path("bench/data/ensemble/majority_v1.json")
    )
    parser.add_argument("--out", type=Path, default=Path("bench/results/label_audit.json"))
    parser.add_argument("--n-boot", type=int, default=1000)
    ns = parser.parse_args(argv)
    majority = json.loads(ns.majority.read_text(encoding="utf-8"))
    out = json.loads(ns.out.read_text(encoding="utf-8")) if ns.out.exists() else {}

    if ns.gate:
        report = dev50_report(_load(ns.corpus), _load(ns.split_corpus), majority, n_boot=ns.n_boot)
        out["dev50"] = report
        code = 0 if report["passed"] else (STOP if report["ready"] else NOT_READY)
    else:
        from bench.label.holdout_gold import load_gold, test_id_rows

        gold = load_gold()
        servers = {r.id: r.server_name for r in test_id_rows(_load(ns.split_corpus))}
        out["test_id"] = test_id_report(gold, majority, servers, n_boot=ns.n_boot)
        code = 0
    ns.out.parent.mkdir(parents=True, exist_ok=True)
    ns.out.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(out.get("dev50" if ns.gate else "test_id"), indent=2))
    if code == NOT_READY:
        print("dev-50: not every audit row has a human label yet; run `make label-dev-audit`.")
    elif code == STOP:
        print(f"dev-50: kappa below {KAPPA_GATE}: STOP and ask the owner.")
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
