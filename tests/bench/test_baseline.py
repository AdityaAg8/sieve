from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from bench.baseline import report
from bench.baseline.run import BaselineResult, evaluate, main
from sieve.eval.metrics import bootstrap_ci
from sieve.serialise import serialise
from sieve.types import CorpusRecord, ScannerFinding, ToolRecord

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
MINI_50 = FIXTURES / "corpus" / "mini-50.jsonl"


class FakeAdapter:
    """Flags every record whose serialised text contains the word "MUST" (brief, Task 2.5)."""

    name = "static_json"
    version = "fake@0"

    def scan(self, records: list[ToolRecord]) -> list[ScannerFinding]:
        out: list[ScannerFinding] = []
        for r in records:
            if "MUST" in serialise(r):
                out.append(
                    ScannerFinding(
                        scanner="static_json",
                        scanner_version=self.version,
                        rule_id="contains_must",
                        severity="low",
                        message=f"Imperative language found in {r.tool.name}.",
                        raw={"tool_name": r.tool.name},
                    )
                )
        return out

    def normalise(self, raw: dict[str, Any]) -> list[ScannerFinding]:
        return []


def _load_mini_50() -> list[CorpusRecord]:
    records = []
    with MINI_50.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                records.append(CorpusRecord(**json.loads(line)))
    return records


@pytest.fixture(scope="module")
def result() -> BaselineResult:
    return evaluate(_load_mini_50(), FakeAdapter())


def test_counts_and_metrics_match_hand_computation(result: BaselineResult) -> None:
    assert result.n == 50
    assert (result.tp, result.fp, result.fn, result.tn) == (0, 10, 17, 23)
    assert result.precision == pytest.approx(0.0)
    assert result.recall == pytest.approx(0.0)
    assert result.fpr == pytest.approx(10 / 33)
    assert result.f1 == pytest.approx(0.0)
    assert result.scanner == "static_json"
    assert result.version == "fake@0"


def test_confusion_matrix_cells(result: BaselineResult) -> None:
    # rows = LABELS order (benign, imperative_benign, poisoned); cols = [not_flagged, flagged]
    assert result.confusion == [[13, 0], [10, 10], [17, 0]]


def test_markdown_calls_out_imperative_benign_flagged_line(
    result: BaselineResult, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report, "RESULTS_DIR", tmp_path / "bench" / "results")
    report.write([result])
    md = (tmp_path / "bench" / "results" / "baseline.md").read_text(encoding="utf-8")
    assert "imperative_benign" in md
    assert "flagged" in md
    assert "10" in md


def test_field_misses_exact_and_approx_are_separate(result: BaselineResult) -> None:
    assert result.field_misses == {"description": 16, "inputSchema": 1}
    assert result.field_misses_approx == {}


def test_per_source_and_per_concealment_have_one_entry_per_observed_value(
    result: BaselineResult,
) -> None:
    assert set(result.per_source) == {"public_repo", "synthetic_mutation"}
    # mini-023 is labelled `html_comment` (contracts C2.4): its payload hides in an HTML
    # comment in the description, so it is no longer the only concealment bucket in mini-50.
    assert set(result.per_concealment) == {"none", "html_comment"}
    for bucket in result.per_source.values():
        assert {"precision", "recall", "fpr", "n"} <= set(bucket)


def test_fp_causes_bucket_by_rule_id_with_at_most_three_examples(result: BaselineResult) -> None:
    assert set(result.fp_causes) == {"contains_must"}
    cause = result.fp_causes["contains_must"]
    assert cause.count == 10
    assert len(cause.examples) <= 3


def test_cluster_ci_differs_from_row_level_bootstrap(result: BaselineResult) -> None:
    records = _load_mini_50()
    y_true = [r.label for r in records]
    y_score = [1.0 if "MUST" in serialise(r.to_tool_record()) else 0.0 for r in records]

    def fpr_stat(yt: Any, ys: Any) -> float:
        from sieve.eval.metrics import precision_recall_fpr

        return precision_recall_fpr(list(yt), list(ys), 0.5)[2]

    _, row_lo, row_hi = bootstrap_ci(fpr_stat, y_true, y_score, seed=0)
    cluster_lo, cluster_hi = result.ci["fpr"]
    assert (cluster_lo, cluster_hi) != (row_lo, row_hi)


def test_baseline_raw_json_has_one_entry_per_record_and_round_trips(
    result: BaselineResult, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report, "RESULTS_DIR", tmp_path / "bench" / "results")
    report.write([result])
    raw_path = tmp_path / "bench" / "results" / "baseline_raw.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))

    assert len(raw) == 50
    ids = {row["id"] for row in raw}
    assert len(ids) == 50
    for row in raw:
        assert set(row) >= {"id", "server_name", "label", "source", "flagged", "rule_ids"}
        assert isinstance(row["flagged"], bool)
        assert isinstance(row["rule_ids"], list)


def test_cli_fails_clearly_when_corpus_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.jsonl"
    with pytest.raises(SystemExit) as exc_info:
        main(["--corpus", str(missing)])
    assert "not found" in str(exc_info.value).lower()


def test_cli_refuses_holdout_split_without_sieve_final(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIEVE_FINAL", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        main(["--corpus", str(MINI_50), "--split", "holdout"])
    assert "SIEVE_FINAL" in str(exc_info.value)


def test_cli_rejects_an_unresolvable_scanner_name(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--corpus", str(MINI_50), "--scanners", "static_json_fake", "--split", "all"])


@pytest.mark.skipif(shutil.which("mcp-scanner") is None, reason="mcp-scanner not on PATH")
def test_cli_runs_end_to_end_on_dev_split_with_real_cisco_yara(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results_dir = tmp_path / "bench" / "results"
    monkeypatch.setattr(report, "RESULTS_DIR", results_dir)
    exit_code = main(["--corpus", str(MINI_50), "--scanners", "cisco_yara", "--split", "dev"])
    baseline = json.loads((results_dir / "baseline.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert len(baseline) == 1
    assert baseline[0]["scanner"] == "cisco_yara"
    assert baseline[0]["n"] == 50
