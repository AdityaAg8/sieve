"""Render the model card from committed results and push the artefact to the Hub.

The card is not prose about the model, it is the model's numbers: every slot is filled from
`bench/results/` and a slot left as an em dash is a build failure. It also refuses to render at
all unless `bench/results/test_opened.log` carries a line for the current git SHA — a card that
quotes holdout numbers without the log line is not publishable (PRD §11.6, §11.7).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sieve.model.version import UNKNOWN_SHA, git_sha

log = logging.getLogger("sieve.model.push")

CARD_TEMPLATE = Path(__file__).with_name("card.md")
EMPTY = "—"  # em dash: the "not filled in" marker the test forbids in the output
_SLOT = re.compile(r"\{\{(\w+)\}\}")
LINE_BREAK = chr(10)

DEFAULT_LICENCES = (
    "code under the repository's licence; corpus rows carry their own `source_license` and are "
    "redistributed only where that licence permits it"
)


class IncompleteCard(ValueError):  # noqa: N818 - names the artefact, not the failure mode
    """A card slot had no value. Publishing a card with a blank metric is not allowed."""


class HoldoutLogMissing(ValueError):  # noqa: N818 - names the artefact, not the failure mode
    """`test_opened.log` has no line for this git SHA, so the holdout numbers are unattributed."""


def read_json(path: Path) -> Any:
    if not path.exists():
        raise IncompleteCard(f"{path} is missing; run `make bench` before building the card")
    return json.loads(path.read_text(encoding="utf-8"))


def holdout_log_line(results_dir: Path, sha: str) -> str:
    """The `test_opened.log` entry for `sha`, verbatim. Raises when there is none."""
    path = results_dir / "test_opened.log"
    if not path.exists():
        raise HoldoutLogMissing(
            f"{path} does not exist; the holdout has never been opened for this version "
            "(`make eval-final`)"
        )
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if str(entry.get("git_sha")) == sha:
            return line
    raise HoldoutLogMissing(
        f"{path} has no line for git SHA {sha}; a model card that quotes holdout numbers "
        "without the log line is not publishable (PRD §11.6)"
    )


def _interval(block: Mapping[str, Any] | None) -> str | None:
    if not block or block.get("point") is None:
        return None
    return f"{block['point']:.4f} (95% CI {block['lo']:.4f}–{block['hi']:.4f})"


def _spread(std: float | None) -> str:
    """A sample std (ddof=1) is undefined for one seed; say so rather than print 0."""
    return "n/a" if std is None else f"{std:.4f}"


def _mean_std(row: Mapping[str, Any]) -> str | None:
    mean, std = row.get("macro_f1_mean"), row.get("macro_f1_std")
    if mean is None:
        return None
    return f"{mean:.4f} ± {_spread(std)} over seeds {row.get('seeds')}"


def _crossfit(config: Mapping[str, Any]) -> str | None:
    """Training plan D6: the cross-fitted recall and ECE `calibrate` writes, or None."""
    keys = ("recall_at_t_low_crossfit", "ece_after_crossfit", "ece_poisoned_after_crossfit")
    values = [config.get(k) for k in keys]
    if any(v is None for v in values):
        return None
    recall, top, poisoned = values
    return f"{recall:.4f} / {top:.4f}, {poisoned:.4f}"


def _table(breakdown: Mapping[str, Mapping[str, float]] | None) -> str | None:
    if not breakdown:
        return None
    parts = [
        f"`{bucket}` n={int(values['n'])} recall={values['recall_at_t_low']:.3f} "
        f"FPR_pipeline={values['fpr_pipeline']:.3f}"
        for bucket, values in breakdown.items()
    ]
    return "; ".join(parts)


def _selection_table(selection: Mapping[str, Any]) -> str | None:
    per = selection.get("per_architecture")
    if not per:
        return None
    lines = ["| Architecture | Pooled OOF macro-F1 | Seeds | p95 ms |", "|---|---|---|---|"]
    for name, values in per.items():
        lines.append(
            f"| `{name}` | {values['macro_f1_mean']:.4f} ± {_spread(values['macro_f1_std'])} | "
            f"{values['seeds']} | {values.get('p95_ms')} |"
        )
    return "\n".join(lines)


def _limitations(derived: str | None, extra: str | None) -> str | None:
    """What `eval_final` derived from the run, plus anything the release note adds."""
    parts = [p for p in (derived, extra) if p]
    return LINE_BREAK.join(parts) if parts else None


def _headline_row(ablations: Mapping[str, Any], row: int) -> Mapping[str, Any]:
    for entry in ablations.get("rows", []):
        if entry.get("row") == row:
            return dict(entry)
    raise IncompleteCard(f"ablations.json has no row {row}; the headline configuration is §11.5")


#: Never uploaded (H6): fold models, checkpoints, OOF arrays and their id lists stay local.
IGNORE_PATTERNS = ("fold*/**", "_checkpoints/**", "*.npy", "oof_ids.json")


def build_context(
    model_dir: Path,
    results_dir: Path,
    *,
    sha: str,
    headline_row: int = 4,
    limitations: str | None = None,
) -> dict[str, Any]:
    """Every card slot, read from committed artefacts. Nothing here is typed by a human."""
    ablations = read_json(results_dir / "ablations.json")
    selection = read_json(results_dir / "selection.json")
    holdout = read_json(results_dir / "holdout.json")
    read_json(results_dir / "curves.json")  # presence is the claim that the curves are published
    # H6: measured numbers come from bench/results, never from a figure copied into a config.
    latency = read_json(results_dir / "latency.json")
    export_parity = read_json(results_dir / "export_parity.json")
    config = read_json(model_dir / "sieve_config.json")
    row = _headline_row(ablations, headline_row)

    spread = config.get("t_low_spread") or []
    return {
        "model_version": config.get("model_version"),
        "base_model": config.get("base_model") or selection.get("winner"),
        "git_sha": sha,
        "dataset_version": holdout.get("dataset_version") or ablations.get("dataset_version"),
        "licences": DEFAULT_LICENCES,
        "training_command": config.get("training_command"),
        "selection_winner": selection.get("winner"),
        "selection_margin": (
            f"{selection['margin']:.4f} pooled OOF macro-F1"
            if selection.get("margin") is not None
            else None
        ),
        "selection_rule": selection.get("rule"),
        "selection_table": _selection_table(selection),
        "t_low": config.get("t_low"),
        "t_high": config.get("t_high"),
        "t_low_spread": (
            f"{min(spread):.4f}–{max(spread):.4f} over {len(spread)} refits" if spread else None
        ),
        "temperature": config.get("temperature"),
        "ece_before": config.get("ece_before"),
        "ece_after": config.get("ece_after"),
        "crossfit": _crossfit(config),
        "routing_rate": config.get("routing_rate"),
        "macro_f1": _mean_std(row),
        "fpr_stage1_at_t_low": _interval(row.get("fpr_stage1_at_t_low")),
        "fpr_pipeline": _interval(row.get("fpr_pipeline")),
        "minimal_pair_accuracy": row.get("minimal_pair_accuracy"),
        "holdout_recall": holdout.get("recall"),
        "holdout_recall_lcb": holdout.get("recall_lcb"),
        "holdout_fpr_pipeline": _interval(holdout.get("fpr_pipeline_real")),
        "real_poisoned_stage1": holdout.get("real_poisoned_recall_stage1"),
        "real_poisoned_pipeline": holdout.get("real_poisoned_recall_pipeline"),
        "per_tier": _table(holdout.get("per_tier")),
        "per_concealment": _table(holdout.get("per_concealment")),
        "onnx_int8_parity": export_parity.get("max_abs_delta"),
        "p50_ms": latency.get("p50_ms"),
        "p95_ms": latency.get("p95_ms"),
        "holdout_log_line": holdout_log_line(results_dir, sha),
        "limitations": _limitations(holdout.get("limitations"), limitations),
    }


def template_slots(template: Path = CARD_TEMPLATE) -> list[str]:
    """Every `{{name}}` the card asks for, in order of first appearance."""
    seen: list[str] = []
    for name in _SLOT.findall(template.read_text(encoding="utf-8")):
        if name not in seen:
            seen.append(name)
    return seen


def unfilled_slots(context: Mapping[str, Any], template: Path = CARD_TEMPLATE) -> list[str]:
    """The slots the result files did not supply.

    Tracked by name rather than by sniffing the rendered text for an em dash: prose is allowed
    to contain one, and a card that failed to fill a slot should not depend on punctuation.
    """
    return [
        name
        for name in template_slots(template)
        if context.get(name) is None or context.get(name) == ""
    ]


def render_card(context: Mapping[str, Any], template: Path = CARD_TEMPLATE) -> str:
    """Fill the template; a slot with no value renders as `EMPTY` and `write_card` refuses it."""
    text = template.read_text(encoding="utf-8")

    def fill(match: re.Match[str]) -> str:
        value = context.get(match.group(1))
        return EMPTY if value is None or value == "" else str(value)

    return _SLOT.sub(fill, text)


def write_card(context: Mapping[str, Any], out_dir: Path, template: Path = CARD_TEMPLATE) -> Path:
    blank = unfilled_slots(context, template)
    if blank:
        raise IncompleteCard(
            f"the model card has {len(blank)} unfilled slot(s): {blank}; every number comes "
            "from bench/results and none may be left blank"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "README.md"
    path.write_text(render_card(context, template), encoding="utf-8", newline="\n")
    return path


def main(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(prog="python -m sieve.model.push")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--results-dir", type=Path, default=Path("bench/results"))
    parser.add_argument("--headline-row", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--limitations",
        type=Path,
        default=None,
        help="markdown file appended to the limitations derived from holdout.json",
    )
    ns = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    sha = git_sha(short=False)
    if sha == UNKNOWN_SHA:
        raise IncompleteCard(
            "no git SHA is available; a published card records the commit that trained it"
        )
    extra = ns.limitations.read_text(encoding="utf-8") if ns.limitations else None
    context = build_context(ns.model_dir, ns.results_dir, sha=sha, limitations=extra)
    path = write_card(context, ns.model_dir)
    log.info("wrote %s", path)
    if ns.dry_run:
        log.info("dry run: not uploading to %s", ns.repo)
        return path

    from huggingface_hub import HfApi

    HfApi().upload_folder(
        folder_path=str(ns.model_dir),
        repo_id=ns.repo,
        repo_type="model",
        ignore_patterns=list(IGNORE_PATTERNS),
    )
    log.info("pushed %s to %s", ns.model_dir, ns.repo)
    return path


__all__ = [
    "CARD_TEMPLATE",
    "EMPTY",
    "HoldoutLogMissing",
    "IncompleteCard",
    "build_context",
    "main",
    "template_slots",
    "unfilled_slots",
    "render_card",
    "holdout_log_line",
    "write_card",
]

if __name__ == "__main__":  # pragma: no cover
    main()
