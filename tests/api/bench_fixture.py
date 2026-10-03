"""The `bench/results/` the web's measured-benchmark fixture is served from (training plan I5).

Every file is written by its **real** producer on the mini-50 fixture with the contract fakes:
`ablations.json` by `bench.ablations.run` (H5) over the committed matrix, `holdout.json` by
`bench.report_final` (H3, through `tests/bench/report_fixture.py`), `label_audit.json` by
`bench.label.audit` (F3) and the opening log line by `bench.eval_final.append_log_line`. Nothing
is a measurement and nothing is typed by hand: these are fixture numbers, and the web shows
them only in its mocked build and its tests.

`uv run python -m tests.api.bench_fixture` rewrites `web/src/mocks/fixtures/bench-summary.json`,
the `GET /bench/summary` body the API serves over those files. Everything runs in a temporary
directory; `bench/results/` is never written to.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
MATRIX = REPO / "bench" / "ablations.yaml"
MINI_50 = REPO / "fixtures" / "corpus" / "mini-50.jsonl"
WEB_FIXTURE = REPO / "web" / "src" / "mocks" / "fixtures" / "bench-summary.json"
#: The fixture's wall clock: a file's mtime would make every regeneration a diff.
GENERATED_AT = "2026-10-03T00:00:00Z"
N_BOOT = 40
AUDIT_ROWS = 104
AUDIT_SERVERS = 26
DATASET_VERSION = "mini-50.jsonl@fixture"
REASON = "fixture: the web's measured-benchmark state (training plan I5)"


def _dev_records() -> list[Any]:
    from bench.split.split import DEFAULT_SEED, assign
    from sieve.model.data import load_corpus

    return [r for r in assign(load_corpus(MINI_50), seed=DEFAULT_SEED) if r.split == "dev"]


def _baseline_rows(records: list[Any]) -> dict[str, dict[str, Any]]:
    """The scanner flags every poisoned row plus four benign ones, so its FPR is not zero."""
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


def write_ablations(directory: Path, *, n_boot: int = N_BOOT) -> Path:
    """The committed matrix, every encoder row re-inferred with a seeded contract fake."""
    from bench.ablations.run import (
        DEFAULT_SEEDS,
        Inputs,
        RowSpec,
        load_matrix,
        run_matrix,
        write_results,
    )
    from sieve.protocols import Classifier
    from tests.bench.report_fixture import SeededFake

    def classifier_factory(spec: RowSpec, seed: int) -> Classifier:
        return SeededFake(seed)

    records = _dev_records()
    specs = [
        s.model_copy(update={"reinference": True}) if s.seeded else s for s in load_matrix(MATRIX)
    ]
    poisoned = [r for r in records if r.label == "poisoned" and r.mutation_of]
    inputs = Inputs(
        records=records,
        baseline_rows=_baseline_rows(records),
        minimal_pairs=[{"host_id": r.mutation_of, "mutation_id": r.id} for r in poisoned],
        scanner_findings_by_id={r.id: [] for r in records},
        dataset_version=DATASET_VERSION,
    )
    payload = run_matrix(
        specs, inputs, classifier_factory=classifier_factory, seeds=DEFAULT_SEEDS, n_boot=n_boot
    )
    return write_results(payload, directory / "ablations.json")


def write_report(directory: Path, *, n_boot: int = N_BOOT) -> Path:
    from tests.bench.report_fixture import fixture_world, render

    report = render(*fixture_world(), n_boot=n_boot, reason=REASON, dataset_version=DATASET_VERSION)
    path = directory / "holdout.json"
    path.write_text(
        # `allow_nan=False`, as `bench.report_final.main` writes it: NaN is not JSON.
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def write_label_audit(directory: Path, *, n_boot: int = N_BOOT) -> Path:
    """Human gold vs ensemble on a 104-row audit: three disagreements and one undecided row."""
    from bench.label.audit import test_id_report

    ids = [f"audit-{i:04d}" for i in range(AUDIT_ROWS)]
    gold = {i: ("imperative_benign" if n % 4 == 0 else "benign") for n, i in enumerate(ids)}
    majority: dict[str, str | None] = dict(gold)
    for n in (3, 41, 77):
        majority[ids[n]] = "imperative_benign" if gold[ids[n]] == "benign" else "benign"
    majority[ids[90]] = None
    servers = {i: f"server-{n % AUDIT_SERVERS:02d}" for n, i in enumerate(ids)}
    report = {"test_id": test_id_report(gold, majority, servers, n_boot=n_boot)}
    path = directory / "label_audit.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def write_opened_log(directory: Path, rows_evaluated: int) -> Path:
    from bench import eval_final as ef

    path = directory / "test_opened.log"
    with mock.patch.object(ef, "LOG_PATH", path):
        ef.append_log_line(
            rows_evaluated=rows_evaluated,
            reason=REASON,
            version=DATASET_VERSION,
            models=["deberta@s0", "deberta@s1"],
            new_version=False,
        )
    return path


def write_results(directory: Path, *, n_boot: int = N_BOOT) -> Path:
    """Every results file the benchmark page reads, through its producer."""
    directory.mkdir(parents=True, exist_ok=True)
    write_ablations(directory, n_boot=n_boot)
    report = write_report(directory, n_boot=n_boot)
    write_label_audit(directory, n_boot=n_boot)
    n = json.loads(report.read_text(encoding="utf-8"))["n_holdout"]
    write_opened_log(directory, int(n))
    return directory


@contextmanager
def results_dir(*, n_boot: int = N_BOOT) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="sieve-bench-fixture-") as tmp:
        yield write_results(Path(tmp) / "results", n_boot=n_boot)


def summary_body(directory: Path) -> dict[str, Any]:
    """`GET /bench/summary` over `directory`, as JSON, with the fixture's fixed clock."""
    from sieve.api.routes import bench

    body: dict[str, Any] = bench.summary(directory).model_dump(mode="json")
    body["generated_at"] = GENERATED_AT
    return body


def main() -> Path:
    with results_dir() as directory:
        body = summary_body(directory)
    WEB_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    WEB_FIXTURE.write_text(
        json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote {WEB_FIXTURE.relative_to(REPO)}")
    return WEB_FIXTURE


if __name__ == "__main__":  # pragma: no cover
    main()
