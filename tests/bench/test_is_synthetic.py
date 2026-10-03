"""`is_synthetic` and the build-time view asserts (training plan G5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.collect.ood_rows import OodRow, write_rows
from bench.split import split as sp
from sieve.types import CorpusRecord


def _row(
    rid: str,
    source: str = "public_repo",
    *,
    split: str | None = "dev",
    generator: str | None = None,
    label: str = "benign",
) -> CorpusRecord:
    return CorpusRecord(
        id=rid,
        source=source,  # type: ignore[arg-type]
        source_repo="https://example.invalid/r",
        source_commit="c",
        source_license="MIT",
        capture_method="authored",
        schema_fidelity="exact",
        server_name="s",
        tool={"name": "t", "description": rid, "inputSchema": {"type": "object"}},  # type: ignore[arg-type]
        label=label,  # type: ignore[arg-type]
        labeller="aditya",
        labelled_at="2026-09-21T00:00:00Z",  # type: ignore[arg-type]
        generator_model=generator,
        split=split,  # type: ignore[arg-type]
    )


def test_is_synthetic_is_derived_not_stored() -> None:
    assert _row("a").is_synthetic is False
    assert _row("b", "synthetic_mutation", generator="qwen").is_synthetic is True
    assert _row("c", generator="human").is_synthetic is True  # generated text on any source
    assert "is_synthetic" not in _row("a").model_dump()


def test_the_views_name_every_sealed_row_once() -> None:
    rows = [
        _row("dev-1"),
        _row("t-1", split="holdout"),
        _row("t-2", "wild_fp", split="holdout"),
        _row("hs-1", "synthetic_mutation", split="holdout", generator="gemma", label="poisoned"),
        _row("rp-1", "real_poisoned", split="eval_only", label="poisoned"),
    ]
    views = sp.sealed_views(rows)
    assert [r.id for r in views["test_id"]] == ["t-1", "t-2"]
    assert [r.id for r in views["holdout_synthetic"]] == ["hs-1"]
    assert [r.id for r in views["real_poisoned"]] == ["rp-1"]


def test_a_synthetic_row_posing_as_real_in_test_id_is_refused() -> None:
    rows = [_row("t-1", split="holdout", generator="qwen")]  # real source, generated text
    with pytest.raises(sp.SyntheticLeakError, match="test_id"):
        sp.check_views(rows)


def test_a_synthetic_real_poisoned_row_is_refused() -> None:
    rows = [_row("rp-1", "real_poisoned", split="eval_only", generator="human", label="poisoned")]
    with pytest.raises(sp.SyntheticLeakError, match="real_poisoned"):
        sp.check_views(rows)


def test_split_main_runs_the_asserts(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        _row("x", split=None, generator="qwen").model_dump_json() + "\n", encoding="utf-8"
    )
    with pytest.raises(sp.SyntheticLeakError):
        sp.main(["--corpus", str(corpus), "--out", str(tmp_path / "out.jsonl"), "--holdout", "1.0"])


def test_no_ood_or_val_row_can_be_written_synthetic(tmp_path: Path) -> None:
    good = OodRow("v-1", "mtp", "s", {"name": "t", "inputSchema": {}}, poisoned=False)
    bad = OodRow("v-2", "synthetic_mutation", "s", {"name": "t", "inputSchema": {}}, poisoned=True)
    assert good.is_synthetic is False and bad.is_synthetic is True
    write_rows(tmp_path / "ok.jsonl", [good])
    with pytest.raises(ValueError, match="synthetic"):
        write_rows(tmp_path / "bad.jsonl", [good, bad])
