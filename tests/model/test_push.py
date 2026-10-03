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
    unfilled_slots,
    write_card,
)
from sieve.protocols import Thresholds

SHA = "0123456789abcdef0123456789abcdef01234567"


def _ci(point: float) -> dict[str, float]:
    return {"point": point, "lo": point - 0.02, "hi": point + 0.02}


def _breakdown() -> dict[str, dict[str, float]]:
    return {
        "easy": {"n": 10.0, "recall_at_t_low": 1.0, "fpr_stage1_at_t_low": 0.1,
                 "fpr_pipeline": 0.05},
        "hard": {"n": 6.0, "recall_at_t_low": 0.9, "fpr_stage1_at_t_low": 0.2,
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


# ===========================================================================================
# The same card, built from files the REAL writers emitted.
#
# The fixtures above are hand-typed and answer "does every slot render". These answer the
# question that actually matters: do `run_matrix`, `eval_final.evaluate`, `select` and the
# `train` config writer emit the key vocabulary the card reads? A drift between writer and
# reader is invisible to a hand-typed fixture, and that is how `oof_recall_at_t_low` vs
# `recall_at_t_low` survived the first round.
# ===========================================================================================


@pytest.fixture
def dev_records_for_writers() -> list[Any]:
    from sieve.model.data import load_corpus

    records = load_corpus(Path("fixtures/corpus/mini-50.jsonl"))
    servers = sorted({r.server_name for r in records})
    fold_of = {name: i % 5 for i, name in enumerate(servers)}
    return [r.model_copy(update={"split": "dev", "fold": fold_of[r.server_name]}) for r in records]


@pytest.fixture
def written_results(dev_records_for_writers: list[Any], tmp_path: Path) -> Path:
    """`ablations.json`, `holdout.json`, `selection.json`, `curves.json`, `test_opened.log`,
    each produced by the production writer rather than typed here."""
    import numpy as np

    from bench.ablations.run import Inputs, RowSpec, dataset_version, run_matrix, write_results
    from bench.eval_final import evaluate as holdout_evaluate
    from sieve.model.data import LABEL2ID
    from sieve.model.select import select, write_selection
    from sieve.model.thresholds import write_curves
    from sieve.protocols import Classifier, FakeClassifier

    dev = dev_records_for_writers
    results = tmp_path / "results"
    results.mkdir(parents=True, exist_ok=True)
    corpus = tmp_path / "labelled.jsonl"
    corpus.write_text(
        "".join(r.model_dump_json() + "\n" for r in dev), encoding="utf-8", newline="\n"
    )

    def never(spec: RowSpec, seed: int) -> Classifier:  # pragma: no cover - must not be reached
        raise AssertionError("row 4 must be scored from pooled OOF arrays")

    baseline = {
        r.id: {
            "id": r.id,
            "server_name": r.server_name,
            "label": r.label,
            "source": r.source,
            "flagged": r.label == "poisoned",
            "rule_ids": ["R1"] if r.label == "poisoned" else [],
        }
        for r in dev
    }
    pairs = [{"host_id": r.mutation_of, "mutation_id": r.id} for r in dev if r.mutation_of]

    # `run_matrix` writes ablations.json. Row 4 is the headline the card reads.
    write_results(
        run_matrix(
            [RowSpec(row=4, name="headline", classifier="tfidf", judge="none", minimal_pairs=True)],
            Inputs(
                records=dev,
                baseline_rows=baseline,
                minimal_pairs=pairs,
                dataset_version=dataset_version(corpus),
            ),
            classifier_factory=never,
            n_boot=40,
        ),
        results / "ablations.json",
    )

    # `eval_final.evaluate` writes holdout.json, including the derived `limitations` slot.
    sealed = [r.model_copy(update={"split": "holdout"}) for r in dev]
    eval_only = [
        r.model_copy(
            update={
                "id": f"real-poisoned-{i}",
                "server_name": f"wild-{i}",
                "source": "real_poisoned",
                "label": "poisoned",
                "split": "eval_only",
            }
        )
        for i, r in enumerate(dev[:4])
    ]
    holdout = holdout_evaluate(sealed, eval_only, FakeClassifier(), None, n_boot=40)
    holdout["dataset_version"] = dataset_version(corpus)
    (results / "holdout.json").write_text(
        json.dumps(holdout, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )

    # `select` writes selection.json.
    def seed_dir(name: str, skill: float, p95: float) -> Path:
        directory = tmp_path / name
        directory.mkdir(parents=True, exist_ok=True)
        rng = np.random.default_rng(abs(hash(name)) % 2**32)
        logits = rng.normal(0.0, 1.0, size=(len(dev), 3))
        for row, record in zip(logits, dev, strict=True):
            row[LABEL2ID[record.label]] += skill
        np.save(directory / "oof_logits.npy", logits.astype(np.float32))
        np.save(
            directory / "oof_labels.npy",
            np.asarray([LABEL2ID[r.label] for r in dev], dtype=np.int64),
        )
        (directory / "oof_ids.json").write_text(
            json.dumps([r.id for r in dev]), encoding="utf-8", newline="\n"
        )
        (directory / "sieve_config.json").write_text(
            json.dumps({"p95_ms": p95}), encoding="utf-8", newline="\n"
        )
        return directory

    write_selection(
        select(
            {
                "deberta-v3-base": [seed_dir(f"deberta-s{s}", 3.0, 120.0) for s in range(3)],
                "modernbert-base": [seed_dir(f"modernbert-s{s}", 1.0, 90.0) for s in range(3)],
            },
            expected_ids=[r.id for r in dev],
        ),
        results / "selection.json",
    )

    # `thresholds.write_curves` writes curves.json.
    probs = FakeClassifier().predict_proba(
        [
            __import__("sieve.serialise", fromlist=["serialise"]).serialise(r.to_tool_record())
            for r in dev
        ]
    )
    write_curves(
        probs,
        [LABEL2ID[r.label] for r in dev],
        [r.server_name for r in dev],
        results / "curves.json",
        n_thresholds=8,
        n_boot=40,
    )

    # `eval_final.append_log_line` writes the log line.
    from bench.eval_final import append_log_line

    append_log_line(results, rows_evaluated=len(sealed), reason="fixture release")
    line = json.loads((results / "test_opened.log").read_text(encoding="utf-8").splitlines()[0])
    assert line["git_sha"], "the log line must carry a SHA for the card to quote"
    return results


@pytest.fixture
def written_model_dir(dev_records_for_writers: list[Any], tmp_path: Path) -> Path:
    """`sieve_config.json` as `sieve.model.train.run` writes it, then as `calibrate` fills it."""
    from sieve.model.thresholds import threshold_payload
    from sieve.model.train import load_profile

    directory = tmp_path / "artefact"
    directory.mkdir(parents=True, exist_ok=True)
    config = load_profile(Path("bench/train.yaml"), "deberta", fold="all", seed=1, epochs=4)
    payload: dict[str, Any] = {
        "model_version": config.model_version(),
        "base_model": config.base_model,
        "training_command": config.training_command(
            Path("bench/data/labelled.jsonl"), Path("models/deberta-v3-base-final")
        ),
        "fold": config.fold,
        "seed": config.seed,
        "mode": config.mode,
        "num_labels": config.num_labels,
        "max_length": config.max_length,
        "temperature": None,
        "t_low": None,
        "t_high": None,
    }
    # What `calibrate.main` merges in.
    chosen = Thresholds(0.11, 0.87, 1.4, 0.99, 0.96, 0.21)
    payload.update(
        threshold_payload(
            chosen, t_low_spread=[0.09, 0.11, 0.12, 0.1, 0.13], ece_before=0.18, ece_after=0.03
        )
    )
    # What `export.main` appends.
    payload.update({"onnx_int8_parity": 0.004, "p50_ms": 61.0, "p95_ms": 120.0})
    (directory / "sieve_config.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return directory


def test_a_card_built_entirely_from_real_writer_output_has_no_blank_slot(
    written_results: Path, written_model_dir: Path
) -> None:
    context = build_context(written_model_dir, written_results, sha=_written_sha(written_results))
    assert unfilled_slots(context) == []
    path = write_card(context, written_model_dir)
    assert EMPTY not in path.read_text(encoding="utf-8")


def test_the_breakdown_keys_the_writers_emit_are_the_keys_the_card_reads(
    written_results: Path, written_model_dir: Path
) -> None:
    """The exact drift that round 1 found: `oof_recall_at_t_low` vs `recall_at_t_low`."""
    from bench.ablations.run import BREAKDOWN_KEYS

    holdout = json.loads((written_results / "holdout.json").read_text(encoding="utf-8"))
    ablations = json.loads((written_results / "ablations.json").read_text(encoding="utf-8"))
    for table in (holdout["per_tier"], holdout["per_concealment"]):
        assert set(next(iter(table.values()))) == set(BREAKDOWN_KEYS)
    assert set(next(iter(ablations["rows"][0]["per_tier"].values()))) == set(BREAKDOWN_KEYS)

    context = build_context(written_model_dir, written_results, sha=_written_sha(written_results))
    assert "recall=" in str(context["per_tier"])


def test_the_three_previously_missing_fields_are_emitted(
    written_results: Path, written_model_dir: Path
) -> None:
    ablations = json.loads((written_results / "ablations.json").read_text(encoding="utf-8"))
    holdout = json.loads((written_results / "holdout.json").read_text(encoding="utf-8"))
    config = json.loads((written_model_dir / "sieve_config.json").read_text(encoding="utf-8"))
    assert ablations["dataset_version"]
    assert holdout["limitations"]
    assert config["training_command"].startswith("python -m sieve.model.train")
    assert "--fold all" in config["training_command"]
    assert "--epochs 4" in config["training_command"]


def test_a_writer_that_stops_emitting_a_field_fails_the_card(
    written_results: Path, written_model_dir: Path
) -> None:
    holdout = json.loads((written_results / "holdout.json").read_text(encoding="utf-8"))
    del holdout["limitations"]
    (written_results / "holdout.json").write_text(
        json.dumps(holdout), encoding="utf-8", newline="\n"
    )
    context = build_context(written_model_dir, written_results, sha=_written_sha(written_results))
    assert unfilled_slots(context) == ["limitations"]
    with pytest.raises(IncompleteCard, match="limitations"):
        write_card(context, written_model_dir)


def test_a_release_note_can_add_to_the_derived_limitations(
    written_results: Path, written_model_dir: Path, tmp_path: Path
) -> None:
    note = tmp_path / "note.md"
    note.write_text("- Trained on a toy corpus.", encoding="utf-8", newline="\n")
    context = build_context(
        written_model_dir,
        written_results,
        sha=_written_sha(written_results),
        limitations=note.read_text(encoding="utf-8"),
    )
    assert "Trained on a toy corpus." in str(context["limitations"])
    assert "The holdout is" in str(context["limitations"])


def _written_sha(results: Path) -> str:
    """The SHA the real `append_log_line` recorded, so the card's gate is exercised for real."""
    line = (results / "test_opened.log").read_text(encoding="utf-8").splitlines()[0]
    return str(json.loads(line)["git_sha"])


def test_a_single_seed_spread_is_written_as_undefined_not_zero() -> None:
    """D5: one seed has no sample std (ddof=1); the card says so instead of crashing."""
    from sieve.model.push import _mean_std, _selection_table

    assert "± n/a" in (_mean_std({"macro_f1_mean": 0.9, "macro_f1_std": None, "seeds": [0]}) or "")
    table = _selection_table(
        {"per_architecture": {"x": {"macro_f1_mean": 0.9, "macro_f1_std": None, "seeds": 1}}}
    )
    assert table is not None and "± n/a" in table
