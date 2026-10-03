from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bench.eval_final import SEALED_MESSAGE, HoldoutSealed, evaluate, main
from sieve.model.data import load_corpus
from sieve.protocols import FakeClassifier, FakeJudge

LOG_KEYS = {"git_sha", "utc", "rows_evaluated", "reason"}

#: Every file allowed to mention a sealed split at all. Plain substrings, so
#: `rec.split == "holdout"`, `in {"holdout", "eval_only"}` and a bare `eval_only`
#: all match; the earlier exact-spacing pattern matched none of them and passed vacuously.
GIT_GREP_SEALED = [
    "git", "grep", "-l", "-e", "holdout", "-e", "eval_only", "--", "sieve", "bench",
]  # fmt: skip


# --------------------------------------------------------- the brief's three failing tests


def test_refuses_without_the_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SIEVE_FINAL", raising=False)
    with pytest.raises(HoldoutSealed):
        main(results_dir=tmp_path, reason="test")
    assert not (tmp_path / "test_opened.log").exists()


def test_appends_exactly_one_line_with_the_env_var(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SIEVE_FINAL", "1")
    main(results_dir=tmp_path, reason="unit test")
    lines = (tmp_path / "test_opened.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert set(entry) == LOG_KEYS
    assert entry["reason"] == "unit test"
    assert (tmp_path / "holdout.json").exists()


def test_a_second_run_appends_a_second_line_and_never_rewrites_the_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SIEVE_FINAL", "1")
    main(results_dir=tmp_path, reason="first")
    main(results_dir=tmp_path, reason="second")
    lines = (tmp_path / "test_opened.log").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["reason"] for x in lines] == ["first", "second"]


# ------------------------------------------------------------------------------ the seal


def test_the_refusal_quotes_the_prd_sentence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("SIEVE_FINAL", raising=False)
    with pytest.raises(HoldoutSealed, match="opened once per published version"):
        main(results_dir=tmp_path, reason="test")
    assert SEALED_MESSAGE.endswith("PRD §11.6")


@pytest.mark.parametrize("value", ["0", "true", "yes", "", "TRUE"])
def test_only_the_literal_one_unseals_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    monkeypatch.setenv("SIEVE_FINAL", value)
    with pytest.raises(HoldoutSealed):
        main(results_dir=tmp_path, reason="test")


def test_nothing_else_in_the_repository_opens_the_holdout() -> None:
    """The one grep that keeps the rule true as the repository grows."""
    import subprocess

    hits = set(
        subprocess.run(  # noqa: S603 - fixed argv, no shell; git is on PATH in CI
            GIT_GREP_SEALED, capture_output=True, text=True, check=False
        ).stdout.split()
    )
    # A vacuous pass is the failure mode this test exists to avoid: the earlier pattern matched
    # nothing at all and `set() <= allowed` was true.
    assert "bench/eval_final.py" in hits, f"the grep matched nothing useful: {hits}"
    allowed = {
        # Reads sealed ROWS. There is exactly one.
        "bench/eval_final.py",
        # Assigns the splits in the first place (L1 Task 1.8).
        "bench/split/split.py",
        # The frozen assignment split.py honours (training plan A4): server names, never rows.
        "bench/data/split_map.json",
        # Blind human labelling of the real holdout rows (training plan F4): labels only,
        # imports no model code, scores nothing; its gold labels are report-only.
        "bench/label/holdout_gold.py",
        # The local labellers' report-only votes on test-ID for the F3 audit (training plan F1),
        # read through holdout_gold.test_id_rows; never a training label, threshold or choice.
        "bench/label/ensemble.py",
        # The report-only test-ID audit (training plan F3): gold labels through holdout_gold,
        # server names for the cluster CI; it scores labellers, never a model.
        "bench/label/audit.py",
        # Produces `eval_only` rows (L1's real_poisoned collector).
        "bench/collect/real_poisoned.py",
        # L2's own gate: refuses to score a sealed row.
        "bench/baseline/run.py",
        # L1 publishes the split counts in the datasheet.
        "bench/publish.py",
        # Refuse or drop a sealed row; none of them can read one.
        "sieve/model/data.py",
        "sieve/model/train.py",
        "sieve/model/select.py",
        "sieve/model/__init__.py",
        "bench/ablations/run.py",
        "bench/analysis/errors.py",
        # Declares the `Split` literal.
        "sieve/types.py",
        # Read the holdout REPORT (`holdout.json`), never a row.
        "sieve/model/push.py",
        "sieve/model/card.md",
    }
    assert hits <= allowed, f"unexpected holdout/eval_only reader(s): {sorted(hits - allowed)}"


# ------------------------------------------------------------------------------ the report


@pytest.fixture
def sealed_corpus(tmp_path: Path) -> Path:
    """mini-50 under L1's real assignment, plus a planted `real_poisoned` slice.

    The holdout rows are the ones `bench.split.split.assign` actually sealed, not rows this
    test relabelled; only the `eval_only` slice is planted, because mini-50 carries no
    `real_poisoned` source of its own.
    """
    from bench.split.split import DEFAULT_SEED, assign

    records = load_corpus(Path("fixtures/corpus/mini-50.jsonl"))
    planted = [
        r.model_copy(
            update={
                "id": f"real-poisoned-{index}",
                "server_name": f"wild-{index}",
                "source": "real_poisoned",
                "label": "poisoned",
                "mutation_of": None,
            }
        )
        for index, r in enumerate(records[:4])
    ]
    out = assign([*records, *planted], seed=DEFAULT_SEED)
    assert {r.split for r in out} == {"dev", "holdout", "eval_only"}
    path = tmp_path / "labelled.jsonl"
    path.write_text(
        "".join(r.model_dump_json() + "\n" for r in out), encoding="utf-8", newline="\n"
    )
    return path


def test_the_report_carries_every_required_figure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sealed_corpus: Path
) -> None:
    monkeypatch.setenv("SIEVE_FINAL", "1")
    results = tmp_path / "results"
    path = main(
        results_dir=results,
        reason="v0.0.0 smoke",
        corpus=sealed_corpus,
        classifier=FakeClassifier(),
        judge=FakeJudge(),
        n_boot=60,
    )
    report = json.loads(path.read_text(encoding="utf-8"))
    for key in (
        "recall",
        "recall_lcb",
        "fpr_pipeline_real",
        "per_tier",
        "per_concealment",
        "real_poisoned_recall_stage1",
        "real_poisoned_recall_pipeline",
        "release_gate",
        "release_gate_passed",
    ):
        assert key in report, key
    assert report["recall_lcb"] <= report["recall"]
    assert report["n_eval_only"] == 4
    assert report["n_holdout"] > 0
    assert report["rows_evaluated"] == report["n_holdout"] + report["n_eval_only"]
    assert json.loads(report["log_line"])["rows_evaluated"] == report["rows_evaluated"]
    assert path.read_bytes().count(b"\r\n") == 0


