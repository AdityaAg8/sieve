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
from scipy.stats import beta  # type: ignore[import-untyped]

from bench.collect.sandbox_capture import EXCLUSIONS_PATH, load_exclusions
from bench.split.dedup import (
    DEFAULT_THRESHOLD,
    Embedder,
    HashEmbedder,
    MiniLMEmbedder,
    near_duplicates,
    text_of,
)
from sieve.serialise import serialise
from sieve.types import CorpusRecord

log = logging.getLogger(__name__)

DEFAULT_CORPUS = Path("bench/data/corpus.jsonl")
DEFAULT_OUT = Path("bench/results/corpus_stats.json")
DEFAULT_DEDUP_REPORT = Path("bench/results/dedup_dropped.json")

TOKENIZERS: dict[str, str] = {
    "deberta-v3": "microsoft/deberta-v3-base",
    "modernbert": "answerdotai/ModernBERT-base",
    "minilm": MiniLMEmbedder.MODEL,
}
#: Only this tokenizer may decide the §11.2 escalation — it is the one that truncates at 512.
PAYLOAD_LOSS_TOKENIZER = "deberta-v3"
LENGTH_TABLE_TOKENIZERS = ("deberta-v3", "modernbert")
DEVELOPMENT_SPLIT = "dev"
#: The eval-only slice is `real_poisoned` by construction (`bench.split.split.assign`).
EVAL_ONLY_SOURCE = "real_poisoned"
TRUNCATION_LIMIT = 512
PAYLOAD_LOSS_ESCALATION = 0.10
EXACT_SHARE_TARGET = 0.60
HAND_AUTHORED_TARGET = 0.25

#: Which serialisation marker carries each payload-bearing field (`sieve.serialise`).
FIELD_MARKERS: dict[str, str] = {
    "name": "[NAME]",
    "title": "[TITLE]",
    "description": "[DESC]",
    "inputSchema": "[PARAM]",
    "outputSchema": "[OUTPUT]",
    "annotations": "[ANNOT]",
}


class Tokenizer(Protocol):
    def encode(self, text: str, add_special_tokens: bool = True) -> list[int]: ...


class FastTokenizer(Tokenizer, Protocol):
    """A tokenizer that can say *where* in the string each token came from."""

    def __call__(
        self,
        text: str,
        return_offsets_mapping: bool = False,
        add_special_tokens: bool = True,
    ) -> Any: ...


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
        }
    return out


def minilm_over_256_share(records: list[CorpusRecord], tokenizer: Tokenizer) -> float:
    """Share of records past MiniLM's 256-token dedup limit, per MiniLM's own tokenizer.

    Measuring this off the DeBERTa or ModernBERT length table answers a different question:
    three tokenizers disagree about what a token is, and the number published beside "MiniLM
    truncates at 256" has to come from MiniLM.
    """
    if not records:
        return 0.0
    lengths = np.array([len(tokenizer.encode(text_of(r))) for r in records], dtype=float)
    return float((lengths > MiniLMEmbedder.MAX_TOKENS).mean())


def _payload_end_char(record: CorpusRecord, text: str) -> int:
    """The character offset at which the last payload-bearing line of `text` ends."""
    markers = {FIELD_MARKERS[f] for f in record.payload_fields if f in FIELD_MARKERS}
    end = 0
    cursor = 0
    for line in text.split("\n"):
        line_end = cursor + len(line)
        if any(line.startswith(m) for m in markers):
            end = max(end, line_end)
        cursor = line_end + 1
    return end


def payload_loss_share(records: list[CorpusRecord], tokenizer: FastTokenizer) -> float:
    """Share of `poisoned` rows that would lose a `payload_fields` entry at 512 tokens.

    Not "is the row longer than 512 tokens" — the serialiser puts payload-bearing fields
    first precisely so that truncation removes *context*, so a long row usually keeps its
    payload. The question is whether the 512-token cut falls before the last payload-bearing
    line ends, which needs the tokenizer's offset mapping, not its length.
    """
    poisoned = [r for r in records if r.label == "poisoned" and r.payload_fields]
    if not poisoned:
        return 0.0
    lost = 0
    for record in poisoned:
        text = serialise(record.to_tool_record())
        payload_end = _payload_end_char(record, text)
        if payload_end == 0:
            continue
        offsets = tokenizer(text, return_offsets_mapping=True, add_special_tokens=False)[
            "offset_mapping"
        ]
        if len(offsets) <= TRUNCATION_LIMIT:
            continue
        kept_to = int(offsets[TRUNCATION_LIMIT - 1][1])
        if kept_to < payload_end:
            lost += 1
    return lost / len(poisoned)


