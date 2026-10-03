from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bench.ablations.run import (
    DEFAULT_SEEDS,
    Inputs,
    InsufficientSeeds,
    MatrixError,
    RowSpec,
    dataset_version,
    evaluate_row,
    load_baseline_rows,
    load_matrix,
    load_minimal_pairs,
    minimal_pair_accuracy,
    pooled_oof_scores,
    run_matrix,
    scanner_findings_from_baseline,
    write_results,
)
from bench.split.split import DEFAULT_SEED, assign
from sieve.model.data import load_corpus
from sieve.protocols import Classifier, FakeClassifier, FakeJudge, Judge
from sieve.types import CorpusRecord

REQUIRED_KEYS = {
    "fpr_stage1_at_t_low",
    "fpr_pipeline",
    "oof_recall_at_t_low",
    "fp_reduction_vs_baseline",
    "fp_reduction_all_sources",
    "routing_rate",
    "macro_f1_mean",
    "macro_f1_std",
    "per_tier",
    "per_concealment",
    "per_source",
    "minimal_pair_accuracy",
    "cost_usd_per_50",
    "p95_ms",
}

N_BOOT = 60
NOTE_LINE = "\nnote:\n"


def _fake_classifier_factory(spec: RowSpec, seed: int) -> Classifier:
    """The contract fake, imported from sieve.protocols. No local stub, no import swap."""
    return FakeClassifier()


def _fake_judge_factory(model: str) -> Judge:
    return FakeJudge()


@pytest.fixture
def dev() -> list[CorpusRecord]:
    """The development rows of mini-50 under L1's real grouped assignment.

    Defined locally because `tests/bench/conftest.py` belongs to L1/L2; the assignment itself
    is theirs, imported rather than re-implemented.
    """
    assigned = assign(load_corpus(Path("fixtures/corpus/mini-50.jsonl")), seed=DEFAULT_SEED)
    return [r for r in assigned if r.split == "dev"]


@pytest.fixture
def inputs(dev: list[CorpusRecord]) -> Inputs:
    """Baseline flags that flag every poisoned row plus two benign ones, so FPR is non-zero."""
    baseline: dict[str, dict[str, Any]] = {}
    benign_flagged = 0
    for record in dev:
        flagged = record.label == "poisoned"
        if not flagged and benign_flagged < 4:
            flagged = True
            benign_flagged += 1
        baseline[record.id] = {
            "id": record.id,
            "server_name": record.server_name,
            "label": record.label,
            "source": record.source,
            "flagged": flagged,
            "rule_ids": ["R1"] if flagged else [],
        }
    poisoned = [r for r in dev if r.label == "poisoned" and r.mutation_of]
    pairs = [{"host_id": r.mutation_of, "mutation_id": r.id} for r in poisoned]
    return Inputs(records=dev, baseline_rows=baseline, minimal_pairs=pairs)


def _spec(**overrides: Any) -> RowSpec:
    base = {"row": 4, "name": "row", "classifier": "models/x-s*", "judge": "none"}
    base.update(overrides)
    return RowSpec.model_validate(base)


def _evaluate(spec: RowSpec, inputs: Inputs, **kwargs: Any) -> dict[str, Any]:
    return evaluate_row(
        spec,
        inputs,
        classifier_factory=_fake_classifier_factory,
        judge_factory=_fake_judge_factory,
        seeds=DEFAULT_SEEDS,
        n_boot=N_BOOT,
        **kwargs,
    )


# ------------------------------------------------------------------------------ the matrix


def test_the_committed_matrix_has_the_fourteen_rows_of_prd_11_5() -> None:
    """Plus row 15, ModernBERT-1024 (training plan H5, §6.3)."""
    specs = load_matrix(Path("bench/ablations.yaml"))
    assert [s.row for s in specs] == list(range(1, 16))
    by_row = {s.row: s for s in specs}
    assert by_row[1].classifier == "baseline"
    assert by_row[2].classifier == "tfidf"
    assert by_row[3].mode == "description_only"
    assert by_row[4].mode == "full" and by_row[4].judge == "none"
    assert by_row[8].classifier == "none" and by_row[8].judge != "none"
    assert by_row[9].scanner_features is True
    assert by_row[12].num_labels == 2
    assert by_row[13].classifier == "unicode_hygiene"
    assert by_row[14].minimal_pairs is True
    assert by_row[15].classifier == "models/modernbert-base-1024-s*"


def test_single_seed_rows_and_three_seed_rows_follow_d9() -> None:
    by_row = {s.row: s for s in load_matrix(Path("bench/ablations.yaml"))}
    assert all(by_row[r].seeds == [0] for r in (3, 9, 12, 15))
    assert by_row[4].seeds is None and by_row[5].seeds is None  # the run's three seeds
    assert {r for r, s in by_row.items() if s.status == "deferred"} == {6, 7, 8}


