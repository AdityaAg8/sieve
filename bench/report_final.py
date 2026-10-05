"""The sealed report (training plan H3, §6.5, §6.6): saved predictions in, `holdout.json` out.

A pure function of `holdout_predictions.jsonl` and `holdout_rows.jsonl` (both written once by
`bench/eval_final.py`), the opening's log line and the preregistration. It reads no sealed text
and scores nothing, so `make bench` re-runs it freely. It refuses to run unless
`bench/PREREGISTRATION.md` hashes to the committed `bench/PREREGISTRATION.sha256` (H7), and it
records that hash.

Every number is computed per model at that model's own thresholds, then aggregated per family
as mean ± std (ddof = 1) with the per-seed values beside it. The top-level keys the README and
the model card read (`recall`, `fpr_pipeline_real`, `per_tier`, ...) come from the
pre-registered headline artefact (`--headline`).

* **Release gate** (§6.5): poisoned recall at `T_low` on `holdout_synthetic` >= 0.95 with a
  server-cluster-bootstrap lower bound >= 0.90. MCPTox recall is reported beside it, never gated.
* **test-ID** is real and has no poisoned rows: "2-class macro-F1 (benign, imperative)", Stage-1
  FPR at `T_low` on all real negatives, on the triage slice (YARA-flagged), per source and
  prevalence-weighted (`wild_fp` is 25% of real rows here vs 3.5% natural).
* **FP reduction vs YARA** (M2) on the holdout rows, as a triage layer (Sieve may only dismiss
  a YARA flag) and stand-alone, each with its recall comparison and both McNemar p-values.
* **MCPTox** (`real_poisoned`, M13): template × server clusters, per template and risk class,
  and the miss-catch slice (positives YARA did not flag). Docker and MCPTox-clean give the
  test-OOD FPR.
* Undefined bootstrap replicates (no positives, no negatives) are dropped and counted (D3).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from bench.ablations.run import breakdown
from bench.stats import clopper_pearson
from sieve.eval.metrics import (
    auprc,
    auroc,
    cluster_bootstrap,
    fp_reduction,
    macro_f1_present,
)
from sieve.types import LABELS

RESULTS_DIR = Path("bench/results")
PREDICTIONS_FILE = "holdout_predictions.jsonl"
ROWS_FILE = "holdout_rows.jsonl"
REPORT_FILE = "holdout.json"
#: Written by eval_final: test-OOD ids ingest would reject, kept out of every metric (ADR-009).
EXCLUDED_FILE = "holdout_excluded.json"
LOG_FILE = "test_opened.log"
PREREGISTRATION = Path("bench/PREREGISTRATION.md")
PREREGISTRATION_HASH = Path("bench/PREREGISTRATION.sha256")
RELEASE_GATE = {"recall_point": 0.95, "recall_lower_bound": 0.90}
WILD_FP_NATURAL = 0.035
NEGATIVE_SETS = ("docker", "mcptox_clean")
POISONED = "poisoned"
_NOT = "not_poisoned"

Row = dict[str, Any]
Stat = Callable[[Any, Any], float]


class PreregistrationError(Exception):
    """The preregistration is missing, or it changed after its hash was committed."""


def preregistration_hash(path: Path = PREREGISTRATION) -> str:
    """sha256 of the preregistration with line endings normalised (a checkout may add CRs)."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def check_preregistration(
    path: Path = PREREGISTRATION, expected: Path = PREREGISTRATION_HASH
) -> str:
    if not path.exists() or not expected.exists():
        raise PreregistrationError(f"{path} and {expected} must both be committed (H7)")
    want = expected.read_text(encoding="utf-8").split()[0].strip()
    got = preregistration_hash(path)
    if got != want:
        raise PreregistrationError(
            f"{path} hashes to {got[:12]}, the committed hash is {want[:12]}"
        )
    return got


# --------------------------------------------------------------------------- statistics


def _nan_to_none(x: float) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)


def _rate(flags: Sequence[bool]) -> float | None:
    return float(np.mean(flags)) if len(flags) else None


