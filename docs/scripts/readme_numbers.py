"""Rewrite the README's headline-number block from `bench/results/` (PRD §17.1, Task 10.1).

`make readme` runs this script by path. It is the only writer of the block between
`<!-- numbers:start -->` and `<!-- numbers:end -->`, and the four headline numbers PRD §17.1
names are never typed by hand:

1. M1: with the judge deferred (D8) it is stated as not measured, and the line leads with the
   share of real holdout tools Stage 1 sends to review, beside the scanner's rate on the same
   tools (`holdout.json`);
2. M2 as triage: the share of the scanner's false positives on real holdout tools that Stage 1
   dismisses, then the all-negatives figure with McNemar, then the standalone comparison;
3. holdout poisoned recall with its exact (Clopper-Pearson) lower bound (`holdout.json`);
4. the `real_poisoned` recall (M13), beside its false-positive cost on clean OOD tools.

Each figure names its population. The v1 audit (F1-F20) found a headline that compared an
auto-confirm rate with a flag rate and paired real-subset FPRs with synthetic recall; the
development figures now sit on their own labelled line, followed by the post-hoc operating
point (when `operating_points.json` exists), the labels caveat and the provenance line.

It refuses, loudly and without touching the README, when any headline figure is missing.
`baseline.json`, `curves.json` and `selection.json` supply the names and provenance around
the numbers (which scanner, which encoder, which dataset version), never a headline figure.
`--check` writes nothing and exits 1 when the README differs from what the results generate.

This script lives in `docs/` rather than `bench/` because L10 owns `docs/` (PRD §19) and
because `bench/` is the tree the sealed-holdout grep polices; it reads the holdout *report*,
never a holdout row.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS = REPO / "bench" / "results"
DEFAULT_README = REPO / "README.md"

MARK_START = "<!-- numbers:start -->"
MARK_END = "<!-- numbers:end -->"

#: The judge model the shipped pipeline uses (PRD §12.1). The headline row is the ablation row
#: that runs the classifier *and* this judge, because that is the configuration M1 is defined
#: at. Read from settings rather than repeated here, for the same reason thresholds are: a
#: fallback string here would let the README quote a row that the deployment does not run, so
#: an unimportable `sieve` is a crash rather than a guess.
from sieve.config import settings  # noqa: E402 - the comment above belongs to this import

HEADLINE_JUDGE = settings.judge_model


class Refused(Exception):  # noqa: N818 - it is a refusal to write, not an error condition
    """The generator will not write a README it would have to invent a number for."""


# --------------------------------------------------------------------------- formatting


def pct(value: float | None) -> str:
    """A ratio as a percentage. One decimal place, so 0.1234 reads 12.3%."""
    if value is None:
        raise Refused("a percentage was asked for but the results file carries null")
    return f"{value * 100:.1f}%"


def ratio(value: float | None) -> str:
    """A recall or a probability, three decimals, as the model card prints it."""
    if value is None:
        raise Refused("a ratio was asked for but the results file carries null")
    return f"{value:.3f}"


def p_value(value: float | None) -> str:
    if value is None:
        raise Refused("McNemar's p-value is missing from the ablation row")
    return "p < 0.0001" if value < 0.0001 else f"p = {value:.4f}"


# ------------------------------------------------------------------------------- reading


def _read(directory: Path, name: str, *, required: bool, hint: str = "") -> Any:
    path = directory / name
    if not path.exists():
        if required:
            raise Refused(f"{path} is missing{hint}")
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def headline_row(ablations: dict[str, Any], winner: str | None = None) -> dict[str, Any]:
    """The full-pipeline ablation row: an encoder artefact, full serialisation, the real judge.

    Chosen by what the row *is* rather than by its number, so renumbering `bench/ablations.yaml`
    cannot silently change which figure the README quotes. Ties break on the lowest row number.
    """
    rows = ablations.get("rows") or []
    candidates = [
        row
        for row in rows
        if row.get("judge") == HEADLINE_JUDGE
        and str(row.get("classifier", "")).startswith("models/")
        and row.get("mode") == "full"
        and not row.get("breakdown_of")
        and row.get("status") != "deferred"
    ]
    judge_deferred = any(
        row.get("judge") == HEADLINE_JUDGE and row.get("status") == "deferred" for row in rows
    )
    if not candidates and judge_deferred:
        # D8: the judge is deferred, so the shipped pipeline is Stage 1 alone. The headline is
        # that encoder's own full-serialisation row, and `_reduction_line` says so.
        candidates = [
            row
            for row in rows
            if row.get("judge") == "none"
            and str(row.get("classifier", "")).startswith("models/")
            and row.get("mode") == "full"
            and not row.get("breakdown_of")
            and not row.get("single_seed")
        ]
        # The pre-registered selection's winner, when known, not whichever row is listed first.
        chosen = [
            row
            for row in candidates
            if winner and str(row.get("classifier", "")).startswith(f"models/{winner}-s")
        ]
        candidates = chosen or candidates
    if not candidates:
        raise Refused(
            "no ablation row runs the classifier with the shipped judge "
            f"({HEADLINE_JUDGE}, full serialisation, its own artefact), so the M2 figure at the "
            "pipeline's real operating point does not exist yet; run `make bench` over a matrix "
            "that contains one"
        )
    return min(candidates, key=lambda row: row.get("row", 0))


def baseline_name(baseline: Any) -> str:
    """`cisco_yara 4.8.0`, from the committed baseline report; never guessed."""
    if isinstance(baseline, list) and baseline:
        first = baseline[0]
        return f"{first['scanner']} {first['version']}"
    return "the committed scanner baseline (`bench/results/baseline_raw.json`)"


# ------------------------------------------------------------------------------ rendering


def _interval(ci: dict[str, Any], formatter: Any = None) -> str:
    """`(95% cluster-bootstrap CI a to b)`, or nothing when the bootstrap collapsed.

    A resample of all hits or all misses gives the interval [x, x]; printing it would claim a
    certainty the sample does not have, so a collapsed interval is dropped and the caller states
    the count instead (audit F2, F7).
    """
    fmt = formatter or pct
    lo, hi = ci.get("lo"), ci.get("hi")
    if lo is None or hi is None or lo == hi:
        return ""
    return f" (95% cluster-bootstrap CI {fmt(lo)} to {fmt(hi)})"


def _headline_report(holdout: dict[str, Any]) -> dict[str, Any]:
    """The published model's own block of `holdout.json`, by the name the report gives it."""
    report = (holdout.get("per_model") or {}).get(holdout.get("headline_model"))
    if not isinstance(report, dict):
        raise Refused(
            "holdout.json carries no `per_model` block for its `headline_model`; the triage and "
            "out-of-distribution figures live there"
        )
    return report


