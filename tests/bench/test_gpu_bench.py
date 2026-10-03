"""`make gpu-bench` (training plan C3): measured profile choice and non-determinism."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bench import gpu, gpu_bench

pytestmark = [
    pytest.mark.filterwarnings("ignore::FutureWarning"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
    pytest.mark.filterwarnings("ignore::UserWarning"),
]

GIB = 1024**3
IDLE_WINDOWS = "13840, C:\\Windows\\explorer.exe, [N/A]\n"


def _state(apps: str, board: str) -> gpu.GpuState | None:
    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        out = apps if any("compute-apps" in a for a in argv) else board
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    return gpu.query(run=run)


def test_an_idle_windows_card_is_measured() -> None:
    assert gpu_bench.refusal({}, _state(IDLE_WINDOWS, "400, 12282, 2\n"), force=False) is None


def test_a_busy_card_is_refused_unless_forced() -> None:
    busy = _state(IDLE_WINDOWS, "8396, 12282, 88\n")
    reason = gpu_bench.refusal({}, busy, force=False)
    assert reason is not None and "busy" in reason
    assert gpu_bench.refusal({}, busy, force=True) is None
    assert gpu_bench.refusal({}, busy, force=False, idle_mib=9000) is None


def test_another_compute_process_is_refused() -> None:
    state = _state("4242, /usr/bin/python3, 9120\n", "9500, 12282, 99\n")
    reason = gpu_bench.refusal({}, state, force=False)
    assert reason is not None and "4242" in reason


def test_the_cpu_path_refuses_cleanly_even_when_forced() -> None:
    idle = _state(IDLE_WINDOWS, "400, 12282, 2\n")
    for force in (False, True):
        reason = gpu_bench.refusal({"CUDA_VISIBLE_DEVICES": "-1"}, idle, force=force)
        assert reason is not None and "CUDA" in reason
        assert gpu_bench.refusal({}, None, force=force) is not None  # no nvidia-smi


def test_main_on_the_cpu_path_exits_2_without_running_anything(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "-1")
    calls: list[Any] = []
    with pytest.raises(SystemExit) as exit_info:
        gpu_bench.main(["--out", str(tmp_path / "g.json")], runner=calls.append)
    assert exit_info.value.code == 2
    assert calls == []
    assert "CUDA" in capsys.readouterr().err
    assert not (tmp_path / "g.json").exists()


def _metrics(step: float, peak_gib: float | None, *, rows: int = 880) -> dict[str, Any]:
    steps = [9.0] * gpu_bench.WARMUP + [step] * gpu_bench.TIMED
    peak = None if peak_gib is None else int(peak_gib * GIB)
    return {"step_seconds": steps, "peak_cuda_mem_bytes": peak, "train_rows": rows}


def test_a_measurement_ignores_the_warm_up_steps() -> None:
    m = gpu_bench.measure(_metrics(2.0, 4.0), effective_batch=16)
    assert m["seconds_per_step"] == pytest.approx(2.0)
    assert m["steps_per_epoch"] == 55
    assert m["seconds_per_epoch"] == pytest.approx(110.0)
    assert m["peak_gib"] == pytest.approx(4.0)


def test_a_run_that_never_touched_cuda_is_refused() -> None:
    with pytest.raises(gpu_bench.BenchError, match="CUDA"):
        gpu_bench.measure(_metrics(2.0, None), effective_batch=16)


def test_too_few_steps_is_refused() -> None:
    short = {"step_seconds": [1.0] * 20, "peak_cuda_mem_bytes": GIB, "train_rows": 880}
    with pytest.raises(gpu_bench.BenchError, match="steps"):
        gpu_bench.measure(short, effective_batch=16)


def test_the_choice_is_the_fastest_variant_within_11_gib() -> None:
    measured = {
        "deberta": {"seconds_per_epoch": 160.0, "peak_gib": 4.1},
        "deberta-nockpt": {"seconds_per_epoch": 120.0, "peak_gib": 10.9},
        "modernbert": {"seconds_per_epoch": 40.0, "peak_gib": 11.2},  # faster, over the limit
        "modernbert-acc": {"seconds_per_epoch": 55.0, "peak_gib": 6.0},
    }
    assert gpu_bench.choose(measured) == {
        "deberta-v3-base": "deberta-nockpt",
        "modernbert-base": "modernbert-acc",
    }


def test_no_variant_within_the_limit_chooses_nothing() -> None:
    measured = {
        "deberta": {"seconds_per_epoch": 160.0, "peak_gib": 11.5},
        "deberta-nockpt": {"seconds_per_epoch": 120.0, "peak_gib": 13.0},
        "modernbert": {"seconds_per_epoch": 40.0, "peak_gib": 3.0},
        "modernbert-acc": {"seconds_per_epoch": 55.0, "peak_gib": 3.0},
    }
    assert gpu_bench.choose(measured)["deberta-v3-base"] is None


def test_nondeterminism_is_the_macro_f1_gap_between_two_identical_runs(tmp_path: Path) -> None:
    labels = np.asarray([0, 1, 2, 2, 1, 0], dtype=np.int64)
    a, b = tmp_path / "a", tmp_path / "b"
    for directory, flip in ((a, False), (b, True)):
        directory.mkdir()
        logits = np.eye(3)[labels].astype(np.float32)
        if flip:
            logits[0] = [0.0, 0.0, 1.0]  # one row predicted differently
        np.save(directory / "oof_logits.npy", logits)
        np.save(directory / "oof_labels.npy", labels)
    out = gpu_bench.nondeterminism(a, b)
    assert out["macro_f1_a"] == pytest.approx(1.0)
    assert out["delta_macro_f1"] > 0
    assert out["max_abs_logit_diff"] == pytest.approx(1.0)
    assert out["identical_logits"] is False


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> None:
        self.calls.append(list(argv))
        out = Path(argv[argv.index("--out") + 1])
        out.mkdir(parents=True, exist_ok=True)
        profile = argv[argv.index("--profile") + 1]
        speed = {"deberta": 3.0, "deberta-nockpt": 2.0, "modernbert": 1.0}.get(profile, 1.5)
        peak = 12.0 if profile == "deberta-nockpt" else 5.0
        (out / "metrics.json").write_text(json.dumps(_metrics(speed, peak)), encoding="utf-8")
        np.save(out / "oof_logits.npy", np.eye(3, dtype=np.float32))
        np.save(out / "oof_labels.npy", np.arange(3, dtype=np.int64))


def test_main_measures_every_variant_chooses_and_repeats_fold_0(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    idle = gpu.GpuState(memory_used_mib=400, memory_total_mib=12282, utilization=1, apps=[])
    monkeypatch.setattr(gpu, "query", lambda **_: idle)
    runner = FakeRunner()
    out = tmp_path / "gpu_bench.json"
    gpu_bench.main(["--out", str(out), "--work", str(tmp_path / "work")], runner=runner)

    timed = [c for c in runner.calls if "--max-steps" in c]
    assert [c[c.index("--profile") + 1] for c in timed] == [*gpu_bench.VARIANTS_IN_ORDER]
    for call in timed:
        assert call[call.index("--max-steps") + 1] == str(gpu_bench.WARMUP + gpu_bench.TIMED)
        assert call[call.index("--fold") + 1] == "0"
    repeats = [c for c in runner.calls if "--epochs" in c]
    # The chosen variant of each architecture, twice, fold 0 seed 0, 2 epochs.
    assert [c[c.index("--profile") + 1] for c in repeats] == ["deberta"] * 2 + ["modernbert"] * 2
    for call in repeats:
        assert (call[call.index("--epochs") + 1], call[call.index("--seed") + 1]) == ("2", "0")

    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["choice"] == {"deberta-v3-base": "deberta", "modernbert-base": "modernbert"}
    assert result["peak_limit_gib"] == 11.0
    assert set(result["variants"]) == set(gpu_bench.VARIANTS_IN_ORDER)
    assert result["variants"]["modernbert-1024"]["role"] == "ablation"
    assert result["nondeterminism"]["deberta-v3-base"]["identical_logits"] is True
    printed = capsys.readouterr().out
    assert "deberta-v3-base: deberta" in printed and "modernbert-base: modernbert" in printed


@pytest.mark.slow
def test_max_steps_trains_exactly_that_many_timed_steps(tmp_path: Path) -> None:
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
    out = tmp_path / "bench"
    train_main(["--profile", "tiny", "--fold", "0", "--seed", "0", "--max-steps", "7",
                "--allow-dirty", "--corpus", str(corpus), "--out", str(out)])  # fmt: skip
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert len(metrics["step_seconds"]) == 7
    assert all(s > 0 for s in metrics["step_seconds"])
    assert metrics["max_steps"] == 7
    assert metrics["early_stop_macro_f1_per_epoch"] == []  # no evaluation inside a benchmark
    assert not (out / "_checkpoints").exists()
