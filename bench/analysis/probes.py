"""`make probes`: the four style-leakage probes of the pre-registered gate (plan C4, §5.3).

Each asks whether synthetic rows carry a tell a model could learn instead of the label. All run
on **dev rows** (the rows that will be trained), pooled out-of-fold over a split **grouped by
server**, and each must score ≤ 0.70:

* **source leakage, per stratum** (`imperative`, `benign`): real vs synthetic inside one label,
  TF-IDF + logistic regression -- `bench.analysis.leakage_probe` exactly;
* **surface**: real vs synthetic `imperative_benign` from six shape features alone (length,
  newlines, capitals, sentences, non-ASCII, backticks), so a tell that is not a word still shows;
* **length-only**: `imperative_benign` vs `benign` from the serialised-token length (the model's
  view, plan A3). Length has no natural direction, so the gate reads max(AUROC, 1 - AUROC);
* **transfer**: a real-vs-synthetic model fitted on non-poisoned rows only, scoring poisoned vs
  real `imperative_benign`. High means "sounds synthetic" ≈ "poisoned", the shortcut the
  synthetic positives would teach. The audit's wider reading (poisoned vs every real row) is
  reported beside it.

`--all-splits` reproduces the legacy whole-corpus numbers for history only; the gate never
reads them.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from bench.analysis import leakage_probe
from bench.split.dedup import text_of
from sieve.types import CorpusRecord

GATE = leakage_probe.GATE
SYNTHETIC = leakage_probe.SYNTHETIC
SURFACE_NAMES = ("log_len", "newlines", "caps_ratio", "sentences", "non_ascii", "backticks")
_SENTENCE = re.compile(r"[.!?](\s|$)")

__all__ = ["GATE", "SURFACE_NAMES", "length_probe", "main", "run_all", "surface",
           "surface_probe", "text_of", "transfer_probe"]  # fmt: skip


def surface(text: str) -> list[float]:
    """The six shape features of the 2026-10-02 leakage audit, unchanged."""
    letters = [c for c in text if c.isalpha()]
    return [
        float(np.log1p(len(text))),
        float(text.count("\n")),
        sum(c.isupper() for c in letters) / max(len(letters), 1),
        float(len(_SENTENCE.findall(text))),
        sum(ord(c) > 127 for c in text) / max(len(text), 1),
        float(text.count("`")),
    ]


def _tfidf_model() -> Any:
    # The repo probe's feature space and classifier (`leakage_probe.run`).
    return make_pipeline(
        TfidfVectorizer(sublinear_tf=True, min_df=1, ngram_range=(1, 2)),
        LogisticRegression(max_iter=1000),
    )


def _surface_model() -> Any:
    return make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))


def _result(auroc: float, n: int, **extra: Any) -> dict[str, Any]:
    return {"auroc": round(auroc, 4), "n": n, "gate": GATE, "passed": auroc <= GATE, **extra}


def _grouped_oof(
    features: Any, y: npt.NDArray[np.int_], groups: Sequence[str], make: Callable[[], Any]
) -> npt.NDArray[np.float64]:
    groups_arr = np.asarray(groups)
    n_splits = min(5, len(set(groups_arr.tolist())))
    oof = np.full(len(y), 0.5)
    idx = np.arange(len(y))
    for train, test in GroupKFold(n_splits=n_splits).split(idx, y, groups_arr):
        if len(set(y[train].tolist())) < 2:
            continue  # a fold that saw one class cannot rank; 0.5 is the honest abstention
        model = make()
        if isinstance(features, list):
            model.fit([features[i] for i in train], y[train])
            oof[test] = model.predict_proba([features[i] for i in test])[:, 1]
        else:
            model.fit(features[train], y[train])
            oof[test] = model.predict_proba(features[test])[:, 1]
    return oof


def stratum_probe(records: Sequence[CorpusRecord], label: str) -> dict[str, Any]:
    """Real vs synthetic within one label: `leakage_probe.run` on that stratum."""
    stratum = [
        r.model_copy(update={"label": "imperative_benign"}) for r in records if r.label == label
    ]
    result = leakage_probe.run(stratum)
    return _result(result.auroc, result.n, stratum=label)


def surface_probe(records: Sequence[CorpusRecord]) -> dict[str, Any]:
    stratum = [r for r in records if r.label == "imperative_benign"]
    y = np.array([int(r.source == SYNTHETIC) for r in stratum])
    x = np.array([surface(text_of(r)) for r in stratum])
    oof = _grouped_oof(x, y, [r.server_name for r in stratum], _surface_model)
    return _result(float(roc_auc_score(y, oof)), len(stratum), features=list(SURFACE_NAMES))


def length_probe(records: Sequence[CorpusRecord], lengths: npt.NDArray[Any]) -> dict[str, Any]:
    """`imperative_benign` vs `benign` from length alone; `lengths` aligns with `records`."""
    labels = np.array([r.label for r in records])
    keep = labels != "poisoned"
    auroc = float(roc_auc_score(labels[keep] == "imperative_benign", np.asarray(lengths)[keep]))
    separation = max(auroc, 1.0 - auroc)
    return {
        "auroc": round(auroc, 4),
        "separation": round(separation, 4),
        "n": int(keep.sum()),
        "gate": GATE,
        "passed": separation <= GATE,
        "feature": "serialised-token length (DeBERTa-v3, untruncated)",
    }


def transfer_probe(records: Sequence[CorpusRecord]) -> dict[str, Any]:
    rows = list(records)
    texts = [text_of(r) for r in rows]
    labels = np.array([r.label for r in rows])
    synthetic = np.array([r.source == SYNTHETIC for r in rows])
    groups = np.array([r.server_name for r in rows])
    score = np.full(len(rows), 0.5)
    n_splits = min(5, len(set(groups.tolist())))
    for train, test in GroupKFold(n_splits=n_splits).split(texts, groups=groups):
        fit_rows = [i for i in train if labels[i] != "poisoned"]
        y = synthetic[fit_rows].astype(int)
        if len(set(y.tolist())) < 2:
            continue
        model = _tfidf_model()
        model.fit([texts[i] for i in fit_rows], y)
        score[test] = model.predict_proba([texts[i] for i in test])[:, 1]
    poisoned = labels == "poisoned"
    real = ~synthetic
    primary = poisoned | (real & (labels == "imperative_benign"))
    wide = poisoned | real
    auroc = float(roc_auc_score(poisoned[primary], score[primary]))
    return _result(
        auroc,
        int(primary.sum()),
        versus="real imperative_benign",
        auroc_vs_all_real=round(float(roc_auc_score(poisoned[wide], score[wide])), 4),
    )


def run_all(
    records: Sequence[CorpusRecord],
    *,
    lengths: npt.NDArray[Any] | None,
    all_splits: bool = False,
) -> dict[str, Any]:
    """Every probe on dev rows (or every row, for history). `lengths` aligns with `records`."""
    keep = [all_splits or r.split == "dev" for r in records]
    rows = [r for r, k in zip(records, keep, strict=True) if k]
    out: dict[str, Any] = {
        "rows": "all_splits" if all_splits else "dev",
        "imperative": stratum_probe(rows, "imperative_benign"),
        "benign": stratum_probe(rows, "benign"),
        "surface": surface_probe(rows),
        "length": None
        if lengths is None
        else length_probe(rows, np.asarray(lengths)[np.asarray(keep)]),
        "transfer": transfer_probe(rows),
    }
    probes = [v for v in out.values() if isinstance(v, dict)]
    out["passed"] = all(p["passed"] for p in probes)
    return out


def _token_lengths(records: Sequence[CorpusRecord], config: Path) -> npt.NDArray[Any]:
    import yaml

    from bench.analysis.plan_evidence.text import serialised_token_length
    from sieve.model.data import add_markers

    profile = yaml.safe_load(config.read_text(encoding="utf-8"))["profiles"]["deberta"]
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(profile["base_model"], revision=profile["revision"])  # type: ignore[no-untyped-call]
    add_markers(tok)
    return np.asarray(serialised_token_length(records, tok))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.analysis.probes")
    parser.add_argument("--corpus", type=Path, default=Path("bench/data/labelled.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("bench/results/leakage_probe.json"))
    parser.add_argument("--stats", type=Path, default=Path("bench/results/corpus_stats.json"))
    parser.add_argument("--config", type=Path, default=Path("bench/train.yaml"))
    parser.add_argument("--all-splits", action="store_true", help="legacy whole-corpus numbers")
    parser.add_argument("--no-length", action="store_true", help="skip the tokenizer (tests)")
    ns = parser.parse_args(argv)

    records = [
        CorpusRecord.model_validate_json(line)
        for line in ns.corpus.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    lengths = None if ns.no_length else _token_lengths(records, ns.config)
    result = run_all(records, lengths=lengths, all_splits=ns.all_splits)
    imperative = result["imperative"]
    # `publish.py` reads auroc / n / gate / passed: the imperative probe, and the gate as a whole.
    payload = {"auroc": imperative["auroc"], "n": imperative["n"], "gate": GATE, **result}
    text = json.dumps(payload, indent=2) + "\n"
    print(text, end="")
    if not ns.all_splits:
        ns.out.parent.mkdir(parents=True, exist_ok=True)
        ns.out.write_text(text, encoding="utf-8", newline="\n")
        stats = json.loads(ns.stats.read_text(encoding="utf-8")) if ns.stats.exists() else {}
        stats["leakage_probes"] = result
        ns.stats.parent.mkdir(parents=True, exist_ok=True)
        ns.stats.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0 if result["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