def _fpr_line(holdout: dict[str, Any], baseline: Any) -> str:
    """M1. PRD M1 is defined with the judge running; v1 defers it (D8), so M1 is not measured
    and the line leads with what a user actually gets: the share of real tools sent to a human,
    beside the share the scanner flags (audit F1, F2)."""
    review = holdout.get("fpr_stage1_real") or {}
    scanner = holdout.get("baseline_fpr_real") or {}
    confirmed = holdout.get("fpr_pipeline_real") or {}
    if review.get("point") is None or confirmed.get("point") is None:
        raise Refused(
            "holdout.json carries no `fpr_stage1_real` / `fpr_pipeline_real` point estimate: the "
            "real subset of the holdout was empty, so the review share is unmeasured"
        )
    if scanner.get("point") is None:
        raise Refused(
            "holdout.json carries no `baseline_fpr_real`: the scanner's rate on the same real "
            "tools is the comparison the review share needs; run `make bench`"
        )
    return (
        "**M1 · `FPR_pipeline` on the real subset:** not measured in v1, because the PRD defines "
        "it with the LLM judge running and the judge is deferred to v1.1 (D8). What a user gets "
        f"instead: **Stage 1 sends {pct(review['point'])} of the {int(review['n'])} real "
        f"non-poisoned holdout tools to human review**{_interval(review)}, where "
        f"{baseline_name(baseline)} flags {pct(scanner['point'])} "
        f"({int(scanner['k'])} of {int(scanner['n'])}) of the same tools. It auto-confirms "
        f"{pct(confirmed['point'])} of them. Frozen thresholds T_low = {ratio(holdout['t_low'])}, "
        f"T_high = {ratio(holdout['t_high'])}."
    )


def _n_poisoned(holdout: dict[str, Any]) -> int:
    synthetic = _headline_report(holdout).get("holdout_synthetic") or {}
    if synthetic.get("n_poisoned") is None:
        raise Refused("holdout.json carries no `holdout_synthetic.n_poisoned`")
    return int(synthetic["n_poisoned"])


