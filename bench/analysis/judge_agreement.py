"""Judge-vs-human agreement, with the circularity disclosure attached to the number itself.

The synthetic payloads in SieveBench were authored by a model from the same family as the
judge, so an agreement figure computed over them measures partly self-consistency. The caveat
is a required field of the output and the figure is broken down per `generator_model` so the
circularity is visible rather than averaged away. This goes in the datasheet and never in the
README (PRD §11.5, §9.5).

The judge arrives through the `Judge` protocol from a factory: `RecordingJudge(AnthropicJudge)`
under `SIEVE_RERECORD=1`, `ReplayJudge` otherwise, resolved inside `main` only.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from sieve.eval.metrics import cohens_kappa, gwet_ac1
from sieve.protocols import Judge, JudgeOutput
from sieve.types import CorpusRecord, Label

log = logging.getLogger("bench.analysis.judge_agreement")

CAVEAT = "the same model family authored the synthetic payloads; this figure supports no claim."

#: The judge answers on three values; the human gold label has three classes. Both collapse
#: onto the same axis — is this row poisoned — and `uncertain` stays its own value rather than
#: being folded into either side, because "route to a human" is not an answer.
JUDGE_TO_AXIS: dict[str, str] = {
    "confirmed": "poisoned",
    "dismissed": "not_poisoned",
    "uncertain": "uncertain",
}

JudgeFactory = Callable[[], Judge]


def gold_axis(label: Label) -> str:
    return "poisoned" if label == "poisoned" else "not_poisoned"


def _answers(
    records: Sequence[CorpusRecord], judge: Judge
) -> tuple[list[str], list[str], list[str]]:
    """`(gold, judge, failures)` on the poisoned axis, in record order."""
    gold: list[str] = []
    verdicts: list[str] = []
    failures: list[str] = []
    for record in records:
        answer = judge.judge(record.to_tool_record(), 0.5)
        gold.append(gold_axis(record.label))
        if isinstance(answer, JudgeOutput):
            verdicts.append(JUDGE_TO_AXIS[answer.verdict])
        else:
            verdicts.append("judge_failed")
            failures.append(record.id)
    return gold, verdicts, failures


def _scores(gold: Sequence[str], verdicts: Sequence[str]) -> dict[str, Any]:
    if not gold:
        return {"n": 0, "kappa": None, "gwet_ac1": None, "per_class": {}}
    per_class: dict[str, dict[str, float]] = {}
    for value in sorted(set(gold)):
        rows = [i for i, g in enumerate(gold) if g == value]
        agreed = sum(1 for i in rows if verdicts[i] == value)
        per_class[value] = {"n": float(len(rows)), "observed_agreement": agreed / len(rows)}
    return {
        "n": len(gold),
        "kappa": cohens_kappa(list(gold), list(verdicts)),
        "gwet_ac1": gwet_ac1(list(gold), list(verdicts)),
        "per_class": per_class,
    }


def agreement(records: Sequence[CorpusRecord], judge: Judge) -> dict[str, Any]:
    """Overall and per-`generator_model` agreement, with the mandatory disclosure attached."""
    gold, verdicts, failures = _answers(records, judge)
    per_generator: dict[str, dict[str, Any]] = {}
    for value in sorted({r.generator_model or "none" for r in records}):
        rows = [i for i, r in enumerate(records) if (r.generator_model or "none") == value]
        per_generator[value] = _scores([gold[i] for i in rows], [verdicts[i] for i in rows])
    return {
        "caveat": CAVEAT,
        "judge_model": judge.model,
        "overall": _scores(gold, verdicts),
        "per_generator_model": per_generator,
        "judge_failures": failures,
        "n_judge_failures": len(failures),
    }


def write_agreement(payload: dict[str, Any], path: Path) -> Path:
    if not payload.get("caveat"):
        raise ValueError("refusing to write an agreement figure with no disclosure string")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return path


def run(records: Sequence[CorpusRecord], judge: Judge, results_dir: Path) -> Path:
    return write_agreement(agreement(records, judge), results_dir / "judge_agreement.json")


# ----------------------------------------------------------------------------------- CLI


def _judge_factory(transcript: Path, model: str) -> JudgeFactory:
    """`RecordingJudge(AnthropicJudge)` only under SIEVE_RERECORD=1; `ReplayJudge` otherwise."""

    def factory() -> Judge:
        try:
            from sieve.judge.record import RecordingJudge, ReplayJudge
        except ImportError as exc:  # pragma: no cover - only before the judge lane merges
            raise SystemExit("judge lane not merged yet") from exc
        if os.environ.get("SIEVE_RERECORD") == "1":
            from sieve.judge.client import AnthropicJudge

            log.warning("SIEVE_RERECORD=1: calling the live API and re-recording %s", transcript)
            return RecordingJudge(AnthropicJudge(model=model), transcript)
        if not transcript.exists():
            raise SystemExit(
                f"{transcript} is missing; record it once with "
                f"`SIEVE_RERECORD=1 python -m bench.analysis.judge_agreement`"
            )
        return ReplayJudge(transcript)

    return factory


def main(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(prog="python -m bench.analysis.judge_agreement")
    parser.add_argument("--corpus", type=Path, default=Path("bench/data/labelled.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("bench/results/judge_outputs.jsonl"))
    parser.add_argument("--results-dir", type=Path, default=Path("bench/results"))
    parser.add_argument("--model", default="claude-haiku-4-5")
    ns = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    from sieve.model.data import load_corpus

    records = [r for r in load_corpus(ns.corpus) if r.split == "dev"]
    return run(records, _judge_factory(ns.out, ns.model)(), ns.results_dir)


__all__ = ["CAVEAT", "agreement", "gold_axis", "main", "run", "write_agreement"]

if __name__ == "__main__":  # pragma: no cover
    main()
