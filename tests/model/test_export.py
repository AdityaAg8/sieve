from __future__ import annotations

import json
from pathlib import Path

import pytest

from sieve.model.data import load_corpus
from sieve.model.export import FP32_ONNX, INT8_ONNX, bench_latency, export, main, parity

# Targeted, not blanket: the project turns warnings into errors on purpose. Only the
# noise `transformers`/`torch` emit on a CPU training or export run is filtered, so a
# new deprecation in this lane's own code still fails the run.
pytestmark = [
    pytest.mark.filterwarnings("ignore::FutureWarning"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
    pytest.mark.filterwarnings("ignore::UserWarning"),
    # torch.onnx traces the graph and warns about constant tensors on every export.
    pytest.mark.filterwarnings("ignore::torch.jit.TracerWarning"),
]


def _texts(n: int = 20) -> list[str]:
    from sieve.serialise import serialise

    records = load_corpus(Path("fixtures/corpus/mini-50.jsonl"))[:n]
    return [serialise(r.to_tool_record()) for r in records]


@pytest.fixture(scope="module")
def trained_tiny(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One `tiny` artefact for the whole module; training it is the expensive part."""
    from sieve.model.train import main as train_main

    root = tmp_path_factory.mktemp("export")
    corpus = root / "corpus.jsonl"
    records = load_corpus(Path("fixtures/corpus/mini-50.jsonl"))
    servers = sorted({r.server_name for r in records})
    fold_of = {name: i % 5 for i, name in enumerate(servers)}
    corpus.write_text(
        "".join(
            r.model_copy(update={"split": "dev", "fold": fold_of[r.server_name]}).model_dump_json()
            + "\n"
            for r in records
        ),
        encoding="utf-8",
        newline="\n",
    )
    out = root / "tiny-f0-s0"
    train_main(
        [
            "--config", "bench/train.yaml", "--profile", "tiny", "--fold", "0",
            "--seed", "0", "--corpus", str(corpus), "--out", str(out),
        ]
    )  # fmt: skip
    return out


@pytest.mark.slow
def test_export_writes_both_graphs_and_the_int8_is_smaller(
    trained_tiny: Path, tmp_path: Path
) -> None:
    out = export(trained_tiny, tmp_path / "onnx")
    assert out == tmp_path / "onnx"
    fp32, int8 = out / FP32_ONNX, out / INT8_ONNX
    assert fp32.exists() and int8.exists()
    assert int8.stat().st_size < fp32.stat().st_size
    assert (out / "sieve_config.json").exists()
    assert (out / "tokenizer_config.json").exists()


@pytest.mark.slow
def test_export_can_skip_quantisation(trained_tiny: Path, tmp_path: Path) -> None:
    out = export(trained_tiny, tmp_path / "onnx", quantize=False)
    assert (out / FP32_ONNX).exists()
    assert not (out / INT8_ONNX).exists()


@pytest.mark.slow
def test_int8_parity_with_the_torch_model(trained_tiny: Path, tmp_path: Path) -> None:
    out = export(trained_tiny, tmp_path / "onnx")
    delta = parity(trained_tiny, out / INT8_ONNX, _texts(20))
    assert delta <= 0.01, f"max |dp| = {delta}"


@pytest.mark.slow
def test_fp32_parity_is_tighter_still(trained_tiny: Path, tmp_path: Path) -> None:
    out = export(trained_tiny, tmp_path / "onnx", quantize=False)
    assert parity(trained_tiny, out / FP32_ONNX, _texts(20)) <= 1e-3


@pytest.mark.slow
def test_bench_latency_returns_positive_percentiles(trained_tiny: Path, tmp_path: Path) -> None:
    out = export(trained_tiny, tmp_path / "onnx")
    timings = bench_latency(out / INT8_ONNX, _texts(1)[0], n=10)
    assert set(timings) == {"p50_ms", "p95_ms"}
    assert 0.0 < timings["p50_ms"] <= timings["p95_ms"]


@pytest.mark.slow
def test_cli_appends_parity_and_latency_to_the_artefact(trained_tiny: Path, tmp_path: Path) -> None:
    out = tmp_path / "onnx"
    main([str(trained_tiny), "--out", str(out), "--latency", "--latency-n", "5",
          "--results-dir", str(tmp_path / "results")])  # fmt: skip
    config = json.loads((out / "sieve_config.json").read_text(encoding="utf-8"))
    assert config["onnx_int8_parity"] <= 0.01
    assert config["p50_ms"] > 0
    assert config["p95_ms"] >= config["p50_ms"]
    # The CLI adds keys; it must not quietly fill in an operating point.
    assert config["t_low"] is None and config["t_high"] is None

    # The SOURCE artefact gets the same numbers, because that is where `sieve.model.select`
    # and the ablation matrix look for `p95_ms`.
    source = json.loads((trained_tiny / "sieve_config.json").read_text(encoding="utf-8"))
    assert source["p95_ms"] == config["p95_ms"]
    assert source["p50_ms"] == config["p50_ms"]
    assert source["onnx_int8_parity"] == config["onnx_int8_parity"]
    # ...without losing what training wrote there.
    assert source["model_version"] == config["model_version"]
    assert source["training_command"].startswith("python -m sieve.model.train")


def test_parity_refuses_a_missing_graph(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parity(tmp_path, tmp_path / "nope.onnx", ["[DESC] x"])


# ------------------------------------------------------------------- enforced parity (H6)


@pytest.mark.slow
def test_parity_runs_on_the_dev_set_and_is_written_to_results(
    trained_tiny: Path, tmp_path: Path
) -> None:
    from sieve.model.export import PARITY_REPORT

    results = tmp_path / "results"
    main([str(trained_tiny), "--out", str(tmp_path / "onnx"), "--results-dir", str(results),
          "--corpus", "fixtures/corpus/mini-50.jsonl"])  # fmt: skip
    report = json.loads((results / PARITY_REPORT).read_text(encoding="utf-8"))
    assert report["n"] == 50 and report["passed"] is True
    assert report["max_abs_delta"] <= 0.01
    assert {"flips_t_low", "flips_t_high"} <= set(report)


@pytest.mark.slow
def test_parity_above_tolerance_refuses_the_export(
    trained_tiny: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sieve.model import export as ex

    monkeypatch.setattr(ex, "PARITY_TOLERANCE", -1.0)
    with pytest.raises(ex.ParityError, match="refused"):
        main([str(trained_tiny), "--out", str(tmp_path / "onnx"), "--results-dir",
              str(tmp_path / "r"), "--corpus", "fixtures/corpus/mini-50.jsonl"])  # fmt: skip
    assert (
        json.loads((tmp_path / "r" / ex.PARITY_REPORT).read_text(encoding="utf-8"))["passed"]
        is False
    )
    # Z2: the refused graph is removed, so nothing can load it.
    assert not (tmp_path / "onnx" / INT8_ONNX).exists()
    assert not (tmp_path / "onnx" / FP32_ONNX).exists()


@pytest.mark.slow
def test_an_fp32_export_removes_a_stale_int8_graph(trained_tiny: Path, tmp_path: Path) -> None:
    export(trained_tiny, tmp_path / "onnx")
    assert (tmp_path / "onnx" / INT8_ONNX).exists()
    export(trained_tiny, tmp_path / "onnx", quantize=False)
    assert not (tmp_path / "onnx" / INT8_ONNX).exists(), "infer would have served the stale int8"


def test_parity_counts_verdict_flips_at_each_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import numpy as np

    from sieve.model import export as ex

    (tmp_path / "g.onnx").write_bytes(b"x")
    (tmp_path / "sieve_config.json").write_text(json.dumps({"t_low": 0.3, "t_high": 0.8}),
                                                encoding="utf-8")  # fmt: skip
    torch_p = np.array([[0.7, 0.0, 0.29], [0.1, 0.1, 0.81], [0.5, 0.0, 0.5]])
    onnx_p = np.array([[0.69, 0.0, 0.31], [0.1, 0.11, 0.79], [0.5, 0.0, 0.5]])
    monkeypatch.setattr(ex, "_torch_probs", lambda d, t: torch_p)
    monkeypatch.setattr(ex, "_onnx_probs", lambda g, t, model_dir: onnx_p)
    report = ex.parity_report(tmp_path, tmp_path / "g.onnx", ["a", "b", "c"])
    assert (report["flips_t_low"], report["flips_t_high"]) == (1, 1)
    assert report["max_abs_delta"] == pytest.approx(0.02)