def _tests(reduction: dict[str, Any]) -> str:
    return (
        f"McNemar on non-poisoned rows: exact {p_value(reduction['mcnemar_p'])}, "
        f"server-clustered {p_value(reduction.get('mcnemar_p_clustered'))}"
    )


def _reduction_line(holdout: dict[str, Any], baseline: Any) -> str:
    """M2 as triage: of the real tools the scanner wrongly flagged, the share Stage 1 dismisses.

    The v1 README used to print the development row's auto-confirm FPR against the scanner's
    flag rate ("97.1% fewer false positives"), which counted every tool sent to review as a
    success. That comparison is gone; the development figures sit on their own labelled line
    (audit F1, F3, F4).
    """
    if "fp_dismissed_real_triage" not in holdout:
        raise Refused(
            "holdout.json carries no `fp_dismissed_real_triage`: M2's real-subset triage figure "
            "is computed by `bench.report_final`; run `make bench`"
        )
    real = holdout.get("fp_dismissed_real_triage") or {}
    own = _headline_report(holdout).get("holdout") or {}
    triage = own.get("fp_reduction_triage") or {}
    standalone = own.get("fp_reduction_standalone") or {}
    if "fp_reduction_triage" not in own or "fp_reduction_standalone" not in own:
        raise Refused(
            "holdout.json carries no `fp_reduction_triage` / `fp_reduction_standalone` for the "
            "headline model"
        )
    name = baseline_name(baseline)
    if real.get("point") is None:
        # The scanner flagged no real holdout tool, so there is nothing to dismiss: undefined,
        # and said so rather than printed as 0% or 100%.
        real_part = (
            f"{name} flagged none of the real holdout tools, so the share of its false positives "
            "Stage 1 dismisses is undefined there."
        )
    else:
        real_part = (
            f"of the {int(real['n'])} real tools {name} wrongly flagged, Stage 1 dismisses "
            f"{pct(real['point'])}{_interval(real)}."
        )
    negatives = int(own["n"]) - _n_poisoned(holdout)
    if triage.get("relative") is None:
        all_part = (
            f"Across all {negatives} non-poisoned holdout rows the scanner flagged none, so the "
            "all-negatives figure is undefined too."
        )
    else:
        all_part = (
            f"Across all {negatives} non-poisoned holdout rows, synthetic included, it dismisses "
            f"{pct(triage['relative'])} of the scanner's false positives (95% cluster-bootstrap "
            f"CI {pct(triage['relative_lo'])} to {pct(triage['relative_hi'])}; "
            f"{_tests(triage)})."
        )
    standalone_part = ""
    if standalone.get("sieve_fpr") is not None and standalone.get("baseline_fpr") is not None:
        standalone_part = (
            f" On its own it is not a scanner replacement: Stage 1 flags "
            f"{pct(standalone['sieve_fpr'])} of those rows against the scanner's "
            f"{pct(standalone['baseline_fpr'])}, at recall {ratio(standalone['sieve_recall'])} "
            f"against {ratio(standalone['baseline_recall'])} on the synthetic attacks; the real "
            "holdout holds no poisoned row, so no recall exists on real tools."
        )
    return (
        f"**M2 · Scanner false positives Stage 1 dismisses, real holdout tools:** {real_part} "
        f"{all_part}{standalone_part}"
    )


def _recall_line(holdout: dict[str, Any]) -> str:
    """The release gate, read off the exact binomial bound: at k = n the cluster bootstrap
    collapses to [1, 1] and says nothing (audit F7)."""
    exact = holdout.get("recall_cp") or {}
    if holdout.get("recall") is None or exact.get("lo") is None:
        raise Refused(
            "holdout.json carries no poisoned recall or no `recall_cp`: the release gate is "
            "unmeasured, or `bench.report_final` predates the exact bound; run `make bench`"
        )
    gate = holdout.get("release_gate") or {}
    floor = gate.get("recall_lower_bound")
    exact_clears = floor is not None and exact["lo"] >= floor
    return (
        f"**Holdout poisoned recall (synthetic attacks; the real holdout holds none):** "
        f"{int(exact['k'])} of {int(exact['n'])} caught at T_low, recall "
        f"{ratio(holdout['recall'])} with an exact (Clopper-Pearson) 95% lower bound of "
        f"{ratio(exact['lo'])}. The release gate is {ratio(gate.get('recall_point'))} point and "
        f"{ratio(floor)} lower bound; this run "
        f"{'passes' if holdout.get('release_gate_passed') else 'does not pass'} it, and the "
        f"exact bound {'clears' if exact_clears else 'does not clear'} the floor too."
    )


