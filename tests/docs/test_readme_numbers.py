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
PERCENT_IS_ALLOWED = ("target", "goal")


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
    from bench.ablations.run import dataset_version
    from bench.split.split import DEFAULT_SEED, assign
    from sieve.model.data import load_corpus
    from tests.bench.report_fixture import render

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
    # H3: `holdout.json` is rendered by `bench.report_final` from saved predictions; the
    # fixture scores the assigned holdout and the planted slice through the production writers.
    report = render(
        [r for r in assigned if r.split == "holdout"],
        [r for r in assigned if r.source == "real_poisoned"],
        [],
        n_boot=N_BOOT,
        reason="fixture run for tests/docs/test_readme_numbers.py",
        dataset_version=dataset_version(corpus),
    )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "holdout.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
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
    assert rn.MARK_START in text and rn.MARK_END in text, "the block is part of the committed file"
    block = _block(text)
    assert block.strip(), "the block is never empty"
    if "—" not in block:
        pytest.skip("the numbers have landed: `make readme` has filled the block in")
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
    if rn.RESULTS_START in outside:  # generated too
        head, _, rest = outside.partition(rn.RESULTS_START)
        outside = head + rest.partition(rn.RESULTS_END)[2]
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
    # The review cost sits beside M1: with no judge, FPR_pipeline counts auto-confirmed false
    # alarms only, and the share routed to a human is the price of the recall (ADR-009).
    review = holdout["fpr_stage1_real"]
    assert f"sends {rn.pct(review['point'])} of the real non-poisoned tools to review" in lines[0]

    reduction = row["fp_reduction_vs_baseline"]
    if reduction["point"] is not None:
        assert rn.pct(reduction["point"]) in lines[1]
    assert rn.points(reduction["absolute"]) in lines[1]
    assert rn.p_value(reduction["mcnemar_p"]) in lines[1]
    assert rn.p_value(reduction["mcnemar_p_clustered"]) in lines[1]
    # D7: M2 never appears without the pipeline recall it was bought at.
    assert rn.ratio(reduction["sieve_recall"]) in lines[1]
    assert rn.ratio(reduction["baseline_recall"]) in lines[1]

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
    # The two differ in the absolute reduction, which is the point: the synthetic rows are
    # easier, so including them flatters the number. (D7 runs McNemar on non-poisoned rows only,
    # so the two p-values can coincide and are not what tells the figures apart.)
    assert rn.points(real["absolute"]) != rn.points(synthetic_inclusive["absolute"])

    lines = _block_lines(readme.read_text(encoding="utf-8"))
    first_five = "\n".join(lines[:5])
    assert rn.points(synthetic_inclusive["absolute"]) not in first_five
    # M2 asks for it second and labelled, so it appears below and says what it is.
    rest = "\n".join(lines[5:])
    assert rn.points(synthetic_inclusive["absolute"]) in rest
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

    def outside_results(text: str) -> str:
        if rn.RESULTS_START not in text:
            return text
        head, _, rest = text.partition(rn.RESULTS_START)
        return head + rest.partition(rn.RESULTS_END)[2]

    tail_before = outside_results(before.partition(rn.MARK_END)[2])
    assert tail_before == outside_results(after.partition(rn.MARK_END)[2])


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


def _results_block(text: str) -> str:
    start = text.index(rn.RESULTS_START) + len(rn.RESULTS_START)
    return text[start : text.index(rn.RESULTS_END)]


def test_the_results_table_lists_every_scored_row_from_ablations_json(
    results: Path, readme: Path
) -> None:
    assert _run(results, readme) == 0
    table = _results_block(readme.read_text(encoding="utf-8"))
    rows = json.loads((results / "ablations.json").read_text(encoding="utf-8"))["rows"]
    for row in rows:
        if row.get("breakdown_of"):
            continue
        assert f"| {row['row']} | {row['name']} |" in table
        if row.get("macro_f1_mean") is not None:
            assert f"{row['macro_f1_mean']:.3f}" in table
    assert "—" not in table


