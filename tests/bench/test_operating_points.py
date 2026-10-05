"""`bench/analysis/operating_points.py`: a post-hoc, cross-fitted point-recall rule on dev OOF."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bench.analysis import operating_points as op
from bench.split.split import DEFAULT_SEED, assign
from sieve.model.data import LABEL2ID, load_corpus
from sieve.types import CorpusRecord


def test_the_threshold_is_the_floor_indexed_lowest_positive_of_the_other_folds() -> None:
    other = [0.9, 0.1, 0.5, 0.3, 0.7, 0.2, 0.8, 0.4, 0.6, 0.05]
    assert op.crossfit_threshold(other, 1.0) == 0.05  # k = 0: every positive kept
    assert op.crossfit_threshold(other, 0.9) == 0.1  # k = floor(0.1 * 10) = 1
    assert op.crossfit_threshold(other, 0.8) == 0.2  # k = 2
    assert op.crossfit_threshold(other, 0.98) == 0.05  # k = floor(0.2) = 0


def test_the_floor_is_not_fooled_by_binary_rounding() -> None:
    """(1 - 0.9) * 10 is 0.9999999999999998 in floating point; the rule means 1."""
    assert (1 - 0.9) * 10 < 1
    other = [i / 100 for i in range(10)]
    assert op.crossfit_threshold(other, 0.9) == 0.01


def _world() -> tuple[list[str], list[str], list[int], list[float], list[bool]]:
    #            fold 0                    fold 1
    labels = ["poisoned", "poisoned", "benign", "benign", "poisoned", "poisoned", "benign"]
    sources = ["synthetic_mutation", "public_repo", "public_repo", "wild_fp",
               "synthetic_mutation", "synthetic_mutation", "public_repo"]  # fmt: skip
    folds = [0, 0, 0, 0, 1, 1, 1]
    scores = [0.9, 0.3, 0.5, 0.1, 0.4, 0.8, 0.35]
    yara = [True, False, True, True, True, False, True]
    return labels, sources, folds, scores, yara


def test_each_fold_is_scored_at_the_threshold_of_the_others() -> None:
    labels, sources, folds, scores, yara = _world()
    (row,) = op.operating_points(labels, sources, folds, scores, yara, targets=(1.0,))
    # fold 0 uses fold 1's positives {0.4, 0.8} -> t = 0.4; fold 1 uses {0.9, 0.3} -> t = 0.3.
    assert row["thresholds_per_fold"] == {"0": 0.4, "1": 0.3}
    # positives: 0.9 >= 0.4 yes, 0.3 >= 0.4 no, 0.4 >= 0.3 yes, 0.8 >= 0.3 yes
    assert row["recall"] == pytest.approx(3 / 4)
    assert row["misses"] == 1 and row["n_poisoned"] == 4
    # real negatives: 0.5 (f0, >= 0.4 review), 0.1 (f0, dismissed), 0.35 (f1, >= 0.3 review)
    assert row["real_negative_review_share"] == pytest.approx(2 / 3)
    assert row["n_real_negatives"] == 3
    # all three are YARA-flagged; one (0.1) is dismissed
    assert row["yara_fp_dismissed"] == pytest.approx(1 / 3)
    assert row["n_yara_fp"] == 3
    assert row["target_recall"] == 1.0


# ------------------------------------------------------------------------------- the CLI


def _model_dir(path: Path, dev: list[CorpusRecord], *, temperature: float) -> Path:
    path.mkdir(parents=True)
    rng = np.random.default_rng(0)
    logits = rng.normal(0.0, 0.5, size=(len(dev), 3))
    for row, record in zip(logits, dev, strict=True):
        row[LABEL2ID[record.label]] += 2.0
    np.save(path / "oof_logits.npy", logits.astype(np.float32))
    np.save(path / "oof_labels.npy", np.asarray([LABEL2ID[r.label] for r in dev], dtype=np.int64))
    (path / "oof_ids.json").write_text(json.dumps([r.id for r in dev]), encoding="utf-8")
    (path / "sieve_config.json").write_text(
        json.dumps({"num_labels": 3, "temperature": temperature}), encoding="utf-8"
    )
    return path


@pytest.fixture
def world(tmp_path: Path) -> dict[str, Any]:
    assigned = assign(load_corpus(Path("fixtures/corpus/mini-50.jsonl")), seed=DEFAULT_SEED)
    corpus = tmp_path / "labelled.jsonl"
    corpus.write_text(
        "".join(r.model_dump_json() + "\n" for r in assigned), encoding="utf-8", newline="\n"
    )
    dev = [r for r in assigned if r.split == "dev"]
    results = tmp_path / "results"
    results.mkdir()
    (results / "baseline_raw.json").write_text(
        json.dumps([{"id": r.id, "flagged": i % 2 == 0} for i, r in enumerate(dev)]),
        encoding="utf-8",
    )
    dirs = [_model_dir(tmp_path / f"arch-s{s}", dev, temperature=1.0 + s) for s in (0, 1)]
    return {"corpus": corpus, "results": results, "dirs": dirs, "dev": dev}


def test_the_cli_writes_a_deterministic_post_hoc_payload(world: dict[str, Any]) -> None:
    args = ["--corpus", str(world["corpus"]), "--results-dir", str(world["results"])]
    for d in world["dirs"]:
        args += ["--candidate", str(d)]
    path = op.main(args)
    first = path.read_bytes()
    assert op.main(args).read_bytes() == first, "two runs, one byte stream"
    assert b"\r\n" not in first
    payload = json.loads(first)
    assert payload["status"] == "post-hoc; not adopted for v1"
    assert payload["rule"] == op.RULE
    assert payload["dataset_version"].startswith("labelled.jsonl@")
    assert sorted(payload["per_seed"]) == ["arch-s0", "arch-s1"]
    rows = payload["per_seed"]["arch-s0"]
    assert [r["target_recall"] for r in rows] == list(op.TARGETS)
    assert rows[0]["n_poisoned"] == sum(r.label == "poisoned" for r in world["dev"])


def test_the_candidates_default_to_the_selected_architectures_seeds(
    world: dict[str, Any],
) -> None:
    dirs = [d.as_posix() for d in world["dirs"]]
    (world["results"] / "selection.json").write_text(
        json.dumps(
            {
                "winner": "arch",
                "published_dir": dirs[1],
                "per_architecture": {"arch": {"directories": dirs}, "other": {"directories": []}},
            }
        ),
        encoding="utf-8",
    )
    path = op.main(["--corpus", str(world["corpus"]), "--results-dir", str(world["results"])])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["published_dir"] == dirs[1]
    assert sorted(payload["per_seed"]) == ["arch-s0", "arch-s1"]


def test_a_candidate_whose_labels_disagree_with_the_corpus_is_refused(
    world: dict[str, Any],
) -> None:
    labels = world["dirs"][0] / "oof_labels.npy"
    np.save(labels, np.roll(np.load(labels), 1))
    with pytest.raises(op.PoolError, match="corpus says"):
        op.main(["--corpus", str(world["corpus"]), "--results-dir", str(world["results"]),
                 "--candidate", str(world["dirs"][0])])  # fmt: skip


def test_an_uncalibrated_candidate_is_refused(world: dict[str, Any]) -> None:
    (world["dirs"][0] / "sieve_config.json").write_text(
        json.dumps({"num_labels": 3, "temperature": None}), encoding="utf-8"
    )
    with pytest.raises(op.OperatingPointError, match="temperature"):
        op.main(["--corpus", str(world["corpus"]), "--results-dir", str(world["results"]),
                 "--candidate", str(world["dirs"][0])])  # fmt: skip


def test_without_candidates_or_a_selection_it_says_what_is_missing(world: dict[str, Any]) -> None:
    with pytest.raises(op.OperatingPointError, match="selection.json"):
        op.main(["--corpus", str(world["corpus"]), "--results-dir", str(world["results"])])
