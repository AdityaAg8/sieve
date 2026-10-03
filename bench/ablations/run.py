"""The fourteen-row ablation matrix (PRD §11.5), computed offline from committed artefacts.

Every number here comes from pooled out-of-fold predictions over `split == "dev"` rows, the
recorded judge transcript and the recorded baseline flags. Nothing reaches the network and
nothing reads a `holdout` or `eval_only` row: that is what makes `make bench` reproducible on a
clean clone with no API key (M6a), and it is enforced rather than documented — the run installs
a socket guard and refuses to start with `SIEVE_RERECORD` set.

The judge is taken through the `Judge` protocol and supplied by an injectable factory, so this
module imports no lane package; the concrete `ReplayJudge` is resolved inside `main`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import socket
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
import yaml
from pydantic import BaseModel, ConfigDict

from sieve.eval.metrics import (
    cluster_bootstrap_ci,
    macro_f1_present,
    mcnemar_exact,
    paired_bootstrap_diff,
    precision_recall_fpr,
)
from sieve.model import hygiene
from sieve.model import thresholds as th
from sieve.protocols import Classifier, Judge, JudgeOutput, Thresholds
from sieve.serialise import serialise
from sieve.types import LABELS, CorpusRecord, Label, ScannerFinding, Verdict

log = logging.getLogger("bench.ablations")

DEFAULT_SEEDS: tuple[int, ...] = (0, 1, 2)
REAL_SOURCES: frozenset[str] = frozenset({"public_repo", "wild_fp"})

#: A boolean detector emits 0 or 1, so its decision point is the midpoint. This is NOT an
#: operating point: calibrated thresholds only ever come from `thresholds.select` or the
#: artefact's `sieve_config.json` (PRD §11.4).
BINARY_DECISION = 0.5

TWO_CLASS_LABELS: tuple[str, str] = ("not_poisoned", "poisoned")

_JUDGE_VERDICT: dict[str, Verdict] = {
    "confirmed": "confirmed",
    "dismissed": "dismissed",
    "uncertain": "needs_review",
}

HYGIENE_NOTE = (
    "If the ten-line unicode detector beats the encoder on a concealment bucket, that is the "
    "honest result and it belongs in preprocessing, not in the model."
)


class MatrixError(ValueError):
    """The matrix file, or an artefact a row needs, is not usable."""


class InsufficientSeeds(ValueError):  # noqa: N818 - named by the lane brief (Task 3.11)
    """An encoder row was asked for with fewer than three seeds.

    Single-seed numbers are never published (PRD §11.3).
    """


class RowSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row: int
    name: str
    classifier: str  # "tfidf" | "unicode_hygiene" | "baseline" | "none" | "models/<profile>-s*"
    judge: Literal["none", "claude-haiku-4-5", "claude-sonnet-5", "all"] = "none"
    mode: Literal["full", "description_only"] = "full"
    num_labels: int = 3
    scanner_features: bool = False
    breakdown_of: list[int] = []
    minimal_pairs: bool = False
    #: Score by loading the model and re-running inference instead of reading committed
    #: OOF arrays. In-sample over dev; off for every committed row.
    reinference: bool = False
    note: str | None = None

    @property
    def seeded(self) -> bool:
        """True when the row has a seed dimension, i.e. it names a trained encoder artefact."""
        return self.classifier not in {"tfidf", "unicode_hygiene", "baseline", "none"}


ClassifierFactory = Callable[[RowSpec, int], Classifier]
JudgeFactory = Callable[[str], Judge]


@dataclass
class Inputs:
    """Everything a run reads. Each is a committed artefact; a missing one is an error."""

    records: list[CorpusRecord]
    baseline_rows: dict[str, dict[str, Any]]
    minimal_pairs: list[dict[str, Any]] | None = None
    judge_usage: list[dict[str, Any]] = field(default_factory=list)
    scanner_findings_by_id: dict[str, list[ScannerFinding]] = field(default_factory=dict)
    dataset_version: str | None = None


# --------------------------------------------------------------------------- offline guard


def require_offline() -> None:
    """Refuse to start a bench run that could re-record or phone home."""
    if os.environ.get("SIEVE_RERECORD"):
        raise MatrixError(
            "SIEVE_RERECORD is set; `make bench` replays committed artefacts and never "
            "records. Unset it, or run the recorder deliberately from "
            "bench.analysis.judge_agreement."
        )


@contextmanager
def no_network() -> Iterator[None]:
    """Make any outbound connection during the run an error rather than a silent dependency."""
    original = socket.socket.connect

    def refuse(self: socket.socket, address: Any) -> None:
        raise MatrixError(
            f"the ablation matrix attempted a network connection to {address!r}; every row is "
            "computed from committed artefacts (M6a)"
        )

    socket.socket.connect = refuse  # type: ignore[method-assign]
    try:
        yield
    finally:
        socket.socket.connect = original  # type: ignore[method-assign]


# ------------------------------------------------------------------------------- loading


def load_matrix(path: Path) -> list[RowSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = raw.get("rows")
    if not rows:
        raise MatrixError(f"{path} has no rows")
    specs = [RowSpec.model_validate(row) for row in rows]
    numbers = [s.row for s in specs]
    if len(set(numbers)) != len(numbers):
        raise MatrixError(f"{path} repeats a row number: {sorted(numbers)}")
    return specs


def load_baseline_rows(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        raise MatrixError(f"{path} is missing; run `make baseline` (L2) before the ablation matrix")
    return {str(row["id"]): row for row in json.loads(path.read_text(encoding="utf-8"))}


def load_minimal_pairs(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise MatrixError(f"{path} is missing; row 14 reports minimal-pair accuracy (M11)")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload["pairs"] if isinstance(payload, dict) else payload)


def load_judge_usage(path: Path | None) -> list[dict[str, Any]]:
    """The recorded token usage, for the cost column. Absent means the column is null."""
    if path is None or not path.exists():
        return []
    usage: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            if entry.get("usage"):
                usage.append({"model": entry.get("model"), "usage": entry["usage"]})
    return usage


# -------------------------------------------------------------------------------- scoring


@dataclass
class Scored:
    scores: list[float]
    thresholds: Thresholds
    predicted: list[str] | None
    head_labels: tuple[str, ...]


def _texts(spec: RowSpec, records: Sequence[CorpusRecord], inputs: Inputs) -> list[str]:
    findings = inputs.scanner_findings_by_id if spec.scanner_features else {}
    return [
        serialise(r.to_tool_record(), mode=spec.mode, scanner_findings=findings.get(r.id))
        for r in records
    ]


def _usable(thresholds: Thresholds) -> bool:
    return not (np.isnan(thresholds.t_low) or np.isnan(thresholds.t_high))


def _binary(point: float) -> Thresholds:
    return Thresholds(
        t_low=point,
        t_high=point,
        temperature=1.0,
        val_recall_at_t_low=float("nan"),
        val_precision_at_t_high=float("nan"),
        routing_rate=0.0,
    )


def seed_dir(classifier: str, seed: int) -> Path:
    """`models/<profile>-s*` names a family; one seed is one directory."""
    return Path(classifier.replace("*", str(seed)))


def _scored_from_probs(probs: npt.NDArray[np.float64], thresholds: Thresholds) -> Scored:
    two_class = probs.shape[1] == 2
    head = TWO_CLASS_LABELS if two_class else LABELS
    scores = [float(p) for p in probs[:, -1 if two_class else LABELS.index("poisoned")]]
    predicted = [head[int(i)] for i in probs.argmax(axis=1)]
    return Scored(scores, thresholds, predicted, head)


def pooled_oof_scores(model_dir: Path, records: Sequence[CorpusRecord]) -> Scored:
    """Calibrated probabilities for one (architecture, seed), from committed OOF arrays.

    This is the whole point of the lane: a row named `oof_*` is scored by the fold's model on
    rows that fold never saw. Re-running `predict_proba` with the pooled artefact would score
    dev rows in-sample and every headline number would be optimistic under an out-of-fold name.
    Thresholds come from the artefact via `thresholds.load`, which raises on a null.
    """
    from sieve.model.calibrate import PoolError, apply, pool_oof

    expected = [r.id for r in records]
    try:
        logits, _, ids = pool_oof([Path(model_dir)], expected_ids=expected)
    except PoolError as exc:
        raise MatrixError(str(exc)) from exc
    thresholds = th.load(Path(model_dir))
    probs = apply(logits, thresholds.temperature)
    # `oof_ids.json` is fold-concatenated; every metric downstream is positional over `records`.
    position = {record_id: index for index, record_id in enumerate(ids)}
    order = np.asarray([position[r.id] for r in records], dtype=np.int64)
    return _scored_from_probs(probs[order], thresholds)


def tfidf_pooled_oof(
    spec: RowSpec, records: Sequence[CorpusRecord], inputs: Inputs, *, n_boot: int
) -> Scored:
    """Fit the TF-IDF baseline per fold and pool its held-out probabilities (row 2).

    Row 2 must be comparable to row 4, so it uses the same five folds and the same threshold
    procedure. Nothing is loaded from the Hub: `TfidfClassifier` is fitted here, in process.
    """
    from sieve.model.data import build_splits, texts_and_labels
    from sieve.model.tfidf import TfidfClassifier

    folds = sorted({r.fold for r in records if r.fold is not None})
    if not folds:
        raise MatrixError(
            "row 2 needs fold assignments on the dev records; run bench.split.assign first"
        )
    findings = inputs.scanner_findings_by_id if spec.scanner_features else None
    by_id: dict[str, npt.NDArray[np.float64]] = {}
    for fold in folds:
        splits = build_splits(records, fold=fold, mode=spec.mode, scanner_findings_by_id=findings)
        train_texts, train_labels = texts_and_labels(splits["train"])
        early_texts, early_labels = texts_and_labels(splits["early_stop"])
        model = TfidfClassifier().fit(train_texts + early_texts, train_labels + early_labels)
        held_texts, _ = texts_and_labels(splits["oof"])
        for record_id, row in zip(
            splits["oof"]["id"], model.predict_proba(held_texts), strict=True
        ):
            by_id[str(record_id)] = row

    missing = [r.id for r in records if r.id not in by_id]
    if missing:
        raise MatrixError(f"row 2 produced no pooled prediction for {missing[:5]}")
    probs = np.asarray([by_id[r.id] for r in records], dtype=np.float64)
    if spec.num_labels == 2:
        probs = _two_class_block(probs)
    thresholds = th.select(
        probs if probs.shape[1] == 3 else _widen(probs),
        [_three_class_id(r.label) for r in records],
        [r.server_name for r in records],
        temperature=1.0,
        n_boot=n_boot,
    )
    return _scored_from_probs(probs, thresholds)


def score_row(
    spec: RowSpec,
    records: Sequence[CorpusRecord],
    inputs: Inputs,
    *,
    classifier_factory: ClassifierFactory,
    seed: int,
    n_boot: int,
) -> Scored:
    if spec.scanner_features and not inputs.scanner_findings_by_id:
        raise MatrixError(
            f"row {spec.row} asks for scanner features but no scanner findings were supplied; "
            "load bench/results/baseline_raw.json before the run"
        )
    if spec.classifier == "unicode_hygiene":
        scores = [hygiene.score(r.to_tool_record()) for r in records]
        return Scored(scores, _binary(BINARY_DECISION), None, LABELS)
    if spec.classifier == "baseline":
        missing = [r.id for r in records if r.id not in inputs.baseline_rows]
        if missing:
            raise MatrixError(
                f"baseline_raw.json has no row for {len(missing)} record(s): {missing[:5]}"
            )
        scores = [1.0 if inputs.baseline_rows[r.id]["flagged"] else 0.0 for r in records]
        return Scored(scores, _binary(BINARY_DECISION), None, LABELS)
    if spec.classifier == "none":
        # Row 8: no Stage 1 at all, so every row lands in the abstain band and reaches the judge.
        everything_routes = Thresholds(0.0, 2.0, 1.0, float("nan"), float("nan"), 1.0)
        return Scored([0.5] * len(records), everything_routes, None, LABELS)
    if spec.classifier == "tfidf":
        return tfidf_pooled_oof(spec, records, inputs, n_boot=n_boot)
    if spec.reinference:
        # The escape hatch, off by default and never used by a committed row: score with a
        # loaded model instead of committed OOF arrays. In-sample over dev, so the row's own
        # `note` has to say so.
        model = classifier_factory(spec, seed)
        probs = np.asarray(model.predict_proba(_texts(spec, records, inputs)), dtype=np.float64)
        return _scored_from_probs(probs, model.thresholds)
    return pooled_oof_scores(seed_dir(spec.classifier, seed), records)


def _widen(probs: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """A two-class block as three columns, so `thresholds.select` reads one shape."""
    out = np.zeros((probs.shape[0], 3), dtype=np.float64)
    out[:, 0] = probs[:, 0]
    out[:, 2] = probs[:, 1]
    return out


def _two_class_block(probs: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """Three columns to the two-class head of ablation 12: `not_poisoned`, `poisoned`."""
    poisoned = probs[:, LABELS.index("poisoned")]
    return np.stack([1.0 - poisoned, poisoned], axis=1)


def _three_class_id(label: Label) -> int:
    return LABELS.index(label)


def scanner_findings_from_baseline(
    baseline_rows: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[ScannerFinding]]:
    """`[SCANNER] cisco_yara:<rule_id>` lines for ablation 9, from the recorded flags.

    The rule ids are the only part of a scanner finding the serialisation renders (PRD §11.2),
    and `baseline_raw.json` is the committed record of them, so row 9 needs no second artefact.
    """
    out: dict[str, list[ScannerFinding]] = {}
    for record_id, row in baseline_rows.items():
        rule_ids = list(row.get("rule_ids") or [])
        if not rule_ids:
            continue
        out[str(record_id)] = [
            ScannerFinding(
                scanner="cisco_yara",
                scanner_version=str(row.get("scanner_version") or "replayed"),
                rule_id=str(rule_id),
                severity=None,
                message=None,
                raw={},
            )
            for rule_id in rule_ids
        ]
    return out


def dataset_version(corpus_path: Path) -> str:
    """`<filename>@<sha256[:12]>` of the corpus file. Deterministic, and it names what it is."""
    digest = hashlib.sha256(Path(corpus_path).read_bytes()).hexdigest()[:12]
    return f"{Path(corpus_path).name}@{digest}"


# ------------------------------------------------------------------------------- pipeline


def pipeline_verdicts(
    records: Sequence[CorpusRecord],
    scores: Sequence[float],
    thresholds: Thresholds,
    judge: Judge | None,
) -> list[Verdict]:
    """Auto-dismiss below `t_low`, auto-confirm at/above `t_high`, route the band."""
    verdicts: list[Verdict] = []
    for record, score in zip(records, scores, strict=True):
        if score < thresholds.t_low:
            verdicts.append("dismissed")
        elif score >= thresholds.t_high:
            verdicts.append("confirmed")
        elif judge is None:
            verdicts.append("needs_review")
        else:
            answer = judge.judge(record.to_tool_record(), score)
            verdicts.append(
                _JUDGE_VERDICT[answer.verdict]
                if isinstance(answer, JudgeOutput)
                else "needs_review"
            )
    return verdicts


def _confirmed_flags(verdicts: Sequence[Verdict]) -> list[float]:
    return [1.0 if v == "confirmed" else 0.0 for v in verdicts]


# -------------------------------------------------------------------------------- metrics


def _ci(
    stat_kind: Literal["recall", "fpr"],
    truth: Sequence[str],
    scores: Sequence[float],
    groups: Sequence[str],
    threshold: float,
    n_boot: int,
) -> dict[str, float]:
    index = 1 if stat_kind == "recall" else 2

    def stat(y_true: Any, y_score: Any) -> float:
        return precision_recall_fpr(list(y_true), list(y_score), threshold)[index]

    point, lo, hi = cluster_bootstrap_ci(stat, truth, scores, groups, n_boot=n_boot)
    return {"point": point, "lo": lo, "hi": hi}


#: The one key vocabulary for a per-bucket breakdown. `bench.eval_final` imports `breakdown`
#: rather than repeating it, and `sieve.model.push._table` reads exactly these names.
BREAKDOWN_KEYS: tuple[str, ...] = (
    "n",
    "recall_at_t_low",
    "fpr_stage1_at_t_low",
    "fpr_pipeline",
)


def breakdown(
    records: Sequence[CorpusRecord],
    truth: Sequence[str],
    scores: Sequence[float],
    confirmed: Sequence[float],
    t_low: float,
    key: Callable[[CorpusRecord], str],
) -> dict[str, dict[str, float]]:
    """Per-bucket recall, Stage-1 FPR and pipeline FPR. Shared by the matrix and the holdout."""
    buckets: dict[str, list[int]] = {}
    for index, record in enumerate(records):
        buckets.setdefault(key(record), []).append(index)
    out: dict[str, dict[str, float]] = {}
    for bucket, indices in sorted(buckets.items()):
        bucket_truth = [truth[i] for i in indices]
        _, recall, fpr = precision_recall_fpr(bucket_truth, [scores[i] for i in indices], t_low)
        _, _, fpr_pipeline = precision_recall_fpr(
            bucket_truth, [confirmed[i] for i in indices], BINARY_DECISION
        )
        out[bucket] = {
            "n": float(len(indices)),
            "recall_at_t_low": recall,
            "fpr_stage1_at_t_low": fpr,
            "fpr_pipeline": fpr_pipeline,
        }
    return out


def _fp_reduction(
    truth: Sequence[str],
    sieve_confirmed: Sequence[float],
    baseline_flagged: Sequence[float],
    groups: Sequence[str],
    n_boot: int,
) -> dict[str, float | None]:
    if not truth:
        return {"point": None, "lo": None, "hi": None, "mcnemar_p": None, "relative": None}
    point, lo, hi = paired_bootstrap_diff(
        truth,
        baseline_flagged,
        BINARY_DECISION,
        sieve_confirmed,
        BINARY_DECISION,
        groups,
        n_boot=n_boot,
    )
    _, _, baseline_fpr = precision_recall_fpr(truth, baseline_flagged, BINARY_DECISION)
    correct_sieve = [
        (c >= BINARY_DECISION) == (t == "poisoned")
        for c, t in zip(sieve_confirmed, truth, strict=True)
    ]
    correct_baseline = [
        (b >= BINARY_DECISION) == (t == "poisoned")
        for b, t in zip(baseline_flagged, truth, strict=True)
    ]
    b = sum(1 for s, base in zip(correct_sieve, correct_baseline, strict=True) if s and not base)
    c = sum(1 for s, base in zip(correct_sieve, correct_baseline, strict=True) if base and not s)
    return {
        "point": point,
        "lo": lo,
        "hi": hi,
        "mcnemar_p": mcnemar_exact(b, c),
        # Absolute FPR difference above; M2 is stated as a relative reduction, so both are here.
        "relative": (point / baseline_fpr) if baseline_fpr else None,
    }


def minimal_pair_accuracy(
    pairs: Sequence[dict[str, Any]], predicted_by_id: dict[str, str]
) -> float | None:
    """Share of (host, mutation) pairs where BOTH sides are classified correctly (M11)."""
    scored = 0
    correct = 0
    for pair in pairs:
        host, mutation = str(pair["host_id"]), str(pair["mutation_id"])
        if host not in predicted_by_id or mutation not in predicted_by_id:
            continue
        scored += 1
        if predicted_by_id[host] != "poisoned" and predicted_by_id[mutation] == "poisoned":
            correct += 1
    return correct / scored if scored else None


def _cost_per_50(routing_rate: float, usage: Sequence[dict[str, Any]]) -> float | None:
    if not usage:
        return None
    try:
        from anthropic.types import Usage

        from sieve.judge.cost import estimate
    except ImportError:  # pragma: no cover - the judge lane is optional at import time
        return None
    costs = [estimate(Usage.model_validate(u["usage"]), str(u["model"])) for u in usage]
    return float(np.mean(costs) * routing_rate * 50)


# ------------------------------------------------------------------------------ one row


def evaluate_row(
    spec: RowSpec,
    inputs: Inputs,
    *,
    classifier_factory: ClassifierFactory,
    judge_factory: JudgeFactory | None,
    seeds: Sequence[int],
    n_boot: int,
) -> dict[str, Any]:
    records = inputs.records

    row_seeds = tuple(seeds) if spec.seeded else (0,)
    if spec.seeded and len(row_seeds) < 3:
        raise InsufficientSeeds(
            f"row {spec.row} names an encoder artefact and was given {len(row_seeds)} seed(s); "
            "mean and std across three seeds are required and a single-seed number is never "
            "published (PRD §11.3)"
        )

    judge: Judge | None = None
    if spec.judge != "none":
        if judge_factory is None:
            raise MatrixError(f"row {spec.row} needs a judge but no judge factory was supplied")
        judge = judge_factory(spec.judge)

    # Training plan D5: every thresholded number is computed per seed, at THAT seed's own
    # operating point, then reported as the mean across seeds with the sample std (ddof=1) and
    # the per-seed values. Reading every number off the first seed's thresholds hid the seed
    # spread of everything except macro-F1.
    blocks = []
    for seed in row_seeds:
        scored = score_row(
            spec, records, inputs, classifier_factory=classifier_factory, seed=seed, n_boot=n_boot
        )
        blocks.append(_seed_block(spec, records, inputs, scored, judge, seed=seed, n_boot=n_boot))

    macro = [b["macro_f1"] for b in blocks if b["macro_f1"] is not None]
    present = sorted({c for b in blocks for c in b["classes_present"]})
    result: dict[str, Any] = {
        "row": spec.row,
        "name": spec.name,
        "classifier": spec.classifier,
        "judge": spec.judge,
        "mode": spec.mode,
        "num_labels": spec.num_labels,
        "scanner_features": spec.scanner_features,
        "seeds": list(row_seeds),
        "n": len(records),
        **{key: _aggregate([b[key] for b in blocks]) for key in _SEED_KEYS},
        "macro_f1_mean": float(np.mean(macro)) if macro else None,
        "macro_f1_std": _sample_std(macro),
        "macro_f1_per_seed": macro,
        "classes_present": present,
        "cost_usd_per_50": (
            _cost_per_50(float(np.mean([b["routing_rate"] for b in blocks])), inputs.judge_usage)
            if judge
            else None
        ),
        "p95_ms": _p95_ms(spec, row_seeds),
        "breakdown_of": spec.breakdown_of,
        "note": spec.note,
        "per_seed": blocks,
    }
    if spec.classifier == "unicode_hygiene":
        result["note"] = spec.note or HYGIENE_NOTE
    return result


#: The per-seed keys `evaluate_row` aggregates across seeds.
_SEED_KEYS: tuple[str, ...] = (
    "t_low",
    "t_high",
    "fpr_stage1_at_t_low",
    "fpr_pipeline",
    "oof_recall_at_t_low",
    "fp_reduction_vs_baseline",
    "fp_reduction_all_sources",
    "routing_rate",
    "per_tier",
    "per_concealment",
    "per_source",
    "minimal_pair_accuracy",
)


def _seed_block(
    spec: RowSpec,
    records: Sequence[CorpusRecord],
    inputs: Inputs,
    head: Scored,
    judge: Judge | None,
    *,
    seed: int,
    n_boot: int,
) -> dict[str, Any]:
    """Everything thresholded for one seed, at that seed's own thresholds."""
    truth = [r.label for r in records]
    groups = [r.server_name for r in records]
    verdicts = pipeline_verdicts(records, head.scores, head.thresholds, judge)
    confirmed = _confirmed_flags(verdicts)
    band = np.asarray(head.scores)
    routing_rate = float(np.mean((band >= head.thresholds.t_low) & (band < head.thresholds.t_high)))
    real = [i for i, r in enumerate(records) if r.source in REAL_SOURCES]
    baseline_flags = [
        1.0 if inputs.baseline_rows.get(r.id, {}).get("flagged") else 0.0 for r in records
    ]
    macro: float | None = None
    present: list[str] = []
    if head.predicted is not None:
        macro, present = macro_f1_present(
            [s if s in head.head_labels else _collapse(s) for s in truth],
            head.predicted,
            labels=list(head.head_labels),
        )
    t_low = head.thresholds.t_low
    block: dict[str, Any] = {
        "seed": seed,
        "t_low": t_low,
        "t_high": head.thresholds.t_high,
        "fpr_stage1_at_t_low": _ci("fpr", truth, head.scores, groups, t_low, n_boot),
        "fpr_pipeline": _ci("fpr", truth, confirmed, groups, BINARY_DECISION, n_boot),
        "oof_recall_at_t_low": _ci("recall", truth, head.scores, groups, t_low, n_boot),
        "fp_reduction_vs_baseline": _fp_reduction(
            [truth[i] for i in real],
            [confirmed[i] for i in real],
            [baseline_flags[i] for i in real],
            [groups[i] for i in real],
            n_boot,
        ),
        "fp_reduction_all_sources": _fp_reduction(truth, confirmed, baseline_flags, groups, n_boot),
        "routing_rate": routing_rate,
        "macro_f1": macro,
        "classes_present": present,
        "per_tier": breakdown(
            records, truth, head.scores, confirmed, t_low, lambda r: r.difficulty or "unscored"
        ),
        "per_concealment": breakdown(
            records, truth, head.scores, confirmed, t_low, lambda r: r.concealment
        ),
        "per_source": breakdown(
            records,
            truth,
            head.scores,
            confirmed,
            t_low,
            lambda r: f"{r.source}/{r.generator_model or 'none'}",
        ),
        "minimal_pair_accuracy": None,
    }
    if spec.minimal_pairs:
        if inputs.minimal_pairs is None:
            raise MatrixError(f"row {spec.row} asks for minimal pairs but none were supplied")
        predicted_by_id = (
            {r.id: p for r, p in zip(records, head.predicted, strict=True)}
            if head.predicted is not None
            else {
                r.id: ("poisoned" if c >= BINARY_DECISION else "benign")
                for r, c in zip(records, confirmed, strict=True)
            }
        )
        block["minimal_pair_accuracy"] = minimal_pair_accuracy(
            inputs.minimal_pairs, predicted_by_id
        )
    return block


