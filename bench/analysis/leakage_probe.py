"""The source-leakage probe, redefined to be falsifiable (PRD §9.5).

The question is whether synthetic rows carry a stylistic tell that a model could learn
instead of learning the label. v2.0 asked it over the whole corpus, where `source` is very
nearly `label` — so the probe just relearned the label, scored high for an uninteresting
reason, and its gate could never fail.

Here it is restricted to the `imperative_benign` stratum, the one place where real and
synthetic rows both exist in quantity. Within that stratum `label` is constant, so anything
the probe finds is style, which is exactly what the gate is about. AUROC is pooled
out-of-fold over a **grouped** split, because rows from one server share a voice and an
ungrouped split would let the probe memorise servers instead of style.

Gate: AUROC <= 0.70. Above that, revise the synthesis style and regenerate.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from pydantic import BaseModel
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

from bench.split.dedup import text_of
from sieve.types import CorpusRecord

GATE = 0.70
MIN_PER_SOURCE = 20
SYNTHETIC = "synthetic_mutation"


class InsufficientStratum(Exception):  # noqa: N818 (name fixed by the lane's interface)
    """Fewer than 20 `imperative_benign` rows of one source: the probe cannot mean anything."""


class LeakageResult(BaseModel):
    auroc: float
    n: int
    gate: float = GATE
    passed: bool


def run(records: list[CorpusRecord], *, seed: int = 0) -> LeakageResult:
    """Predict real vs synthetic within `imperative_benign`. Returns pooled out-of-fold AUROC."""
    stratum = [r for r in records if r.label == "imperative_benign"]
    y = np.array([1 if r.source == SYNTHETIC else 0 for r in stratum])
    if int(y.sum()) < MIN_PER_SOURCE or int((1 - y).sum()) < MIN_PER_SOURCE:
        raise InsufficientStratum(
            f"imperative_benign stratum has {int(y.sum())} synthetic and "
            f"{int((1 - y).sum())} real rows; {MIN_PER_SOURCE} of each are required"
        )
    texts = [text_of(r) for r in stratum]
    groups = np.array([r.server_name for r in stratum])

    n_groups = len(set(groups.tolist()))
    if n_groups < 2:
        raise InsufficientStratum(
            f"the imperative_benign stratum spans {n_groups} server(s); a grouped split needs "
            "at least two, or the probe would be scored on the servers it trained on"
        )
    n_splits = min(5, n_groups)
    oof = np.zeros(len(stratum), dtype=float)
    for train, test in GroupKFold(n_splits=n_splits).split(texts, y, groups):
        if len(set(y[train].tolist())) < 2:
            # A fold that saw one source only cannot rank; 0.5 is the honest abstention.
            oof[test] = 0.5
            continue
        vectoriser = TfidfVectorizer(sublinear_tf=True, min_df=1, ngram_range=(1, 2))
        x_train = vectoriser.fit_transform([texts[i] for i in train])
        x_test = vectoriser.transform([texts[i] for i in test])
        # lbfgs is deterministic, so `random_state` would be inert; the seed belongs to the
        # caller's reproducibility contract and is recorded, not passed somewhere it is ignored.
        model = LogisticRegression(max_iter=1000)
        model.fit(x_train, y[train])
        oof[test] = model.predict_proba(x_test)[:, 1]

    auroc = float(roc_auc_score(y, oof))
    return LeakageResult(auroc=auroc, n=len(stratum), passed=auroc <= GATE)


def _load(path: Path) -> list[CorpusRecord]:
    return [
        CorpusRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bench.analysis.leakage_probe",
        description="Within-stratum real-vs-synthetic probe over imperative_benign rows.",
    )
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("bench/results/leakage_probe.json"))
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    result = run(_load(args.corpus), seed=args.seed)
    print(result.model_dump_json(indent=2))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0 if result.passed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
