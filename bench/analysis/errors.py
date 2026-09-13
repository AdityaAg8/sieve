"""`bench/results/errors.md`: every out-of-fold false positive and false negative at `T_low`.

Reading and annotating this file is allowed at any time and may inform prompt, synthesis or
serialisation changes — which is exactly why it must never be built from the holdout (PRD
§11.6). The writer refuses a record whose split is not `dev` rather than trusting the caller.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import numpy.typing as npt

from sieve.protocols import Thresholds
from sieve.serialise import serialise
from sieve.types import LABELS, CorpusRecord

log = logging.getLogger("bench.analysis.errors")

HEADER = """# Out-of-fold errors at T_low

Every development-split row the classifier got wrong at the frozen `T_low`, from pooled
out-of-fold predictions. The `note:` line under each entry is for a human; annotating it is
allowed at any time, because none of this is holdout data.
"""


class NotOutOfFold(ValueError):  # noqa: N818 - reads as the condition it guards
    """A record that is not a development row reached the error dump."""


def _rows(
    records: Sequence[CorpusRecord],
    probs: npt.NDArray[np.float64],
    t_low: float,
) -> list[tuple[str, CorpusRecord, npt.NDArray[np.float64]]]:
    poisoned_column = LABELS.index("poisoned")
    out: list[tuple[str, CorpusRecord, npt.NDArray[np.float64]]] = []
    for record, row in zip(records, probs, strict=True):
        flagged = float(row[poisoned_column]) >= t_low
        truly_poisoned = record.label == "poisoned"
        if flagged and not truly_poisoned:
            out.append(("false positive", record, row))
        elif truly_poisoned and not flagged:
            out.append(("false negative", record, row))
    return out


def render(
    records: Sequence[CorpusRecord], probs: npt.NDArray[np.float64], thresholds: Thresholds
) -> str:
    non_dev = [r.id for r in records if r.split != "dev"]
    if non_dev:
        raise NotOutOfFold(
            f"{len(non_dev)} non-dev record(s) reached the error dump ({non_dev[:5]}); "
            "errors.md is built from out-of-fold predictions only (PRD §11.6)"
        )
    errors = _rows(records, np.asarray(probs, dtype=np.float64), thresholds.t_low)
    lines = [
        HEADER,
        f"T_low = {thresholds.t_low:.6f}  ·  T_high = {thresholds.t_high:.6f}  ·  "
        f"{len(errors)} error(s) over {len(records)} dev row(s)",
        "",
    ]
    for kind, record, row in errors:
        probabilities = ", ".join(f"{label}={float(row[i]):.4f}" for i, label in enumerate(LABELS))
        lines.extend(
            [
                f"## {record.id} — {kind}",
                "",
                f"- gold: `{record.label}`",
                f"- probabilities: {probabilities}",
                f"- difficulty: `{record.difficulty or 'unscored'}`",
                f"- concealment: `{record.concealment}`",
                f"- source: `{record.source}`",
                f"- server: `{record.server_name}`",
                "",
                "```",
                serialise(record.to_tool_record()),
                "```",
                "",
                "note:",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def write_errors(
    records: Sequence[CorpusRecord],
    probs: npt.NDArray[np.float64],
    thresholds: Thresholds,
    path: Path,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(records, probs, thresholds), encoding="utf-8", newline="\n")
    return path


def main(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(prog="python -m bench.analysis.errors")
    parser.add_argument("--dirs", nargs="+", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, default=Path("bench/results"))
    ns = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    from sieve.model import thresholds as th
    from sieve.model.calibrate import apply, pool_oof
    from sieve.model.data import load_corpus

    records = [r for r in load_corpus(ns.corpus) if r.split == "dev"]
    by_id = {r.id: r for r in records}
    logits, _, ids = pool_oof(ns.dirs, expected_ids=list(by_id))
    thresholds = th.load(ns.model_dir)
    probs = apply(logits, thresholds.temperature)
    ordered = [by_id[i] for i in ids]
    path = write_errors(ordered, probs, thresholds, ns.results_dir / "errors.md")
    log.info("wrote %s", path)
    return path


__all__ = ["NotOutOfFold", "main", "render", "write_errors"]

if __name__ == "__main__":  # pragma: no cover
    main()