def _sample_std(values: Sequence[float]) -> float | None:
    """The sample std across seeds (ddof=1); None for fewer than two values."""
    return float(np.std(values, ddof=1)) if len(values) >= 2 else None


def _aggregate(values: Sequence[Any]) -> Any:
    """The across-seed summary of one key's per-seed values (all of one shape).

    * a number: the mean;
    * an interval dict (`point`/`lo`/`hi`): `point` and the bounds averaged over seeds, plus
      `std` (sample std of the per-seed points) and `per_seed` (the points). `lo`/`hi` are the
      seeds' cluster-bootstrap intervals averaged: the typical interval of one model, while
      `std` is the spread between models;
    * any other dict: aggregated key by key (breakdowns);
    * None everywhere: None. A McNemar p-value takes the largest (most conservative) seed.
    """
    present = [v for v in values if v is not None]
    if not present:
        return None
    first = present[0]
    if isinstance(first, dict):
        keys = list(first)
        out = {k: _aggregate_key(k, [v.get(k) for v in present]) for k in keys}
        if "point" in first:
            points = [v["point"] for v in present if v.get("point") is not None]
            out["std"] = _sample_std(points)
            out["per_seed"] = points
        return out
    return float(np.mean(present))


def _aggregate_key(key: str, values: Sequence[Any]) -> Any:
    numbers = [v for v in values if v is not None]
    if key == "mcnemar_p" and numbers:
        return float(max(numbers))
    return _aggregate(values)