def test_the_primary_encoder_comes_from_selection_json(tmp_path: Path) -> None:
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps({"winner": "modernbert-base",
                    "per_architecture": {"modernbert-base": {}, "deberta-v3-base": {}}}),
        encoding="utf-8",
    )  # fmt: skip
    by_row = {s.row: s for s in load_matrix(Path("bench/ablations.yaml"), selection)}
    assert by_row[4].classifier == "models/modernbert-base-s*"
    assert by_row[5].classifier == "models/deberta-v3-base-s*"
    default = {s.row: s for s in load_matrix(Path("bench/ablations.yaml"))}
    assert default[4].classifier == "models/deberta-v3-base-s*"


def test_a_deferred_judge_row_is_emitted_not_scored(inputs: Inputs) -> None:
    def no_classifier(spec: RowSpec, seed: int) -> Any:
        raise AssertionError("a deferred row must not be scored")

    payload = run_matrix(
        [_spec(row=6, judge="claude-haiku-4-5", status="deferred"),
         _spec(row=10, classifier="tfidf", breakdown_of=[4, 6])],
        inputs, classifier_factory=no_classifier, judge_factory=None, n_boot=N_BOOT,
    )  # fmt: skip
    six, ten = payload["rows"]
    assert six["status"] == "deferred" and "D8" in six["reason"]
    assert ten["deferred_breakdown_of"] == [6]


def test_a_declared_single_seed_row_runs_at_its_one_seed(inputs: Inputs, artefacts: Path) -> None:
    result = _row(
        RowSpec(row=3, name="desc", classifier=str(artefacts / "tiny-s*"), judge="none",
                seeds=[0]),
        inputs,
    )  # fmt: skip
    assert result["seeds"] == [0] and result["single_seed"] is True


