"""Agreement sampling and the three-way kappa report (PRD §9.6 items 3 and 5)."""

from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from bench.label import kappa, sample
from sieve.types import CorpusRecord, Label


def _record(
    record_id: str,
    label: Label,
    *,
    source: str = "public_repo",
    second: Label | None = None,
    server: str = "weather",
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
        tool={"name": "t", "inputSchema": {"type": "object"}},  # type: ignore[arg-type]
        label=label,
        labeller="aditya",
        labelled_at="2026-09-11T00:00:00Z",  # type: ignore[arg-type]
        second_label=second,
    )


def _agreeing(n: int = 9) -> list[CorpusRecord]:
    labels: list[Label] = ["benign", "imperative_benign", "poisoned"]
    return [_record(f"r{i}", labels[i % 3], second=labels[i % 3]) for i in range(n)]


# -- sampling ---------------------------------------------------------------


def test_simple_random_is_reproducible_and_returns_exactly_n() -> None:
    records = _agreeing(30)
    first = sample.simple_random(records, 10, seed=42)
    assert len(first) == 10
    assert first == sample.simple_random(records, 10, seed=42)
    assert first != sample.simple_random(records, 10, seed=43)
    assert set(first) <= {r.id for r in records}


def test_simple_random_caps_at_the_corpus_size() -> None:
    records = _agreeing(6)
    assert len(sample.simple_random(records, 50, seed=0)) == 6


def test_stratified_preserves_class_proportions_within_one_record() -> None:
    records = [_record(f"b{i}", "benign") for i in range(60)]
    records += [_record(f"i{i}", "imperative_benign") for i in range(30)]
    records += [_record(f"p{i}", "poisoned") for i in range(10)]
    picked = sample.stratified(records, 20, seed=7)
    assert len(picked) == 20
    by_id = {r.id: r for r in records}
    counts = collections.Counter(by_id[i].label for i in picked)
    for label, total in (("benign", 60), ("imperative_benign", 30), ("poisoned", 10)):
        assert abs(counts[label] - 20 * total / 100) <= 1


def test_retest_excludes_the_inter_rater_subset() -> None:
    records = _agreeing(40)
    inter = set(sample.simple_random(records, 10, seed=1))
    picked = sample.retest(records, 0.15, seed=2, exclude_ids=inter)
    assert len(picked) == 4  # 15% of the 30 remaining
    assert not set(picked) & inter


# -- kappa ------------------------------------------------------------------


def test_perfect_agreement_gives_kappa_and_ac1_of_one() -> None:
    report = kappa.report(_agreeing(), which="inter")
    assert report.n == 9
    assert report.kappa_overall == pytest.approx(1.0)
    assert report.kappa_real_subset == pytest.approx(1.0)
    assert report.disagreements == []
    for entry in report.per_class.values():
        assert entry.observed_agreement == pytest.approx(1.0)
        assert entry.ac1 == pytest.approx(1.0)


def test_per_class_carries_all_three_labels_even_with_no_disagreement() -> None:
    records = [_record("a", "benign", second="benign")]
    report = kappa.report(records, which="inter")
    assert set(report.per_class) == {"benign", "imperative_benign", "poisoned"}


def test_disagreements_name_the_ids() -> None:
    records = _agreeing(6)
    records.append(_record("bad", "poisoned", second="imperative_benign"))
    report = kappa.report(records, which="inter")
    assert ("bad", "poisoned", "imperative_benign") in report.disagreements
    assert report.kappa_overall < 1.0


def test_real_subset_ignores_synthetic_and_real_poisoned_rows() -> None:
    records = _agreeing(6)
    records.append(
        _record("syn", "poisoned", source="synthetic_mutation", second="imperative_benign")
    )
    records.append(_record("rp", "poisoned", source="real_poisoned", second="benign"))
    report = kappa.report(records, which="inter")
    assert report.n == 8
    assert report.n_real_subset == 6
    assert report.kappa_real_subset == pytest.approx(1.0)
    assert report.kappa_overall < 1.0


def test_rows_without_a_second_label_are_not_scored() -> None:
    records = _agreeing(6)
    records.append(_record("unrated", "poisoned"))
    assert kappa.report(records, which="inter").n == 6


def test_sampler_is_recorded_on_the_report() -> None:
    assert kappa.report(_agreeing(), which="inter").sampler == "simple_random"
    assert kappa.report(_agreeing(), which="inter", sampler="stratified").sampler == "stratified"


# -- blind vs generator -----------------------------------------------------


def _sidecar(path: Path, entries: list[tuple[str, Label]]) -> Path:
    path.write_text(
        "".join(
            json.dumps(
                {
                    "id": rid,
                    "intended_label": label,
                    "intended_sub_labels": [],
                    "intended_difficulty": None,
                    "generator_model": "human",
                }
            )
            + "\n"
            for rid, label in entries
        ),
        encoding="utf-8",
        newline="\n",
    )
    return path


def test_generator_mode_compares_blind_labels_against_the_sidecar(tmp_path: Path) -> None:
    records = [
        _record("s0", "poisoned", source="synthetic_mutation"),
        _record("s1", "benign", source="synthetic_mutation"),
    ]
    intent = _sidecar(tmp_path / "i.jsonl", [("s0", "poisoned"), ("s1", "poisoned")])
    report = kappa.report(records, which="generator", intent_path=intent)
    assert report.which == "generator"
    assert report.n == 2
    assert report.blind_vs_generator == pytest.approx(0.5)
    assert ("s1", "poisoned", "benign") in report.disagreements


def test_a_row_missing_from_the_sidecar_is_skipped_not_counted(tmp_path: Path) -> None:
    records = [
        _record("s0", "poisoned", source="synthetic_mutation"),
        _record("s1", "benign", source="synthetic_mutation"),
    ]
    intent = _sidecar(tmp_path / "i.jsonl", [("s0", "poisoned")])
    report = kappa.report(records, which="generator", intent_path=intent)
    assert report.n == 1
    assert report.disagreements == []
    assert report.blind_vs_generator == pytest.approx(1.0)


def test_blind_vs_generator_is_none_for_the_inter_rater_report() -> None:
    assert kappa.report(_agreeing(), which="inter").blind_vs_generator is None


# -- CLI --------------------------------------------------------------------


def test_cli_merges_the_report_under_its_key(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        "".join(r.model_dump_json() + "\n" for r in _agreeing()),
        encoding="utf-8",
        newline="\n",
    )
    out = tmp_path / "agreement.json"
    out.write_text(json.dumps({"retest": {"kept": True}}), encoding="utf-8", newline="\n")
    assert kappa.main(["--which", "inter", "--corpus", str(corpus), "--out", str(out)]) == 0
    merged = json.loads(out.read_text(encoding="utf-8"))
    assert merged["retest"] == {"kept": True}
    assert merged["inter"]["kappa_overall"] == pytest.approx(1.0)
