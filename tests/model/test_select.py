from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from sieve.model.select import (
    RULE,
    IncompleteDevSet,
    InsufficientSeeds,
    select,
    write_selection,
)

IDS = [f"rec-{i:03d}" for i in range(30)]
LABELS_BY_ID = {rid: i % 3 for i, rid in enumerate(IDS)}


def _seed_dir(
    root: Path, name: str, *, accuracy: float, p95_ms: float | None, ids: list[str] | None = None
) -> Path:
    """A pooled-OOF seed directory whose macro-F1 is controlled by `accuracy`."""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    rows = ids if ids is not None else IDS
    logits = np.zeros((len(rows), 3), dtype=np.float32)
    labels = np.asarray([LABELS_BY_ID.get(r, 0) for r in rows], dtype=np.int64)
    n_correct = round(accuracy * len(rows))
    for index, label in enumerate(labels):
        # Wrong rows are pushed onto the NEXT class, so every class keeps some mass and
        # macro-F1 tracks `accuracy` monotonically instead of collapsing to zero.
        logits[index, int(label) if index < n_correct else (int(label) + 1) % 3] = 5.0
    np.save(directory / "oof_logits.npy", logits)
    np.save(directory / "oof_labels.npy", labels)
    (directory / "oof_ids.json").write_text(json.dumps(rows), encoding="utf-8", newline="\n")
    if p95_ms is not None:
        (directory / "sieve_config.json").write_text(
            json.dumps({"p95_ms": p95_ms}), encoding="utf-8", newline="\n"
        )
    return directory


def _candidate(
    root: Path, name: str, accuracies: list[float], p95_ms: float | None = None
) -> list[Path]:
    return [
        _seed_dir(root, f"{name}-s{seed}", accuracy=a, p95_ms=p95_ms)
        for seed, a in enumerate(accuracies)
    ]


def test_the_higher_mean_wins_even_when_the_other_has_the_better_single_seed(
    tmp_path: Path,
) -> None:
    candidates = {
        "deberta-v3-base": _candidate(tmp_path, "deberta", [0.80, 0.80, 0.80], p95_ms=120.0),
        "modernbert-base": _candidate(tmp_path, "modernbert", [1.00, 0.50, 0.50], p95_ms=90.0),
    }
    selection = select(candidates, expected_ids=IDS)
    per = selection.per_architecture
    assert max(per["modernbert-base"].macro_f1_per_seed) > max(
        per["deberta-v3-base"].macro_f1_per_seed
    )
    assert per["deberta-v3-base"].mean > per["modernbert-base"].mean
    assert selection.winner == "deberta-v3-base"
    assert selection.margin > 0


def test_a_clear_win_for_the_other_architecture_is_respected(tmp_path: Path) -> None:
    candidates = {
        "deberta-v3-base": _candidate(tmp_path, "deberta", [0.60, 0.60, 0.60], p95_ms=120.0),
        "modernbert-base": _candidate(tmp_path, "modernbert", [0.95, 0.95, 0.95], p95_ms=90.0),
    }
    assert select(candidates, expected_ids=IDS).winner == "modernbert-base"


def test_a_tie_inside_the_band_goes_to_the_lower_p95(tmp_path: Path) -> None:
    same = [0.80, 0.80, 0.80]
    candidates = {
        "deberta-v3-base": _candidate(tmp_path, "deberta", same, p95_ms=140.0),
        "modernbert-base": _candidate(tmp_path, "modernbert", same, p95_ms=70.0),
    }
    selection = select(candidates, expected_ids=IDS)
    assert selection.margin == pytest.approx(0.0)
    assert selection.winner == "modernbert-base"
    assert selection.decided_by == "p95"


def test_a_full_tie_goes_to_deberta(tmp_path: Path) -> None:
    same = [0.80, 0.80, 0.80]
    candidates = {
        "modernbert-base": _candidate(tmp_path, "modernbert", same, p95_ms=100.0),
        "deberta-v3-base": _candidate(tmp_path, "deberta", same, p95_ms=100.0),
    }
    selection = select(candidates, expected_ids=IDS)
    assert selection.winner == "deberta-v3-base"
    assert selection.decided_by == "default"


def test_a_full_tie_with_no_latency_recorded_still_goes_to_deberta(tmp_path: Path) -> None:
    same = [0.80, 0.80, 0.80]
    candidates = {
        "modernbert-base": _candidate(tmp_path, "modernbert", same, p95_ms=None),
        "deberta-v3-base": _candidate(tmp_path, "deberta", same, p95_ms=None),
    }
    assert select(candidates, expected_ids=IDS).winner == "deberta-v3-base"


def test_a_gap_wider_than_the_tie_band_ignores_latency(tmp_path: Path) -> None:
    candidates = {
        "deberta-v3-base": _candidate(tmp_path, "deberta", [0.90, 0.90, 0.90], p95_ms=400.0),
        "modernbert-base": _candidate(tmp_path, "modernbert", [0.60, 0.60, 0.60], p95_ms=20.0),
    }
    selection = select(candidates, expected_ids=IDS)
    assert selection.winner == "deberta-v3-base"
    assert selection.margin > 0.005


# ------------------------------------------------------------------------------ refusals


