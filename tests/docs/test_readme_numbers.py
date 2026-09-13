"""Task 10.1 - `docs/scripts/readme_numbers.py`, the only writer of the README's number block.

Every fixture here is produced by the **real** writers (`bench.ablations.run`,
`bench.baseline.report`, `bench.eval_final`, `sieve.model.thresholds`, `sieve.model.select`),
never by hand-typed JSON: the generator's whole job is to read the shapes those writers emit,
so a test that invents the shape tests nothing. No training has happened on this machine, so
the files live and die inside `tmp_path` and `bench/results/` is never written to.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "docs" / "scripts" / "readme_numbers.py"
README = REPO / "README.md"
MINI_50 = REPO / "fixtures" / "corpus" / "mini-50.jsonl"

N_BOOT = 30
DEFAULT_JUDGE = "claude-haiku-4-5"
PERCENT = re.compile(r"\d+(?:\.\d+)?\s?%")
#: A line outside the generated block may only carry a percentage when it says whose it is.
PERCENT_IS_ALLOWED = ("target", "goal", "prd", "not measured", "budget", "pending")


def _load_script() -> ModuleType:
    """Import the generator by path: `docs/` is a documentation tree, not a package."""
    spec = importlib.util.spec_from_file_location("readme_numbers", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rn = _load_script()


@contextmanager
def _sieve_final() -> Iterator[None]:
    """`SIEVE_FINAL=1` for exactly as long as the holdout writer runs, restored afterwards."""
    previous = os.environ.get("SIEVE_FINAL")
    os.environ["SIEVE_FINAL"] = "1"
    try:
        yield
    finally:
        if previous is None:
            del os.environ["SIEVE_FINAL"]
        else:
            os.environ["SIEVE_FINAL"] = previous


def _dev_records() -> list[Any]:
    from bench.split.split import DEFAULT_SEED, assign
    from sieve.model.data import load_corpus

    return [r for r in assign(load_corpus(MINI_50), seed=DEFAULT_SEED) if r.split == "dev"]


def _baseline_rows(records: list[Any]) -> dict[str, dict[str, Any]]:
    """Flag every poisoned row plus four benign ones, so the baseline's FPR is not zero."""
    rows: dict[str, dict[str, Any]] = {}
    benign_flagged = 0
    for record in records:
        flagged = record.label == "poisoned"
        if not flagged and benign_flagged < 4:
            flagged = True
            benign_flagged += 1
        rows[record.id] = {
            "id": record.id,
            "server_name": record.server_name,
            "label": record.label,
            "source": record.source,
            "flagged": flagged,
            "rule_ids": ["R1"] if flagged else [],
        }
    return rows


def _write_ablations(directory: Path) -> None:
    from bench.ablations.run import DEFAULT_SEEDS, Inputs, RowSpec, run_matrix, write_results
    from sieve.protocols import Classifier, FakeClassifier, FakeJudge, Judge

    def classifier_factory(spec: RowSpec, seed: int) -> Classifier:
        return FakeClassifier()

    def judge_factory(model: str) -> Judge:
        return FakeJudge()

    records = _dev_records()
    inputs = Inputs(
        records=records,
        baseline_rows=_baseline_rows(records),
        dataset_version="sievebench-0.0.0-mini50",
    )
    specs = [
        RowSpec.model_validate(
            {
                "row": 4,
                "name": "Primary encoder, full serialisation (pre-registered headline)",
                "classifier": "models/deberta-v3-base-s*",
                "judge": "none",
                "mode": "full",
                "reinference": True,
            }
        ),
        RowSpec.model_validate(
            {
                "row": 6,
                "name": "Classifier + judge, Haiku 4.5",
                "classifier": "models/deberta-v3-base-s*",
                "judge": DEFAULT_JUDGE,
                "mode": "full",
                "reinference": True,
            }
        ),
    ]
    payload = run_matrix(
        specs,
        inputs,
        classifier_factory=classifier_factory,
        judge_factory=judge_factory,
        seeds=DEFAULT_SEEDS,
        n_boot=N_BOOT,
    )
    write_results(payload, directory / "ablations.json")


def _write_holdout(directory: Path, tmp_path: Path) -> None:
    from bench.eval_final import main as eval_final_main
    from bench.split.split import DEFAULT_SEED, assign
    from sieve.model.data import load_corpus
    from sieve.protocols import FakeClassifier, FakeJudge

    records = load_corpus(MINI_50)
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
    assigned = assign([*records, *planted], seed=DEFAULT_SEED)
    corpus = tmp_path / "labelled.jsonl"
    corpus.write_text(
        "".join(r.model_dump_json() + "\n" for r in assigned),
        encoding="utf-8",
        newline="\n",
    )
    with _sieve_final():
        eval_final_main(
            results_dir=directory,
            reason="fixture run for tests/docs/test_readme_numbers.py",
            corpus=corpus,
            classifier=FakeClassifier(),
            judge=FakeJudge(),
            n_boot=N_BOOT,
        )


