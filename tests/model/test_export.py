from __future__ import annotations

import json
from pathlib import Path

import pytest

from sieve.model.data import load_corpus
from sieve.model.export import FP32_ONNX, INT8_ONNX, bench_latency, export, main, parity

pytestmark = pytest.mark.filterwarnings("ignore")


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
    main([str(trained_tiny), "--out", str(out), "--latency", "--latency-n", "5"])
    config = json.loads((out / "sieve_config.json").read_text(encoding="utf-8"))
    assert config["onnx_int8_parity"] <= 0.01
    assert config["p50_ms"] > 0
    assert config["p95_ms"] >= config["p50_ms"]
    # The CLI adds keys; it must not quietly fill in an operating point.
    assert config["t_low"] is None and config["t_high"] is None


def test_parity_refuses_a_missing_graph(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parity(tmp_path, tmp_path / "nope.onnx", ["[DESC] x"])
