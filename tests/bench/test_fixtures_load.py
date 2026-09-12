"""The shared mini-corpus fixtures must load the committed corpus."""

from __future__ import annotations

from sieve.types import CorpusRecord


def test_mini_record_is_the_first_committed_record(mini_record: CorpusRecord) -> None:
    assert mini_record.id == "mini-000"
    assert mini_record.server_name == "weather"
    assert mini_record.tool.name == "get_current"
    assert mini_record.label == "benign"


def test_mini_records_loads_all_fifty(mini_records: list[CorpusRecord]) -> None:
    assert len(mini_records) == 50
    assert mini_records[0] == mini_record_first(mini_records)


def mini_record_first(records: list[CorpusRecord]) -> CorpusRecord:
    return next(r for r in records if r.id == "mini-000")
