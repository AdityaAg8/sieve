"""`make sweep` (training plan C2): folds, best epoch, the whole-dev model, calibration.

Resumable, and never mixes evidence: a fold is skipped only when its `metrics.json` was made from
this corpus at this commit; a fold from another corpus or commit is a refusal, not a retrain.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from bench import gpu, sweep
from sieve.model.version import git_sha

pytestmark = [
    pytest.mark.filterwarnings("ignore::FutureWarning"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
    pytest.mark.filterwarnings("ignore::UserWarning"),
]


def _flag(argv: Sequence[str], name: str) -> str:
    return argv[list(argv).index(name) + 1]


class FakeRunner:
    """Writes the files each step would, and records every step it was asked to run."""

    def __init__(self, corpus: Path, *, fail_on_fold: int | None = None, best: int = 3) -> None:
        self.calls: list[list[str]] = []
        self.corpus = corpus
        self.fail_on_fold = fail_on_fold
        self.best = best

    def __call__(self, argv: Sequence[str]) -> None:
        self.calls.append(list(argv))
        if argv[0] == "sieve.model.calibrate":
            out = Path(_flag(argv, "--out"))
            (out / "sieve_config.json").write_text("{}", encoding="utf-8")
            return
        fold = _flag(argv, "--fold")
        if self.fail_on_fold is not None and fold == str(self.fail_on_fold):
            raise sweep.StepFailedError(f"fold {fold} died")
        out = Path(_flag(argv, "--out"))
        out.mkdir(parents=True, exist_ok=True)
        metrics = {
            "corpus_sha256": hashlib.sha256(self.corpus.read_bytes()).hexdigest(),
            "git_sha": git_sha(short=False),
            "best_epoch": self.best + (int(fold) % 2 if fold != "all" else 0),
            "stop_after_epochs": int(_flag(argv, "--epochs")) if fold == "all" else None,
        }
        (out / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")

    def folds_trained(self) -> list[str]:
        return [_flag(c, "--fold") for c in self.calls if c[0] == "sieve.model.train"]


def _corpus(tmp_path: Path) -> Path:
    path = tmp_path / "corpus.jsonl"
    path.write_text('{"id": "x"}\n', encoding="utf-8")
    return path


def _sweep(tmp_path: Path, corpus: Path, runner: Any, **kw: Any) -> list[Path]:
    return sweep.sweep(
        "tiny", seeds=[0], corpus=corpus, models_root=tmp_path / "models",
        results_dir=tmp_path / "results", runner=runner, env={"CUDA_VISIBLE_DEVICES": "-1"},
        **kw,
    )  # fmt: skip


def test_one_seed_runs_five_folds_then_the_whole_dev_model_then_calibration(
    tmp_path: Path,
) -> None:
    corpus = _corpus(tmp_path)
    runner = FakeRunner(corpus)
    (model_dir,) = _sweep(tmp_path, corpus, runner)
    assert model_dir == tmp_path / "models" / "tiny-s0"
    assert runner.folds_trained() == ["0", "1", "2", "3", "4", "all"]
    whole = next(c for c in runner.calls if "all" in c)
    # best epochs 3,4,3,4,3 -> median 3, passed as the stop point
    assert _flag(whole, "--epochs") == "3"
    assert _flag(whole, "--out") == str(model_dir)
    calibrate = runner.calls[-1]
    assert calibrate[0] == "sieve.model.calibrate"
    assert _flag(calibrate, "--out") == str(model_dir)
    assert calibrate[calibrate.index("--dirs") + 1 : calibrate.index("--dirs") + 6] == [
        str(model_dir / f"fold{k}") for k in range(5)
    ]


def test_a_failure_stops_the_sweep_and_a_rerun_skips_the_finished_folds(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    first = FakeRunner(corpus, fail_on_fold=3)
    with pytest.raises(sweep.StepFailedError):
        _sweep(tmp_path, corpus, first)
    assert first.folds_trained() == ["0", "1", "2", "3"]

    second = FakeRunner(corpus)
    _sweep(tmp_path, corpus, second)
    assert second.folds_trained() == ["3", "4", "all"]


def test_a_fold_without_metrics_is_retrained(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    (tmp_path / "models" / "tiny-s0" / "fold1").mkdir(parents=True)
    runner = FakeRunner(corpus)
    _sweep(tmp_path, corpus, runner)
    assert runner.folds_trained()[:5] == ["0", "1", "2", "3", "4"]


def test_a_changed_corpus_is_refused(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    with pytest.raises(sweep.StepFailedError):
        _sweep(tmp_path, corpus, FakeRunner(corpus, fail_on_fold=2))
    corpus.write_text('{"id": "y"}\n', encoding="utf-8")
    runner = FakeRunner(corpus)
    with pytest.raises(sweep.SweepRefusedError, match="corpus_sha256"):
        _sweep(tmp_path, corpus, runner)
    assert runner.calls == []


def test_another_commits_fold_is_refused(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    fold0 = tmp_path / "models" / "tiny-s0" / "fold0"
    fold0.mkdir(parents=True)
    stale = {"corpus_sha256": hashlib.sha256(corpus.read_bytes()).hexdigest(), "git_sha": "0" * 40}
    (fold0 / "metrics.json").write_text(json.dumps(stale), encoding="utf-8")
    with pytest.raises(sweep.SweepRefusedError, match="git_sha"):
        _sweep(tmp_path, corpus, FakeRunner(corpus))


def test_a_busy_gpu_is_refused_before_anything_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = _corpus(tmp_path)
    busy = gpu.GpuState(memory_used_mib=8396, memory_total_mib=12282, utilization=88, apps=[])
    monkeypatch.setattr(gpu, "query", lambda **_: busy)
    runner = FakeRunner(corpus)
    with pytest.raises(sweep.SweepRefusedError, match="busy"):
        sweep.sweep("tiny", seeds=[0], corpus=corpus, models_root=tmp_path / "models",
                    results_dir=tmp_path / "r", runner=runner, env={})  # fmt: skip
    assert runner.calls == []
    sweep.sweep("tiny", seeds=[0], corpus=corpus, models_root=tmp_path / "models",
                results_dir=tmp_path / "r", runner=runner, env={}, force=True)  # fmt: skip
    assert runner.folds_trained()[-1] == "all"


def test_several_seeds_each_get_their_own_family_directory(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    dirs = sweep.sweep(
        "tiny", seeds=[0, 1], corpus=corpus, models_root=tmp_path / "models",
        results_dir=tmp_path / "r", runner=FakeRunner(corpus), env={"CUDA_VISIBLE_DEVICES": "-1"},
    )  # fmt: skip
    assert [d.name for d in dirs] == ["tiny-s0", "tiny-s1"]


def test_the_cli_parses_seeds_as_make_passes_them(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(sweep, "sweep", lambda profile, **kw: seen.update(profile=profile, **kw))
    sweep.main(["--profile", "deberta-nockpt", "--seeds", "0 1 2"])
    assert seen["profile"] == "deberta-nockpt"
    assert seen["seeds"] == [0, 1, 2]


@pytest.mark.slow
def test_a_real_tiny_sweep_killed_after_fold_2_resumes(tmp_path: Path) -> None:
    from sieve.model.calibrate import main as calibrate_main
    from sieve.model.data import load_corpus
    from sieve.model.train import main as train_main

    records = load_corpus(Path("fixtures/corpus/mini-50.jsonl"))
    servers = sorted({r.server_name for r in records})
    fold_of = {name: i % 5 for i, name in enumerate(servers)}
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        "".join(
            r.model_copy(update={"split": "dev", "fold": fold_of[r.server_name]}).model_dump_json()
            + "\n"
            for r in records
        ),
        encoding="utf-8",
        newline="\n",
    )

    trained: list[str] = []

    def in_process(kill_at: int | None) -> Any:
        def run(argv: Sequence[str]) -> None:
            if argv[0] == "sieve.model.train":
                fold = _flag(argv, "--fold")
                if kill_at is not None and fold == str(kill_at):
                    raise sweep.StepFailedError("killed")
                trained.append(fold)
                train_main([*argv[1:], "--allow-dirty"])
            else:
                calibrate_main([*argv[1:], "--n-boot", "20", "--curve-boot", "10",
                                "--curve-points", "5"])  # fmt: skip

        return run

    with pytest.raises(sweep.StepFailedError):
        _sweep(tmp_path, corpus, in_process(kill_at=3))
    assert trained == ["0", "1", "2"]
    trained.clear()
    (model_dir,) = _sweep(tmp_path, corpus, in_process(kill_at=None))
    assert trained == ["3", "4", "all"]
    config = json.loads((model_dir / "sieve_config.json").read_text(encoding="utf-8"))
    assert config["t_low"] is not None