def _real_poisoned_line(holdout: dict[str, Any], val_ood: Any) -> str:
    """M13, printed beside what its recall costs on clean out-of-distribution tools (F8)."""
    stage1 = holdout.get("real_poisoned_recall_stage1")
    pipeline = holdout.get("real_poisoned_recall_pipeline")
    if stage1 is None or pipeline is None:
        raise Refused(
            "holdout.json carries no `real_poisoned` recall: the M13 slice was empty for this "
            "run, so the number does not exist and is not going to be guessed"
        )
    ood = _headline_report(holdout).get("test_ood") or {}
    clean, docker = ood.get("mcptox_clean") or {}, ood.get("docker") or {}
    line = (
        f"**M13 · `real_poisoned` recall:** {ratio(stage1)} at Stage 1 (not dismissed at "
        f"T_low) and {ratio(pipeline)} auto-confirmed, {ratio(stage1 - pipeline)} going to "
        f"human review, over {int(holdout['n_eval_only'])} MCPTox attacks collected from the "
        "wild."
    )
    if clean.get("point") is not None and docker.get("point") is not None:
        line += (
            f" The cost at the same threshold: Stage 1 also flags {pct(clean['point'])} of "
            f"MCPTox's {int(clean['n'])} clean tools and {pct(docker['point'])} of "
            f"{int(docker['n'])} Docker catalogue tools."
        )
    else:
        # Never silent: a recall printed without its false-positive cost reads as free (F8).
        line += " Its false-positive cost on clean out-of-distribution tools was not measured."
    model = holdout.get("headline_model")
    row = (((val_ood or {}).get("models") or {}).get(model) or {}).get("per_source") or {}
    independent = row.get("all_but_stress") or {}
    if independent.get("recall_at_t_low") is not None:
        line += (
            " On independent attack sets (val-OOD; labels from each source's metadata; "
            f"report-only) recall at T_low is {ratio(independent['recall_at_t_low'])} over "
            f"{int(independent['n_poisoned'])} attacks."
        )
    return line


def _published_seed(row: dict[str, Any], selection: Any) -> Any:
    """The per-seed block of the artefact `selection.json` published (D14), or None."""
    published = str((selection or {}).get("published_dir") or "")
    seed = published.rsplit("-s", 1)[-1] if "-s" in published else ""
    for block in row.get("per_seed") or []:
        if str(block.get("seed")) == seed:
            return block
    return None


def _development_line(row: dict[str, Any], published: Any, n_records: int) -> str:
    """Development (pooled out-of-fold) figures, each labelled with its population (F1, F3,
    F5, F20). Development holds no real poisoned row, so every recall here is synthetic."""
    real = row.get("fpr_stage1_real_at_t_low") or {}
    dismissed = row.get("fp_dismissed_real_triage") or {}
    confirm = row.get("fp_reduction_vs_baseline") or {}
    if real.get("point") is None or dismissed.get("point") is None:
        raise Refused(
            "the headline ablation row carries no `fpr_stage1_real_at_t_low` / "
            "`fp_dismissed_real_triage`; run `make bench`"
        )
    line = (
        f"*Development, pooled out-of-fold over {n_records:,} rows, ablation row {row['row']} "
        f"averaged over three seeds: Stage 1 sends {pct(real['point'])} of real development "
        f"tools to review and dismisses {pct(dismissed['point'])} of the scanner's false "
        "positives on them. "
    )
    if published is not None:
        own = published.get("fpr_stage1_real_at_t_low") or {}
        own_dismissed = published.get("fp_dismissed_real_triage") or {}
        if own.get("point") is not None and own_dismissed.get("point") is not None:
            line += (
                f"The published artefact is seed {published['seed']} (the median seed by "
                f"macro-F1, D14): {pct(own['point'])} reviewed, {pct(own_dismissed['point'])} "
                "dismissed. "
            )
    recall = row.get("oof_recall_at_t_low") or {}
    auto = (row.get("fp_reduction_all_sources") or {}).get("sieve_recall")
    line += (
        "Counting only auto-confirmed tools as flagged, the row's false-positive rate is "
        f"{pct(confirm.get('sieve_fpr'))} against the scanner's "
        f"{pct(confirm.get('baseline_fpr'))}; that comparison leaves out the review band, so it "
        "is not a false-positive reduction. Recall on development attacks, all synthetic: "
        f"{ratio(recall.get('point'))} at T_low, {ratio(auto)} auto-confirmed.*"
    )
    return line


