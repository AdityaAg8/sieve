"""`make corpus-stats` is a gate, not a report (PRD §9.7)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bench import stats
from sieve.serialise import serialise
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
    # The artefacts the pipeline actually produces: dedup already removed the row from the
    # corpus, so the gate can only learn its label from the report.
    poisoned = next(r for r in mini_records if r.label == "poisoned")
    survivors = [r for r in mini_records if r.id != poisoned.id]
    dedup_report = tmp_path / "dedup.json"
    dedup_report.write_text(
        json.dumps(
            {
                "poisoned/synthetic_mutation": [
                    {
                        "id": poisoned.id,
                        "label": "poisoned",
                        "source": poisoned.source,
                        "mutation_of": poisoned.mutation_of,
                    }
                ]
            }
        ),
        encoding="utf-8",
        newline="\n",
    )
    code, report = _run(tmp_path, survivors, "--dedup-report", str(dedup_report))
    assert code != 0
    assert report["gates"]["poisoned_dropped"] == [poisoned.id]


def test_a_dropped_row_a_mutation_references_fails_the_gate(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    mutation = next(r for r in mini_records if r.mutation_of)
    assert mutation.mutation_of is not None
    dedup_report = tmp_path / "dedup.json"
    dedup_report.write_text(
        json.dumps(
            {
                "benign/public_repo": [
                    {
                        "id": mutation.mutation_of,
                        "label": "benign",
                        "source": "public_repo",
                        "mutation_of": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
        newline="\n",
    )
    survivors = [r for r in mini_records if r.id != mutation.mutation_of]
    code, report = _run(tmp_path, survivors, "--dedup-report", str(dedup_report))
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
        json.dumps(
            {
                "benign/public_repo": [
                    {
                        "id": benign.id,
                        "label": "benign",
                        "source": "public_repo",
                        "mutation_of": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
        newline="\n",
    )
    survivors = [r for r in mini_records if r.id != benign.id]
    code, report = _run(tmp_path, survivors, "--dedup-report", str(dedup_report))
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
    # The MiniLM statistic is emitted once, by MiniLM's own tokenizer — not per tokenizer.
    assert "share_over_minilm_256" not in entry


def test_payload_loss_at_512_triggers_the_escalation_note() -> None:
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
    assert stats.payload_loss_share(records, CharTokenizer()) > 0.10


# -- fix round 1: payload loss is about the payload, not the record length ----


class CharTokenizer:
    """A fast-tokenizer stand-in: one token per character, so offsets are exact and offline."""

    def encode(self, text: str, add_special_tokens: bool = True) -> list[int]:
        return list(range(len(text)))

    def __call__(
        self,
        text: str,
        return_offsets_mapping: bool = False,
        add_special_tokens: bool = True,
    ) -> dict[str, list[Any]]:
        out: dict[str, list[Any]] = {"input_ids": list(range(len(text)))}
        if return_offsets_mapping:
            out["offset_mapping"] = [(i, i + 1) for i in range(len(text))]
        return out


def _poisoned(record_id: str, *, description: str, siblings: list[str], fields: list[str]):
    return CorpusRecord(
        id=record_id,
        source="synthetic_mutation",
        source_repo="r",
        source_commit="c",
        source_license="MIT",
        capture_method="authored",
        schema_fidelity="exact",
        server_name="s",
        tool={  # type: ignore[arg-type]
            "name": "t",
            "description": description,
            "inputSchema": {"type": "object"},
            "annotations": {"title": "TAIL-PAYLOAD " + "z" * 200},
        },
        sibling_tool_names=siblings,
        label="poisoned",
        payload_fields=fields,  # type: ignore[arg-type]
        labeller="aditya",
        labelled_at="2026-09-11T00:00:00Z",  # type: ignore[arg-type]
    )


def test_an_over_length_row_whose_payload_survives_the_cut_is_not_counted() -> None:
    # [DESC] comes before [SIBLINGS] in the serialisation, so this row is far over 512
    # characters yet loses no payload. Counting record length would count it.
    survives = _poisoned(
        "survives",
        description="short payload",
        siblings=["sibling" + str(i) for i in range(200)],
        fields=["description"],
    )
    assert len(serialise(survives.to_tool_record())) > 512
    assert stats.payload_loss_share([survives], CharTokenizer()) == 0.0


def test_an_over_length_row_whose_payload_is_cut_is_counted() -> None:
    # The payload is in `annotations`, which the serialiser emits after the parameters and
    # therefore past the 512-character cut.
    cut = _poisoned(
        "cut",
        description="x" * 600,
        siblings=[],
        fields=["annotations"],
    )
    assert stats.payload_loss_share([cut], CharTokenizer()) == 1.0


def test_payload_loss_share_ignores_rows_with_no_payload_fields() -> None:
    assert stats.payload_loss_share([], CharTokenizer()) == 0.0


def test_the_escalation_is_computed_by_deberta_or_not_at_all(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    corpus = _write(tmp_path / "c.jsonl", mini_records)
    out = tmp_path / "s.json"
    report = stats.build_report(
        mini_records,
        dropped_per_stratum={},
        exclusions_path=tmp_path / "none.jsonl",
        tokenizers={"modernbert": CharTokenizer()},
        unavailable={"deberta-v3": "ImportError: no sentencepiece"},
    )
    # ModernBERT must never be the tokenizer that decides "escalate to ModernBERT".
    assert "payload_loss_share_at_512" not in report
    assert "escalate_to_modernbert_1024" not in report
    assert report["tokenizers_unavailable"]["deberta-v3"].startswith("ImportError")
    assert corpus.exists() and out.parent.exists()


def test_the_escalation_records_which_tokenizer_produced_it(
    mini_records: list[CorpusRecord], tmp_path: Path
) -> None:
    report = stats.build_report(
        mini_records,
        dropped_per_stratum={},
        exclusions_path=tmp_path / "none.jsonl",
        tokenizers={"deberta-v3": CharTokenizer(), "modernbert": CharTokenizer()},
        unavailable={},
    )
    assert report["payload_loss_tokenizer"] == "deberta-v3"
    assert "payload_loss_share_at_512" in report


def test_the_minilm_share_is_measured_with_minilms_own_tokenizer(
    mini_records: list[CorpusRecord],
) -> None:
    from transformers import AutoTokenizer

    from bench.split.dedup import MiniLMEmbedder

    minilm = AutoTokenizer.from_pretrained(MiniLMEmbedder.MODEL)
    share = stats.minilm_over_256_share(mini_records, minilm)
    assert 0.0 <= share <= 1.0
    # The mini corpus is short, so nothing is over MiniLM's 256-token dedup limit. A
    # DeBERTa/ModernBERT length table cannot answer this question on MiniLM's behalf.
    assert share == 0.0
    long_row = mini_records[0].model_copy(deep=True)
    long_row.tool.description = "word " * 400
    assert stats.minilm_over_256_share([long_row], minilm) == 1.0


def test_cross_boundary_near_duplicates_are_disclosed(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    from bench.split.split import assign

    assigned = assign(mini_records)
    dev = next(r for r in assigned if r.split == "dev")
    holdout = next(r for r in assigned if r.split == "holdout")
    planted = holdout.model_copy(deep=True)
    planted.tool = dev.tool.model_copy(deep=True)
    planted.sibling_tool_names = list(dev.sibling_tool_names)
    records = [*assigned, planted.model_copy(update={"id": "planted"})]

    corpus = _write(tmp_path / "c.jsonl", records)
    out = tmp_path / "s.json"
    code = stats.main(
        [
            "--corpus",
            str(corpus),
            "--out",
            str(out),
            "--no-tokenizers",
            "--embedder",
            "hash",
        ]
    )
    assert code == 0  # a disclosure, never a gate
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["cross_boundary_near_duplicate_pairs"] >= 1


def test_the_printed_tables_survive_a_report_with_tokenizers(
    tmp_path: Path, mini_records: list[CorpusRecord], capsys: pytest.CaptureFixture[str]
) -> None:
    # The `--no-tokenizers` path leaves `tokens` empty, so the per-tokenizer print loop was
    # never executed by a test and a stale key in it reached the end-to-end run instead.
    report = stats.build_report(
        mini_records,
        dropped_per_stratum={},
        exclusions_path=tmp_path / "none.jsonl",
        tokenizers={"deberta-v3": CharTokenizer(), "minilm": CharTokenizer()},
        unavailable={"modernbert": "OSError: offline"},
        cross_boundary_pairs=2,
    )
    stats._print_tables(report)
    printed = capsys.readouterr().out
    assert "tokens [deberta-v3]" in printed
    assert "MiniLM's 256-token dedup limit" in printed
    assert "payload lost at 512 tokens" in printed
    assert "cross-boundary near-duplicate pairs: 2" in printed
    assert "tokenizer modernbert unavailable" in printed


def test_a_poisoned_row_dropped_with_its_host_is_listed_not_failed(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    """C8: a dev host that duplicates a sealed row goes, and its mutations go with it.

    That is a deliberate leakage drop, listed in the report; the gate still fails on a poisoned
    row dropped as a near-duplicate (the v2.0 bug it exists for).
    """
    mutation = next(r for r in mini_records if r.mutation_of and r.label == "poisoned")
    host_id = mutation.mutation_of
    gone = {mutation.id, host_id}
    survivors = [r for r in mini_records if r.id not in gone and r.mutation_of != host_id]
    rows = [
        {"id": host_id, "label": "benign", "source": "public_repo", "mutation_of": None,
         "reason": "sealed_twin"},
        {"id": mutation.id, "label": "poisoned", "source": mutation.source,
         "mutation_of": host_id, "reason": "host_dropped"},
    ]  # fmt: skip
    dedup_report = tmp_path / "dedup.json"
    dedup_report.write_text(json.dumps({"mixed": rows}), encoding="utf-8", newline="\n")
    code, report = _run(tmp_path, survivors, "--dedup-report", str(dedup_report))
    assert report["gates"]["poisoned_dropped"] == []
    assert report["gates"]["dropped_for_leakage"] == sorted(gone)
    assert code == 0
