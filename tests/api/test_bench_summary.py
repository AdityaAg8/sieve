"""`GET /bench/summary` over the real results files (training plan I5, finding F73).

The files come from their real producers (`tests/api/bench_fixture.py`): the ablation matrix
(H5), the sealed report (H3), the label audit (F3) and the opening log. The route serves them
under the producers' own keys; the web reads exactly those keys.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from sieve.api.routes import bench
from tests.api import bench_fixture

DEFERRED_ROWS = {6, 7, 8}
METRIC_KEYS = {"oof_recall_at_t_low", "fpr_pipeline", "macro_f1_mean", "fpr_stage1_at_t_low"}


@pytest.fixture(scope="module")
def results() -> Iterator[Path]:
    with bench_fixture.results_dir(n_boot=20) as directory:
        yield directory


@pytest.fixture
def tables(results: Path) -> dict[str, Any]:
    return bench.summary(results).tables


def test_measured_results_clear_the_provisional_notice(results: Path) -> None:
    summary = bench.summary(results)
    assert summary.provisional is False
    assert summary.model_version == "deberta@s0"
    assert summary.dataset_version == bench_fixture.DATASET_VERSION


def test_a_provisional_demo_does_not_hide_measured_results(results: Path, tmp_path: Path) -> None:
    """The banner is for missing files; a demo scan flagged provisional is not one."""
    shutil.copytree(results, tmp_path / "results")
    shutil.copy(Path("bench/results/demo.json"), tmp_path / "results" / "demo.json")
    summary = bench.summary(tmp_path / "results")
    assert summary.provisional is False
    assert summary.headline["tool_count"] == 27


def test_the_ablation_rows_are_served_under_the_producer_keys(tables: dict[str, Any]) -> None:
    rows = {r["row"]: r for r in tables["ablations"]}
    assert set(rows) == set(range(1, 16))
    primary = rows[4]
    assert primary["status"] == "active"
    assert primary["single_seed"] is False and len(primary["seeds"]) == 3
    assert {"point", "lo", "hi", "std", "per_seed"} <= set(primary["oof_recall_at_t_low"])
    assert primary["macro_f1_mean"] is not None
    assert rows[15]["single_seed"] is True
    assert rows[10]["deferred_breakdown_of"] == [6]
    # `per_seed` repeats every aggregate per seed; the page never reads it.
    assert all("per_seed" not in r for r in rows.values())


def test_a_deferred_judge_row_carries_its_reason_and_no_number(tables: dict[str, Any]) -> None:
    for row in tables["ablations"]:
        if row["row"] in DEFERRED_ROWS:
            assert row["status"] == "deferred"
            assert "D8" in row["reason"]
            assert not METRIC_KEYS & set(row), row


def test_the_sealed_report_travels_without_its_bulk(tables: dict[str, Any]) -> None:
    report = tables["holdout"]
    assert {"per_model", "per_family", "models_by_family"}.isdisjoint(report)
    assert {"point", "lo", "hi"} <= set(report["recall_interval"])
    assert {"point", "lo", "hi"} <= set(report["fpr_pipeline_real"])
    assert report["judge"] == {"status": "deferred", "decision": "D8"}
    own = report["headline_report"]
    assert {"point", "lo", "hi"} <= set(own["real_poisoned"]["recall_stage1"])
    assert "fp_reduction_triage" in own["holdout"]


def test_the_label_audit_and_the_opening_log_are_served(tables: dict[str, Any]) -> None:
    audit = tables["label_audit"]["test_id"]
    assert audit["n"] == bench_fixture.AUDIT_ROWS
    assert {"kappa", "kappa_lo", "kappa_hi", "error_rate", "cp_lo", "cp_hi"} <= set(audit)
    (entry,) = tables["test_opened"]
    assert {"git_sha", "utc", "rows_evaluated", "reason"} <= set(entry)


def test_a_missing_file_leaves_its_key_out(results: Path, tmp_path: Path) -> None:
    shutil.copytree(results, tmp_path / "results")
    (tmp_path / "results" / "holdout.json").unlink()
    (tmp_path / "results" / "label_audit.json").unlink()
    summary = bench.summary(tmp_path / "results")
    assert "holdout" not in summary.tables and "label_audit" not in summary.tables
    assert summary.provisional is False  # the ablation matrix is still a measured result


def test_no_results_at_all_is_provisional(tmp_path: Path) -> None:
    shutil.copy(Path("bench/results/demo.json"), tmp_path / "demo.json")
    summary = bench.summary(tmp_path)
    assert summary.provisional is True
    assert "ablations" not in summary.tables and "holdout" not in summary.tables


def _keys(value: Any, prefix: str = "") -> set[str]:
    if isinstance(value, dict):
        out = set()
        for k, v in value.items():
            out |= {f"{prefix}{k}"} | _keys(v, f"{prefix}{k}.")
        return out
    if isinstance(value, list):
        return set().union(*(_keys(v, f"{prefix}[].") for v in value)) if value else set()
    return set()


def test_the_web_fixture_has_the_shape_the_producers_write(results: Path) -> None:
    """`web/src/mocks/fixtures/bench-summary.json` is regenerated, never edited: if a producer
    adds, drops or renames a key, this fails until `python -m tests.api.bench_fixture` runs."""
    committed = json.loads(bench_fixture.WEB_FIXTURE.read_text(encoding="utf-8"))
    fresh = bench_fixture.summary_body(results)
    assert _keys(committed) == _keys(fresh)
