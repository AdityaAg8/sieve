"""val-OOD scoring, report-only (training plan H4, §5.1, §6.5), and the dev dry run of H2 + H3.

**val-OOD** (`bench/data/ood/val_ood.jsonl`, G3, minus G4's dev twins) is unsealed and never
chooses anything: not a model, not a threshold, not a label (CLAUDE.md: every decision uses
pooled OOF). Per source it reports recall and FPR at the frozen `T_low`, AUROC and the score
quantiles, plus the **threshold-transfer diagnostic**: the five fold models of each artefact,
scored at that artefact's frozen thresholds, beside the `FOLD=all` model. scannerbench-tr is its
own stress row. Output: `bench/results/val_ood.json`.

**`--dry-run-on-dev`** rehearses the one-shot opening without opening anything: H2's model
preflight (`eval_final.preflight_models`), H2's scoring and row writers, and H3's
`report_final.build_report` (with the committed preregistration hash), end to end on dev rows.
A fold model scores only its own out-of-fold rows; a `FOLD=all` model scores every dev row,
which is in-sample and labelled so. Nothing is written to `test_opened.log`; the report goes
to `bench/data/_dryrun/` (gitignored).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from bench import eval_final as ef
from bench import report_final as rf
from bench.ablations.run import dataset_version
from bench.collect.ood_rows import OodRow, load_rows
from sieve.eval.metrics import auroc
from sieve.protocols import Classifier, Thresholds
from sieve.serialise import serialise
from sieve.types import LABELS

VAL_ROWS = Path("bench/data/ood/val_ood.jsonl")
DEDUP = Path("bench/results/dedup_cross.json")
OUT = Path("bench/results/val_ood.json")
DRY_RUN_DIR = Path("bench/data/_dryrun/eval")
STRESS = ("scannerbench_tr",)
QUANTILES = (0.05, 0.25, 0.5, 0.75, 0.95)

FoldLoader = Callable[[Path, Thresholds], Classifier]


def _fold_loader(fold_dir: Path, thresholds: Thresholds) -> Classifier:
    from sieve.model.infer import TorchClassifier

    return TorchClassifier(fold_dir, thresholds=thresholds)


def load_val(path: Path = VAL_ROWS, dedup: Path = DEDUP) -> list[OodRow]:
    dropped: set[str] = set()
    if dedup.exists():
        report = json.loads(dedup.read_text(encoding="utf-8"))
        dropped = {i for entry in report["sets"].values() for i in entry.get("dropped", [])}
    return [r for r in load_rows(path) if r.id not in dropped]


def _summary(rows: Sequence[OodRow], p: np.ndarray, t_low: float) -> dict[str, Any]:
    pos = np.array([r.poisoned for r in rows], dtype=bool)
    flagged = p >= t_low
    truth = ["poisoned" if x else "not" for x in pos]
    return {
        "n": len(rows),
        "n_poisoned": int(pos.sum()),
        "recall_at_t_low": float(flagged[pos].mean()) if pos.any() else None,
        "fpr_at_t_low": float(flagged[~pos].mean()) if (~pos).any() else None,
        "auroc": float(auroc(truth, list(p))) if 0 < pos.sum() < len(rows) else None,
        "quantiles": {str(q): float(np.quantile(p, q)) for q in QUANTILES} if len(p) else {},
    }


def summarise(rows: Sequence[OodRow], p: np.ndarray, t_low: float) -> dict[str, Any]:
    sources = sorted({r.source for r in rows})
    out: dict[str, Any] = {}
    for source in sources:
        ix = [i for i, r in enumerate(rows) if r.source == source]
        out[source] = _summary([rows[i] for i in ix], p[ix], t_low)
    main = [i for i, r in enumerate(rows) if r.source not in STRESS]
    out["all_but_stress"] = _summary([rows[i] for i in main], p[main], t_low)
    return out


def _poisoned(classifier: Classifier, rows: Sequence[OodRow], mode: str) -> np.ndarray:
    texts = [serialise(r.tool_record(), mode=mode) for r in rows]  # type: ignore[arg-type]
    probs = np.asarray(classifier.predict_proba(texts), dtype=np.float64)
    return probs[:, LABELS.index("poisoned")]


def score_val(
    models: Sequence[ef.LoadedModel],
    rows: Sequence[OodRow],
    *,
    fold_loader: FoldLoader | None = _fold_loader,
) -> dict[str, Any]:
    out: dict[str, Any] = {"report_only": True, "n_rows": len(rows), "models": {}}
    for m in models:
        t = m.classifier.thresholds
        entry: dict[str, Any] = {
            "family": m.family,
            "seed": m.seed,
            "fold": m.fold,
            "t_low": t.t_low,
            "per_source": summarise(rows, _poisoned(m.classifier, rows, m.mode), t.t_low),
        }
        folds = sorted(m.model_dir.glob("fold[0-9]")) if fold_loader is not None else []
        if folds and fold_loader is not None:
            entry["threshold_transfer"] = {
                f.name: summarise(rows, _poisoned(fold_loader(f, t), rows, m.mode), t.t_low)
                for f in folds
            }
        out["models"][m.version] = entry
    return out


# ------------------------------------------------------------------------------ dry run


def dry_run_on_dev(
    model_dirs: Sequence[Path],
    *,
    corpus: Path = ef.DEFAULT_CORPUS,
    out_dir: Path = DRY_RUN_DIR,
    loader: ef.Loader = ef._default_loader,
    headline: str | None = None,
    n_boot: int = 200,
) -> Path:
    """H2's preflight and writers and H3's report, end to end on dev rows. Opens nothing."""
    models = ef.preflight_models(model_dirs, corpus, out_dir, loader=loader)
    records = [r for r in ef._load_corpus(corpus) if r.split == "dev"]
    rows = [
        ef._sealed_record(
            "holdout_synthetic" if r.is_synthetic else "test_id",
            r,
            r.source == "wild_fp" if not r.is_synthetic else None,
        )
        for r in sorted(records, key=lambda r: r.id)
    ]
    fold_of = {r.id: r.fold for r in records}
    predictions: list[dict[str, Any]] = []
    for m in models:
        own = rows
        if isinstance(m.fold, int) and m.fold >= 0:
            own = [r for r in rows if fold_of[r.id] == m.fold]  # out-of-fold rows only
        predictions += ef.score([m], own)
    report = rf.build_report(
        predictions,
        ef.row_entries(rows, None),
        headline=headline or models[0].version,
        n_boot=n_boot,
        preregistration_sha256=rf.check_preregistration(),
        log_entry={"reason": "dry run on dev (nothing opened)",
                   "dataset_version": dataset_version(corpus)},
    )  # fmt: skip
    report["dry_run"] = {
        "rows": "dev",
        "in_sample": [m.version for m in models if not (isinstance(m.fold, int) and m.fold >= 0)],
    }
    ef.write_jsonl_atomic(out_dir / ef.PREDICTIONS_FILE, predictions)
    path = out_dir / "holdout_dryrun.json"
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def default_model_dirs(root: Path = Path("models")) -> list[Path]:
    """Every calibrated `models/<family>-s<seed>` artefact (finite `t_low` in its config)."""
    out = []
    for d in sorted(root.glob("*-s*")):
        config = d / "sieve_config.json"
        if config.exists():
            t_low = json.loads(config.read_text(encoding="utf-8")).get("t_low")
            if isinstance(t_low, int | float) and np.isfinite(t_low):
                out.append(d)
    return out


def main(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(prog="python -m bench.eval_val_ood")
    parser.add_argument("--model-dirs", type=Path, nargs="*", default=None)
    parser.add_argument("--dry-run-on-dev", action="store_true")
    parser.add_argument("--corpus", type=Path, default=ef.DEFAULT_CORPUS)
    parser.add_argument("--headline", default=None)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--n-boot", type=int, default=200)
    ns = parser.parse_args(argv)
    model_dirs = ns.model_dirs if ns.model_dirs else default_model_dirs()
    if ns.dry_run_on_dev:
        path = dry_run_on_dev(model_dirs, corpus=ns.corpus, headline=ns.headline, n_boot=ns.n_boot)
        print(f"dry run green: {path}")
        return path
    models = ef.preflight_models(model_dirs, ns.corpus, ns.out.parent)
    report = score_val(models, load_val())
    ns.out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote {ns.out} (report-only)")
    return Path(ns.out)


if __name__ == "__main__":  # pragma: no cover
    main()
