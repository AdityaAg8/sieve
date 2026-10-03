"""Render `holdout.json` from a fixture through the production writers (H2's `score` and
`row_entries`, H3's `build_report`), so no test types a report by hand."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from bench import eval_final as ef
from bench import report_final as rf
from bench.collect.ood_rows import OodRow
from sieve.protocols import FakeClassifier
from sieve.types import CorpusRecord


class SeededFake(FakeClassifier):
    """The contract fake under a family/seed version, nudged per seed so seeds differ."""

    def __init__(self, seed: int) -> None:
        self.model_version = f"deberta@s{seed}"
        self.seed = seed

    def predict_proba(self, texts: Sequence[str]) -> Any:
        probs = super().predict_proba(texts)
        probs[:, 2] = probs[:, 2] * (1.0 - 0.01 * self.seed)
        return probs / probs.sum(axis=1, keepdims=True)


def sealed_rows(
    holdout: Sequence[CorpusRecord],
    real_poisoned: Sequence[CorpusRecord],
    negatives: Sequence[OodRow],
) -> list[ef.SealedRow]:
    rows = [
        ef._sealed_record(
            "holdout_synthetic" if r.is_synthetic else "test_id",
            r,
            r.source == "wild_fp" or (r.is_synthetic and "ignore" in (r.tool.description or "")),
        )
        for r in holdout
    ]
    rows += [ef._sealed_record("real_poisoned", r, i % 2 == 0) for i, r in enumerate(real_poisoned)]
    rows += [ef._sealed_ood(r, {r.id: i % 3 == 0}) for i, r in enumerate(negatives)]
    return rows


def render(
    holdout: Sequence[CorpusRecord],
    real_poisoned: Sequence[CorpusRecord],
    negatives: Sequence[OodRow],
    *,
    seeds: Sequence[int] = (0, 1),
    n_boot: int = 40,
    reason: str = "fixture",
    dataset_version: str = "fixture.jsonl@000000000000",
) -> dict[str, Any]:
    rows = sealed_rows(holdout, real_poisoned, negatives)
    models = [
        ef.LoadedModel(Path(f"deberta-s{s}"), SeededFake(s), f"deberta@s{s}", s, "deberta", -1,
                       "full")
        for s in seeds
    ]  # fmt: skip
    return rf.build_report(
        ef.score(models, rows),
        ef.row_entries(rows, None),
        headline=models[0].version,
        n_boot=n_boot,
        preregistration_sha256="0" * 64,
        log_line=f'{{"reason": "{reason}"}}',
        log_entry={"reason": reason, "dataset_version": dataset_version},
    )


def fixture_world() -> tuple[list[CorpusRecord], list[CorpusRecord], list[OodRow]]:
    """mini-50 as the holdout, four planted MCPTox rows, and five OOD negatives."""
    from bench.collect.real_poisoned import MCPTOX_RATIONALE
    from sieve.model.data import load_corpus

    records = load_corpus(Path("fixtures/corpus/mini-50.jsonl"))
    holdout = [r.model_copy(update={"split": "holdout"}) for r in records]
    poisoned = [
        r.model_copy(
            update={
                "id": f"rp-{i:04d}",
                "server_name": f"hashed:{i % 2}",
                "source": "real_poisoned",
                "label": "poisoned",
                "generator_model": None,
                "mutation_of": None,
                "split": None,
                "label_rationale": MCPTOX_RATIONALE.format(
                    template=f"Template-{1 + i % 3}", risk="Privacy Leakage"
                ),
            }
        )
        for i, r in enumerate(records[:6])
    ]
    negatives = [
        OodRow(f"ood-docker-{i:04d}", "docker" if i < 3 else "mcptox_clean", f"srv{i % 2}",
               {"name": f"t{i}", "description": "Lists files in a folder.", "inputSchema": {}},
               False)
        for i in range(5)
    ]  # fmt: skip
    return holdout, poisoned, negatives
