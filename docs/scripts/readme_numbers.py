"""Rewrite the README's headline-number block from `bench/results/` (PRD §17.1, Task 10.1).

`make readme` runs this script by path. It is the only writer of the block between
`<!-- numbers:start -->` and `<!-- numbers:end -->`, and the four numbers PRD §17.1 names are
never typed by hand:

1. `FPR_pipeline` on the real subset, with its cluster-bootstrap 95% CI (`holdout.json`),
2. false-positive reduction against the scanner baseline on the **real** subset, with its
   paired-bootstrap CI and McNemar's exact p-value (`ablations.json`),
3. holdout poisoned recall, point and 95% lower bound (`holdout.json`),
4. the `real_poisoned` recall (M13), Stage 1 and after the pipeline (`holdout.json`).

It refuses, loudly and without touching the README, when any of those four is missing: a README
carrying two of the four numbers is not shippable, and lines 3 and 4 cannot be invented at all.
The synthetic-inclusive reduction is reported *below* the five headline lines and labelled, per
M2. `baseline.json`, `curves.json` and `selection.json` supply the names and provenance around
the numbers (which scanner, which encoder, which dataset version), never a headline figure.

This script lives in `docs/` rather than `bench/` because L10 owns `docs/` (PRD §19) and
because `bench/` is the tree the sealed-holdout grep polices; it reads the holdout *report*,
never a holdout row.
"""

from __future__ import annotations

import argparse
import json
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
#: at. Read from settings rather than repeated here, for the same reason thresholds are.
try:  # pragma: no cover - exercised indirectly; the fallback only matters without the package
    from sieve.config import settings

    HEADLINE_JUDGE = settings.judge_model
except Exception:  # pragma: no cover - `sieve` must be importable for `make readme`
    HEADLINE_JUDGE = "claude-haiku-4-5"


class Refused(Exception):  # noqa: N818 - it is a refusal to write, not an error condition
    """The generator will not write a README it would have to invent a number for."""


# --------------------------------------------------------------------------- formatting


def pct(value: float | None) -> str:
    """A ratio as a percentage. One decimal place, so 0.1234 reads 12.3%."""
    if value is None:
        raise Refused("a percentage was asked for but the results file carries null")
    return f"{value * 100:.1f}%"


def points(value: float | None) -> str:
    """An absolute difference between two rates, in percentage points."""
    if value is None:
        raise Refused("a difference was asked for but the results file carries null")
    return f"{value * 100:.1f} points"


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


def headline_row(ablations: dict[str, Any]) -> dict[str, Any]:
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
    ]
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


def _fpr_line(holdout: dict[str, Any]) -> str:
    interval = holdout.get("fpr_pipeline_real") or {}
    if interval.get("point") is None:
        raise Refused(
            "holdout.json carries no `fpr_pipeline_real` point estimate: the real subset of the "
            "holdout was empty, so M1 is unmeasured and the README cannot state it"
        )
    return (
        f"**M1 · `FPR_pipeline` on the real subset:** {pct(interval['point'])} "
        f"(95% cluster-bootstrap CI {pct(interval['lo'])} to {pct(interval['hi'])}), "
        f"over {int(holdout['n_holdout'])} sealed holdout rows at the frozen thresholds "
        f"T_low = {ratio(holdout['t_low'])}, T_high = {ratio(holdout['t_high'])}."
    )


def _reduction_line(row: dict[str, Any], baseline: Any) -> str:
    reduction = row.get("fp_reduction_vs_baseline") or {}
    if reduction.get("point") is None:
        raise Refused(
            "the headline ablation row carries no `fp_reduction_vs_baseline`: the real subset "
            "was empty, so M2 is unmeasured"
        )
    relative = (
        f"{pct(reduction['relative'])} fewer false positives"
        if reduction.get("relative") is not None
        else "a false-positive reduction whose relative size is undefined "
        "(the baseline flagged no non-poisoned row)"
    )
    return (
        f"**M2 · False positives vs {baseline_name(baseline)}, real subset:** {relative} "
        f"({points(reduction['point'])} of absolute `FPR` difference, 95% paired "
        f"cluster-bootstrap CI {points(reduction['lo'])} to {points(reduction['hi'])}; "
        f"McNemar exact {p_value(reduction['mcnemar_p'])}), from ablations "
        f"row {row['row']}, {row['name']}."
    )


def _recall_line(holdout: dict[str, Any]) -> str:
    if holdout.get("recall") is None or holdout.get("recall_lcb") is None:
        raise Refused(
            "holdout.json carries no poisoned recall: the holdout had no poisoned row, so the "
            "release gate is unmeasured"
        )
    gate = holdout.get("release_gate") or {}
    return (
        f"**Holdout poisoned recall:** {ratio(holdout['recall'])} "
        f"(95% cluster-bootstrap lower bound {ratio(holdout['recall_lcb'])}); the release gate "
        f"is {ratio(gate.get('recall_point'))} point and "
        f"{ratio(gate.get('recall_lower_bound'))} lower bound, and this run "
        f"{'passes' if holdout.get('release_gate_passed') else 'does not pass'} it."
    )


