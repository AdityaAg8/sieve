"""The one-shot holdout evaluation. The only code in the repository that reads a sealed row.

It refuses to run without `SIEVE_FINAL=1` and appends one line to an append-only log every
time it is permitted to run, including a run that finds nothing to evaluate: the log's job is
to let a reader count how many times the seal was broken, not how many times it was productive.

After it runs, no code, prompt, serialisation or synthesis change may follow for that version.
A change means a new version and a new log line (PRD §11.6).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from bench.ablations.run import dataset_version
from bench.stats import EVAL_ONLY_SOURCE
from sieve.eval.metrics import cluster_bootstrap_ci, precision_recall_fpr
from sieve.protocols import Classifier, Judge, Thresholds
from sieve.types import CorpusRecord, Verdict

log = logging.getLogger("bench.eval_final")

SEALED_MESSAGE = "the holdout is opened once per published version; see PRD §11.6"
LOG_FILE = "test_opened.log"
RESULTS_FILE = "holdout.json"
REAL_SOURCES = frozenset({"public_repo", "wild_fp"})
SEALED_SPLITS = frozenset({"holdout", "eval_only"})
DEFAULT_CORPUS = Path("bench/data/labelled.jsonl")
RELEASE_GATE = {"recall_point": 0.95, "recall_lower_bound": 0.90}


def m13_rows[R](rows: Sequence[R]) -> list[R]:
    """The M13 slice: poisoned rows whose source is `real_poisoned`, and nothing else. A
    test-OOD negative (G2) or any other row that lands in the eval-only split stays out."""
    return [
        r
        for r in rows
        if getattr(r, "source", None) == EVAL_ONLY_SOURCE
        and getattr(r, "label", None) == "poisoned"
    ]


class HoldoutSealed(Exception):  # noqa: N818 - named by the lane brief; it is a refusal
    """`bench/eval_final.py` was called without `SIEVE_FINAL=1`."""


def require_unsealed() -> None:
    if os.environ.get("SIEVE_FINAL") != "1":
        raise HoldoutSealed(SEALED_MESSAGE)


def append_log_line(results_dir: Path, *, rows_evaluated: int, reason: str) -> str:
    """Append exactly one line. The file is opened with `"a"` and never truncated."""
    from sieve.model.version import git_sha

    entry = {
        "git_sha": git_sha(short=False),
        "utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "rows_evaluated": rows_evaluated,
        "reason": reason,
    }
    line = json.dumps(entry, sort_keys=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    with (results_dir / LOG_FILE).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
    return line


def _verdicts(
    records: Sequence[CorpusRecord],
    scores: Sequence[float],
    thresholds: Thresholds,
    judge: Judge | None,
) -> list[Verdict]:
    from bench.ablations.run import pipeline_verdicts

    return pipeline_verdicts(records, scores, thresholds, judge)


def _recall_with_lower_bound(
    truth: Sequence[str], scores: Sequence[float], groups: Sequence[str], t_low: float, n_boot: int
) -> dict[str, float | None]:
    if not truth or "poisoned" not in set(truth):
        return {"point": None, "lo": None, "hi": None}

    def stat(y_true: Any, y_score: Any) -> float:
        return precision_recall_fpr(list(y_true), list(y_score), t_low)[1]

    point, lo, hi = cluster_bootstrap_ci(stat, truth, scores, groups, n_boot=n_boot)
    return {"point": point, "lo": lo, "hi": hi}


def _fpr_with_interval(
    truth: Sequence[str], confirmed: Sequence[float], groups: Sequence[str], n_boot: int
) -> dict[str, float | None]:
    if not truth:
        return {"point": None, "lo": None, "hi": None}

    def stat(y_true: Any, y_score: Any) -> float:
        return precision_recall_fpr(list(y_true), list(y_score), 0.5)[2]

    point, lo, hi = cluster_bootstrap_ci(stat, truth, confirmed, groups, n_boot=n_boot)
    return {"point": point, "lo": lo, "hi": hi}


def _breakdown(
    records: Sequence[CorpusRecord],
    scores: Sequence[float],
    confirmed: Sequence[float],
    t_low: float,
    key: Callable[[CorpusRecord], str],
) -> dict[str, dict[str, float]]:
    """One key vocabulary with the ablation matrix, so the model card reads one set of names."""
    from bench.ablations.run import breakdown

    return breakdown(records, [r.label for r in records], scores, confirmed, t_low, key)


def _scores(classifier: Classifier, records: Sequence[CorpusRecord]) -> list[float]:
    from sieve.serialise import serialise
    from sieve.types import LABELS

    if not records:
        return []
    probs = np.asarray(
        classifier.predict_proba([serialise(r.to_tool_record()) for r in records]),
        dtype=np.float64,
    )
    return [float(p) for p in probs[:, LABELS.index("poisoned")]]


def evaluate(
    holdout: Sequence[CorpusRecord],
    eval_only: Sequence[CorpusRecord],
    classifier: Classifier,
    judge: Judge | None,
    *,
    n_boot: int = 1000,
) -> dict[str, Any]:
    """The holdout report. Every interval is a cluster bootstrap resampling servers."""
    thresholds = classifier.thresholds
    scores = _scores(classifier, holdout)
    verdicts = _verdicts(holdout, scores, thresholds, judge)
    confirmed = [1.0 if v == "confirmed" else 0.0 for v in verdicts]
    truth = [r.label for r in holdout]
    groups = [r.server_name for r in holdout]
    real = [i for i, r in enumerate(holdout) if r.source in REAL_SOURCES]

    recall = _recall_with_lower_bound(truth, scores, groups, thresholds.t_low, n_boot)
    eval_scores = _scores(classifier, eval_only)
    eval_verdicts = _verdicts(eval_only, eval_scores, thresholds, judge)
    m13 = {id(r) for r in m13_rows(eval_only)}
    poisoned = [i for i, r in enumerate(eval_only) if id(r) in m13]

    return {
        "rows_evaluated": len(holdout) + len(eval_only),
        "n_holdout": len(holdout),
        "n_eval_only": len(eval_only),
        "model_version": classifier.model_version,
        "judge_model": judge.model if judge is not None else None,
        "t_low": thresholds.t_low,
        "t_high": thresholds.t_high,
        "recall": recall["point"],
        "recall_lcb": recall["lo"],
        "recall_interval": recall,
        "fpr_pipeline_real": _fpr_with_interval(
            [truth[i] for i in real],
            [confirmed[i] for i in real],
            [groups[i] for i in real],
            n_boot,
        ),
        "fpr_pipeline_all_sources": _fpr_with_interval(truth, confirmed, groups, n_boot),
        "per_tier": _breakdown(
            holdout, scores, confirmed, thresholds.t_low, lambda r: r.difficulty or "unscored"
        ),
        "per_concealment": _breakdown(
            holdout, scores, confirmed, thresholds.t_low, lambda r: r.concealment
        ),
        # M13: the `real_poisoned` slice, at Stage 1 and after the whole pipeline.
        "real_poisoned_recall_stage1": (
            sum(1 for i in poisoned if eval_scores[i] >= thresholds.t_low) / len(poisoned)
            if poisoned
            else None
        ),
        "real_poisoned_recall_pipeline": (
            sum(1 for i in poisoned if eval_verdicts[i] == "confirmed") / len(poisoned)
            if poisoned
            else None
        ),
        "release_gate": RELEASE_GATE,
        "release_gate_passed": (
            recall["point"] is not None
            and recall["lo"] is not None
            and recall["point"] >= RELEASE_GATE["recall_point"]
            and recall["lo"] >= RELEASE_GATE["recall_lower_bound"]
        ),
        "limitations": _limitations(holdout, eval_only, recall),
    }


def _limitations(
    holdout: Sequence[CorpusRecord],
    eval_only: Sequence[CorpusRecord],
    recall: Mapping[str, float | None],
) -> str:
    """Limitations derived from what was actually evaluated, not typed by a human.

    The model card refuses to publish with this slot blank, so it is written here from facts
    the run has in hand: how wide the interval is, how many servers backed it, and what the
    holdout does not contain.
    """
    servers = len({r.server_name for r in holdout})
    real = sum(1 for r in holdout if r.source in REAL_SOURCES)
    lines = [
        f"- The holdout is {len(holdout)} row(s) across {servers} server(s); "
        f"{real} of them are real (`public_repo`/`wild_fp`). Every interval below is a cluster "
        "bootstrap over that many clusters, so it is wide by construction.",
    ]
    if recall["point"] is not None and recall["lo"] is not None:
        lines.append(
            f"- Holdout recall is {recall['point']:.3f} with a 95% lower bound of "
            f"{recall['lo']:.3f}; read the lower bound, not the point."
        )
    buckets = sorted({r.concealment for r in holdout})
    lines.append(f"- Concealment buckets present in the holdout: {', '.join(buckets) or 'none'}.")
    if not eval_only:
        lines.append("- The `real_poisoned` slice was empty for this run, so M13 is unmeasured.")
    return "\n".join(lines)


def count_sealed_rows(corpus_path: Path) -> int:
    """How many rows the run is about to open, read before anything else touches them."""
    if not Path(corpus_path).exists():
        return 0
    from sieve.model.data import load_corpus

    return sum(1 for r in load_corpus(Path(corpus_path)) if r.split in SEALED_SPLITS)


def _empty_report(note: str) -> dict[str, Any]:
    return {
        "rows_evaluated": 0,
        "n_holdout": 0,
        "n_eval_only": 0,
        "note": note,
        "limitations": f"- Nothing was evaluated: {note}.",
        "release_gate": RELEASE_GATE,
        "release_gate_passed": False,
    }


def main(
    *,
    results_dir: Path = Path("bench/results"),
    reason: str,
    corpus: Path | None = None,
    classifier: Classifier | None = None,
    judge: Judge | None = None,
    n_boot: int = 1000,
) -> Path:
    """Open the holdout once. Refuses without `SIEVE_FINAL=1`; writes `holdout.json`."""
    require_unsealed()
    results_dir = Path(results_dir)
    corpus_path = corpus if corpus is not None else DEFAULT_CORPUS

    # The log line is written BEFORE anything is read, so a run that crashes half way through
    # the holdout still leaves a record that the seal was broken. `rows_evaluated` is filled in
    # from the corpus, not from the evaluation, for the same reason.
    sealed_rows = count_sealed_rows(corpus_path)
    log_line = append_log_line(results_dir, rows_evaluated=sealed_rows, reason=reason)

    if not corpus_path.exists():
        # Still logged: the seal was broken even though there was nothing behind it.
        log.warning("%s does not exist; nothing to evaluate", corpus_path)
        report = _empty_report(f"no corpus at {corpus_path}")
    else:
        from sieve.model.data import load_corpus

        records = load_corpus(corpus_path)
        holdout = [r for r in records if r.split == "holdout"]
        eval_only = [r for r in records if r.split == "eval_only"]
        if not holdout and not eval_only:
            report = _empty_report(f"no sealed rows in {corpus_path}")
        else:
            if classifier is None:
                from sieve.config import settings
                from sieve.model.infer import load_classifier

                classifier = load_classifier(settings.model_id)
            report = evaluate(holdout, eval_only, classifier, judge, n_boot=n_boot)

    report["reason"] = reason
    report["log_line"] = log_line
    report["dataset_version"] = dataset_version(corpus_path) if corpus_path.exists() else None
    path = results_dir / RESULTS_FILE
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    log.info("wrote %s (%s rows)", path, report["rows_evaluated"])
    return path


def cli(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(prog="python -m bench.eval_final")
    parser.add_argument("--reason", required=True, help='free text, e.g. "v0.1.0 release"')
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path("bench/results"),
        help="exists so the test can point at a temp directory; do not use it otherwise",
    )
    parser.add_argument("--corpus", type=Path, default=None)
    parser.add_argument("--n-boot", type=int, default=1000)
    ns = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    return main(results_dir=ns.results_dir, reason=ns.reason, corpus=ns.corpus, n_boot=ns.n_boot)


__all__ = [
    "SEALED_MESSAGE",
    "HoldoutSealed",
    "append_log_line",
    "cli",
    "count_sealed_rows",
    "evaluate",
    "main",
]

if __name__ == "__main__":  # pragma: no cover
    cli()