def _collapse(label: str) -> str:
    return "poisoned" if label == "poisoned" else "not_poisoned"


def _p95_ms(spec: RowSpec, seeds: Sequence[int]) -> float | None:
    """Mean measured p95 across the row's seeds, from each artefact's `sieve_config.json`.

    The seed glob has to be substituted: `models/<profile>-s*/sieve_config.json` is a path that
    never exists, and reading it silently produced `null` for every row.
    """
    if not spec.seeded:
        return None
    values: list[float] = []
    for seed in seeds:
        config = seed_dir(spec.classifier, seed) / th.CONFIG_FILE
        if not config.exists():
            continue
        value = json.loads(config.read_text(encoding="utf-8")).get("p95_ms")
        if value is not None:
            values.append(float(value))
    return float(np.mean(values)) if values else None


# ------------------------------------------------------------------------------ the matrix


def run_matrix(
    specs: Sequence[RowSpec],
    inputs: Inputs,
    *,
    classifier_factory: ClassifierFactory,
    judge_factory: JudgeFactory | None = None,
    rows: Sequence[int] | None = None,
    seeds: Sequence[int] = DEFAULT_SEEDS,
    n_boot: int = 1000,
) -> dict[str, Any]:
    require_offline()
    wanted = [s for s in specs if rows is None or s.row in set(rows)]
    if not wanted:
        raise MatrixError(f"no matrix row matches {list(rows or [])}")
    if any(r.split != "dev" for r in inputs.records):
        raise MatrixError(
            "the ablation matrix was handed a non-dev row; every number here is out-of-fold "
            "over the development split (PRD §11.6)"
        )
    with no_network():
        results = [
            evaluate_row(
                spec,
                inputs,
                classifier_factory=classifier_factory,
                judge_factory=judge_factory,
                seeds=seeds,
                n_boot=n_boot,
            )
            for spec in wanted
        ]
    return {
        "rows": results,
        "dataset_version": inputs.dataset_version,
        "n_records": len(inputs.records),
        "seeds": list(seeds),
        "n_boot": n_boot,
    }