def _write_baseline(directory: Path) -> None:
    from bench.baseline import report
    from bench.baseline.run import evaluate
    from sieve.model.data import load_corpus
    from sieve.serialise import serialise
    from sieve.types import ScannerFinding, ToolRecord

    class FakeAdapter:
        name = "cisco_yara"
        version = "4.8.0"

        def scan(self, records: list[ToolRecord]) -> list[ScannerFinding]:
            return [
                ScannerFinding(
                    scanner="cisco_yara",
                    scanner_version=self.version,
                    rule_id="contains_must",
                    severity="low",
                    message=f"Imperative language found in {r.tool.name}.",
                    raw={"tool_name": r.tool.name},
                )
                for r in records
                if "MUST" in serialise(r)
            ]

        def normalise(self, raw: dict[str, Any]) -> list[ScannerFinding]:
            return []

    previous = report.RESULTS_DIR
    report.RESULTS_DIR = directory
    try:
        report.write([evaluate(load_corpus(MINI_50), FakeAdapter())])
    finally:
        report.RESULTS_DIR = previous


def _write_curves_and_selection(directory: Path) -> None:
    import numpy as np

    from sieve.model.select import RULE, Architecture, Selection, write_selection
    from sieve.model.thresholds import write_curves
    from sieve.protocols import FakeClassifier
    from sieve.serialise import serialise
    from sieve.types import LABELS

    records = _dev_records()
    probs = np.asarray(
        FakeClassifier().predict_proba([serialise(r.to_tool_record()) for r in records]),
        dtype=np.float64,
    )
    write_curves(
        probs,
        [LABELS.index(r.label) for r in records],
        [r.server_name for r in records],
        directory / "curves.json",
        n_thresholds=20,
        n_boot=20,
    )
    write_selection(
        Selection(
            winner="deberta-v3-base",
            rule=RULE,
            margin=0.012,
            decided_by="mean",
            per_architecture={
                "deberta-v3-base": Architecture("deberta-v3-base", [0.90, 0.91, 0.89], 120.0),
                "modernbert-base": Architecture("modernbert-base", [0.88, 0.89, 0.88], 90.0),
            },
        ),
        directory / "selection.json",
    )


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """`bench/results/` as it will look after `make bench` and `make eval-final`."""
    tmp_path = tmp_path_factory.mktemp("bench")
    directory = tmp_path / "results"
    directory.mkdir()
    _write_ablations(directory)
    _write_holdout(directory, tmp_path)
    _write_baseline(directory)
    _write_curves_and_selection(directory)
    assert {p.name for p in directory.glob("*.json")} >= {
        "ablations.json",
        "baseline.json",
        "holdout.json",
        "curves.json",
        "selection.json",
    }
    return directory