def cross_boundary_near_duplicates(
    records: list[CorpusRecord],
    *,
    embedder: Embedder,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[tuple[str, str, float]]:
    """Near-duplicate pairs that straddle the development / sealed-split boundary.

    Deduplication is deliberately stratum-local (PRD §9.7): it never compares a record with
    its mutation host, a sibling mutation, or a record of a different label, because dropping
    any of those would break the mutation graph. The price is that a near-duplicate can
    survive with one member used for every decision and its twin in the sealed split, which
    quietly makes the sealed evaluation easier than it looks.

    This is a **disclosure, not a gate**. Removing the pairs would do more damage than the
    optimism they cause, so the count is published beside the leakage AUROC and the reader
    judges it. Any label and any source qualify — a leak does not care about strata.
    """
    development = [r for r in records if r.split == DEVELOPMENT_SPLIT]
    # The sealed side is everything assigned but not development, minus the eval-only slice,
    # which is `real_poisoned` by construction and is never compared against anything.
    sealed = [
        r
        for r in records
        if r.split is not None and r.split != DEVELOPMENT_SPLIT and r.source != EVAL_ONLY_SOURCE
    ]
    if not development or not sealed:
        return []
    both = development + sealed
    pairs = near_duplicates([text_of(r) for r in both], threshold, embedder=embedder)
    boundary = len(development)
    return [
        (both[a].id, both[b].id, cosine)
        for a, b, cosine in pairs
        if (a < boundary) != (b < boundary)
    ]


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
    dropped_per_stratum: dict[str, list[dict[str, Any]]],
    exclusions_path: Path,
    tokenizers: dict[str, Tokenizer],
    unavailable: dict[str, str],
    cross_boundary_pairs: int | None = None,
) -> dict[str, Any]:
    # The dropped rows are gone from `records` by the time this runs — dedup removed them —
    # so the gate reads their labels from the dedup report, never from the corpus.
    dropped = [row for rows in dropped_per_stratum.values() for row in rows]
    referenced = {r.mutation_of for r in records if r.mutation_of}

    # PRD §9.7 as amended by training plan C8 (I1 ADR): the gate fails on a poisoned row dropped
    # as a near-duplicate, the v2.0 bug. A dev row duplicating a sealed row, and the mutations
    # of such a host, are dropped on purpose to keep the sealed splits clean; they are listed
    # instead.
    leakage = {"sealed_twin", "host_dropped"}
    poisoned_dropped = sorted(
        str(row["id"])
        for row in dropped
        if row.get("label") == "poisoned" and row.get("reason", "near_duplicate") not in leakage
    )
    dropped_for_leakage = sorted(str(row["id"]) for row in dropped if row.get("reason") in leakage)
    dropped_hosts_referenced = sorted({str(row["id"]) for row in dropped} & referenced)

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
        "tokens": token_table(
            records,
            {k: v for k, v in tokenizers.items() if k in LENGTH_TABLE_TOKENIZERS},
        ),
        "tokenizers_unavailable": unavailable,
        "gates": {
            "poisoned_dropped": poisoned_dropped,
            "dropped_for_leakage": dropped_for_leakage,
            "dropped_hosts_referenced": dropped_hosts_referenced,
        },
    }
    if cross_boundary_pairs is not None:
        report["cross_boundary_near_duplicate_pairs"] = cross_boundary_pairs
    minilm = tokenizers.get("minilm")
    if minilm is not None:
        report["minilm_over_256_share"] = minilm_over_256_share(records, minilm)
    # Only DeBERTa-v3 may answer this: it is the encoder that truncates at 512, and letting
    # ModernBERT decide "escalate to ModernBERT" would be the wrong tokenizer voting for
    # itself. When it is unavailable the keys are absent rather than wrong.
    deberta = tokenizers.get(PAYLOAD_LOSS_TOKENIZER)
    if deberta is not None:
        share = payload_loss_share(records, deberta)  # type: ignore[arg-type]
        report["payload_loss_share_at_512"] = share
        report["payload_loss_tokenizer"] = PAYLOAD_LOSS_TOKENIZER
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
            f"max={table['max']:.0f} over512={table['share_over_512']:.3f}"
        )
    if "minilm_over_256_share" in report:
        print(
            f"\nover MiniLM's 256-token dedup limit: {report['minilm_over_256_share']:.3f} "
            "(measured with MiniLM's own tokenizer)"
        )
    if "payload_loss_share_at_512" in report:
        print(
            f"payload lost at 512 tokens: {report['payload_loss_share_at_512']:.3f} "
            f"(per {report['payload_loss_tokenizer']})"
        )
    if "cross_boundary_near_duplicate_pairs" in report:
        print(
            "cross-boundary near-duplicate pairs: "
            f"{report['cross_boundary_near_duplicate_pairs']} (disclosed, not gated)"
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


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """The exact (Clopper-Pearson) two-sided `1 - alpha` interval of a binomial share k/n.

    A count with no cluster structure worth bootstrapping, or one at the boundary (41/41, 0/104),
    where a bootstrap collapses to a point: the beta quantiles still give an honest width. The
    ends are pinned at k = 0 (lo = 0) and k = n (hi = 1), where the quantile is undefined.
    """
    if n <= 0 or not 0 <= k <= n:
        raise ValueError(f"clopper_pearson needs 0 <= k <= n and n > 0, got k={k}, n={n}")
    lo = 0.0 if k == 0 else float(beta.ppf(alpha / 2, k, n - k + 1))
    hi = 1.0 if k == n else float(beta.ppf(1 - alpha / 2, k + 1, n - k))
    return lo, hi


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
    parser.add_argument(
        "--embedder",
        choices=("minilm", "hash"),
        default="minilm",
        help="embedder for the cross-boundary near-duplicate disclosure; `hash` is for tests",
    )
    args = parser.parse_args(argv)

    records = _load(args.corpus)
    dropped: dict[str, list[dict[str, Any]]] = (
        json.loads(args.dedup_report.read_text(encoding="utf-8"))
        if args.dedup_report.exists()
        else {}
    )
    tokenizers, unavailable = ({}, {}) if args.no_tokenizers else _load_tokenizers(TOKENIZERS)
    embedder: Embedder = MiniLMEmbedder() if args.embedder == "minilm" else HashEmbedder()
    report = build_report(
        records,
        dropped_per_stratum=dropped,
        exclusions_path=args.exclusions,
        tokenizers=tokenizers,
        unavailable=unavailable,
        cross_boundary_pairs=len(cross_boundary_near_duplicates(records, embedder=embedder)),
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