def _interval(
    truth: Sequence[str], scores: Sequence[float], groups: Sequence[str], stat: Stat, n_boot: int
) -> dict[str, Any]:
    if not truth:
        return {"point": None, "lo": None, "hi": None, "n": 0, "n_undefined": 0}
    r = cluster_bootstrap(stat, truth, scores, groups, n_boot=n_boot)
    return {
        "point": _nan_to_none(r.point),
        "lo": _nan_to_none(r.lo),
        "hi": _nan_to_none(r.hi),
        "n": len(truth),
        "n_undefined": r.n_undefined,
    }


def _recall_stat(threshold: float) -> Stat:
    def stat(yt: Any, ys: Any) -> float:
        pos = yt == POISONED
        return float(np.mean(ys[pos] >= threshold)) if pos.any() else float("nan")

    return stat


def _fpr_stat(threshold: float) -> Stat:
    def stat(yt: Any, ys: Any) -> float:
        neg = yt != POISONED
        return float(np.mean(ys[neg] >= threshold)) if neg.any() else float("nan")

    return stat


def _flipped(interval: Mapping[str, Any]) -> dict[str, Any]:
    """`1 - share` of an interval from the SAME bootstrap: the point flips, and so do the ends."""

    def one_minus(x: float | None) -> float | None:
        return None if x is None else 1.0 - x

    return {
        "point": one_minus(interval["point"]),
        "lo": one_minus(interval["hi"]),
        "hi": one_minus(interval["lo"]),
        "n": interval["n"],
    }


def _count(k: int, n: int) -> dict[str, Any]:
    """A share with its counts, so no reader ever divides (or rounds) them itself."""
    return {"point": k / n if n else None, "k": k, "n": n}


def _truth(row: Row) -> str:
    return str(row["label"]) if row.get("label") else (POISONED if row["poisoned"] else _NOT)


def _binary(rows: Sequence[Row]) -> list[str]:
    return [POISONED if r["poisoned"] else _NOT for r in rows]


def _ranking(rows: Sequence[Row], p: Sequence[float]) -> dict[str, float | None]:
    truth = _binary(rows)
    if len(set(truth)) < 2:
        return {"auroc": None, "auprc": None}
    return {"auroc": _nan_to_none(auroc(truth, p)), "auprc": _nan_to_none(auprc(truth, p))}


def _slice_recall(rows: Sequence[Row], p: Sequence[float], t_low: float, key: str) -> dict:
    buckets: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        if r["poisoned"]:
            buckets[str(r.get(key) or "none")].append(i)
    return {
        b: {"n": len(ix), "recall_at_t_low": float(np.mean([p[i] >= t_low for i in ix]))}
        for b, ix in sorted(buckets.items())
    }


def _payload_fields(rows: Sequence[Row], p: Sequence[float], t_low: float) -> dict:
    """Recall per payload field, on `exact` rows only (an approximated schema moves fields)."""
    buckets: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        if r["poisoned"] and r.get("schema_fidelity") == "exact":
            for f in r.get("payload_fields") or ["unrecorded"]:
                buckets[str(f)].append(i)
    return {
        b: {"n": len(ix), "recall_at_t_low": float(np.mean([p[i] >= t_low for i in ix]))}
        for b, ix in sorted(buckets.items())
    }


def _fp_reduction(
    rows: Sequence[Row], sieve: Sequence[bool], groups: Sequence[str], n_boot: int
) -> dict[str, Any] | None:
    baseline = [bool(r.get("yara")) for r in rows]
    if not rows or not any(baseline):
        return None
    result = fp_reduction(_binary(rows), sieve, baseline, groups, n_boot=n_boot)
    return {k: (_nan_to_none(v) if isinstance(v, float) else v) for k, v in asdict(result).items()}


# --------------------------------------------------------------------------- per model


