"""`make corpus-stats` — a gate, not a report (PRD §9.7).

Two things here refuse to be advisory. A `poisoned` row lost to deduplication is a silently
shrunken positive class, and a dropped record that some mutation still points at is a
dangling minimal pair; either one makes every downstream number wrong in a direction nobody
would notice. Both exit non-zero.

The token tables answer the question PRD §11.2 leaves open: DeBERTa-v3 truncates at 512, and
the escalation to ModernBERT at 1,024 is triggered by evidence — the share of `poisoned` rows
that would lose a `payload_fields` entry at 512 — not by preference.
"""

from __future__ import annotations

import argparse
import collections
import json
import logging
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from bench.collect.sandbox_capture import EXCLUSIONS_PATH, load_exclusions
from bench.split.dedup import MiniLMEmbedder, text_of
from sieve.serialise import serialise
from sieve.types import CorpusRecord

log = logging.getLogger(__name__)

DEFAULT_CORPUS = Path("bench/data/corpus.jsonl")
DEFAULT_OUT = Path("bench/results/corpus_stats.json")
DEFAULT_DEDUP_REPORT = Path("bench/results/dedup_dropped.json")

TOKENIZERS: dict[str, str] = {
    "deberta-v3": "microsoft/deberta-v3-base",
    "modernbert": "answerdotai/ModernBERT-base",
}
TRUNCATION_LIMIT = 512
PAYLOAD_LOSS_ESCALATION = 0.10
EXACT_SHARE_TARGET = 0.60
HAND_AUTHORED_TARGET = 0.25


class Tokenizer(Protocol):
    def encode(self, text: str, add_special_tokens: bool = True) -> list[int]: ...


def _counts(records: list[CorpusRecord]) -> dict[str, dict[str, int]]:
    fields = {
        "label": lambda r: r.label,
        "difficulty": lambda r: str(r.difficulty),
        "concealment": lambda r: r.concealment,
        "source": lambda r: r.source,
        "split": lambda r: str(r.split),
        "schema_fidelity": lambda r: r.schema_fidelity,
        "capture_method": lambda r: r.capture_method,
        "generator_model": lambda r: str(r.generator_model),
    }
    return {
        name: dict(sorted(collections.Counter(fn(r) for r in records).items()))
        for name, fn in fields.items()
    }


def _crossed(records: list[CorpusRecord]) -> dict[str, int]:
    """class × tier × concealment × source × split × fidelity, the cells that are non-empty."""
    cells = collections.Counter(
        "|".join(
            (r.label, str(r.difficulty), r.concealment, r.source, str(r.split), r.schema_fidelity)
        )
        for r in records
    )
    return dict(sorted(cells.items()))


def token_table(
    records: list[CorpusRecord], tokenizers: dict[str, Tokenizer]
) -> dict[str, dict[str, Any]]:
    """Tokens per record and the share truncated at 512, per tokenizer."""
    texts = [text_of(r) for r in records]
    out: dict[str, dict[str, Any]] = {}
    for name, tokenizer in tokenizers.items():
        lengths = np.array([len(tokenizer.encode(t)) for t in texts], dtype=float)
        out[name] = {
            "n": int(lengths.size),
            "p50": float(np.percentile(lengths, 50)),
            "p95": float(np.percentile(lengths, 95)),
            "max": float(lengths.max()) if lengths.size else 0.0,
            "share_over_512": float((lengths > TRUNCATION_LIMIT).mean()) if lengths.size else 0.0,
            "share_over_minilm_256": (
                float((lengths > MiniLMEmbedder.MAX_TOKENS).mean()) if lengths.size else 0.0
            ),
        }
    return out


def payload_loss_share(records: list[CorpusRecord], tokenizer: Tokenizer) -> float:
    """Share of `poisoned` rows that would lose a `payload_fields` entry at 512 tokens.

    The serialiser puts payload-bearing fields first, so a row loses evidence only when the
    512-token cut falls before the last marked payload field.
    """
    poisoned = [r for r in records if r.label == "poisoned" and r.payload_fields]
    if not poisoned:
        return 0.0
    lost = 0
    for record in poisoned:
        text = serialise(record.to_tool_record())
        kept = tokenizer.encode(text)[:TRUNCATION_LIMIT]
        # Re-tokenising the prefix is not possible without a decoder, so compare lengths:
        # a row whose full serialisation exceeds the limit has lost its trailing fields.
        if len(tokenizer.encode(text)) > len(kept):
            lost += 1
    return lost / len(poisoned)


def _load_tokenizers(names: dict[str, str]) -> tuple[dict[str, Tokenizer], dict[str, str]]:
    loaded: dict[str, Tokenizer] = {}
    unavailable: dict[str, str] = {}
    for name, model in names.items():
        try:
            from transformers import AutoTokenizer

            loaded[name] = AutoTokenizer.from_pretrained(model)
        except Exception as exc:  # noqa: BLE001 (any failure means "not measurable here")
            unavailable[name] = f"{type(exc).__name__}: {exc}"[:200]
            log.warning("tokenizer %s unavailable: %s", model, exc)
    return loaded, unavailable


