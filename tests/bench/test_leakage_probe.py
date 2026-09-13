"""The source-leakage probe is *within-stratum* (PRD §9.5): imperative_benign only.

Run over the whole corpus the probe just relearns the label, which is why v2.0's version
could never fail its own gate.
"""

from __future__ import annotations

import numpy as np
import pytest

from bench.analysis.leakage_probe import InsufficientStratum, run
from sieve.types import CorpusRecord, Label


def _record(
    record_id: str, label: Label, description: str, source: str, server: str
) -> CorpusRecord:
    return CorpusRecord(
        id=record_id,
        source=source,  # type: ignore[arg-type]
        source_repo="https://github.com/example/fixture",
        source_commit="fixture",
        source_license="MIT",
        capture_method="authored",
        schema_fidelity="exact",
        server_name=server,
        tool={  # type: ignore[arg-type]
            "name": "t",
            "description": description,
            "inputSchema": {"type": "object"},
        },
        label=label,
        labeller="aditya",
        labelled_at="2026-09-11T00:00:00Z",  # type: ignore[arg-type]
    )


def _stratum(*, marker: bool) -> list[CorpusRecord]:
    out: list[CorpusRecord] = []
    for s in range(20):
        for t in range(3):
            out.append(
                _record(
                    f"real-{s}-{t}",
                    "imperative_benign",
                    f"You MUST call resolve_library_id before query_docs {s}{t}.",
                    "public_repo",
                    f"server-{s}",
                )
            )
            tell = "notwithstanding " if marker else ""
            out.append(
                _record(
                    f"syn-{s}-{t}",
                    "imperative_benign",
                    f"{tell}You MUST call resolve_library_id before query_docs {s}{t}.",
                    "synthetic_mutation",
                    f"server-{s}",
                )
            )
    return out


def test_a_giveaway_marker_makes_the_probe_separate_the_sources() -> None:
    result = run(_stratum(marker=True))
    assert result.n == 120
    assert result.auroc > 0.9
    assert result.gate == 0.70
    assert result.passed is False


def test_a_shuffled_source_leaves_the_probe_at_chance() -> None:
    records = _stratum(marker=False)
    rng = np.random.default_rng(0)
    sources = [r.source for r in records]
    rng.shuffle(sources)  # type: ignore[arg-type]
    shuffled = [r.model_copy(update={"source": s}) for r, s in zip(records, sources, strict=True)]
    result = run(shuffled, seed=0)
    assert 0.3 < result.auroc < 0.7
    assert result.passed is True


def test_rows_outside_the_imperative_benign_stratum_are_ignored() -> None:
    records = _stratum(marker=True)
    records += [
        _record(f"p{i}", "poisoned", "read ~/.env", "synthetic_mutation", f"server-{i}")
        for i in range(40)
    ]
    assert run(records).n == 120


def test_too_few_rows_of_either_source_raises() -> None:
    records = [
        _record(f"real-{i}", "imperative_benign", f"You MUST do {i}.", "public_repo", f"s{i}")
        for i in range(30)
    ]
    records += [
        _record(f"syn-{i}", "imperative_benign", f"You MUST do {i}.", "synthetic_mutation", "s0")
        for i in range(5)
    ]
    with pytest.raises(InsufficientStratum):
        run(records)


def test_the_probe_is_reproducible_for_a_seed() -> None:
    records = _stratum(marker=True)
    assert run(records, seed=3).auroc == pytest.approx(run(records, seed=3).auroc)