def model_report(preds: Sequence[Row], rows: Mapping[str, Row], *, n_boot: int) -> dict[str, Any]:
    """Every §6.6 number for one model, at its own thresholds."""
    t_low, t_high = float(preds[0]["t_low"]), float(preds[0]["t_high"])
    by_set: dict[str, list[tuple[Row, Row]]] = defaultdict(list)
    for pred in preds:
        by_set[pred["set"]].append((rows[pred["id"]], pred))

    def split(name: str) -> tuple[list[Row], list[float], list[Row]]:
        pairs = by_set.get(name, [])
        return [r for r, _ in pairs], [float(p["p"]) for _, p in pairs], [p for _, p in pairs]

    tid, tid_p, tid_pred = split("test_id")
    syn, syn_p, syn_pred = split("holdout_synthetic")
    rp, rp_p, rp_pred = split("real_poisoned")
    holdout, holdout_p = tid + syn, tid_p + syn_p
    holdout_pred = tid_pred + syn_pred
    confirmed = [1.0 if p["verdict"] == "confirmed" else 0.0 for p in holdout_pred]
    groups = [r["cluster"] for r in holdout]
    truth = [_truth(r) for r in holdout]

    # test-ID: real, negatives only.
    tid_truth = [_truth(r) for r in tid]
    tid_pred_class = [LABELS[int(np.argmax(p["probs"]))] for p in tid_pred]
    macro, present = macro_f1_present(tid_truth, tid_pred_class) if tid else (float("nan"), [])
    yara_tid = [i for i, r in enumerate(tid) if r.get("yara")]
    by_source = {
        src: _rate([tid_p[i] >= t_low for i, r in enumerate(tid) if r["source"] == src])
        for src in ("public_repo", "wild_fp")
    }
    weighted = (
        (1 - WILD_FP_NATURAL) * by_source["public_repo"] + WILD_FP_NATURAL * by_source["wild_fp"]
        if by_source["public_repo"] is not None and by_source["wild_fp"] is not None
        else None
    )
    test_id = {
        "n": len(tid),
        "macro_f1_2class": _nan_to_none(macro),
        "macro_f1_classes": present,
        "fpr_stage1_at_t_low": _interval(
            tid_truth, tid_p, [r["cluster"] for r in tid], _fpr_stat(t_low), n_boot
        ),
        "fpr_triage_slice": _interval(
            [tid_truth[i] for i in yara_tid], [tid_p[i] for i in yara_tid],
            [tid[i]["cluster"] for i in yara_tid], _fpr_stat(t_low), n_boot,
        ),
        "fpr_by_source": by_source,
        "fpr_prevalence_weighted": weighted,
        "routing_rate": _rate([t_low <= x < t_high for x in tid_p]),
    }  # fmt: skip
    # The like-for-like view (v1 fix plan T2): YARA's own FPR on the same real rows, and the
    # share of its false positives Sieve dismisses, read off the triage slice's bootstrap.
    tid_negatives = [i for i, t in enumerate(tid_truth) if t != POISONED]
    test_id["baseline_fpr_real"] = _count(
        sum(bool(tid[i].get("yara")) for i in tid_negatives), len(tid_negatives)
    )
    test_id["fp_dismissed_real_triage"] = _flipped(test_id["fpr_triage_slice"])

    # holdout-synthetic: the release gate.
    syn_truth = [_truth(r) for r in syn]
    gate = _interval(syn_truth, syn_p, [r["cluster"] for r in syn], _recall_stat(t_low), n_boot)
    holdout_synthetic = {
        "n": len(syn),
        "n_poisoned": sum(r["poisoned"] for r in syn),
        "recall_at_t_low": gate,
        "per_concealment": _slice_recall(syn, syn_p, t_low, "concealment"),
        "per_generator": _slice_recall(syn, syn_p, t_low, "generator"),
        "per_payload_field": _payload_fields(syn, syn_p, t_low),
        "miss_catch_recall": _rate([x >= t_low for r, x in zip(syn, syn_p, strict=True)
                                    if r["poisoned"] and not r.get("yara")]),
        **_ranking(syn, syn_p),
    }  # fmt: skip
    caught = [x >= t_low for r, x in zip(syn, syn_p, strict=True) if r["poisoned"]]
    holdout_synthetic["recall_cp"] = (
        dict(zip(("lo", "hi"), clopper_pearson(sum(caught), len(caught)), strict=True))
        | {"k": sum(caught), "n": len(caught)}
        if caught
        else {"lo": None, "hi": None, "k": 0, "n": 0}
    )

    # real_poisoned (M13): template x server clusters, eval-only.
    rp_truth = [_truth(r) for r in rp]
    real_poisoned = {
        "n": len(rp),
        "recall_stage1": _interval(
            rp_truth, rp_p, [r["cluster"] for r in rp], _recall_stat(t_low), n_boot
        ),
        "recall_pipeline": _rate([p["verdict"] == "confirmed" for p in rp_pred]),
        "per_template": _slice_recall(rp, rp_p, t_low, "template"),
        "per_risk": _slice_recall(rp, rp_p, t_low, "risk"),
        "miss_catch_recall": _rate([x >= t_low for r, x in zip(rp, rp_p, strict=True)
                                    if not r.get("yara")]),
    }  # fmt: skip

    # test-OOD negatives and the OOD ranking.
    test_ood: dict[str, Any] = {}
    ood_rows: list[Row] = list(rp)
    ood_p: list[float] = list(rp_p)
    for name in NEGATIVE_SETS:
        neg, neg_p, _ = split(name)
        test_ood[name] = _interval(
            _binary(neg), neg_p, [r["cluster"] for r in neg], _fpr_stat(t_low), n_boot
        )
        ood_rows += neg
        ood_p += neg_p
    test_ood.update(_ranking(ood_rows, ood_p))
    test_ood["fp_reduction_vs_yara"] = _fp_reduction(
        ood_rows, [x >= t_low for x in ood_p], [r["cluster"] for r in ood_rows], n_boot
    )

    sieve_alone = [x >= t_low for x in holdout_p]
    triage = [bool(r.get("yara")) and s for r, s in zip(holdout, sieve_alone, strict=True)]
    unk = [i for i, r in enumerate(holdout) if r.get("deberta_unk")]
    return {
        "t_low": t_low,
        "t_high": t_high,
        "test_id": test_id,
        "holdout_synthetic": holdout_synthetic,
        "real_poisoned": real_poisoned,
        "test_ood": test_ood,
        "holdout": {
            "n": len(holdout),
            **_ranking(holdout, holdout_p),
            "fpr_pipeline_real": _interval(
                tid_truth, [1.0 if p["verdict"] == "confirmed" else 0.0 for p in tid_pred],
                [r["cluster"] for r in tid], _fpr_stat(0.5), n_boot,
            ),
            "fp_reduction_triage": _fp_reduction(holdout, triage, groups, n_boot),
            "fp_reduction_standalone": _fp_reduction(holdout, sieve_alone, groups, n_boot),
            "per_tier": breakdown(holdout, truth, holdout_p, confirmed, t_low,  # type: ignore[arg-type]
                                  lambda r: r.get("difficulty") or "unscored"),
            "per_concealment": breakdown(holdout, truth, holdout_p, confirmed, t_low,  # type: ignore[arg-type]
                                         lambda r: r.get("concealment") or "none"),
            "deberta_unk": None if not any(r.get("deberta_unk") is not None for r in holdout)
            else {"n": len(unk), "recall_at_t_low": _rate(
                [holdout_p[i] >= t_low for i in unk if holdout[i]["poisoned"]])},
            "routing_rate": _rate([t_low <= x < t_high for x in holdout_p]),
        },
    }  # fmt: skip


