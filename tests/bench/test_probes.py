"""`make probes` (training plan C4, §5.3): the four style-leakage probes, on dev rows only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bench.analysis import probes
from sieve.types import CorpusRecord, Label

SYN = "synthetic_mutation"


def _record(
    record_id: str, label: Label, description: str, source: str, server: str, split: str = "dev"
) -> CorpusRecord:
    return CorpusRecord(
        id=record_id,
        source=source,  # type: ignore[arg-type]
        source_repo="https://github.com/example/fixture",
        source_commit="fixture",
        source_license="MIT",
        capture_method="authored",
        schema_fidelity="exact",
        server_name=server,
        tool={"name": "t", "description": description, "inputSchema": {"type": "object"}},  # type: ignore[arg-type]
        label=label,
        labeller="aditya",
        labelled_at="2026-09-11T00:00:00Z",  # type: ignore[arg-type]
        split=split,  # type: ignore[arg-type]
        fold=0 if split == "dev" else None,
    )


def _corpus(
    *, tell: str = "", surface_tell: bool = False, n_servers: int = 20
) -> list[CorpusRecord]:
    """Real and synthetic rows of every label, one server each; `tell` marks synthetic text."""
    rng = np.random.default_rng(0)
    out: list[CorpusRecord] = []
    words = ["alpha", "beta", "gamma", "delta", "eps", "zeta", "eta", "theta"]
    for s in range(n_servers):
        for label in ("benign", "imperative_benign", "poisoned"):
            for k in range(2):
                body = " ".join(rng.choice(words, size=6)) + f" {s}{k}."
                if label != "poisoned":
                    out.append(_record(f"r-{label}-{s}-{k}", label, body, "public_repo", f"s{s}"))  # type: ignore[arg-type]
                syn_body = f"{tell} {body}".strip()
                if surface_tell:
                    syn_body += "\n`x` `y` `z`\n" + "LONG TAIL " * 30
                out.append(_record(f"y-{label}-{s}-{k}", label, syn_body, SYN, f"s{s}"))  # type: ignore[arg-type]
    return out


def test_a_style_tell_fails_every_tfidf_probe() -> None:
    result = probes.run_all(_corpus(tell="notwithstanding"), lengths=None)
    assert result["imperative"]["auroc"] > 0.9
    assert result["benign"]["auroc"] > 0.9
    assert result["transfer"]["auroc"] > 0.9  # "sounds synthetic" == "poisoned"
    assert result["passed"] is False


def test_without_a_tell_the_tfidf_probes_sit_near_chance() -> None:
    result = probes.run_all(_corpus(), lengths=None)
    for name in ("imperative", "benign"):
        assert 0.3 < result[name]["auroc"] < 0.7, name


def test_the_surface_probe_sees_shape_not_words() -> None:
    assert probes.surface_probe(_corpus(surface_tell=True))["auroc"] > 0.9
    assert probes.surface_probe(_corpus())["auroc"] < 0.75


def test_surface_features_are_the_audits_six() -> None:
    assert probes.SURFACE_NAMES == (
        "log_len", "newlines", "caps_ratio", "sentences", "non_ascii", "backticks",
    )  # fmt: skip
    f = probes.surface("Hi. `a` B\nc!")
    assert f[1] == 1.0 and f[3] == 2.0 and f[5] == 2.0


def test_the_length_probe_is_symmetric_because_length_has_no_direction() -> None:
    records = [r for r in _corpus() if r.label != "poisoned"]
    lengths = np.array([5 if r.label == "imperative_benign" else 50 for r in records])
    result = probes.length_probe(records, lengths)
    assert result["auroc"] == pytest.approx(0.0)
    assert result["separation"] == pytest.approx(1.0)
    assert result["passed"] is False


def test_the_transfer_probe_never_trains_on_a_poisoned_row(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    real_fit = probes._tfidf_model

    def spy() -> Any:
        model = real_fit()
        original = model.fit

        def fit(texts: list[str], y: Any) -> Any:
            seen.append(list(texts))
            return original(texts, y)

        model.fit = fit
        return model

    monkeypatch.setattr(probes, "_tfidf_model", spy)
    records = _corpus(tell="notwithstanding")
    probes.transfer_probe(records)
    poisoned_texts = {probes.text_of(r) for r in records if r.label == "poisoned"}
    assert seen and all(not (set(t) & poisoned_texts) for t in seen)


def test_only_dev_rows_are_probed_unless_all_splits() -> None:
    dev = _corpus()
    other = [
        _record(f"o-{i}", "imperative_benign", "notwithstanding x", SYN, f"o{i}", split="eval_only")
        for i in range(30)
    ]
    assert (
        probes.run_all(dev + other, lengths=None)["imperative"]["n"]
        == probes.run_all(dev, lengths=None)["imperative"]["n"]
    )
    wide = probes.run_all(dev + other, lengths=None, all_splits=True)
    assert wide["imperative"]["n"] == probes.run_all(dev, lengths=None)["imperative"]["n"] + 30
    assert wide["rows"] == "all_splits"


def test_main_writes_the_results_and_corpus_stats_and_exits_1_on_a_leak(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        "".join(r.model_dump_json() + "\n" for r in _corpus(tell="notwithstanding")),
        encoding="utf-8",
    )
    stats = tmp_path / "corpus_stats.json"
    stats.write_text(json.dumps({"rows": 1}), encoding="utf-8")
    out = tmp_path / "leakage_probe.json"
    code = probes.main(
        ["--corpus", str(corpus), "--out", str(out), "--stats", str(stats), "--no-length"]
    )
    assert code == 1
    written = json.loads(out.read_text(encoding="utf-8"))
    # `publish.py` reads these four keys; they stay the imperative probe's.
    assert {"auroc", "n", "gate", "passed"} <= set(written)
    assert written["auroc"] == written["imperative"]["auroc"]
    assert written["passed"] is False
    merged = json.loads(stats.read_text(encoding="utf-8"))
    assert merged["rows"] == 1
    assert merged["leakage_probes"]["transfer"] == written["transfer"]


def test_main_exits_0_when_every_probe_passes(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("".join(r.model_dump_json() + "\n" for r in _corpus()), encoding="utf-8")
    out = tmp_path / "p.json"
    code = probes.main(["--corpus", str(corpus), "--out", str(out),
                        "--stats", str(tmp_path / "s.json"), "--no-length"])  # fmt: skip
    written = json.loads(out.read_text(encoding="utf-8"))
    assert code == (0 if written["passed"] else 1)
    assert written["length"] is None