@pytest.fixture
def readme(tmp_path: Path) -> Path:
    """A copy of the committed README, so a test run never rewrites the real one."""
    target = tmp_path / "README.md"
    target.write_text(README.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    return target


def _run(results_dir: Path, readme_path: Path) -> int:
    return int(rn.main(["--results-dir", str(results_dir), "--readme", str(readme_path)]))


def _block(text: str) -> str:
    start = text.index(rn.MARK_START) + len(rn.MARK_START)
    return text[start : text.index(rn.MARK_END)]


def _block_lines(text: str) -> list[str]:
    return [line for line in _block(text).splitlines() if line.strip()]


# ------------------------------------------------- the committed README, before `make bench`


def test_the_committed_readme_carries_the_placeholder_block() -> None:
    text = README.read_text(encoding="utf-8")
    assert rn.MARK_START in text and rn.MARK_END in text
    block = _block(text)
    assert "—" in block, "the placeholder block says the numbers are not measured yet"
    assert "make bench" in block and "make eval-final" in block


def test_the_committed_readme_never_quotes_the_judge_as_rater_agreement() -> None:
    """PRD §17.1 names four headline numbers; the judge-as-rater kappa is not one of them."""
    text = README.read_text(encoding="utf-8").lower()
    for forbidden in ("judge_agreement", "judge-as-rater", "agreement"):
        assert forbidden not in text, f"README must not quote {forbidden!r}"


def test_the_committed_readme_keeps_its_quickstart() -> None:
    text = README.read_text(encoding="utf-8")
    for target in (
        "make setup",
        "make lint",
        "make test",
        "make up",
        "make serve",
        "make bench",
        "make eval-final",
        "make readme",
        "make train",
        "make train-all",
        "make corpus-stats",
        "make schemas",
        "sieve triage",
        "docs/mcp-server.md",
        "action/",
        "winget install ezwinports.make",
    ):
        assert target in text, f"the quickstart lost {target!r}"


def test_the_committed_readme_states_no_measured_percentage_of_its_own() -> None:
    text = README.read_text(encoding="utf-8")
    outside = text.replace(_block(text), "")
    for line in outside.splitlines():
        if PERCENT.search(line):
            assert any(word in line.lower() for word in PERCENT_IS_ALLOWED), (
                f"README quotes a percentage that is not a stated target: {line!r}"
            )


def test_make_readme_calls_the_generator_by_path() -> None:
    assert "docs/scripts/readme_numbers.py" in (REPO / "Makefile").read_text(encoding="utf-8")


# ------------------------------------------------------------------------ the generated block


def test_the_generated_block_carries_the_four_headline_numbers(results: Path, readme: Path) -> None:
    assert _run(results, readme) == 0
    holdout = json.loads((results / "holdout.json").read_text(encoding="utf-8"))
    row = rn.headline_row(json.loads((results / "ablations.json").read_text(encoding="utf-8")))
    lines = _block_lines(readme.read_text(encoding="utf-8"))

    assert rn.pct(holdout["fpr_pipeline_real"]["point"]) in lines[0]
    assert rn.pct(holdout["fpr_pipeline_real"]["lo"]) in lines[0]
    assert rn.pct(holdout["fpr_pipeline_real"]["hi"]) in lines[0]

    reduction = row["fp_reduction_vs_baseline"]
    assert rn.pct(reduction["relative"]) in lines[1]
    assert rn.p_value(reduction["mcnemar_p"]) in lines[1]

    assert rn.ratio(holdout["recall"]) in lines[2]
    assert rn.ratio(holdout["recall_lcb"]) in lines[2]

    assert rn.ratio(holdout["real_poisoned_recall_stage1"]) in lines[3]
    assert rn.ratio(holdout["real_poisoned_recall_pipeline"]) in lines[3]


def test_the_generated_block_has_no_placeholder_dash(results: Path, readme: Path) -> None:
    assert _run(results, readme) == 0
    assert "—" not in _block(readme.read_text(encoding="utf-8"))


def test_the_synthetic_inclusive_reduction_is_never_in_the_first_five_lines(
    results: Path, readme: Path
) -> None:
    assert _run(results, readme) == 0
    row = rn.headline_row(json.loads((results / "ablations.json").read_text(encoding="utf-8")))
    real = row["fp_reduction_vs_baseline"]
    synthetic_inclusive = row["fp_reduction_all_sources"]
    # The two differ in the absolute reduction and in the paired test, which is the point: the
    # synthetic rows are easier, so including them flatters the number and weakens the evidence.
    assert rn.points(real["point"]) != rn.points(synthetic_inclusive["point"])
    assert rn.p_value(real["mcnemar_p"]) != rn.p_value(synthetic_inclusive["mcnemar_p"])

    lines = _block_lines(readme.read_text(encoding="utf-8"))
    first_five = "\n".join(lines[:5])
    assert rn.points(synthetic_inclusive["point"]) not in first_five
    assert rn.p_value(synthetic_inclusive["mcnemar_p"]) not in first_five
    # M2 asks for it second and labelled, so it appears below and says what it is.
    rest = "\n".join(lines[5:])
    assert rn.points(synthetic_inclusive["point"]) in rest
    assert rn.p_value(synthetic_inclusive["mcnemar_p"]) in rest
    assert "synthetic" in rest.lower()


def test_the_four_numbers_are_one_per_line_in_prd_17_1_order(results: Path, readme: Path) -> None:
    assert _run(results, readme) == 0
    lines = _block_lines(readme.read_text(encoding="utf-8"))
    assert len(lines) >= 5
    assert "FPR_pipeline" in lines[0] and "real subset" in lines[0]
    assert "McNemar" in lines[1]
    assert "recall" in lines[2].lower() and "lower bound" in lines[2].lower()
    assert "real_poisoned" in lines[3]
    assert "make readme" in lines[4] and "bench/results" in lines[4]


def test_the_block_names_the_baseline_and_the_ablation_row_it_read(
    results: Path, readme: Path
) -> None:
    assert _run(results, readme) == 0
    baseline = json.loads((results / "baseline.json").read_text(encoding="utf-8"))[0]
    row = rn.headline_row(json.loads((results / "ablations.json").read_text(encoding="utf-8")))
    lines = _block_lines(readme.read_text(encoding="utf-8"))
    assert baseline["scanner"] in lines[1] and baseline["version"] in lines[1]
    assert f"row {row['row']}" in lines[1]


def test_the_rest_of_the_readme_is_untouched(results: Path, readme: Path) -> None:
    before = readme.read_text(encoding="utf-8")
    assert _run(results, readme) == 0
    after = readme.read_text(encoding="utf-8")
    assert before.partition(rn.MARK_START)[0] == after.partition(rn.MARK_START)[0]
    assert before.partition(rn.MARK_END)[2] == after.partition(rn.MARK_END)[2]


def test_the_readme_is_written_utf8_with_lf_newlines(results: Path, readme: Path) -> None:
    assert _run(results, readme) == 0
    raw = readme.read_bytes()
    assert b"\r\n" not in raw
    raw.decode("utf-8")


def test_running_twice_is_idempotent(results: Path, readme: Path) -> None:
    assert _run(results, readme) == 0
    once = readme.read_bytes()
    assert _run(results, readme) == 0
    assert readme.read_bytes() == once


# ------------------------------------------------------------------------------ the refusals


def test_a_missing_holdout_exits_non_zero_and_leaves_the_readme_byte_identical(
    results: Path, readme: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    partial = tmp_path / "partial"
    partial.mkdir()
    for name in ("ablations.json", "baseline.json", "curves.json", "selection.json"):
        (partial / name).write_bytes((results / name).read_bytes())
    before = readme.read_bytes()

    assert _run(partial, readme) != 0
    assert readme.read_bytes() == before
    message = capsys.readouterr().err
    assert "holdout.json" in message and "make eval-final" in message


def test_a_holdout_with_no_real_subset_figure_is_refused(
    results: Path, readme: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    partial = tmp_path / "blank"
    partial.mkdir()
    for path in results.glob("*.json"):
        (partial / path.name).write_bytes(path.read_bytes())
    holdout = json.loads((partial / "holdout.json").read_text(encoding="utf-8"))
    holdout["fpr_pipeline_real"] = {"point": None, "lo": None, "hi": None}
    (partial / "holdout.json").write_text(
        json.dumps(holdout, indent=2), encoding="utf-8", newline="\n"
    )
    before = readme.read_bytes()

    assert _run(partial, readme) != 0
    assert readme.read_bytes() == before
    assert "fpr_pipeline_real" in capsys.readouterr().err


def test_a_missing_ablations_file_is_refused(
    results: Path, readme: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Line 2 cannot be invented either: two of the four numbers is not a shippable README."""
    partial = tmp_path / "no-ablations"
    partial.mkdir()
    for path in results.glob("*.json"):
        if path.name != "ablations.json":
            (partial / path.name).write_bytes(path.read_bytes())
    before = readme.read_bytes()

    assert _run(partial, readme) != 0
    assert readme.read_bytes() == before
    assert "ablations.json" in capsys.readouterr().err


def test_an_ablations_file_with_no_full_pipeline_row_is_refused(
    results: Path, readme: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    partial = tmp_path / "no-row"
    partial.mkdir()
    for path in results.glob("*.json"):
        (partial / path.name).write_bytes(path.read_bytes())
    payload = json.loads((partial / "ablations.json").read_text(encoding="utf-8"))
    payload["rows"] = [r for r in payload["rows"] if r["judge"] == "none"]
    (partial / "ablations.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8", newline="\n"
    )

    assert _run(partial, readme) != 0
    assert DEFAULT_JUDGE in capsys.readouterr().err


def test_the_committed_results_directory_cannot_produce_a_readme_today(
    readme: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No training has happened, so `make readme` must refuse rather than write a placeholder."""
    assert not (REPO / "bench" / "results" / "holdout.json").exists()
    before = readme.read_bytes()
    assert int(rn.main(["--readme", str(readme)])) != 0
    assert readme.read_bytes() == before
    assert "holdout.json" in capsys.readouterr().err


def test_the_generator_never_writes_into_bench_results(results: Path, readme: Path) -> None:
    committed = REPO / "bench" / "results"
    before = sorted(p.name for p in committed.iterdir())
    assert _run(results, readme) == 0
    assert sorted(p.name for p in committed.iterdir()) == before