def _operating_points_line(points_file: Any) -> str | None:
    """A post-hoc look at a less conservative threshold rule, never adopted for v1 (F6)."""
    if not isinstance(points_file, dict):
        return None
    chosen = [
        point
        for rows in (points_file.get("per_seed") or {}).values()
        for point in rows
        if point.get("target_recall") == 0.98
    ]
    if not chosen:
        return None

    def span(key: str, formatter: Any) -> str:
        values = sorted(point[key] for point in chosen)
        low, high = formatter(values[0]), formatter(values[-1])
        return low if low == high else f"{low} to {high}"

    return (
        "*Post-hoc and not adopted for v1 (`operating_points.json`; development out-of-fold "
        "only, each threshold chosen on four folds and applied to the fifth): a point-recall "
        f"rule of 0.98 would send {span('real_negative_review_share', pct)} of real "
        f"development tools to review and dismiss {span('yara_fp_dismissed', pct)} of the "
        f"scanner's false positives, at recall {span('recall', ratio)} across the "
        f"{len(chosen)} seeds. Adopting it would be a new version, measured on a new sealed "
        "test set.*"
    )


def _labels_line(label_audit: Any) -> str:
    """ADR-009: every number carries the labels caveat until the dev-50 human audit is recorded
    in `label_audit.json` (`python -m bench.label.audit --gate dev50`), and says so if it fails."""
    dev50 = (label_audit or {}).get("dev50") if isinstance(label_audit, dict) else None
    if not isinstance(dev50, dict) or not dev50.get("ready"):
        return (
            "*Labels: a three-model LLM ensemble. The dev-50 human audit and the test-ID gold "
            "labels are still pending (ADR-009), and every figure above carries that caveat "
            "until they are recorded.*"
        )
    kappa = (
        f"Cohen's kappa {dev50['kappa']:.2f} (95% CI {dev50['kappa_lo']:.2f} to "
        f"{dev50['kappa_hi']:.2f}) against the owner's blind labels on {int(dev50['n'])} real "
        "development rows"
    )
    if not dev50.get("passed"):
        return (
            f"*Labels: a three-model LLM ensemble whose dev-50 human audit FAILED its "
            f"pre-registered gate ({kappa}, below {dev50['gate']:.2f}). Under ADR-009 the "
            "trained models are void until the labels are corrected and the models retrained.*"
        )
    test_id = "recorded" if (label_audit or {}).get("test_id") else "still pending"
    return (
        f"*Labels: a three-model LLM ensemble, checked by the dev-50 human audit: {kappa}, "
        f"passing the pre-registered {dev50['gate']:.2f} gate. The test-ID gold labels are "
        f"{test_id}.*"
    )


def _required(payload: dict[str, Any], key: str, source: str) -> Any:
    """A provenance key is a fact too. A default here would print a 0 nobody measured."""
    value = payload.get(key)
    if value is None:
        raise Refused(
            f"{source} carries no `{key}`; the provenance line states what produced the "
            "numbers above, so a missing key is a refusal rather than a default"
        )
    return value


def _provenance_line(
    holdout: dict[str, Any], ablations: dict[str, Any], selection: Any, curves: Any
) -> str:
    dataset = holdout.get("dataset_version") or _required(
        ablations, "dataset_version", "ablations.json"
    )
    parts = [
        f"dataset `{dataset}`",
        f"{int(_required(ablations, 'n_boot', 'ablations.json'))} bootstrap resamples",
    ]
    if isinstance(selection, dict) and selection.get("winner"):
        parts.append(f"encoder `{selection['winner']}` (`selection.json`)")
    if isinstance(curves, dict) and curves.get("recall_vs_threshold"):
        parts.append(
            f"{len(curves['recall_vs_threshold'])} threshold points in `curves.json`",
        )
    parts.append(f'holdout opened once for "{_required(holdout, "reason", "holdout.json")}"')
    return (
        "*Every figure above is written by `make readme` from `bench/results/` and none of it "
        "is typed by hand: " + "; ".join(parts) + ".*"
    )


