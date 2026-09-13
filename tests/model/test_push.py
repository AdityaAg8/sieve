from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sieve.model.push import (
    EMPTY,
    HoldoutLogMissing,
    IncompleteCard,
    build_context,
    holdout_log_line,
    main,
    render_card,
    write_card,
)

SHA = "0123456789abcdef0123456789abcdef01234567"


def _ci(point: float) -> dict[str, float]:
    return {"point": point, "lo": point - 0.02, "hi": point + 0.02}


def _breakdown() -> dict[str, dict[str, float]]:
    return {
        "easy": {"n": 10.0, "oof_recall_at_t_low": 1.0, "fpr_stage1_at_t_low": 0.1,
                 "fpr_pipeline": 0.05},
        "hard": {"n": 6.0, "oof_recall_at_t_low": 0.9, "fpr_stage1_at_t_low": 0.2,
                 "fpr_pipeline": 0.12},
    }  # fmt: skip


@pytest.fixture
def results(tmp_path: Path) -> Path:
    """Small synthetic result files. No real benchmark numbers exist yet and none are invented
    into bench/results — these live and die inside the temp directory."""
    directory = tmp_path / "results"
    directory.mkdir()

    def dump(name: str, payload: Any) -> None:
        (directory / name).write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")

    dump(
        "ablations.json",
        {
            "dataset_version": "sievebench-0.0.0-synthetic",
            "rows": [
                {
                    "row": 4,
                    "name": "headline",
                    "seeds": [0, 1, 2],
                    "macro_f1_mean": 0.9,
                    "macro_f1_std": 0.01,
                    "fpr_stage1_at_t_low": _ci(0.3),
                    "fpr_pipeline": _ci(0.15),
                    "minimal_pair_accuracy": 0.8,
                }
            ],
        },
    )
    dump(
        "selection.json",
        {
            "winner": "deberta-v3-base",
            "rule": "higher mean pooled-OOF macro-F1 across seeds",
            "margin": 0.012,
            "per_architecture": {
                "deberta-v3-base": {
                    "macro_f1_mean": 0.9, "macro_f1_std": 0.01, "seeds": 3, "p95_ms": 120.0
                },
                "modernbert-base": {
                    "macro_f1_mean": 0.888, "macro_f1_std": 0.02, "seeds": 3, "p95_ms": 90.0
                },
            },
        },
    )  # fmt: skip
    dump(
        "holdout.json",
        {
            "dataset_version": "sievebench-0.0.0-synthetic",
            "recall": 0.96,
            "recall_lcb": 0.92,
            "fpr_pipeline_real": _ci(0.14),
            "real_poisoned_recall_stage1": 0.9,
            "real_poisoned_recall_pipeline": 0.85,
            "per_tier": _breakdown(),
            "per_concealment": _breakdown(),
            "limitations": "- Synthetic holdout; every number here is a placeholder.",
        },
    )
    dump("curves.json", {"recall_vs_threshold": [], "fpr_vs_recall": []})
    (directory / "test_opened.log").write_text(
        json.dumps({"git_sha": SHA, "utc": "2026-09-12T00:00:00Z", "rows_evaluated": 120,
                    "reason": "v0.1.0 release"}) + "\n",
        encoding="utf-8",
        newline="\n",
    )  # fmt: skip
    return directory