def build_report(
    records: list[CorpusRecord],
    *,
    dropped_per_stratum: dict[str, list[str]],
    exclusions_path: Path,
    tokenizers: dict[str, Tokenizer],
    unavailable: dict[str, str],
) -> dict[str, Any]:
    by_id = {r.id: r for r in records}
    dropped_ids = [rid for ids in dropped_per_stratum.values() for rid in ids]
    referenced = {r.mutation_of for r in records if r.mutation_of}

    poisoned_dropped = sorted(
        rid for rid in dropped_ids if rid in by_id and by_id[rid].label == "poisoned"
    )
    dropped_hosts_referenced = sorted(set(dropped_ids) & referenced)

    exact = [r for r in records if r.schema_fidelity == "exact"]
    poisoned = [r for r in records if r.label == "poisoned"]
    hand = [r for r in poisoned if r.generator_model == "human"]

    report: dict[str, Any] = {
        "n": len(records),
        "counts": _counts(records),
        "crossed": _crossed(records),
        "exact_share": len(exact) / len(records) if records else 0.0,
        "hand_authored_poisoned_share": len(hand) / len(poisoned) if poisoned else 0.0,
        "targets": {
            "exact_share": EXACT_SHARE_TARGET,
            "hand_authored_poisoned_share": HAND_AUTHORED_TARGET,
        },
        "dropped_per_stratum": {k: len(v) for k, v in sorted(dropped_per_stratum.items())},
        "excluded_servers": dict(
            sorted(collections.Counter(e.reason for e in load_exclusions(exclusions_path)).items())
        ),
        "tokens": token_table(records, tokenizers),
        "tokenizers_unavailable": unavailable,
        "gates": {
            "poisoned_dropped": poisoned_dropped,
            "dropped_hosts_referenced": dropped_hosts_referenced,
        },
    }
    if tokenizers:
        first = next(iter(tokenizers.values()))
        share = payload_loss_share(records, first)
        report["payload_loss_share_at_512"] = share
        report["escalate_to_modernbert_1024"] = share > PAYLOAD_LOSS_ESCALATION
    return report


def _print_tables(report: dict[str, Any]) -> None:
    print(f"records: {report['n']}")
    for name, table in report["counts"].items():
        print(f"\n{name}")
        for key, value in table.items():
            print(f"  {key:<24} {value:>5}")
    print(f"\nexact fidelity share: {report['exact_share']:.3f} (target >= {EXACT_SHARE_TARGET})")
    print(
        f"hand-authored share of poisoned: {report['hand_authored_poisoned_share']:.3f} "
        f"(target >= {HAND_AUTHORED_TARGET})"
    )
    for name, table in report["tokens"].items():
        print(
            f"\ntokens [{name}] p50={table['p50']:.0f} p95={table['p95']:.0f} "
            f"max={table['max']:.0f} over512={table['share_over_512']:.3f} "
            f"over_minilm256={table['share_over_minilm_256']:.3f}"
        )
    for name, why in report["tokenizers_unavailable"].items():
        print(f"tokenizer {name} unavailable: {why}")
    if report.get("escalate_to_modernbert_1024"):
        print(
            "\nPRD §11.2 escalation: more than 10% of poisoned rows lose a payload field at "
            "512 tokens. ModernBERT at 1,024 becomes the primary encoder."
        )
    if report["excluded_servers"]:
        print(f"\nexcluded servers: {report['excluded_servers']}")


def _load(path: Path) -> list[CorpusRecord]:
    return [
        CorpusRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bench.stats",
        description="Corpus composition tables, and the gates that make them binding.",
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--dedup-report", type=Path, default=DEFAULT_DEDUP_REPORT)
    parser.add_argument("--exclusions", type=Path, default=EXCLUSIONS_PATH)
    parser.add_argument(
        "--no-tokenizers",
        action="store_true",
        help="skip the token-length tables (they download tokenizers from the Hub)",
    )
    args = parser.parse_args(argv)

    records = _load(args.corpus)
    dropped: dict[str, list[str]] = (
        json.loads(args.dedup_report.read_text(encoding="utf-8"))
        if args.dedup_report.exists()
        else {}
    )
    tokenizers, unavailable = ({}, {}) if args.no_tokenizers else _load_tokenizers(TOKENIZERS)
    report = build_report(
        records,
        dropped_per_stratum=dropped,
        exclusions_path=args.exclusions,
        tokenizers=tokenizers,
        unavailable=unavailable,
    )
    _print_tables(report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")

    gates = report["gates"]
    failed = False
    if gates["poisoned_dropped"]:
        print(f"\nGATE FAILED: poisoned rows were dropped by dedup: {gates['poisoned_dropped']}")
        failed = True
    if gates["dropped_hosts_referenced"]:
        print(
            "\nGATE FAILED: dropped ids are still referenced by a mutation_of: "
            f"{gates['dropped_hosts_referenced']}"
        )
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