def test_dev_rows_never_reach_the_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sealed_corpus: Path
) -> None:
    monkeypatch.setenv("SIEVE_FINAL", "1")
    records = load_corpus(sealed_corpus)
    sealed = [r for r in records if r.split in {"holdout", "eval_only"}]
    report = json.loads(
        main(
            results_dir=tmp_path,
            reason="r",
            corpus=sealed_corpus,
            classifier=FakeClassifier(),
            n_boot=40,
        ).read_text(encoding="utf-8")
    )
    assert report["rows_evaluated"] == len(sealed)
    assert report["rows_evaluated"] < len(records)


def test_a_corpus_with_no_sealed_rows_still_logs_the_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The log counts how many times the seal was broken, not how many were productive."""
    monkeypatch.setenv("SIEVE_FINAL", "1")
    corpus = tmp_path / "dev-only.jsonl"
    corpus.write_text(
        "".join(
            r.model_copy(update={"split": "dev"}).model_dump_json() + "\n"
            for r in load_corpus(Path("fixtures/corpus/mini-50.jsonl"))
        ),
        encoding="utf-8",
        newline="\n",
    )
    report = json.loads(
        main(results_dir=tmp_path, reason="empty", corpus=corpus).read_text(encoding="utf-8")
    )
    assert report["rows_evaluated"] == 0
    assert "no sealed rows" in report["note"]
    assert len((tmp_path / "test_opened.log").read_text(encoding="utf-8").splitlines()) == 1


def test_the_release_gate_reads_the_prd_numbers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sealed_corpus: Path
) -> None:
    monkeypatch.setenv("SIEVE_FINAL", "1")
    report = json.loads(
        main(
            results_dir=tmp_path,
            reason="r",
            corpus=sealed_corpus,
            classifier=FakeClassifier(),
            n_boot=40,
        ).read_text(encoding="utf-8")
    )
    assert report["release_gate"] == {"recall_point": 0.95, "recall_lower_bound": 0.90}
    assert isinstance(report["release_gate_passed"], bool)


def test_evaluate_is_callable_without_a_judge(sealed_corpus: Path) -> None:
    records = load_corpus(sealed_corpus)
    holdout = [r for r in records if r.split == "holdout"]
    eval_only = [r for r in records if r.split == "eval_only"]
    report = evaluate(holdout, eval_only, FakeClassifier(), None, n_boot=40)
    assert report["judge_model"] is None
    assert report["model_version"] == "fake@0"


def test_the_log_is_opened_in_append_mode_and_never_truncated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SIEVE_FINAL", "1")
    (tmp_path / "test_opened.log").write_text(
        json.dumps({"git_sha": "old", "utc": "2026-01-01T00:00:00Z", "rows_evaluated": 1,
                    "reason": "an earlier version"}) + "\n",
        encoding="utf-8",
        newline="\n",
    )  # fmt: skip
    main(results_dir=tmp_path, reason="this version")
    lines = (tmp_path / "test_opened.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["reason"] == "an earlier version"


def test_the_cli_requires_a_reason(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from bench.eval_final import cli

    monkeypatch.setenv("SIEVE_FINAL", "1")
    with pytest.raises(SystemExit):
        cli(["--results-dir", str(tmp_path)])
    assert not (tmp_path / "test_opened.log").exists()


def test_the_env_var_is_not_leaked_between_tests() -> None:
    assert os.environ.get("SIEVE_FINAL") != "1"
