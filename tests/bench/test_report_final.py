"""`bench/report_final.py` (training plan H3): saved predictions in, `holdout.json` out."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from bench import report_final as rf
from tests.bench.report_fixture import fixture_world, render

SCHEMA = json.loads(Path("tests/bench/data/holdout.schema.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def report() -> dict[str, Any]:
    return render(*fixture_world())


def test_the_fixture_report_matches_the_consumer_golden(report: dict[str, Any]) -> None:
    jsonschema.validate(report, SCHEMA)
    json.dumps(report, allow_nan=False)  # no NaN anywhere: every undefined value is null


def test_every_seed_is_reported_with_mean_and_std(report: dict[str, Any]) -> None:
    (family,) = report["per_family"].values()
    gate = family["holdout_synthetic.recall_at_t_low.point"]
    assert len(gate["values"]) == 2 and gate["std"] is not None
    assert report["models_by_family"] == {"deberta": ["deberta@s0", "deberta@s1"]}


def test_the_headline_keys_come_from_the_headline_model(report: dict[str, Any]) -> None:
    head = report["per_model"][report["headline_model"]]
    assert report["recall_interval"] == head["holdout_synthetic"]["recall_at_t_low"]
    assert report["n_eval_only"] == head["real_poisoned"]["n"] == 6


def test_mcptox_uses_template_by_server_clusters(report: dict[str, Any]) -> None:
    rp = report["per_model"]["deberta@s0"]["real_poisoned"]
    assert set(rp["per_template"]) == {"Template-1", "Template-2", "Template-3"}


def test_the_release_gate_is_the_prd_one(report: dict[str, Any]) -> None:
    assert report["release_gate"] == {"recall_point": 0.95, "recall_lower_bound": 0.90}
    gate = report["recall_interval"]
    expected = gate["point"] >= 0.95 and gate["lo"] >= 0.90
    assert report["release_gate_passed"] is expected


def test_several_models_need_a_named_headline() -> None:
    predictions = [
        {"model": m, "set": "test_id", "id": "a", "p": 0.1, "probs": [0.8, 0.1, 0.1],
         "verdict": "dismissed", "t_low": 0.3, "t_high": 0.8}
        for m in ("x@0", "y@0")
    ]  # fmt: skip
    rows = [{"id": "a", "set": "test_id", "cluster": "s", "poisoned": False, "label": "benign",
             "source": "public_repo"}]  # fmt: skip
    with pytest.raises(ValueError, match="headline"):
        rf.build_report(predictions, rows, preregistration_sha256="0" * 64)


def test_the_slices_use_the_ablation_vocabulary(report: dict[str, Any]) -> None:
    from bench.ablations.run import BREAKDOWN_KEYS

    for bucket in report["per_tier"].values():
        assert set(bucket) == set(BREAKDOWN_KEYS)


def test_main_refuses_without_a_matching_preregistration(tmp_path: Path) -> None:
    prereg = tmp_path / "PREREGISTRATION.md"
    prereg.write_text("# plan\n", encoding="utf-8")
    sha = tmp_path / "PREREGISTRATION.sha256"
    args = ["--results-dir", str(tmp_path), "--preregistration", str(prereg),
            "--preregistration-hash", str(sha)]  # fmt: skip
    with pytest.raises(rf.PreregistrationError, match="committed"):
        rf.main(args)
    sha.write_text("f" * 64 + "\n", encoding="utf-8")
    with pytest.raises(rf.PreregistrationError, match="hashes to"):
        rf.main(args)


def test_main_renders_saved_predictions_and_records_the_hash(tmp_path: Path) -> None:
    from bench import eval_final as ef
    from tests.bench.report_fixture import SeededFake, sealed_rows

    rows = sealed_rows(*fixture_world())
    model = ef.LoadedModel(Path("m"), SeededFake(0), "deberta@s0", 0, "deberta", -1, "full")
    ef.write_jsonl_atomic(tmp_path / rf.PREDICTIONS_FILE, ef.score([model], rows))
    ef.write_jsonl_atomic(tmp_path / rf.ROWS_FILE, ef.row_entries(rows, None))
    (tmp_path / rf.LOG_FILE).write_text(
        json.dumps({"reason": "v1 release", "dataset_version": "l@1", "models": ["deberta@s0"]})
        + "\n",
        encoding="utf-8",
    )
    prereg = tmp_path / "PREREGISTRATION.md"
    prereg.write_text("# plan\r\n", encoding="utf-8", newline="")
    (tmp_path / "PREREGISTRATION.sha256").write_text(
        rf.preregistration_hash(prereg) + "\n", encoding="utf-8"
    )
    sha = tmp_path / "PREREGISTRATION.sha256"
    path = rf.main(["--results-dir", str(tmp_path), "--n-boot", "30", "--preregistration",
                    str(prereg), "--preregistration-hash", str(sha)])  # fmt: skip
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["preregistration_sha256"] == rf.preregistration_hash(prereg)
    assert report["reason"] == "v1 release" and report["dataset_version"] == "l@1"
    jsonschema.validate(report, SCHEMA)


def test_the_committed_preregistration_matches_its_committed_hash() -> None:
    """H7: committed before the GPU phase; report_final refuses on any later edit."""
    assert rf.check_preregistration() == rf.preregistration_hash()
    text = rf.PREREGISTRATION.read_text(encoding="utf-8")
    for clause in ("row 4 vs row 1", "Holm", "≥ 0.95", "≥ 0.90", "RULE",
                   "median-seed `FOLD=all` model", "template|server", "Probe gate",
                   "dev-50 gate", "Test-ID labels are report-only"):  # fmt: skip
        assert clause in text, clause


def test_report_final_refuses_an_edited_preregistration(tmp_path: Path) -> None:
    edited = tmp_path / "PREREGISTRATION.md"
    edited.write_text(rf.PREREGISTRATION.read_text(encoding="utf-8") + "\nmoved the gate\n",
                      encoding="utf-8")  # fmt: skip
    with pytest.raises(rf.PreregistrationError, match="hashes to"):
        rf.main(["--results-dir", str(tmp_path), "--preregistration", str(edited)])
