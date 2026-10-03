"""`--scanner-features` (training plan B3, F3): wired end to end, or refused."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sieve.model.data import build_splits, load_scanner_findings
from sieve.model.train import load_profile, payload_survival_failures, scanner_findings_for
from sieve.types import CorpusRecord

CONFIG = Path("bench/train.yaml")


def _findings_file(tmp_path: Path, records: list[CorpusRecord]) -> Path:
    rows = [
        {"id": r.id, "flagged": i % 2 == 0, "rule_ids": ["COERCION"] if i % 2 == 0 else []}
        for i, r in enumerate(records)
    ]
    path = tmp_path / "baseline_raw.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def test_findings_load_keyed_by_record_id(tmp_path: Path, dev_records: list[CorpusRecord]) -> None:
    found = load_scanner_findings(_findings_file(tmp_path, dev_records))
    flagged = dev_records[0].id
    assert [f.rule_id for f in found[flagged]] == ["COERCION"]
    assert found[flagged][0].scanner == "cisco_yara"
    assert dev_records[1].id not in found


def test_the_flag_without_a_findings_file_is_refused() -> None:
    with pytest.raises(ValueError, match="--findings"):
        load_profile(CONFIG, "tiny", fold=0, seed=0, scanner_features=True)


def test_a_findings_file_without_the_flag_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--scanner-features"):
        load_profile(CONFIG, "tiny", fold=0, seed=0, findings=tmp_path / "x.json")


def test_scanner_lines_appear_only_with_the_flag_and_the_file(
    tmp_path: Path, dev_records: list[CorpusRecord]
) -> None:
    path = _findings_file(tmp_path, dev_records)
    on = load_profile(CONFIG, "tiny", fold=0, seed=0, scanner_features=True, findings=path)
    off = load_profile(CONFIG, "tiny", fold=0, seed=0)
    with_lines = build_splits(dev_records, fold=0, scanner_findings_by_id=scanner_findings_for(on))
    without = build_splits(dev_records, fold=0, scanner_findings_by_id=scanner_findings_for(off))
    assert scanner_findings_for(off) is None
    assert any("[SCANNER] cisco_yara:COERCION" in t for t in with_lines["train"]["text"])
    assert not any("[SCANNER]" in t for t in without["train"]["text"])
    assert "--scanner-features --findings" in on.training_command(Path("c.jsonl"), Path("out"))


class SpyTokenizer:
    unk_token = None

    def __init__(self) -> None:
        self.texts: list[str] = []

    def __call__(self, text: str, **_: Any) -> dict[str, list[int]]:
        self.texts.append(text)
        return {"input_ids": list(range(len(text)))}

    def decode(self, ids: list[int], **_: Any) -> str:
        return self.texts[-1][: len(ids)]


def test_the_survival_check_serialises_with_the_scanner_lines(
    tmp_path: Path, dev_records: list[CorpusRecord]
) -> None:
    rows = [r for r in dev_records if r.payload_fields]
    found = load_scanner_findings(_findings_file(tmp_path, rows))
    spy = SpyTokenizer()
    payload_survival_failures(rows, spy, max_length=100000, scanner_findings_by_id=found)
    assert any("[SCANNER] cisco_yara:COERCION" in t for t in spy.texts)