def test_a_readme_without_the_results_markers_keeps_working(results: Path, readme: Path) -> None:
    text = readme.read_text(encoding="utf-8")
    head, _, rest = text.partition(rn.RESULTS_START)
    stripped = head + rest.partition(rn.RESULTS_END)[2]
    readme.write_text(stripped, encoding="utf-8", newline="\n")
    assert _run(results, readme) == 0
    assert rn.RESULTS_START not in readme.read_text(encoding="utf-8")


def test_a_deferred_judge_row_falls_back_to_the_stage_1_headline_and_says_so(
    results: Path, readme: Path, tmp_path: Path
) -> None:
    """D8 defers the judge: its rows are emitted `status: deferred` with no numbers, so the
    headline is the Stage-1 row of the same encoder, and the block must say which it is."""
    deferred = tmp_path / "deferred"
    deferred.mkdir()
    for path in results.glob("*.json"):
        (deferred / path.name).write_bytes(path.read_bytes())
    payload = json.loads((deferred / "ablations.json").read_text(encoding="utf-8"))
    stage1 = [r for r in payload["rows"] if r["judge"] == "none"
              and str(r.get("classifier", "")).startswith("models/") and r.get("mode") == "full"
              and not r.get("breakdown_of")]  # fmt: skip
    assert stage1, "the fixture matrix has a Stage-1 encoder row"
    payload["rows"] = [
        {"row": r["row"], "name": r["name"], "judge": r["judge"], "classifier": r["classifier"],
         "status": "deferred"} if r["judge"] == DEFAULT_JUDGE else r
        for r in payload["rows"]
    ]  # fmt: skip
    (deferred / "ablations.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8", newline="\n"
    )
    assert rn.headline_row(payload)["row"] == min(r["row"] for r in stage1)
    assert _run(deferred, readme) == 0
    assert "judge is deferred" in _block(readme.read_text(encoding="utf-8"))


def test_the_committed_results_directory_cannot_produce_a_readme_today(
    readme: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No training has happened, so `make readme` must refuse rather than write a placeholder."""
    if (REPO / "bench" / "results" / "holdout.json").exists():
        pytest.skip("the holdout has been opened; `make readme` no longer has nothing to read")
    before = readme.read_bytes()
    assert int(rn.main(["--readme", str(readme)])) != 0
    assert readme.read_bytes() == before
    assert "holdout.json" in capsys.readouterr().err


def test_a_failed_write_leaves_the_readme_byte_identical(
    results: Path, readme: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The README is replaced, never truncated in place: a crash mid-write loses nothing."""
    before = readme.read_bytes()
    real_write_text = Path.write_text

    def explode(self: Path, *args: Any, **kwargs: Any) -> int:
        if self.name.endswith(".tmp"):
            raise OSError("disk full")
        return int(real_write_text(self, *args, **kwargs))

    monkeypatch.setattr(Path, "write_text", explode)
    with pytest.raises(OSError, match="disk full"):
        _run(results, readme)
    monkeypatch.undo()

    assert readme.read_bytes() == before, "a failed write must not leave a truncated README"


def test_the_provenance_line_refuses_a_missing_key_instead_of_defaulting(
    results: Path, readme: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`n_boot` is a fact too. A default would print "0 bootstrap resamples" as if measured."""
    partial = tmp_path / "no-n-boot"
    partial.mkdir()
    for path in results.glob("*.json"):
        (partial / path.name).write_bytes(path.read_bytes())
    payload = json.loads((partial / "ablations.json").read_text(encoding="utf-8"))
    del payload["n_boot"]
    (partial / "ablations.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8", newline="\n"
    )
    before = readme.read_bytes()

    assert _run(partial, readme) != 0
    assert readme.read_bytes() == before
    assert "n_boot" in capsys.readouterr().err


def test_the_generator_never_writes_into_bench_results(results: Path, readme: Path) -> None:
    committed = REPO / "bench" / "results"
    before = sorted(p.name for p in committed.iterdir())
    assert _run(results, readme) == 0
    assert sorted(p.name for p in committed.iterdir()) == before