def render_block(results_dir: Path) -> str:
    """The four headline lines (M1, M2, recall, M13), then the labelled development, post-hoc,
    labels and provenance lines. No em dash is ever emitted."""
    holdout = _read(
        results_dir,
        "holdout.json",
        required=True,
        hint=(
            ': the holdout has not been opened yet. Run `make eval-final REASON="..."` once the '
            "model exists; until then the README keeps its placeholder block"
        ),
    )
    ablations = _read(
        results_dir,
        "ablations.json",
        required=True,
        hint=": run `make bench` first; the development figures live there",
    )
    baseline = _read(results_dir, "baseline.json", required=False)
    curves = _read(results_dir, "curves.json", required=False)
    selection = _read(results_dir, "selection.json", required=False)
    val_ood = _read(results_dir, "val_ood.json", required=False)
    points_file = _read(results_dir, "operating_points.json", required=False)
    label_audit = _read(results_dir, "label_audit.json", required=False)

    row = headline_row(ablations, (selection or {}).get("winner"))
    n_records = int(_required(ablations, "n_records", "ablations.json"))
    lines = [
        _fpr_line(holdout, baseline),
        _reduction_line(holdout, baseline),
        _recall_line(holdout),
        _real_poisoned_line(holdout, val_ood),
        _development_line(row, _published_seed(row, selection), n_records),
    ]
    post_hoc = _operating_points_line(points_file)
    if post_hoc:
        lines.append(post_hoc)
    lines += [_labels_line(label_audit), _provenance_line(holdout, ablations, selection, curves)]
    return "\n" + "\n\n".join(lines) + "\n"


#: The README's second generated block: every scored ablation row, side by side.
RESULTS_START = "<!-- results:start -->"
RESULTS_END = "<!-- results:end -->"


def _cell(value: Any, formatter: Any) -> str:
    return "n/a" if value is None else str(formatter(value))


def _f1(row: dict[str, Any]) -> str:
    mean, std = row.get("macro_f1_mean"), row.get("macro_f1_std")
    if mean is None:
        return "n/a"
    # A two-class head's macro-F1 averages two classes, not three: not comparable, so say so.
    notes = ["1 seed"] if std is None else []
    if int(row.get("num_labels") or 3) == 2:
        notes.insert(0, "2 classes")
    figure = f"{mean:.3f}" if std is None else f"{mean:.3f} ± {std:.3f}"
    return f"{figure} ({', '.join(notes)})" if notes else figure


def _point(row: dict[str, Any], key: str) -> Any:
    value = row.get(key)
    return value.get("point") if isinstance(value, dict) else value