def write_results(payload: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return path


# ----------------------------------------------------------------------------------- CLI


def _default_classifier_factory(model_cache: dict[str, Classifier]) -> ClassifierFactory:
    def factory(spec: RowSpec, seed: int) -> Classifier:
        from sieve.model.infer import load_classifier

        key = f"{spec.classifier}:{seed}"
        if key not in model_cache:
            target = spec.classifier.replace("*", str(seed))
            model_cache[key] = load_classifier(target)
        return model_cache[key]

    return factory


def _default_judge_factory(transcript: Path) -> JudgeFactory:
    def factory(model: str) -> Judge:
        try:
            from sieve.judge.record import ReplayJudge
        except ImportError as exc:  # pragma: no cover - only before the judge lane merges
            raise SystemExit("judge lane not merged yet") from exc
        if not transcript.exists():
            raise MatrixError(
                f"{transcript} is missing; record it once with "
                "`SIEVE_RERECORD=1 python -m bench.analysis.judge_agreement`"
            )
        return ReplayJudge(transcript)

    return factory


def main(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(prog="python -m bench.ablations.run")
    parser.add_argument("--matrix", type=Path, default=Path("bench/ablations.yaml"))
    parser.add_argument("--corpus", type=Path, default=Path("bench/data/labelled.jsonl"))
    parser.add_argument("--results-dir", type=Path, default=Path("bench/results"))
    parser.add_argument("--rows", default=None, help="comma-separated row numbers")
    parser.add_argument("--seeds", default="0,1,2")
    parser.add_argument("--n-boot", type=int, default=1000)
    ns = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    require_offline()

    from sieve.model.data import load_corpus

    records = [r for r in load_corpus(ns.corpus) if r.split == "dev"]
    specs = load_matrix(ns.matrix)
    wanted = [int(x) for x in ns.rows.split(",")] if ns.rows else None
    selected = [s for s in specs if wanted is None or s.row in set(wanted)]

    baseline_rows = load_baseline_rows(ns.results_dir / "baseline_raw.json")
    inputs = Inputs(
        records=records,
        baseline_rows=baseline_rows,
        # Only the rows that ask for it pay for it; a missing artefact is still an error there.
        minimal_pairs=(
            load_minimal_pairs(ns.results_dir / "minimal_pairs.json")
            if any(s.minimal_pairs for s in selected)
            else None
        ),
        judge_usage=load_judge_usage(ns.results_dir / "judge_outputs.jsonl"),
        scanner_findings_by_id=(
            scanner_findings_from_baseline(baseline_rows)
            if any(s.scanner_features for s in selected)
            else {}
        ),
        dataset_version=dataset_version(ns.corpus),
    )
    payload = run_matrix(
        specs,
        inputs,
        classifier_factory=_default_classifier_factory({}),
        judge_factory=_default_judge_factory(ns.results_dir / "judge_outputs.jsonl"),
        rows=wanted,
        seeds=[int(x) for x in ns.seeds.split(",")],
        n_boot=ns.n_boot,
    )
    return write_results(payload, ns.results_dir / "ablations.json")


if __name__ == "__main__":  # pragma: no cover
    main()
