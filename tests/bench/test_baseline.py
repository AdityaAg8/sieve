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


def _minimal_corpus_row(record_id: str, split: str) -> dict[str, Any]:
    """A minimal, schema-valid `CorpusRecord` dict, for split-filtering tests only."""
    return {
        "id": record_id,
        "source": "public_repo",
        "source_repo": "https://github.com/example/fixture",
        "source_commit": "fixture",
        "source_license": "MIT",
        "capture_method": "authored",
        "schema_fidelity": "exact",
        "server_name": f"server-{record_id}",
        "tool": {
            "name": f"tool_{record_id.replace('-', '_')}",
            "description": "A test tool with no imperative language.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        "label": "benign",
        "labeller": "fixture",
        "labelled_at": "2026-09-11T00:00:00Z",
        "split": split,
    }


@pytest.mark.parametrize("sealed_split", ["holdout", "all", "eval_only"])
def test_cli_rejects_every_split_except_dev(sealed_split: str) -> None:
    """PRD §9.7/§11.6: this harness is not bench/eval_final.py and must never open sealed rows."""
    with pytest.raises(SystemExit):
        main(["--corpus", str(MINI_50), "--split", sealed_split])


def test_cli_dev_run_never_reads_sealed_split_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bench.baseline.run as run_module

    corpus_path = tmp_path / "mixed.jsonl"
    rows = [
        _minimal_corpus_row("dev-1", "dev"),
        _minimal_corpus_row("dev-2", "dev"),
        _minimal_corpus_row("sealed-holdout", "holdout"),
        _minimal_corpus_row("sealed-eval-only", "eval_only"),
    ]
    corpus_path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8", newline="\n"
    )
    results_dir = tmp_path / "bench" / "results"
    monkeypatch.setattr(report, "RESULTS_DIR", results_dir)
    # A fake adapter stands in so this test does not depend on mcp-scanner being on PATH; the
    # point under test is which RECORDS reach evaluate(), not what the scanner does with them.
    monkeypatch.setattr(run_module, "get_adapter", lambda name: FakeAdapter())

    exit_code = main(["--corpus", str(corpus_path), "--scanners", "cisco_yara"])
    baseline = json.loads((results_dir / "baseline.json").read_text(encoding="utf-8"))
    raw = json.loads((results_dir / "baseline_raw.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert baseline[0]["n"] == 2
    assert {row["id"] for row in raw} == {"dev-1", "dev-2"}


def test_cli_rejects_an_unresolvable_scanner_name(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--corpus", str(MINI_50), "--scanners", "static_json_fake"])


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


def test_records_sharing_a_tool_name_are_joined_by_record_not_by_name() -> None:
    """C5 (F58): a mutation keeps its host's tool name; only the flagged one may be flagged."""
    records = _load_mini_50()
    host = next(r for r in records if "MUST" not in serialise(r.to_tool_record()))
    twin = host.model_copy(
        update={
            "id": host.id + "-twin",
            "tool": host.tool.model_copy(update={"description": "You MUST do this."}),
        }
    )
    assert twin.tool.name == host.tool.name
    result = evaluate([host, twin], FakeAdapter())
    flagged = {row["id"]: row["flagged"] for row in result.raw_rows}
    assert flagged == {host.id: False, twin.id: True}
    rule_ids = {row["id"]: row["rule_ids"] for row in result.raw_rows}
    assert rule_ids[twin.id] == ["contains_must"]
    assert rule_ids[host.id] == []


def test_the_scanner_sees_each_records_own_name_text() -> None:
    seen: list[str] = []

    class Spy(FakeAdapter):
        def scan(self, records: list[ToolRecord]) -> list[ScannerFinding]:
            seen.extend(r.tool.name for r in records)
            return super().scan(records)

    records = _load_mini_50()[:3]
    evaluate(records, Spy())
    assert len(set(seen)) == 3
    for name, record in zip(seen, records, strict=True):
        assert name.startswith(record.tool.name)
