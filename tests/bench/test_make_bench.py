"""`make bench` completeness (training plan C7, finding F72).

`make -n` proves the order and that no step needs a server; the slow test runs the real target
on tiny fixtures and checks it writes every file `docs/scripts/readme_numbers.py`,
`sieve/model/push.py` and the web (through `sieve/api/routes/bench.py`) read.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

MAKE = shutil.which("make")
pytestmark = pytest.mark.skipif(MAKE is None, reason="make is not installed")

#: Every `bench` step, in the plan's order (C7).
BENCH_STEPS = (
    "bench.baseline.run",
    "bench.split.pairs",
    "sieve.model.select --candidate",  # before the ablations: rows 4/5 read it (Z2 review)
    "bench.ablations.run",
    "bench.analysis.errors",
    "bench.report_final",
    "bench.latency --in-process",
    "bench.demo",
)


def _make(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    assert MAKE is not None
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [MAKE, "--no-print-directory", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env=env,
    )


def _dry(*args: str) -> str:
    out = _make("-n", *args, "FAMILY=deberta-v3-base")
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_make_n_bench_lists_every_step_in_order_and_no_server() -> None:
    text = _dry("bench")
    positions = [text.find(step) for step in BENCH_STEPS]
    assert all(p >= 0 for p in positions), dict(zip(BENCH_STEPS, positions, strict=True))
    assert positions == sorted(positions)
    assert "--base-url" not in text, "bench must not need a running server (C7)"
    assert "judge_agreement" not in text, "the judge is deferred (D8)"
    # D14: the published artefact is selection.json's median seed, resolved when the line runs.
    assert "demo --model-id $(uv run python -m sieve.model.select --print-published" in text
    assert "--fallback models/deberta-v3-base-s0) --final" in text
    assert "curves.json" in text


def test_every_bench_step_honours_results_dir() -> None:
    text = _dry("bench", "RESULTS_DIR=elsewhere")
    assert "bench/results" not in text
    assert text.count("elsewhere") >= len(BENCH_STEPS)


def test_push_is_a_dry_run_unless_confirmed() -> None:
    assert "--dry-run" in _dry("push", "HF_REPO=owner/name")
    assert "--dry-run" not in _dry("push", "HF_REPO=owner/name", "CONFIRM=1")
    refused = _make("push")
    assert refused.returncode != 0 and "HF_REPO" in refused.stdout


def test_select_export_and_reproduce_check_targets_exist() -> None:
    assert "sieve.model.select --candidate deberta-v3-base=" in _dry("select")
    assert "sieve.model.export $(uv run python -m sieve.model.select" in _dry("export")
    assert "sieve.model.export elsewhere/x" in _dry("export", "PUBLISHED_DIR=elsewhere/x")
    # ADR-009: int8 failed parity on DeBERTa-v3, so the fp32 graph is exported and served.
    assert "--no-quantize" in _dry("export")
    assert "--no-quantize" not in _dry("export", "EXPORT_FLAGS=")
    text = _dry("reproduce-check")
    assert "--out bench/data/_reproduce/ablations.json" in text
    assert "--tolerance 0.01" in text


def test_eval_final_requires_model_dirs() -> None:
    out = _make("eval-final", "REASON=x")
    assert out.returncode != 0 and "MODEL_DIRS" in out.stdout


# --- the real target on tiny fixtures -----------------------------------------------------


def _oof_family(models: Path, name: str, corpus: Path, results: Path, *, skill: float) -> None:
    """`models/<name>-s{0,1,2}` with real pooled arrays and real calibrated thresholds."""
    from sieve.model.calibrate import main as calibrate_main
    from sieve.model.data import load_corpus
    from tests.bench.test_ablations import _write_fold

    dev = [r for r in load_corpus(corpus) if r.split == "dev"]
    for seed in (0, 1, 2):
        out = models / f"{name}-s{seed}"
        folds = [
            _write_fold(
                out / f"fold{k}", [r for r in dev if r.fold == k], seed=seed * 10 + k, skill=skill
            )
            for k in range(5)
        ]
        calibrate_main(
            ["--dirs", *map(str, folds), "--corpus", str(corpus), "--results-dir", str(results),
             "--out", str(out), "--n-boot", "40", "--curve-boot", "40", "--curve-points", "12"]
        )  # fmt: skip


@pytest.mark.slow
@pytest.mark.filterwarnings("ignore::FutureWarning")
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.filterwarnings("ignore::torch.jit.TracerWarning")
def test_make_bench_on_tiny_fixtures_writes_every_file_the_readers_read(tmp_path: Path) -> None:
    if shutil.which("mcp-scanner") is None:
        pytest.skip("the baseline step needs mcp-scanner (docs/RUNBOOK.md)")
    from tests.model.artefacts import build_calibrated_tiny

    (tmp_path / "tiny").mkdir()
    published, _ = build_calibrated_tiny(tmp_path / "tiny")
    corpus = tmp_path / "tiny" / "corpus.jsonl"
    models, results = tmp_path / "models", tmp_path / "results"
    _oof_family(models, "tinya", corpus, results, skill=2.4)
    _oof_family(models, "tinyb", corpus, results, skill=1.2)
    matrix = tmp_path / "matrix.yaml"
    matrix.write_text(
        json.dumps({"rows": [
            {"row": 1, "name": "baseline", "classifier": "baseline", "judge": "none"},
            {"row": 2, "name": "tfidf", "classifier": "tfidf", "judge": "none",
             "minimal_pairs": True},
            {"row": 4, "name": "headline", "classifier": (models / "tinya-s*").as_posix(),
             "judge": "none"},
        ]}),
        encoding="utf-8",
    )  # fmt: skip

    def posix(p: Path) -> str:
        return p.as_posix()

    candidates = " ".join(
        f"{n}=" + ",".join(posix(models / f"{n}-s{s}") for s in (0, 1, 2))
        for n in ("tinya", "tinyb")
    )
    model_dir = models / "tinya-s0"
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": "-1", "PYTHONUTF8": "1"}
    out = _make(
        "bench",
        f"RESULTS_DIR={posix(results)}",
        f"BENCH_CORPUS={posix(corpus)}",
        f"ABLATIONS_MATRIX={posix(matrix)}",
        f"MODEL_DIR={posix(model_dir)}",
        "FOLD_DIRS=" + " ".join(posix(model_dir / f"fold{k}") for k in range(5)),
        f"PUBLISHED_DIR={posix(published)}",
        f"CANDIDATES={candidates}",
        "N_BOOT=40",
        env=env,
    )
    assert out.returncode == 0, out.stdout[-3000:] + out.stderr[-3000:]

    # readme_numbers.py: ablations, baseline, baseline_raw, curves, selection (holdout.json
    # only after an opening, H3). push.py: + latency, export_parity (`make export`). Web: the
    # API serves demo.json and ablations.json. Plus the errors page and the pair index.
    for name in ("ablations.json", "baseline.json", "baseline_raw.json", "curves.json",
                 "selection.json", "latency.json", "demo.json", "minimal_pairs.json",
                 "errors.md"):  # fmt: skip
        assert (results / name).is_file(), name
    selection = json.loads((results / "selection.json").read_text(encoding="utf-8"))
    assert selection["winner"] == "tinya"
    demo = json.loads((results / "demo.json").read_text(encoding="utf-8"))
    assert demo["meta"]["provisional"] is False
    rows = json.loads((results / "ablations.json").read_text(encoding="utf-8"))["rows"]
    assert [r["row"] for r in rows] == [1, 2, 4]
    assert not (results / "holdout.json").exists(), "nothing was opened"