@pytest.fixture
def model_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "artefact"
    directory.mkdir()
    (directory / "sieve_config.json").write_text(
        json.dumps(
            {
                "model_version": "deberta-v3-base@0123456",
                "base_model": "microsoft/deberta-v3-base",
                "training_command": (
                    "python -m sieve.model.train --config bench/train.yaml "
                    "--profile deberta --fold all --seed 1 --epochs 4 "
                    "--out models/deberta-v3-base-final"
                ),
                "temperature": 1.4,
                "t_low": 0.11,
                "t_high": 0.87,
                "t_low_spread": [0.09, 0.11, 0.12, 0.1, 0.13],
                "ece_before": 0.18,
                "ece_after": 0.03,
                "routing_rate": 0.21,
                "val_recall_at_t_low": 0.99,
                "val_precision_at_t_high": 0.96,
                "onnx_int8_parity": 0.004,
                "p50_ms": 61.0,
                "p95_ms": 120.0,
            },
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    return directory


# ------------------------------------------------------------------------ the log line gate


def test_the_card_refuses_when_the_log_has_no_line_for_this_sha(results: Path) -> None:
    with pytest.raises(HoldoutLogMissing, match="no line for git SHA"):
        holdout_log_line(results, "deadbeef" * 5)


def test_the_card_refuses_when_the_log_does_not_exist(tmp_path: Path) -> None:
    with pytest.raises(HoldoutLogMissing, match="never been opened"):
        holdout_log_line(tmp_path, SHA)


def test_the_log_line_is_carried_verbatim(results: Path) -> None:
    line = holdout_log_line(results, SHA)
    assert json.loads(line)["reason"] == "v0.1.0 release"


def test_build_context_fails_when_the_log_line_is_absent(results: Path, model_dir: Path) -> None:
    (results / "test_opened.log").unlink()
    with pytest.raises(HoldoutLogMissing):
        build_context(model_dir, results, sha=SHA)


# ----------------------------------------------------------------------------- rendering


def test_every_slot_is_filled_from_the_result_files(results: Path, model_dir: Path) -> None:
    path = write_card(build_context(model_dir, results, sha=SHA), model_dir)
    text = path.read_text(encoding="utf-8")
    assert path.name == "README.md"
    assert EMPTY not in text
    assert "{{" not in text
    assert path.read_bytes().count(b"\r\n") == 0


def test_the_card_carries_both_fpr_definitions_with_intervals(
    results: Path, model_dir: Path
) -> None:
    text = render_card(build_context(model_dir, results, sha=SHA))
    assert "`FPR_stage1@T_low`" in text and "`FPR_pipeline`" in text
    assert "0.3000 (95% CI 0.2800–0.3200)" in text
    assert "0.1500 (95% CI 0.1300–0.1700)" in text


def test_the_card_carries_the_thresholds_the_spread_and_the_ece(
    results: Path, model_dir: Path
) -> None:
    text = render_card(build_context(model_dir, results, sha=SHA))
    assert "0.11" in text and "0.87" in text
    assert "0.0900–0.1300 over 5 refits" in text
    assert "0.18" in text and "0.03" in text


def test_the_card_carries_the_selection_outcome_and_margin(results: Path, model_dir: Path) -> None:
    text = render_card(build_context(model_dir, results, sha=SHA))
    assert "deberta-v3-base" in text
    assert "0.0120 pooled OOF macro-F1" in text
    assert "higher mean pooled-OOF macro-F1 across seeds" in text
    assert "modernbert-base" in text


def test_the_card_carries_the_sha_and_the_exact_training_command(
    results: Path, model_dir: Path
) -> None:
    text = render_card(build_context(model_dir, results, sha=SHA))
    assert SHA in text
    assert "--profile deberta --fold all --seed 1 --epochs 4" in text


def test_the_card_says_sieve_is_not_a_scanner(results: Path, model_dir: Path) -> None:
    text = render_card(build_context(model_dir, results, sha=SHA))
    assert "not a scanner" in text.lower()


@pytest.mark.parametrize(
    "field", ["t_low", "ece_after", "p95_ms", "onnx_int8_parity", "training_command"]
)
def test_a_missing_number_is_a_build_failure_not_a_blank(
    results: Path, model_dir: Path, field: str
) -> None:
    config = json.loads((model_dir / "sieve_config.json").read_text(encoding="utf-8"))
    config.pop(field)
    (model_dir / "sieve_config.json").write_text(json.dumps(config), encoding="utf-8", newline="\n")
    with pytest.raises(IncompleteCard, match="unfilled slot"):
        write_card(build_context(model_dir, results, sha=SHA), model_dir)


def test_a_missing_results_file_is_a_build_failure(results: Path, model_dir: Path) -> None:
    (results / "curves.json").unlink()
    with pytest.raises(IncompleteCard, match="curves.json"):
        build_context(model_dir, results, sha=SHA)


def test_a_matrix_without_the_headline_row_is_a_build_failure(
    results: Path, model_dir: Path
) -> None:
    (results / "ablations.json").write_text(
        json.dumps({"rows": []}), encoding="utf-8", newline="\n"
    )
    with pytest.raises(IncompleteCard, match="no row 4"):
        build_context(model_dir, results, sha=SHA)


# ----------------------------------------------------------------------------------- CLI


def test_dry_run_writes_the_card_and_uploads_nothing(
    results: Path, model_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sieve.model.version.git_sha", lambda short=True: SHA)
    monkeypatch.setattr("sieve.model.push.git_sha", lambda short=True: SHA)

    def refuse_upload(*args: object, **kwargs: object) -> None:
        raise AssertionError("a dry run must not touch the Hub")

    monkeypatch.setattr("huggingface_hub.HfApi.upload_folder", refuse_upload)
    path = main(
        [
            "--model-dir", str(model_dir), "--repo", "someone/sieve-triage",
            "--results-dir", str(results), "--dry-run",
        ]
    )  # fmt: skip
    assert path == model_dir / "README.md"
    assert EMPTY not in path.read_text(encoding="utf-8")


def test_the_cli_refuses_without_a_git_sha(
    results: Path, model_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sieve.model.push.git_sha", lambda short=True: "unknown")
    with pytest.raises(IncompleteCard, match="git SHA"):
        main(
            [
                "--model-dir", str(model_dir), "--repo", "someone/sieve-triage",
                "--results-dir", str(results), "--dry-run",
            ]
        )  # fmt: skip
