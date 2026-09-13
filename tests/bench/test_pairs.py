"""Minimal pairs: every (host, mutation) pair, so L3 can compute M11."""

from __future__ import annotations

import json
from pathlib import Path

from bench.split import pairs
from sieve.types import CorpusRecord


def test_every_host_mutation_pair_in_mini_fifty_is_found(
    mini_records: list[CorpusRecord], tmp_path: Path
) -> None:
    out = tmp_path / "minimal_pairs.json"
    count = pairs.write(mini_records, out)
    expected = sum(1 for r in mini_records if r.mutation_of)
    assert count == expected

    written = json.loads(out.read_text(encoding="utf-8"))
    assert len(written) == expected
    by_id = {r.id: r for r in mini_records}
    for entry in written:
        assert entry["host"] in by_id
        assert by_id[entry["mutation"]].mutation_of == entry["host"]
        assert set(entry) == {"host", "mutation", "label", "difficulty", "concealment"}


def test_a_pair_whose_host_is_missing_is_not_emitted(
    mini_records: list[CorpusRecord], tmp_path: Path
) -> None:
    mutation = next(r for r in mini_records if r.mutation_of)
    orphan = mutation.model_copy(update={"id": "orphan", "mutation_of": "no-such-host"})
    out = tmp_path / "p.json"
    count = pairs.write([*mini_records, orphan], out)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert "orphan" not in {e["mutation"] for e in written}
    assert count == len(written)


def test_the_file_is_utf8_with_unix_newlines(
    mini_records: list[CorpusRecord], tmp_path: Path
) -> None:
    out = tmp_path / "p.json"
    pairs.write(mini_records, out)
    assert b"\r\n" not in out.read_bytes()
