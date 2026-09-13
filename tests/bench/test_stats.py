"""`make corpus-stats` is a gate, not a report (PRD §9.7)."""

from __future__ import annotations

import json
from pathlib import Path

from bench import stats
from sieve.types import CorpusRecord


def _write(path: Path, records: list[CorpusRecord]) -> Path:
    path.write_text(
        "".join(r.model_dump_json() + "\n" for r in records), encoding="utf-8", newline="\n"
    )
    return path


def _run(tmp_path: Path, records: list[CorpusRecord], *extra: str) -> tuple[int, dict[str, object]]:
    corpus = _write(tmp_path / "corpus.jsonl", records)
    out = tmp_path / "corpus_stats.json"
    code = stats.main(["--corpus", str(corpus), "--out", str(out), "--no-tokenizers", *extra])
    return code, json.loads(out.read_text(encoding="utf-8"))


def test_a_clean_corpus_passes_and_writes_the_tables(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    code, report = _run(tmp_path, mini_records)
    assert code == 0
    counts = report["counts"]
    assert counts["label"] == {"benign": 13, "imperative_benign": 20, "poisoned": 17}
    assert counts["source"]["synthetic_mutation"] == 23
    assert set(counts) >= {
        "label",
        "difficulty",
        "concealment",
        "source",
        "split",
        "schema_fidelity",
        "generator_model",
    }
    assert report["exact_share"] == 1.0
    assert report["gates"]["poisoned_dropped"] == []


def test_the_hand_authored_share_is_reported_against_the_target(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    _, report = _run(tmp_path, mini_records)
    share = report["hand_authored_poisoned_share"]
    assert 0.0 <= float(share) <= 1.0
    assert report["targets"]["hand_authored_poisoned_share"] == 0.25
    assert report["targets"]["exact_share"] == 0.60


def test_a_dropped_poisoned_row_fails_the_gate(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    poisoned = next(r for r in mini_records if r.label == "poisoned")
    dedup_report = tmp_path / "dedup.json"
    dedup_report.write_text(
        json.dumps({"poisoned/synthetic_mutation": [poisoned.id]}),
        encoding="utf-8",
        newline="\n",
    )
    code, report = _run(tmp_path, mini_records, "--dedup-report", str(dedup_report))
    assert code != 0
    assert report["gates"]["poisoned_dropped"] == [poisoned.id]


def test_a_dropped_row_a_mutation_references_fails_the_gate(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    mutation = next(r for r in mini_records if r.mutation_of)
    assert mutation.mutation_of is not None
    dedup_report = tmp_path / "dedup.json"
    dedup_report.write_text(
        json.dumps({"benign/public_repo": [mutation.mutation_of]}),
        encoding="utf-8",
        newline="\n",
    )
    code, report = _run(tmp_path, mini_records, "--dedup-report", str(dedup_report))
    assert code != 0
    assert mutation.mutation_of in report["gates"]["dropped_hosts_referenced"]


def test_dropped_counts_per_stratum_are_carried_through(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    # A benign row that no mutation points at: dropping it trips neither gate, so the
    # per-stratum count is all that should show up.
    referenced = {r.mutation_of for r in mini_records}
    benign = next(
        r
        for r in mini_records
        if r.label == "benign" and not r.mutation_of and r.id not in referenced
    )
    dedup_report = tmp_path / "dedup.json"
    dedup_report.write_text(
        json.dumps({"benign/public_repo": [benign.id]}), encoding="utf-8", newline="\n"
    )
    code, report = _run(tmp_path, mini_records, "--dedup-report", str(dedup_report))
    assert code == 0
    assert report["dropped_per_stratum"]["benign/public_repo"] == 1


def test_excluded_servers_are_counted_by_reason(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    exclusions = tmp_path / "excluded.jsonl"
    exclusions.write_text(
        '{"name":"a/b","reason":"install_requires_scripts","detail":null}\n'
        '{"name":"c/d","reason":"capture_failed","detail":null}\n'
        '{"name":"e/f","reason":"capture_failed","detail":null}\n',
        encoding="utf-8",
        newline="\n",
    )
    _, report = _run(tmp_path, mini_records, "--exclusions", str(exclusions))
    assert report["excluded_servers"] == {"install_requires_scripts": 1, "capture_failed": 2}


def test_the_output_file_has_unix_newlines(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    corpus = _write(tmp_path / "c.jsonl", mini_records)
    out = tmp_path / "s.json"
    stats.main(["--corpus", str(corpus), "--out", str(out), "--no-tokenizers"])
    assert b"\r\n" not in out.read_bytes()


def test_token_tables_are_reported_when_a_tokenizer_is_available(
    mini_records: list[CorpusRecord],
) -> None:
    class FakeTokenizer:
        def encode(self, text: str, add_special_tokens: bool = True) -> list[int]:
            return list(range(len(text.split())))

    table = stats.token_table(mini_records, {"fake": FakeTokenizer()})
    entry = table["fake"]
    assert entry["n"] == len(mini_records)
    assert 0.0 <= entry["share_over_512"] <= 1.0
    assert "p50" in entry and "p95" in entry and "max" in entry


def test_payload_loss_at_512_triggers_the_escalation_note() -> None:
    class ShortTokenizer:
        def encode(self, text: str, add_special_tokens: bool = True) -> list[int]:
            return list(range(len(text)))

    records: list[CorpusRecord] = []
    for i in range(10):
        records.append(
            CorpusRecord(
                id=f"p{i}",
                source="synthetic_mutation",
                source_repo="r",
                source_commit="c",
                source_license="MIT",
                capture_method="authored",
                schema_fidelity="exact",
                server_name="s",
                tool={  # type: ignore[arg-type]
                    "name": "t",
                    "description": "x" * 4000,
                    "inputSchema": {"type": "object"},
                },
                label="poisoned",
                payload_fields=["description"],
                labeller="aditya",
                labelled_at="2026-09-11T00:00:00Z",  # type: ignore[arg-type]
            )
        )
    assert stats.payload_loss_share(records, ShortTokenizer()) > 0.10
