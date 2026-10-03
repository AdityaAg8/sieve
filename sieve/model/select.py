"""The pre-registered architecture selection rule. It is the only rule, and it has no override.

The point is that the choice was written down before the numbers existed. There is no flag here
that lets a human pick the other architecture: someone who disagrees changes `RULE` in a pull
request, and the pull request is the record (PRD §7.2, §11.5).

Nothing in this module reads a holdout row. Selection is a development-set decision.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np

from sieve.eval.metrics import macro_f1
from sieve.model.version import git_sha
from sieve.types import LABELS

log = logging.getLogger("sieve.model.select")

RULE = (
    "The architecture with the higher mean pooled out-of-fold macro-F1 across seeds wins. "
    "Ties inside 0.005 break toward the lower p95 latency; a remaining tie breaks toward "
    "DeBERTa-v3 as the declared default. Registered before any number existed; there is no "
    "override flag."
)
TIE_BAND = 0.005
DEFAULT_ARCHITECTURE = "deberta"
MIN_SEEDS = 3


class InsufficientSeeds(ValueError):  # noqa: N818 - named by the lane brief
    """An architecture was offered with fewer than three seeds. Single-seed numbers are never
    published (PRD §11.3)."""


class IncompleteDevSet(ValueError):  # noqa: N818 - names the artefact, not the failure mode
    """A seed's pooled out-of-fold predictions do not cover the development set exactly once."""


@dataclass(frozen=True)
class Architecture:
    name: str
    macro_f1_per_seed: list[float]
    p95_ms: float | None

    @property
    def mean(self) -> float:
        return float(np.mean(self.macro_f1_per_seed))

    @property
    def std(self) -> float:
        return float(np.std(self.macro_f1_per_seed))

    def as_dict(self) -> dict[str, Any]:
        return {
            "macro_f1_mean": self.mean,
            "macro_f1_std": self.std,
            "seeds": len(self.macro_f1_per_seed),
            "macro_f1_per_seed": self.macro_f1_per_seed,
            "p95_ms": self.p95_ms,
        }


@dataclass(frozen=True)
class Selection:
    winner: str
    rule: str
    margin: float
    #: Which clause of RULE decided it: the mean, the p95 tie-break, or the declared default.
    decided_by: Literal["mean", "p95", "default"]
    per_architecture: dict[str, Architecture]
    decided_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    sha: str = field(default_factory=lambda: git_sha(short=False))

    def as_dict(self) -> dict[str, Any]:
        return {
            "winner": self.winner,
            "rule": self.rule,
            "margin": self.margin,
            "decided_by": self.decided_by,
            "per_architecture": {
                name: arch.as_dict() for name, arch in self.per_architecture.items()
            },
            "decided_at": self.decided_at,
            "git_sha": self.sha,
        }


def _p95(directory: Path) -> float | None:
    config = directory / "sieve_config.json"
    if not config.exists():
        return None
    value = json.loads(config.read_text(encoding="utf-8")).get("p95_ms")
    return None if value is None else float(value)


def _seed_macro_f1(directory: Path, expected_ids: Sequence[str]) -> float:
    """Pooled out-of-fold macro-F1 for one seed, after checking the pooling covers the dev set."""
    for name in ("oof_logits.npy", "oof_labels.npy", "oof_ids.json"):
        if not (directory / name).exists():
            raise IncompleteDevSet(f"{directory / name} is missing; the seed is not pooled")
    logits = np.load(directory / "oof_logits.npy")
    labels = np.load(directory / "oof_labels.npy").astype(int)
    ids = [str(x) for x in json.loads((directory / "oof_ids.json").read_text(encoding="utf-8"))]
    if len(ids) != len(set(ids)):
        raise IncompleteDevSet(f"{directory} repeats an id; the pooling is not out-of-fold")
    if set(ids) != set(expected_ids):
        missing = sorted(set(expected_ids) - set(ids))
        extra = sorted(set(ids) - set(expected_ids))
        raise IncompleteDevSet(
            f"{directory} does not cover the dev set exactly once: {len(missing)} missing "
            f"{missing[:3]}, {len(extra)} unexpected {extra[:3]}"
        )
    if not (len(logits) == len(labels) == len(ids)):
        raise IncompleteDevSet(f"{directory}: logits, labels and ids disagree in length")
    predicted = np.asarray(logits).argmax(axis=1)
    head = LABELS if logits.shape[1] == 3 else ("not_poisoned", "poisoned")
    return macro_f1(
        [head[int(i)] for i in labels], [head[int(i)] for i in predicted], labels=list(head)
    )


