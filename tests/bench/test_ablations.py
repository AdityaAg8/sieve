from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bench.ablations.run import (
    DEFAULT_SEEDS,
    Inputs,
    InsufficientSeeds,
    MatrixError,
    RowSpec,
    evaluate_row,
    load_baseline_rows,
    load_matrix,
    load_minimal_pairs,
    minimal_pair_accuracy,
    run_matrix,
    write_results,
)
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
    """mini-50 with folds re-assigned by server. Local, because tests/bench/conftest.py is L1's.

    Round-robin over the sorted server names keeps a server whole inside one fold and leaves
    every fold populated, which the sha256 helper in tests/model/conftest.py does not.
    """
    records = load_corpus(Path("fixtures/corpus/mini-50.jsonl"))
    servers = sorted({r.server_name for r in records})
    fold_of = {name: i % 5 for i, name in enumerate(servers)}
    return [r.model_copy(update={"split": "dev", "fold": fold_of[r.server_name]}) for r in records]


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
    specs = load_matrix(Path("bench/ablations.yaml"))
    assert [s.row for s in specs] == list(range(1, 15))
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


@pytest.mark.parametrize(
    "spec",
    [
        _spec(row=2, classifier="tfidf", minimal_pairs=True),
        _spec(row=4, classifier="models/x-s*", minimal_pairs=True),
        _spec(row=12, classifier="models/x-2c-s*", num_labels=2, minimal_pairs=True),
        _spec(row=13, classifier="unicode_hygiene"),
    ],
    ids=["row2-tfidf", "row4-encoder", "row12-two-class", "row13-hygiene"],
)
def test_every_required_key_is_present(spec: RowSpec, inputs: Inputs) -> None:
    result = _evaluate(spec, inputs)
    assert set(result) >= REQUIRED_KEYS
    for key in ("fpr_stage1_at_t_low", "fpr_pipeline", "oof_recall_at_t_low"):
        assert set(result[key]) == {"point", "lo", "hi"}
        assert result[key]["lo"] <= result[key]["point"] <= result[key]["hi"]
    assert set(result["fp_reduction_vs_baseline"]) >= {"point", "lo", "hi", "mcnemar_p"}


def test_the_judge_can_only_remove_confirmations(inputs: Inputs) -> None:
    """Row 6: a row the judge dismisses leaves the confirmed set, so pipeline FPR cannot rise."""
    row6 = _evaluate(_spec(row=6, judge="claude-haiku-4-5"), inputs)
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


def test_row_14_reports_a_fraction(inputs: Inputs) -> None:
    result = _evaluate(_spec(row=14, minimal_pairs=True), inputs)
    accuracy = result["minimal_pair_accuracy"]
    assert accuracy is not None
    assert 0.0 <= accuracy <= 1.0


def test_minimal_pair_accuracy_needs_both_sides_right() -> None:
    pairs = [{"host_id": "h1", "mutation_id": "m1"}, {"host_id": "h2", "mutation_id": "m2"}]
    predicted = {"h1": "benign", "m1": "poisoned", "h2": "poisoned", "m2": "poisoned"}
    assert minimal_pair_accuracy(pairs, predicted) == 0.5
    assert minimal_pair_accuracy(pairs, {}) is None


def test_a_row_without_minimal_pairs_reports_null(inputs: Inputs) -> None:
    assert _evaluate(_spec(row=3, mode="description_only"), inputs)["minimal_pair_accuracy"] is None


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
        _evaluate(_spec(row=14, minimal_pairs=True), inputs)


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

    with pytest.raises(MatrixError, match="network connection"):
        run_matrix(
            [_spec(row=2, classifier="tfidf")],
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


def test_the_fixture_corpus_is_all_dev() -> None:
    records = load_corpus(Path("fixtures/corpus/mini-50.jsonl"))
    assert {r.split for r in records} == {"dev"}


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
    assert json.loads(committed.read_text(encoding="utf-8")) == json.loads(
        fresh.read_text(encoding="utf-8")
    )
    assert json.loads(committed.read_text(encoding="utf-8"))["meta"]["provisional"] is True