def _real_poisoned_line(holdout: dict[str, Any]) -> str:
    stage1 = holdout.get("real_poisoned_recall_stage1")
    pipeline = holdout.get("real_poisoned_recall_pipeline")
    if stage1 is None or pipeline is None:
        raise Refused(
            "holdout.json carries no `real_poisoned` recall: the M13 slice was empty for this "
            "run, so the number does not exist and is not going to be guessed"
        )
    return (
        f"**M13 · `real_poisoned` recall:** {ratio(stage1)} at Stage 1 and {ratio(pipeline)} "
        f"after the pipeline, over {int(holdout['n_eval_only'])} rows collected from the wild. "
        "This is the generalisation number, reported whatever it is."
    )


def _provenance_line(
    holdout: dict[str, Any], ablations: dict[str, Any], selection: Any, curves: Any
) -> str:
    parts = [
        f"dataset `{holdout.get('dataset_version') or ablations.get('dataset_version')}`",
        f"{int(ablations.get('n_boot', 0))} bootstrap resamples",
    ]
    if isinstance(selection, dict) and selection.get("winner"):
        parts.append(f"encoder `{selection['winner']}` (`selection.json`)")
    if isinstance(curves, dict) and curves.get("recall_vs_threshold"):
        parts.append(
            f"{len(curves['recall_vs_threshold'])} threshold points in `curves.json`",
        )
    parts.append(f'holdout opened once for "{holdout.get("reason", "")}"')
    return (
        "*Every figure above is written by `make readme` from `bench/results/` and none of it "
        "is typed by hand: " + "; ".join(parts) + ".*"
    )


def _synthetic_line(row: dict[str, Any]) -> str:
    """M2's second figure, reported below the headline five lines and labelled as such."""
    reduction = row.get("fp_reduction_all_sources") or {}
    if reduction.get("point") is None:
        return (
            "*Reported second, per M2: the synthetic-inclusive false-positive reduction is not "
            "computable for this run.*"
        )
    relative = (
        pct(reduction["relative"]) + " fewer"
        if reduction.get("relative") is not None
        else points(reduction["point"]) + " of absolute difference"
    )
    return (
        f"*Reported second and labelled, per M2: including the synthetic rows the same row "
        f"shows {relative} false positives ({points(reduction['point'])} absolute, 95% CI "
        f"{points(reduction['lo'])} to {points(reduction['hi'])}, "
        f"McNemar exact {p_value(reduction['mcnemar_p'])}). The real-subset figure on line 2 is "
        "the headline; this one is easier and says less.*"
    )


def render_block(results_dir: Path) -> str:
    """The five headline lines, then M2's labelled second figure. No em dash is ever emitted."""
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
        hint=": run `make bench` first; the M2 figure lives there and is not invented here",
    )
    baseline = _read(results_dir, "baseline.json", required=False)
    curves = _read(results_dir, "curves.json", required=False)
    selection = _read(results_dir, "selection.json", required=False)

    row = headline_row(ablations)
    lines = [
        _fpr_line(holdout),
        _reduction_line(row, baseline),
        _recall_line(holdout),
        _real_poisoned_line(holdout),
        _provenance_line(holdout, ablations, selection, curves),
        _synthetic_line(row),
    ]
    return "\n" + "\n\n".join(lines) + "\n"


def replace_block(text: str, block: str) -> str:
    if MARK_START not in text or MARK_END not in text:
        raise Refused(
            f"the README has no {MARK_START} ... {MARK_END} block to fill; the block is part of "
            "the committed file"
        )
    head, _, rest = text.partition(MARK_START)
    _, _, tail = rest.partition(MARK_END)
    return head + MARK_START + block + MARK_END + tail


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python docs/scripts/readme_numbers.py")
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=DEFAULT_RESULTS,
        help="exists so the test can point at a temp directory; do not use it otherwise",
    )
    parser.add_argument("--readme", type=Path, default=DEFAULT_README)
    ns = parser.parse_args(argv)

    try:
        block = render_block(ns.results_dir)
    except Refused as refusal:
        print(f"make readme: {refusal}", file=sys.stderr)
        return 2

    readme = Path(ns.readme)
    text = readme.read_text(encoding="utf-8")
    try:
        updated = replace_block(text, block)
    except Refused as refusal:
        print(f"make readme: {refusal}", file=sys.stderr)
        return 2
    if updated != text:
        readme.write_text(updated, encoding="utf-8", newline="\n")
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
    "points",
    "ratio",
    "render_block",
    "replace_block",
]

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