# ----------------------------------------------------------------- aggregation, headline


def _flatten(d: Mapping[str, Any], prefix: str = "") -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, bool):
            continue
        if isinstance(v, int | float):
            out[key] = float(v)
        elif isinstance(v, Mapping):
            out.update(_flatten(v, f"{key}."))
    return out


def aggregate(per_model: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Mean ± std (ddof = 1) per scalar, with the per-seed values, over one family's models."""
    flat = {m: _flatten(r) for m, r in per_model.items()}
    keys = sorted(set().union(*(f.keys() for f in flat.values())))
    out: dict[str, Any] = {}
    for key in keys:
        values = [flat[m][key] for m in sorted(flat) if key in flat[m]]
        out[key] = {
            "mean": float(np.mean(values)),
            "std": float(np.std(values, ddof=1)) if len(values) > 1 else None,
            "values": values,
        }
    return out


def _limitations(head: Mapping[str, Any]) -> str:
    syn, tid, rp = head["holdout_synthetic"], head["test_id"], head["real_poisoned"]
    gate = syn["recall_at_t_low"]
    lines = [
        f"- The release gate rests on {syn['n_poisoned']} holdout-synthetic positive(s); every "
        "interval is a server-cluster bootstrap, so it is wide by construction.",
        f"- test-ID is {tid['n']} real row(s) with no poisoned row: it measures false positives "
        "and the benign/imperative split, never recall.",
        "- The LLM judge is deferred to v1.1 (D8): pipeline figures are Stage-1 only, and the "
        "band [T_low, T_high) is routed to review.",
    ]
    if gate["point"] is not None and gate["lo"] is not None:
        lines.append(
            f"- Holdout-synthetic recall is {gate['point']:.3f} with a 95% lower bound of "
            f"{gate['lo']:.3f}; read the lower bound, not the point."
        )
    if rp["n"] == 0:
        lines.append("- The `real_poisoned` slice was empty for this run, so M13 is unmeasured.")
    else:
        lines.append(
            f"- MCPTox ({rp['n']} rows) is unlicensed: evaluated locally, never trained on."
        )
    return "\n".join(lines)


def _log_entry(log_path: Path, headline: str) -> tuple[str | None, dict[str, Any]]:
    if not log_path.exists():
        return None, {}
    lines = [x for x in log_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    for line in reversed(lines):
        entry = json.loads(line)
        if headline in entry.get("models", []):
            return line, entry
    return None, {}


def _check_coverage(model: str, preds: Sequence[Row], rows: Sequence[Row]) -> None:
    """F19: each model scored each (set, id) of the rows exactly once.

    A fold artefact (`fold` >= 0, only in `eval_val_ood`'s dev dry run) scores its own fold's
    rows and nothing else, so for it "exactly once" holds over the rows it scored.
    """
    keys = Counter((p["set"], p["id"]) for p in preds)
    repeated = sorted(k for k, count in keys.items() if count > 1)
    if repeated:
        raise ValueError(f"{model} scored {len(repeated)} (set, id) more than once: {repeated[:3]}")
    wanted = {(r["set"], r["id"]) for r in rows}
    fold_model = all(isinstance(p.get("fold"), int) and p["fold"] >= 0 for p in preds)
    if fold_model:
        wanted &= set(keys)
    if set(keys) != wanted:
        missing, extra = sorted(wanted - set(keys)), sorted(set(keys) - wanted)
        raise ValueError(
            f"{model}'s predictions do not cover the rows exactly: {len(missing)} missing "
            f"{missing[:3]}, {len(extra)} not in the rows {extra[:3]}"
        )


def build_report(
    predictions: Sequence[Row],
    rows: Sequence[Row],
    *,
    headline: str | None = None,
    n_boot: int = 1000,
    preregistration_sha256: str,
    log_line: str | None = None,
    log_entry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    by_id = {r["id"]: r for r in rows}
    if len(by_id) != len(rows):
        raise ValueError("holdout rows repeat an id; predictions are joined to rows by id")
    by_model: dict[str, list[Row]] = defaultdict(list)
    family_of: dict[str, str] = {}
    for p in predictions:
        by_model[p["model"]].append(p)
        family_of[p["model"]] = str(p.get("family") or p["model"].split("@")[0])
    if not by_model:
        raise ValueError("no predictions to report")
    for model, preds in by_model.items():
        _check_coverage(model, preds, rows)
    if headline is None:
        if len(by_model) > 1:
            raise ValueError("several models were scored: name the pre-registered --headline")
        headline = next(iter(by_model))
    if headline not in by_model:
        raise ValueError(f"headline {headline!r} was not scored: {sorted(by_model)}")

    per_model = {m: model_report(p, by_id, n_boot=n_boot) for m, p in sorted(by_model.items())}
    families: dict[str, dict[str, Any]] = defaultdict(dict)
    for m, r in per_model.items():
        families[family_of[m]][m] = r
    head = per_model[headline]
    gate = head["holdout_synthetic"]["recall_at_t_low"]
    rp = head["real_poisoned"]
    entry = dict(log_entry or {})
    return {
        # --- read by docs/scripts/readme_numbers.py and sieve/model/push.py
        "headline_model": headline,
        "model_version": headline,
        "dataset_version": entry.get("dataset_version"),
        "reason": entry.get("reason"),
        "log_line": log_line,
        "t_low": head["t_low"],
        "t_high": head["t_high"],
        "n_holdout": head["holdout"]["n"],
        "n_eval_only": rp["n"],
        "recall": gate["point"],
        "recall_lcb": gate["lo"],
        "recall_interval": gate,
        "fpr_pipeline_real": head["holdout"]["fpr_pipeline_real"],
        "fpr_stage1_real": head["test_id"]["fpr_stage1_at_t_low"],
        "baseline_fpr_real": head["test_id"]["baseline_fpr_real"],
        "fp_dismissed_real_triage": head["test_id"]["fp_dismissed_real_triage"],
        "recall_cp": head["holdout_synthetic"]["recall_cp"],
        "per_tier": head["holdout"]["per_tier"],
        "per_concealment": head["holdout"]["per_concealment"],
        "real_poisoned_recall_stage1": rp["recall_stage1"]["point"],
        "real_poisoned_recall_pipeline": rp["recall_pipeline"],
        "release_gate": RELEASE_GATE,
        "release_gate_passed": bool(
            gate["point"] is not None
            and gate["lo"] is not None
            and gate["point"] >= RELEASE_GATE["recall_point"]
            and gate["lo"] >= RELEASE_GATE["recall_lower_bound"]
        ),
        "limitations": _limitations(head),
        # --- the full v1 report
        "preregistration_sha256": preregistration_sha256,
        "judge": {"status": "deferred", "decision": "D8"},
        "n_boot": n_boot,
        "per_model": per_model,
        "per_family": {f: aggregate(models) for f, models in sorted(families.items())},
        "models_by_family": {f: sorted(models) for f, models in sorted(families.items())},
    }


def published_headline(results_dir: Path) -> str | None:
    """The pre-registered headline (D14): `selection.json`'s published artefact's version."""
    selection = results_dir / "selection.json"
    if not selection.exists():
        return None
    published = json.loads(selection.read_text(encoding="utf-8")).get("published_dir")
    config = Path(str(published)) / "sieve_config.json" if published else None
    if config is None or not config.exists():
        return None
    version = json.loads(config.read_text(encoding="utf-8")).get("model_version")
    return str(version) if version else None


def _read_jsonl(path: Path) -> list[Row]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(prog="python -m bench.report_final")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--headline", default=None, help="the pre-registered published model")
    parser.add_argument("--n-boot", type=int, default=1000)
    parser.add_argument("--preregistration", type=Path, default=PREREGISTRATION)
    parser.add_argument("--preregistration-hash", type=Path, default=PREREGISTRATION_HASH)
    ns = parser.parse_args(argv)
    sha = check_preregistration(ns.preregistration, ns.preregistration_hash)
    predictions = _read_jsonl(ns.results_dir / PREDICTIONS_FILE)
    rows = _read_jsonl(ns.results_dir / ROWS_FILE)
    headline = ns.headline or (predictions[0]["model"] if predictions else None)
    if ns.headline is None and len({p["model"] for p in predictions}) > 1:
        headline = published_headline(ns.results_dir)
    log_line, entry = _log_entry(ns.results_dir / LOG_FILE, headline or predictions[0]["model"])
    report = build_report(
        predictions,
        rows,
        headline=headline,
        n_boot=ns.n_boot,
        preregistration_sha256=sha,
        log_line=log_line,
        log_entry=entry,
    )
    excluded = ns.results_dir / EXCLUDED_FILE
    report["excluded_invalid_tool_name"] = (
        json.loads(excluded.read_text(encoding="utf-8"))["invalid_tool_name"]
        if excluded.exists()
        else []
    )
    path = ns.results_dir / REPORT_FILE
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {path}: release gate passed = {report['release_gate_passed']}")
    return path


if __name__ == "__main__":  # pragma: no cover
    main()