def render_results(results_dir: Path) -> str:
    """The ablation matrix as one table, from `ablations.json` only; breakdown rows are left to
    the file (they slice row 4, they do not compete with it)."""
    ablations = _read(results_dir, "ablations.json", required=True, hint=": run `make bench`")
    selection = _read(results_dir, "selection.json", required=False)
    lines = [
        "",
        "| Row | Configuration | Macro-F1 (3 classes) | Poisoned recall at T_low | Real "
        "non-poisoned sent to review | Routed overall | Minimal-pair accuracy |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for row in sorted(ablations.get("rows") or [], key=lambda r: r.get("row", 0)):
        if row.get("breakdown_of"):
            continue
        head = f"| {row['row']} | {row['name']} |"
        if row.get("status") == "deferred":
            lines.append(f"{head} deferred to v1.1 (D8) | | | | |")
            continue
        # The column reads the real-only rate: `fpr_stage1_at_t_low` also counts synthetic
        # negatives, which the column's name excludes (audit F20).
        lines.append(
            f"{head} {_f1(row)} | {_cell(_point(row, 'oof_recall_at_t_low'), ratio)} | "
            f"{_cell(_point(row, 'fpr_stage1_real_at_t_low'), pct)} | "
            f"{_cell(row.get('routing_rate'), pct)} | "
            f"{_cell(row.get('minimal_pair_accuracy'), ratio)} |"
        )
    lines += [
        "",
        f"*Written by `make readme` from `bench/results/ablations.json`: pooled out-of-fold "
        f"predictions over {int(_required(ablations, 'n_records', 'ablations.json'))} "
        "development rows, "
        "mean ± sample std over three seeds unless marked. Rows 1 and 13 are rules, not "
        "classifiers, so they have no macro-F1. Development holds no real poisoned row, so the "
        "recall column is measured on synthetic attacks only.*",
    ]
    published = _published_line(ablations, selection)
    if published:
        lines += ["", published]
    lines.append("")
    return "\n".join(lines)


def _published_line(ablations: dict[str, Any], selection: Any) -> str | None:
    """The shipped artefact's own seed, beside the three-seed means above (audit F5)."""
    try:
        row = headline_row(ablations, (selection or {}).get("winner"))
    except Refused:
        return None
    published = _published_seed(row, selection)
    if published is None:
        return None
    seeds = sorted(row.get("per_seed") or [], key=lambda block: block.get("seed", 0))
    spread = ", ".join(
        f"seed {block['seed']} {_cell(_point(block, 'fpr_stage1_real_at_t_low'), pct)}"
        for block in seeds
    )
    return (
        f"*Row {row['row']} per seed, real development tools sent to review: {spread}. The "
        f"published artefact (`{selection['published_dir']}`, the median seed by macro-F1, "
        f"pre-registered as D14) uses T_low = {ratio(published.get('t_low'))} and routes "
        f"{_cell(published.get('routing_rate'), pct)} of development rows to review.*"
    )


def replace_results(text: str, block: str) -> str:
    """Fill the results block when the README has one; a README without it is left alone."""
    if RESULTS_START not in text and RESULTS_END not in text:
        return text
    if RESULTS_START not in text or RESULTS_END not in text:
        raise Refused("the README has only one of the two results markers; restore the pair")
    head, _, rest = text.partition(RESULTS_START)
    _, _, tail = rest.partition(RESULTS_END)
    return head + RESULTS_START + block + RESULTS_END + tail


def replace_block(text: str, block: str) -> str:
    if MARK_START not in text or MARK_END not in text:
        raise Refused(
            f"the README has no {MARK_START} ... {MARK_END} block to fill; the block is part of "
            "the committed file"
        )
    head, _, rest = text.partition(MARK_START)
    _, _, tail = rest.partition(MARK_END)
    return head + MARK_START + block + MARK_END + tail


def write_atomically(readme: Path, text: str) -> None:
    """Write beside the README, then rename over it.

    `write_text` truncates first, so a failure half way through leaves a README with part of a
    number block and no way to tell. `os.replace` is atomic on Windows and POSIX alike, so the
    file a reader sees is either the old one or the whole new one. A failed attempt leaves the
    temporary file behind rather than removing evidence of it.
    """
    temporary = readme.with_suffix(".md.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, readme)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python docs/scripts/readme_numbers.py")
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=DEFAULT_RESULTS,
        help="exists so the test can point at a temp directory; do not use it otherwise",
    )
    parser.add_argument("--readme", type=Path, default=DEFAULT_README)
    parser.add_argument(
        "--check",
        action="store_true",
        help="write nothing; exit 1 when the README's generated blocks differ from bench/results",
    )
    ns = parser.parse_args(argv)

    try:
        block = render_block(ns.results_dir)
    except Refused as refusal:
        print(f"make readme: {refusal}", file=sys.stderr)
        return 2

    readme = Path(ns.readme)
    text = readme.read_text(encoding="utf-8")
    try:
        updated = replace_results(replace_block(text, block), render_results(ns.results_dir))
    except Refused as refusal:
        print(f"make readme: {refusal}", file=sys.stderr)
        return 2
    if ns.check:
        # CI's drift guard: a hand-edited number, or results regenerated without `make readme`.
        if updated != text:
            print(
                f"make readme: {readme} is out of date with {ns.results_dir}; run `make readme`",
                file=sys.stderr,
            )
            return 1
        print(f"make readme: {readme} matches {ns.results_dir}")
        return 0
    if updated != text:
        write_atomically(readme, updated)
    print(f"make readme: wrote the headline numbers into {readme}")
    return 0


__all__ = [
    "MARK_END",
    "MARK_START",
    "Refused",
    "baseline_name",
    "headline_row",
    "main",
    "p_value",
    "pct",
    "ratio",
    "render_block",
    "replace_block",
    "write_atomically",
]

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
