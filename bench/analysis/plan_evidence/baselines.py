"""The scratch TF-IDF + LR and YARA AUROCs of plan §6.1 (diagnostics only, never quoted as results).

Binary task (poisoned vs rest) on name + description, the view MCPTox has. The repo's own
TF-IDF (`sieve/model/tfidf.py`, ablation row 2) is a different configuration; `make tfidf-ood`
(C5) runs that one. The keyword regex of the first draft is not ported: Sieve writes no rules.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np

from bench.analysis.plan_evidence.pool import Row

Scorer = Callable[[Sequence[Row]], np.ndarray]


def text(row: Row) -> str:
    return f"{row['name'] or ''} {row['desc'] or ''}"


def is_positive(row: Row) -> int:
    return int(row["cls"] in ("poisoned", "attack_case") or row["src"] == "mcptox")


def fit(train: Sequence[Row]) -> Scorer:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression

    vec = TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True, max_features=50000)
    x = vec.fit_transform([text(r) for r in train])
    model = LogisticRegression(max_iter=2000, class_weight="balanced", C=1.0).fit(
        x, [is_positive(r) for r in train]
    )
    return lambda rows: model.predict_proba(vec.transform([text(r) for r in rows]))[:, 1]


def auroc(pos: Sequence[Row], neg: Sequence[Row], scorer: Scorer) -> float:
    from sklearn.metrics import roc_auc_score

    y = np.array([1] * len(pos) + [0] * len(neg))
    return float(roc_auc_score(y, scorer([*pos, *neg])))


def yara_scorer(rows: Sequence[Row]) -> np.ndarray:
    return np.array([float(r["flag"]) for r in rows])


def dev_oof(dev: Sequence[Row]) -> np.ndarray:
    """Grouped 5-fold OOF scores on dev rows (folds are server-grouped by `bench.split`)."""
    folds = np.array([r["fold"] for r in dev])
    oof = np.zeros(len(dev))
    for k in range(5):
        held = np.flatnonzero(folds == k)
        model = fit([r for r, f in zip(dev, folds, strict=True) if f != k])
        oof[held] = model([dev[i] for i in held])
    return oof


def report(pool: Sequence[Row], extra: Sequence[Row]) -> dict[str, dict[str, float]]:
    from sklearn.metrics import roc_auc_score

    dev = [r for r in pool if r["src"].startswith("corpus:") and r["split"] == "dev"]
    y = np.array([is_positive(r) for r in dev])
    out = {
        "dev pooled OOF": {
            "tfidf": float(roc_auc_score(y, dev_oof(dev))),
            "yara": float(roc_auc_score(y, yara_scorer(dev))),
        }
    }
    model = fit(dev)

    tox = [r for r in pool if r["src"] == "mcptox"]
    seen: set[str] = set()
    clean = []
    for r in pool:
        if r["src"] == "mcptox_clean" and r["desc"] not in seen:
            seen.add(r["desc"])
            clean.append(r)
    corpus_keys = {(r["server"], r["name"]) for r in pool if r["src"].startswith("corpus:")}
    catalog = [
        r for r in pool if r["src"] == "catalog" and (r["server"], r["name"]) not in corpus_keys
    ]
    guard = [r for r in pool if r["src"] == "guardbench"]
    cases: list[tuple[str, Sequence[Row], Sequence[Row]]] = [
        ("MCPTox poisoned vs clean", tox, clean),
        *[
            (f"MCPTox {t} vs clean", [r for r in tox if r["template"] == t], clean)
            for t in ("Template-1", "Template-2", "Template-3")
        ],
        ("MCPTox vs unseen catalog tools", tox, catalog),
        (
            "guardbench tools/list",
            [r for r in guard if r["cls"] == "attack_case"],
            [r for r in guard if r["cls"] == "benign_case"],
        ),
    ]
    for src in ("scannerbench_en", "scannerbench_tr", "mtp"):
        rows = [r for r in extra if r["src"] == src]
        cases.append(
            (
                src,
                [r for r in rows if r["cls"] == "poisoned"],
                [r for r in rows if r["cls"] != "poisoned"],
            )
        )
    for name, pos, neg in cases:
        out[f"{name} ({len(pos)}/{len(neg)})"] = {
            "tfidf": auroc(pos, neg, model),
            "yara": auroc(pos, neg, yara_scorer),
        }
    return out