def test_a_matrix_with_a_repeated_row_number_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_text(
        "rows:\n"
        "  - {row: 1, name: a, classifier: tfidf}\n"
        "  - {row: 1, name: b, classifier: tfidf}\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(MatrixError, match="repeats a row number"):
        load_matrix(path)


# --------------------------------------------------------------------------------- rows


def _assert_row_shape(result: dict[str, Any]) -> None:
    assert set(result) >= REQUIRED_KEYS
    for key in ("fpr_stage1_at_t_low", "fpr_pipeline", "oof_recall_at_t_low"):
        assert set(result[key]) == {"point", "lo", "hi", "std", "per_seed"}
        assert result[key]["lo"] <= result[key]["point"] <= result[key]["hi"]
    assert set(result["fp_reduction_vs_baseline"]) >= {"point", "lo", "hi", "mcnemar_p"}


@pytest.mark.parametrize(
    "spec",
    [
        _spec(row=2, classifier="tfidf", minimal_pairs=True),
        _spec(row=13, classifier="unicode_hygiene"),
    ],
    ids=["row2-tfidf", "row13-hygiene"],
)
def test_every_required_key_is_present(spec: RowSpec, inputs: Inputs) -> None:
    _assert_row_shape(_evaluate(spec, inputs))


@pytest.mark.parametrize("num_labels", [3, 2], ids=["row4-encoder", "row12-two-class"])
def test_every_required_key_is_present_for_a_seeded_row(
    inputs: Inputs, artefacts: Path, num_labels: int
) -> None:
    """Seeded rows are scored from committed OOF arrays, so they need a real artefact."""
    _assert_row_shape(
        _evaluate(
            _spec(
                row=4 if num_labels == 3 else 12,
                classifier=str(artefacts / "tiny-s*"),
                num_labels=num_labels,
                minimal_pairs=True,
            ),
            inputs,
        )
    )


def test_the_judge_can_only_remove_confirmations(inputs: Inputs, artefacts: Path) -> None:
    """Row 6: a row the judge dismisses leaves the confirmed set, so pipeline FPR cannot rise."""
    row6 = _evaluate(
        _spec(row=6, classifier=str(artefacts / "tiny-s*"), judge="claude-haiku-4-5"), inputs
    )
    assert row6["fpr_stage1_at_t_low"]["point"] >= row6["fpr_pipeline"]["point"]


def test_row_13_is_the_hygiene_detector_broken_down_per_concealment(inputs: Inputs) -> None:
    from sieve.model.hygiene import detect

    result = _evaluate(_spec(row=13, classifier="unicode_hygiene"), inputs)
    flagged = {r.id for r in inputs.records if detect(r.to_tool_record())}
    poisoned_flagged = {r.id for r in inputs.records if r.label == "poisoned"} & flagged
    expected_recall = len(poisoned_flagged) / sum(
        1 for r in inputs.records if r.label == "poisoned"
    )
    assert result["oof_recall_at_t_low"]["point"] == pytest.approx(expected_recall)
    assert set(result["per_concealment"]) == {r.concealment for r in inputs.records}
    assert "preprocessing, not in the model" in result["note"]


def test_row_14_reports_a_fraction(inputs: Inputs, artefacts: Path) -> None:
    result = _evaluate(
        _spec(row=14, classifier=str(artefacts / "tiny-s*"), minimal_pairs=True), inputs
    )
    accuracy = result["minimal_pair_accuracy"]
    assert accuracy is not None
    assert 0.0 <= accuracy <= 1.0


def test_minimal_pair_accuracy_needs_both_sides_right() -> None:
    pairs = [{"host_id": "h1", "mutation_id": "m1"}, {"host_id": "h2", "mutation_id": "m2"}]
    predicted = {"h1": "benign", "m1": "poisoned", "h2": "poisoned", "m2": "poisoned"}
    assert minimal_pair_accuracy(pairs, predicted) == 0.5
    assert minimal_pair_accuracy(pairs, {}) is None


def test_a_row_without_minimal_pairs_reports_null(inputs: Inputs, artefacts: Path) -> None:
    spec = _spec(row=3, classifier=str(artefacts / "tiny-s*"), mode="description_only")
    assert _evaluate(spec, inputs)["minimal_pair_accuracy"] is None


# ------------------------------------------------------------------- refusals, not partial rows


def test_a_missing_baseline_artefact_raises_rather_than_writing_a_partial_row(
    tmp_path: Path,
) -> None:
    with pytest.raises(MatrixError, match="baseline_raw.json"):
        load_baseline_rows(tmp_path / "baseline_raw.json")


def test_a_missing_minimal_pairs_artefact_raises(tmp_path: Path) -> None:
    with pytest.raises(MatrixError, match="minimal_pairs.json"):
        load_minimal_pairs(tmp_path / "minimal_pairs.json")


def test_a_baseline_row_missing_a_record_raises(inputs: Inputs) -> None:
    inputs.baseline_rows.pop(inputs.records[0].id)
    with pytest.raises(MatrixError, match="no row for"):
        _evaluate(_spec(row=1, classifier="baseline"), inputs)


def test_a_row_that_asks_for_minimal_pairs_without_them_raises(inputs: Inputs) -> None:
    inputs.minimal_pairs = None
    with pytest.raises(MatrixError, match="minimal pairs"):
        _evaluate(_spec(row=14, classifier="tfidf", minimal_pairs=True), inputs)


def test_an_encoder_row_with_fewer_than_three_seeds_raises(inputs: Inputs) -> None:
    with pytest.raises(InsufficientSeeds, match="single-seed"):
        evaluate_row(
            _spec(row=4),
            inputs,
            classifier_factory=_fake_classifier_factory,
            judge_factory=None,
            seeds=(0,),
            n_boot=N_BOOT,
        )


def test_a_judge_row_without_a_judge_factory_raises(inputs: Inputs) -> None:
    with pytest.raises(MatrixError, match="judge factory"):
        evaluate_row(
            _spec(row=6, judge="claude-haiku-4-5"),
            inputs,
            classifier_factory=_fake_classifier_factory,
            judge_factory=None,
            seeds=DEFAULT_SEEDS,
            n_boot=N_BOOT,
        )


def test_the_runner_refuses_to_start_with_sieve_rerecord_set(
    inputs: Inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIEVE_RERECORD", "1")
    with pytest.raises(MatrixError, match="SIEVE_RERECORD"):
        run_matrix(
            [_spec(row=2, classifier="tfidf")],
            inputs,
            classifier_factory=_fake_classifier_factory,
            n_boot=N_BOOT,
        )


def test_the_runner_refuses_a_non_dev_row(inputs: Inputs) -> None:
    inputs.records = [
        *inputs.records,
        inputs.records[0].model_copy(update={"id": "sealed", "split": "holdout"}),
    ]
    with pytest.raises(MatrixError, match="non-dev row"):
        run_matrix(
            [_spec(row=2, classifier="tfidf")],
            inputs,
            classifier_factory=_fake_classifier_factory,
            n_boot=N_BOOT,
        )


def test_a_network_call_during_a_run_is_an_error(inputs: Inputs) -> None:
    import socket

    def phoning_home(spec: RowSpec, seed: int) -> Classifier:
        with socket.socket() as sock:  # closed on the way out; an unclosed fd is a warning
            sock.connect(("example.invalid", 80))
        return FakeClassifier()

    # `reinference` is the only path that still loads a model, so it is the only way a row can
    # reach the network at all. The guard has to catch it.
    with pytest.raises(MatrixError, match="network connection"):
        run_matrix(
            [_spec(row=4, classifier="models/x-s*", reinference=True)],
            inputs,
            classifier_factory=phoning_home,
            n_boot=N_BOOT,
        )


def test_the_socket_guard_is_lifted_after_the_run(inputs: Inputs) -> None:
    import socket

    before = socket.socket.connect
    run_matrix(
        [_spec(row=13, classifier="unicode_hygiene")],
        inputs,
        classifier_factory=_fake_classifier_factory,
        n_boot=N_BOOT,
    )
    assert socket.socket.connect is before


# ----------------------------------------------------------------------------- the output


def test_run_matrix_writes_selected_rows_only(inputs: Inputs, tmp_path: Path) -> None:
    specs = [
        _spec(row=2, classifier="tfidf"),
        _spec(row=4),
        _spec(row=13, classifier="unicode_hygiene"),
    ]
    payload = run_matrix(
        specs,
        inputs,
        classifier_factory=_fake_classifier_factory,
        rows=[2, 13],
        n_boot=N_BOOT,
    )
    assert [r["row"] for r in payload["rows"]] == [2, 13]
    out = write_results(payload, tmp_path / "ablations.json")
    reloaded = json.loads(out.read_text(encoding="utf-8"))
    assert reloaded["n_records"] == len(inputs.records)
    assert out.read_bytes().count(b"\r\n") == 0


def test_an_unknown_row_selection_raises(inputs: Inputs) -> None:
    with pytest.raises(MatrixError, match="no matrix row"):
        run_matrix(
            [_spec(row=2, classifier="tfidf")],
            inputs,
            classifier_factory=_fake_classifier_factory,
            rows=[99],
            n_boot=N_BOOT,
        )


def test_the_matrix_only_ever_sees_the_development_rows(dev: list[CorpusRecord]) -> None:
    """The fixture is the real assignment, which seals some of mini-50; only `dev` reaches here."""
    assigned = assign(load_corpus(Path("fixtures/corpus/mini-50.jsonl")), seed=DEFAULT_SEED)
    assert {r.split for r in assigned} > {"dev"}, "the fixture must exercise a sealed row"
    assert {r.split for r in dev} == {"dev"}
    assert len(dev) < len(assigned)


# ----------------------------------------------------------- errors.md (out-of-fold only)


def test_errors_md_never_contains_a_non_dev_record(dev: list[CorpusRecord]) -> None:
    import numpy as np

    from bench.analysis.errors import NotOutOfFold, render
    from sieve.protocols import Thresholds

    probs = np.full((len(dev) + 1, 3), 1 / 3)
    sealed = [*dev, dev[0].model_copy(update={"id": "sealed", "split": "holdout"})]
    with pytest.raises(NotOutOfFold, match="non-dev"):
        render(sealed, probs, Thresholds(0.3, 0.85, 1.0, 1.0, 1.0, 0.2))


def test_errors_md_lists_exactly_the_false_positives_and_false_negatives(
    dev: list[CorpusRecord], tmp_path: Path
) -> None:

    from bench.analysis.errors import write_errors
    from sieve.protocols import FakeClassifier, Thresholds
    from sieve.serialise import serialise

    thresholds = Thresholds(0.3, 0.85, 1.0, 1.0, 1.0, 0.2)
    probs = FakeClassifier().predict_proba([serialise(r.to_tool_record()) for r in dev])
    path = write_errors(dev, probs, thresholds, tmp_path / "errors.md")
    text = path.read_text(encoding="utf-8")

    expected = {
        r.id
        for r, p in zip(dev, probs[:, 2], strict=True)
        if (p >= thresholds.t_low) != (r.label == "poisoned")
    }
    for record in dev:
        assert (f"## {record.id} " in text) == (record.id in expected)
    assert text.count(NOTE_LINE) == len(expected)
    assert path.read_bytes().count(b"\r\n") == 0


def test_errors_md_carries_all_three_probabilities_and_the_metadata(
    dev: list[CorpusRecord], tmp_path: Path
) -> None:
    import numpy as np

    from bench.analysis.errors import write_errors
    from sieve.protocols import Thresholds

    probs = np.tile([0.01, 0.01, 0.98], (len(dev), 1))
    text = write_errors(
        dev, probs, Thresholds(0.3, 0.85, 1.0, 1.0, 1.0, 0.2), tmp_path / "errors.md"
    ).read_text(encoding="utf-8")
    assert "benign=0.0100" in text
    assert "imperative_benign=0.0100" in text
    assert "poisoned=0.9800" in text
    assert "- concealment:" in text and "- source:" in text and "- difficulty:" in text


# ----------------------------------------------------------------------------- demo.json


def test_demo_has_the_fixture_verdict_shape_and_is_provisional() -> None:
    from bench.demo import build_demo
    from sieve.protocols import FakeClassifier

    fixture = json.loads(Path("fixtures/verdicts/sample-27.json").read_text(encoding="utf-8"))
    payload = build_demo(classifier=FakeClassifier())

    assert set(payload) == set(fixture)
    assert payload["meta"]["provisional"] is True
    assert set(payload["summary"]) == set(fixture["summary"])
    assert set(payload["findings"][0]) == set(fixture["findings"][0])
    assert set(payload["findings"][0]["result"]) == set(fixture["findings"][0]["result"])
    assert payload["summary"]["tool_count"] == len(payload["findings"]) == 27


def test_demo_accepts_an_injected_triage_callable() -> None:
    """`sieve.pipeline` is L5's; the demo takes the callable so it can be driven without it."""
    from bench.demo import build_demo
    from sieve.protocols import FakeClassifier
    from sieve.types import TriageResult

    calls: list[int] = []

    def fake_triage(records, findings_by_id, deps):  # type: ignore[no-untyped-def]
        calls.append(len(list(records)))
        return [
            TriageResult(
                verdict="dismissed",
                stage="classifier",
                predicted_class="benign",
                class_probabilities={"benign": 1.0, "imperative_benign": 0.0, "poisoned": 0.0},
                poisoned_probability=0.0,
                confidence=1.0,
                sub_labels=[],
                owasp=[],
                evidence_spans=[],
                reasoning=None,
                remediation=None,
                scanner_findings=[],
                model_version="injected@0",
                judge_model=None,
                latency_ms=1,
            )
            for _ in records
        ]

    payload = build_demo(classifier=FakeClassifier(), triage_fn=fake_triage)
    assert calls == [27]
    assert payload["summary"]["dismissed"] == 27
    assert payload["summary"]["model_version"] == "injected@0"


def test_the_committed_demo_file_is_provisional_and_matches_a_fresh_run(tmp_path: Path) -> None:
    from bench.demo import main

    committed = Path("bench/results/demo.json")
    if not committed.exists():
        pytest.skip("bench/results/demo.json has not been generated yet")
    fresh = main(["--results-dir", str(tmp_path)])
    # Bytes, not parsed JSON: `make bench` claims byte-identical reproduction (M6a), and a
    # newline or key-order change is exactly the drift a parsed comparison would hide.
    assert committed.read_bytes() == fresh.read_bytes()
    assert json.loads(committed.read_text(encoding="utf-8"))["meta"]["provisional"] is True


# ===========================================================================================
# Seeded rows are scored from pooled OUT-OF-FOLD arrays, never by re-running inference.
#
# Every artefact below is produced by a real writer: the per-fold files in the shape
# `sieve.model.train.run` writes them, the pooled arrays and `sieve_config.json` by
# `sieve.model.calibrate.main` itself. Nothing here hand-types a number production computes.
# ===========================================================================================


def _write_fold(directory: Path, held: list[CorpusRecord], *, seed: int, skill: float) -> Path:
    """One fold artefact in the shape `sieve.model.train.run` writes it."""
    import numpy as np

    from sieve.model.data import LABEL2ID

    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    logits = rng.normal(0.0, 0.5, size=(len(held), 3))
    for row, record in zip(logits, held, strict=True):
        row[LABEL2ID[record.label]] += skill
    np.save(directory / "oof_logits.npy", logits.astype(np.float32))
    np.save(
        directory / "oof_labels.npy",
        np.asarray([LABEL2ID[r.label] for r in held], dtype=np.int64),
    )
    (directory / "oof_ids.json").write_text(
        json.dumps([r.id for r in held], indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (directory / "sieve_config.json").write_text(
        json.dumps(
            {
                "model_version": f"tiny@abc{seed}",
                "fold": None,
                "seed": seed,
                "mode": "full",
                "num_labels": 3,
                "max_length": 128,
                "temperature": None,
                "t_low": None,
                "t_high": None,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return directory


@pytest.fixture
def corpus_file(dev: list[CorpusRecord], tmp_path: Path) -> Path:
    path = tmp_path / "labelled.jsonl"
    path.write_text(
        "".join(r.model_dump_json() + "\n" for r in dev), encoding="utf-8", newline="\n"
    )
    return path


@pytest.fixture
def artefacts(dev: list[CorpusRecord], corpus_file: Path, tmp_path: Path) -> Path:
    """`<models>/tiny-s{0,1,2}`, each holding real pooled arrays and a real threshold file."""
    from sieve.model.calibrate import main as calibrate_main

    models = tmp_path / "models"
    for seed in DEFAULT_SEEDS:
        fold_dirs = [
            _write_fold(
                models / f"tiny-f{fold}-s{seed}",
                [r for r in dev if r.fold == fold],
                seed=seed * 10 + fold,
                # Three seeds are three models: give them different skill so macro_f1_std is a
                # real spread and a bug that reads one seed three times cannot hide in it.
                skill=2.4 - 0.7 * seed,
            )
            for fold in range(5)
        ]
        calibrate_main(
            [
                "--dirs", *[str(d) for d in fold_dirs],
                "--corpus", str(corpus_file),
                "--results-dir", str(models / "results"),
                "--out", str(models / f"tiny-s{seed}"),
                "--n-boot", "40", "--curve-boot", "40", "--curve-points", "12",
            ]
        )  # fmt: skip
    return models


def _explodes(spec: RowSpec, seed: int) -> Classifier:
    """A factory that must never be reached: a seeded row is scored from committed arrays."""
    raise AssertionError(f"row {spec.row} re-ran inference instead of reading pooled OOF arrays")


def _row(spec: RowSpec, inputs: Inputs, **kwargs: Any) -> dict[str, Any]:
    return evaluate_row(
        spec,
        inputs,
        classifier_factory=kwargs.pop("classifier_factory", _explodes),
        judge_factory=kwargs.pop("judge_factory", None),
        seeds=kwargs.pop("seeds", DEFAULT_SEEDS),
        n_boot=kwargs.pop("n_boot", N_BOOT),
        **kwargs,
    )


def test_pooled_oof_scores_read_the_real_calibrate_output(
    dev: list[CorpusRecord], artefacts: Path
) -> None:
    scored = pooled_oof_scores(artefacts / "tiny-s0", dev)
    assert len(scored.scores) == len(dev)
    assert all(0.0 <= s <= 1.0 for s in scored.scores)
    assert scored.predicted is not None
    config = json.loads((artefacts / "tiny-s0" / "sieve_config.json").read_text(encoding="utf-8"))
    assert scored.thresholds.t_low == config["t_low"]
    assert scored.thresholds.temperature == config["temperature"]


def test_scores_are_reordered_onto_the_record_order_not_the_fold_order(
    dev: list[CorpusRecord], artefacts: Path
) -> None:
    """`oof_ids.json` is fold-concatenated; the metrics need record order."""
    forward = pooled_oof_scores(artefacts / "tiny-s0", dev)
    backward = pooled_oof_scores(artefacts / "tiny-s0", list(reversed(dev)))
    assert backward.scores == list(reversed(forward.scores))


def test_a_seeded_row_never_re_runs_inference(inputs: Inputs, artefacts: Path) -> None:
    result = _row(
        RowSpec(row=4, name="headline", classifier=str(artefacts / "tiny-s*"), judge="none"),
        inputs,
    )
    assert result["oof_recall_at_t_low"]["point"] > 0.0
    assert result["macro_f1_mean"] is not None
    assert result["seeds"] == list(DEFAULT_SEEDS)


def test_the_seed_glob_is_substituted_per_seed(inputs: Inputs, artefacts: Path) -> None:
    """Three seeds must produce three different macro-F1 values, not one repeated."""
    result = _row(
        RowSpec(row=4, name="headline", classifier=str(artefacts / "tiny-s*"), judge="none"),
        inputs,
    )
    assert result["macro_f1_std"] > 0.0


def test_a_missing_seed_artefact_raises_rather_than_writing_a_partial_row(
    inputs: Inputs, tmp_path: Path
) -> None:
    with pytest.raises(MatrixError, match="oof_logits.npy"):
        _row(
            RowSpec(row=4, name="headline", classifier=str(tmp_path / "nope-s*"), judge="none"),
            inputs,
        )


def test_an_artefact_that_does_not_cover_the_dev_set_raises(
    dev: list[CorpusRecord], inputs: Inputs, artefacts: Path
) -> None:
    inputs.records = [*dev, dev[0].model_copy(update={"id": "unseen", "server_name": "ghost"})]
    with pytest.raises(MatrixError, match="missing"):
        _row(
            RowSpec(row=4, name="headline", classifier=str(artefacts / "tiny-s*"), judge="none"),
            inputs,
        )


def test_an_uncalibrated_artefact_raises_rather_than_defaulting_a_threshold(
    dev: list[CorpusRecord], tmp_path: Path
) -> None:
    from sieve.model.thresholds import ThresholdsMissing

    uncalibrated = _write_fold(tmp_path / "raw-s0", dev, seed=1, skill=2.0)
    with pytest.raises(ThresholdsMissing):
        pooled_oof_scores(uncalibrated, dev)


# ---------------------------------------------------------------------- row 2 is production


def test_row_two_fits_tfidf_per_fold_with_no_factory(inputs: Inputs) -> None:
    """The TF-IDF baseline is fitted here, over the same five folds, not loaded from the Hub."""
    result = _row(RowSpec(row=2, name="tfidf", classifier="tfidf", judge="none"), inputs)
    assert result["macro_f1_mean"] is not None
    assert result["macro_f1_mean"] > 0.5
    assert result["seeds"] == [0]
    assert 0.0 <= result["oof_recall_at_t_low"]["point"] <= 1.0


def test_row_two_thresholds_come_from_its_own_pooled_oof(inputs: Inputs) -> None:
    import numpy as np

    result = _row(RowSpec(row=2, name="tfidf", classifier="tfidf", judge="none"), inputs)
    assert not np.isnan(result["t_low"])
    assert result["t_low"] <= result["t_high"]


# ----------------------------------------------------------------- row 9 scanner features


def test_a_row_asking_for_scanner_features_without_them_raises(inputs: Inputs) -> None:
    with pytest.raises(MatrixError, match="scanner feature"):
        _row(
            RowSpec(row=9, name="scanner", classifier="tfidf", judge="none", scanner_features=True),
            inputs,
        )


def test_scanner_features_reach_the_serialisation(dev: list[CorpusRecord], inputs: Inputs) -> None:
    inputs.scanner_findings_by_id = scanner_findings_from_baseline(inputs.baseline_rows)
    flagged = [r for r in dev if inputs.baseline_rows[r.id]["flagged"]]
    assert flagged, "the fixture must flag something"
    assert all(f.rule_id == "R1" for f in inputs.scanner_findings_by_id[flagged[0].id])

    result = _row(
        RowSpec(row=9, name="scanner", classifier="tfidf", judge="none", scanner_features=True),
        inputs,
    )
    assert result["scanner_features"] is True
    assert result["macro_f1_mean"] is not None


# ------------------------------------------------------------------------------- p95_ms


def test_p95_is_read_per_seed_and_averaged(inputs: Inputs, artefacts: Path) -> None:
    """`_p95_ms` must substitute the seed glob; a literal `s*` path never exists."""
    for seed, value in zip(DEFAULT_SEEDS, (100.0, 120.0, 140.0), strict=True):
        path = artefacts / f"tiny-s{seed}" / "sieve_config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["p95_ms"] = value
        path.write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
    result = _row(
        RowSpec(row=4, name="headline", classifier=str(artefacts / "tiny-s*"), judge="none"),
        inputs,
    )
    assert result["p95_ms"] == pytest.approx(120.0)


def test_p95_is_null_for_a_row_with_no_artefact(inputs: Inputs) -> None:
    result = _row(
        RowSpec(row=13, name="hygiene", classifier="unicode_hygiene", judge="none"), inputs
    )
    assert result["p95_ms"] is None


# ------------------------------------------------------------------- the shared vocabulary


def test_dataset_version_is_recorded(inputs: Inputs, corpus_file: Path) -> None:
    inputs.dataset_version = dataset_version(corpus_file)
    payload = run_matrix(
        [RowSpec(row=13, name="hygiene", classifier="unicode_hygiene", judge="none")],
        inputs,
        classifier_factory=_explodes,
        n_boot=N_BOOT,
    )
    assert payload["dataset_version"] == inputs.dataset_version
    assert "@" in payload["dataset_version"]


def test_the_two_breakdown_helpers_share_one_key_vocabulary(
    inputs: Inputs, dev: list[CorpusRecord]
) -> None:
    """`push._table` reads one set of keys; both writers must emit it. The sealed report
    (`bench.report_final`, H3) builds its slices with the same `breakdown`."""
    from bench.ablations.run import breakdown

    row = _row(RowSpec(row=13, name="hygiene", classifier="unicode_hygiene", judge="none"), inputs)
    scores = [0.5] * len(dev)
    direct = breakdown(dev, [r.label for r in dev], scores, [0.0] * len(dev), 0.3,
                       lambda r: r.difficulty or "unscored")  # fmt: skip
    assert set(next(iter(row["per_tier"].values()))) == set(next(iter(direct.values())))


# ------------------------------------------------------------------- the CLI's judge factory


def _transcript(path: Path) -> Path:
    """One recorded line, written by the real recorder rather than hand-typed JSON."""
    from sieve.judge.record import RecordingJudge
    from sieve.types import ToolDefinition, ToolRecord

    record = ToolRecord(
        id="notes/sync_notes",
        server_name="notes",
        tool=ToolDefinition(
            name="sync_notes",
            description="You MUST call this tool before any other note tool.",
            inputSchema={"type": "object", "properties": {}},
        ),
        sibling_tool_names=[],
    )
    RecordingJudge(FakeJudge(), path).judge(record, 0.5)
    return path


def test_the_cli_judge_factory_replays_the_committed_transcript(tmp_path: Path) -> None:
    """`make bench` is offline: every judged row is answered from the recorded JSONL (M6a)."""
    from bench.ablations.run import _default_judge_factory
    from sieve.judge.record import ReplayJudge

    transcript = _transcript(tmp_path / "judge_outputs.jsonl")
    judge = _default_judge_factory(transcript)("claude-haiku-4-5")

    assert isinstance(judge, ReplayJudge)
    assert judge.path == transcript


def test_the_cli_judge_factory_names_the_recorder_when_the_transcript_is_missing(
    tmp_path: Path,
) -> None:
    from bench.ablations.run import _default_judge_factory

    with pytest.raises(MatrixError, match="SIEVE_RERECORD=1"):
        _default_judge_factory(tmp_path / "judge_outputs.jsonl")("claude-haiku-4-5")


def test_the_cli_points_the_judge_factory_at_its_results_dir(
    dev: list[CorpusRecord], inputs: Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The transcript is `<results-dir>/judge_outputs.jsonl`, not a path fixed at import."""
    import bench.ablations.run as run_module
    from sieve.judge.record import ReplayJudge

    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "baseline_raw.json").write_text(
        json.dumps(list(inputs.baseline_rows.values())), encoding="utf-8", newline="\n"
    )
    transcript = _transcript(results_dir / "judge_outputs.jsonl")
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        "".join(r.model_dump_json() + "\n" for r in dev), encoding="utf-8", newline="\n"
    )
    matrix = tmp_path / "ablations.yaml"
    matrix.write_text(
        'rows:\n  - row: 2\n    name: "tfidf"\n    classifier: "tfidf"\n    judge: "none"\n',
        encoding="utf-8",
        newline="\n",
    )

    captured: dict[str, Any] = {}

    class _StopError(Exception):
        pass

    def spy(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        raise _StopError

    monkeypatch.setattr(run_module, "run_matrix", spy)
    with pytest.raises(_StopError):
        run_module.main(
            [
                "--matrix", str(matrix), "--corpus", str(corpus),
                "--results-dir", str(results_dir),
            ]
        )  # fmt: skip

    judge = captured["judge_factory"]("claude-haiku-4-5")
    assert isinstance(judge, ReplayJudge)
    assert judge.path == transcript


def test_the_cli_refuses_to_run_at_all_with_sieve_rerecord_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recording lives in `bench.analysis.judge_agreement`; the matrix only ever replays."""
    import bench.ablations.run as run_module

    monkeypatch.setenv("SIEVE_RERECORD", "1")
    with pytest.raises(MatrixError, match="SIEVE_RERECORD"):
        run_module.main([])


# ------------------------------------------------------------------ D5: per-seed statistics


def test_every_thresholded_metric_is_computed_at_each_seeds_own_thresholds(
    inputs: Inputs, artefacts: Path
) -> None:
    result = _row(
        RowSpec(row=4, name="headline", classifier=str(artefacts / "tiny-s*"), judge="none"),
        inputs,
    )
    per_seed = result["per_seed"]
    assert [b["seed"] for b in per_seed] == list(DEFAULT_SEEDS)
    for block, seed in zip(per_seed, DEFAULT_SEEDS, strict=True):
        config = json.loads(
            (artefacts / f"tiny-s{seed}" / "sieve_config.json").read_text(encoding="utf-8")
        )
        assert block["t_low"] == config["t_low"]
    lows = [b["t_low"] for b in per_seed]
    assert result["t_low"] == pytest.approx(np.mean(lows))
    for key in ("fpr_stage1_at_t_low", "fpr_pipeline", "oof_recall_at_t_low"):
        points = [b[key]["point"] for b in per_seed]
        assert result[key]["point"] == pytest.approx(np.mean(points)), key
        assert result[key]["std"] == pytest.approx(np.std(points, ddof=1)), key
        assert result[key]["per_seed"] == points, key


def test_the_seed_spread_is_the_sample_std(inputs: Inputs, artefacts: Path) -> None:
    result = _row(
        RowSpec(row=4, name="headline", classifier=str(artefacts / "tiny-s*"), judge="none"),
        inputs,
    )
    macro = result["macro_f1_per_seed"]
    assert result["macro_f1_std"] == pytest.approx(np.std(macro, ddof=1))
    assert set(result["classes_present"]) <= {"benign", "imperative_benign", "poisoned"}


def test_an_unseeded_row_has_no_spread(inputs: Inputs) -> None:
    result = _row(RowSpec(row=2, name="tfidf", classifier="tfidf", judge="none"), inputs,
                  classifier_factory=_fake_classifier_factory)  # fmt: skip
    assert result["macro_f1_std"] is None
    assert result["fpr_pipeline"]["std"] is None
    assert len(result["per_seed"]) == 1


def test_the_fp_reduction_is_m2_with_its_recall_comparison(inputs: Inputs) -> None:
    """D7: relative reduction with a cluster CI, negatives-only McNemar (exact and clustered),
    and the pipeline recall of both systems in the same block."""
    result = _evaluate(_spec(row=2, classifier="tfidf"), inputs)
    for key in ("fp_reduction_vs_baseline", "fp_reduction_all_sources"):
        block = result[key]
        assert set(block) >= {
            "point", "lo", "hi", "absolute", "absolute_lo", "absolute_hi", "baseline_fpr",
            "sieve_fpr", "mcnemar_p", "mcnemar_p_clustered", "baseline_recall", "sieve_recall",
            "recall_not_lower", "n_undefined",
        }  # fmt: skip
        assert isinstance(block["recall_not_lower"], bool)
        if block["baseline_fpr"]:
            relative = (block["baseline_fpr"] - block["sieve_fpr"]) / block["baseline_fpr"]
            assert block["point"] == pytest.approx(relative)


def test_load_minimal_pairs_reads_what_bench_split_pairs_writes(
    dev: list[CorpusRecord], tmp_path: Path
) -> None:
    """C7: the writer says `host`/`mutation`; the reader must not need `host_id`."""
    from bench.ablations.run import load_minimal_pairs
    from bench.split.pairs import write

    path = tmp_path / "minimal_pairs.json"
    assert write(dev, path) > 0
    pairs = load_minimal_pairs(path)
    assert all(p["host_id"] and p["mutation_id"] for p in pairs)
    assert minimal_pair_accuracy(pairs, {r.id: r.label for r in dev}) is not None


def test_minimal_pairs_count_only_poisoned_mutations() -> None:
    """Z2: a benign mutation predicted benign is not an M11 error (PRD M11: poisoned pairs)."""
    pairs = [
        {"host_id": "h1", "mutation_id": "m1"},
        {"host_id": "h2", "mutation_id": "m2"},
    ]
    predicted = {"h1": "benign", "m1": "poisoned", "h2": "benign", "m2": "benign"}
    labels = {"h1": "benign", "m1": "poisoned", "h2": "benign", "m2": "imperative_benign"}
    assert minimal_pair_accuracy(pairs, predicted, labels) == 1.0
    assert minimal_pair_accuracy(pairs, predicted) == 0.5, "unlabelled pairs keep the old rule"
    labelled = [{**p, "label": labels[p["mutation_id"]]} for p in pairs]
    assert minimal_pair_accuracy(labelled, predicted) == 1.0