def select(candidates: Mapping[str, Sequence[Path]], *, expected_ids: Sequence[str]) -> Selection:
    """Apply `RULE`. Raises rather than guessing when an architecture is under-evidenced."""
    if len(candidates) < 2:
        raise ValueError("selection compares architectures; give it at least two")
    architectures: dict[str, Architecture] = {}
    for name, directories in candidates.items():
        if len(directories) < MIN_SEEDS:
            raise InsufficientSeeds(
                f"{name} was offered {len(directories)} seed(s); the rule is a mean across at "
                f"least {MIN_SEEDS} and a single-seed number is never published"
            )
        scores = [_seed_macro_f1(Path(d), expected_ids) for d in directories]
        latencies = [p for p in (_p95(Path(d)) for d in directories) if p is not None]
        architectures[name] = Architecture(
            name=name,
            macro_f1_per_seed=scores,
            p95_ms=float(np.mean(latencies)) if latencies else None,
        )

    ranked = sorted(architectures.values(), key=lambda a: a.mean, reverse=True)
    best, runner_up = ranked[0], ranked[1]
    # The margin is the mean gap the FIRST clause of the rule saw, recorded before any
    # tie-break so a reader can tell how close the decision actually was.
    margin = best.mean - runner_up.mean
    decided_by: Literal["mean", "p95", "default"] = "mean"

    if margin < TIE_BAND:
        tied = [a for a in ranked if best.mean - a.mean < TIE_BAND]
        with_latency = [a for a in tied if a.p95_ms is not None]
        if with_latency:
            fastest = min(with_latency, key=lambda a: a.p95_ms or float("inf"))
            still_tied = [
                a for a in with_latency if abs((a.p95_ms or 0.0) - (fastest.p95_ms or 0.0)) < 1e-9
            ]
            if len(still_tied) == 1:
                best, decided_by = still_tied[0], "p95"
            else:
                best, decided_by = _default_of(still_tied), "default"
        else:
            best, decided_by = _default_of(tied), "default"
        log.info("tie inside %.4f macro-F1; broke toward %s by %s", TIE_BAND, best.name, decided_by)

    return Selection(
        winner=best.name,
        rule=RULE,
        margin=margin,
        decided_by=decided_by,
        per_architecture=architectures,
    )


def _default_of(tied: Sequence[Architecture]) -> Architecture:
    """The declared default when the rule cannot separate two architectures."""
    for architecture in tied:
        if DEFAULT_ARCHITECTURE in architecture.name.lower():
            return architecture
    return tied[0]


def write_selection(selection: Selection, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(selection.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def main(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(prog="python -m sieve.model.select")
    parser.add_argument(
        "--candidate",
        action="append",
        required=True,
        metavar="NAME=DIR[,DIR...]",
        help="one per architecture, e.g. deberta-v3-base=models/deberta-v3-base-s0,...-s1,...",
    )
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, default=Path("bench/results"))
    ns = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    from sieve.model.data import load_corpus

    expected = [r.id for r in load_corpus(ns.corpus) if r.split == "dev"]
    candidates = {
        name: [Path(p) for p in paths.split(",")]
        for name, _, paths in (c.partition("=") for c in ns.candidate)
    }
    selection = select(candidates, expected_ids=expected)
    log.info("winner %s by %.4f macro-F1", selection.winner, selection.margin)
    return write_selection(selection, ns.results_dir / "selection.json")


__all__ = [
    "MIN_SEEDS",
    "RULE",
    "TIE_BAND",
    "Architecture",
    "IncompleteDevSet",
    "InsufficientSeeds",
    "Selection",
    "main",
    "select",
    "write_selection",
]

if __name__ == "__main__":  # pragma: no cover
    main()
