"""Stratified near-duplicate removal that respects the mutation graph (PRD §9.7).

The rule that matters: **deduplication runs within `(label, source)` strata only**, and
never compares a record with its `mutation_of` host or with another mutation of the same
host. A mutation differs from its host by one sentence, so a naive global pass at cosine
0.92 deletes every mutated sample and keeps the benign host — which silently empties the
poisoned class. That was the v2.0 bug; `forbidden_pairs` and the strata are the fix, and
`drop()` refuses outright to delete a record another record's `mutation_of` points at.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt

from sieve.serialise import serialise
from sieve.types import CorpusRecord

log = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.92
_TOKEN_RE = re.compile(r"[a-z0-9_]+")

Matrix = npt.NDArray[np.float64]


class MutationGraphViolation(Exception):  # noqa: N818 (name fixed by the lane's interface)
    """A record another record's `mutation_of` points at was about to be dropped."""

    def __init__(self, ids: list[str]) -> None:
        self.ids = ids
        super().__init__("refusing to drop records referenced by a mutation_of: " + ", ".join(ids))


@runtime_checkable
class Embedder(Protocol):
    def embed(self, texts: list[str]) -> Matrix: ...


class HashEmbedder:
    """Deterministic hashed bag-of-words. For tests: no download, no nondeterminism."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> Matrix:
        out = np.zeros((len(texts), self.dim), dtype=np.float64)
        for row, text in enumerate(texts):
            for token in _TOKEN_RE.findall(text.lower()):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                out[row, int.from_bytes(digest[:4], "big") % self.dim] += 1.0
        return _l2_normalise(out)


class MiniLMEmbedder:
    """The published embedder. Truncates at 256 tokens; that share is published (§9.7)."""

    MODEL = "sentence-transformers/all-MiniLM-L6-v2"
    MAX_TOKENS = 256

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or self.MODEL
        self._model: Any | None = None

    @property
    def model(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> Matrix:
        vectors = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return _l2_normalise(np.asarray(vectors, dtype=np.float64))


def _l2_normalise(matrix: Matrix) -> Matrix:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0.0, 1.0, norms)


def near_duplicates(
    texts: list[str],
    threshold: float = DEFAULT_THRESHOLD,
    *,
    embedder: Embedder,
    forbidden_pairs: set[tuple[int, int]] | None = None,
) -> list[tuple[int, int, float]]:
    """Upper-triangle cosine pairs at or above `threshold`, minus the forbidden ones."""
    if len(texts) < 2:
        return []
    forbidden = forbidden_pairs or set()
    vectors = embedder.embed(texts)
    similarity = vectors @ vectors.T
    out: list[tuple[int, int, float]] = []
    for i, j in zip(*np.triu_indices(len(texts), k=1), strict=True):
        a, b = int(i), int(j)
        if (a, b) in forbidden or (b, a) in forbidden:
            continue
        cosine = float(similarity[a, b])
        if cosine >= threshold:
            out.append((a, b, cosine))
    return out


def text_of(record: CorpusRecord) -> str:
    """The same text the model sees, so dedup and training agree on what a record is."""
    return serialise(record.to_tool_record())


def drop(
    records: list[CorpusRecord], pairs: list[tuple[int, int, float]]
) -> tuple[list[CorpusRecord], list[str]]:
    """Keep the earlier id of each pair; refuse to drop anything a mutation references."""
    doomed = {records[max(a, b)].id for a, b, _ in pairs}
    referenced = {r.mutation_of for r in records if r.mutation_of}
    violations = sorted(doomed & referenced)
    if violations:
        raise MutationGraphViolation(violations)
    kept = [r for r in records if r.id not in doomed]
    return kept, [r.id for r in records if r.id in doomed]


def _forbidden(stratum: list[CorpusRecord]) -> set[tuple[int, int]]:
    """Pairs that must never be compared: same host, or host and its own mutation."""
    forbidden: set[tuple[int, int]] = set()
    index = {record.id: position for position, record in enumerate(stratum)}
    by_host: dict[str, list[int]] = collections.defaultdict(list)
    for position, record in enumerate(stratum):
        if record.mutation_of:
            by_host[record.mutation_of].append(position)
            host = index.get(record.mutation_of)
            if host is not None:
                forbidden.add((min(host, position), max(host, position)))
    for siblings in by_host.values():
        for a in siblings:
            for b in siblings:
                if a < b:
                    forbidden.add((a, b))
    return forbidden


def run(
    records: list[CorpusRecord],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    embedder: Embedder,
) -> tuple[list[CorpusRecord], dict[str, list[str]]]:
    """Deduplicate within `(label, source)` strata. Returns kept records and drops per stratum."""
    strata: dict[str, list[CorpusRecord]] = collections.defaultdict(list)
    for record in records:
        strata[f"{record.label}/{record.source}"].append(record)

    dropped_ids: set[str] = set()
    dropped_per_stratum: dict[str, list[str]] = {}
    for name, stratum in sorted(strata.items()):
        pairs = near_duplicates(
            [text_of(r) for r in stratum],
            threshold,
            embedder=embedder,
            forbidden_pairs=_forbidden(stratum),
        )
        _, dropped = drop(stratum, pairs)
        dropped_per_stratum[name] = dropped
        dropped_ids.update(dropped)
        if dropped:
            log.info("stratum %s: dropped %d near-duplicates", name, len(dropped))
    kept = [r for r in records if r.id not in dropped_ids]
    return kept, dropped_per_stratum


def _load(path: Path) -> list[CorpusRecord]:
    return [
        CorpusRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bench.split.dedup",
        description="Drop near-duplicates within (label, source) strata, sparing mutations.",
    )
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="deduplicated JSONL")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("bench/results/dedup_dropped.json"),
        help="dropped ids per stratum; `bench.stats` reads this and gates on it",
    )
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument(
        "--embedder",
        choices=("minilm", "hash"),
        default="minilm",
        help="`hash` is the deterministic test embedder and is never used for a release",
    )
    args = parser.parse_args(argv)
    embedder: Embedder = MiniLMEmbedder() if args.embedder == "minilm" else HashEmbedder()
    kept, dropped = run(_load(args.corpus), threshold=args.threshold, embedder=embedder)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(r.model_dump_json() + "\n" for r in kept), encoding="utf-8", newline="\n"
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(dropped, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"kept {len(kept)} of {len(kept) + sum(len(v) for v in dropped.values())}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