def test_fewer_than_three_seeds_raises(tmp_path: Path) -> None:
    candidates = {
        "deberta-v3-base": _candidate(tmp_path, "deberta", [0.9, 0.9]),
        "modernbert-base": _candidate(tmp_path, "modernbert", [0.8, 0.8, 0.8]),
    }
    with pytest.raises(InsufficientSeeds, match="single-seed number is never published"):
        select(candidates, expected_ids=IDS)


def test_a_seed_that_does_not_cover_the_dev_set_raises(tmp_path: Path) -> None:
    short = _seed_dir(tmp_path, "short-s0", accuracy=0.9, p95_ms=None, ids=IDS[:10])
    candidates = {
        "deberta-v3-base": [
            short,
            _seed_dir(tmp_path, "deberta-s1", accuracy=0.9, p95_ms=None),
            _seed_dir(tmp_path, "deberta-s2", accuracy=0.9, p95_ms=None),
        ],
        "modernbert-base": _candidate(tmp_path, "modernbert", [0.8, 0.8, 0.8]),
    }
    with pytest.raises(IncompleteDevSet, match="does not cover the dev set"):
        select(candidates, expected_ids=IDS)


def test_a_seed_with_a_repeated_id_raises(tmp_path: Path) -> None:
    duplicated = _seed_dir(tmp_path, "dup-s0", accuracy=0.9, p95_ms=None, ids=[*IDS, IDS[0]])
    candidates = {
        "deberta-v3-base": [
            duplicated,
            _seed_dir(tmp_path, "deberta-s1", accuracy=0.9, p95_ms=None),
            _seed_dir(tmp_path, "deberta-s2", accuracy=0.9, p95_ms=None),
        ],
        "modernbert-base": _candidate(tmp_path, "modernbert", [0.8, 0.8, 0.8]),
    }
    with pytest.raises(IncompleteDevSet, match="repeats an id"):
        select(candidates, expected_ids=IDS)


def test_a_seed_missing_its_pooled_artefact_raises(tmp_path: Path) -> None:
    empty = tmp_path / "empty-s0"
    empty.mkdir()
    candidates = {
        "deberta-v3-base": [
            empty,
            _seed_dir(tmp_path, "deberta-s1", accuracy=0.9, p95_ms=None),
            _seed_dir(tmp_path, "deberta-s2", accuracy=0.9, p95_ms=None),
        ],
        "modernbert-base": _candidate(tmp_path, "modernbert", [0.8, 0.8, 0.8]),
    }
    with pytest.raises(IncompleteDevSet, match="oof_logits.npy"):
        select(candidates, expected_ids=IDS)


def test_one_architecture_is_not_a_selection(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least two"):
        select(
            {"deberta-v3-base": _candidate(tmp_path, "deberta", [0.9, 0.9, 0.9])},
            expected_ids=IDS,
        )


def test_there_is_no_override_argument() -> None:
    """A human who disagrees changes the rule in a PR; the PR is the record."""
    import inspect

    parameters = set(inspect.signature(select).parameters)
    assert parameters == {"candidates", "expected_ids"}


# -------------------------------------------------------------------------- selection.json


def test_selection_json_records_the_rule_verbatim(tmp_path: Path) -> None:
    candidates = {
        "deberta-v3-base": _candidate(tmp_path, "deberta", [0.90, 0.90, 0.90], p95_ms=120.0),
        "modernbert-base": _candidate(tmp_path, "modernbert", [0.70, 0.70, 0.70], p95_ms=90.0),
    }
    path = write_selection(select(candidates, expected_ids=IDS), tmp_path / "selection.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["rule"] == RULE
    assert set(payload) == {
        "winner",
        "rule",
        "margin",
        "decided_by",
        "per_architecture",
        "decided_at",
        "git_sha",
    }
    assert payload["decided_by"] == "mean"
    assert set(payload["per_architecture"]["deberta-v3-base"]) >= {
        "macro_f1_mean",
        "macro_f1_std",
        "seeds",
        "p95_ms",
    }
    assert payload["per_architecture"]["deberta-v3-base"]["seeds"] == 3
    assert path.read_bytes().count(b"\r\n") == 0


def test_the_losing_architecture_is_reported_in_full(tmp_path: Path) -> None:
    candidates = {
        "deberta-v3-base": _candidate(tmp_path, "deberta", [0.90, 0.90, 0.90], p95_ms=120.0),
        "modernbert-base": _candidate(tmp_path, "modernbert", [0.70, 0.72, 0.71], p95_ms=90.0),
    }
    payload = select(candidates, expected_ids=IDS).as_dict()
    assert set(payload["per_architecture"]) == set(candidates)
    loser = payload["per_architecture"]["modernbert-base"]
    assert len(loser["macro_f1_per_seed"]) == 3
    assert loser["macro_f1_std"] > 0


def test_the_seed_spread_is_the_sample_std() -> None:
    """D5: three seeds are a sample of training runs, so std uses ddof=1."""
    import numpy as np

    from sieve.model.select import Architecture

    arch = Architecture(name="x", macro_f1_per_seed=[0.80, 0.82, 0.87], p95_ms=None)
    assert arch.std == pytest.approx(np.std([0.80, 0.82, 0.87], ddof=1))
