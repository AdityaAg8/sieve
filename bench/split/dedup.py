"""Stratified near-duplicate removal that respects the mutation graph (PRD §9.7).

The rule that matters: **deduplication runs within `(label, source)` strata only**, and
never compares a record with its `mutation_of` host or with another mutation of the same
host. A mutation differs from its host by one sentence, so a naive global pass at cosine
0.92 deletes every mutated sample and keeps the benign host — which silently empties the
poisoned class. That was the v2.0 bug; `forbidden_pairs` and the strata are the fix, and
`drop()` refuses outright to delete a record another record's `mutation_of` points at.

**Training plan C8.** `run()` used to build that guard per stratum, but a mutation never shares
its host's stratum (poisoned/synthetic vs benign/real), so the guard could not fire (F33).
`analyse()` now:

* computes the mutation graph over the whole corpus, and chooses which row of a twin pair goes:
  never a sealed one (the frozen split map says which), never one of two hosts (both stay, and
  grouping keeps them in one fold, so no poisoned mutation is lost), otherwise the row nothing
  points at. When a host does go, its mutations go with it;
* scores a pair by max(cosine of the full serialisation, cosine of the description alone), so a
  long row is no longer judged on whatever MiniLM's 256-token window kept, plus an exact
  NFKC/lower-case key. The parameters are never a twin signal on their own: short shared schemas
  (`path: string`) put 4,943 unrelated pairs at cosine >= 0.92 (owner decision, I1 ADR);
* returns every surviving near-twin pair across strata, for `bench.split.split` to keep each
  pair in one fold and out of the sealed split, and the cross-label pairs for the review queue.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
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
    return np.asarray(matrix / np.where(norms == 0.0, 1.0, norms), dtype=np.float64)


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


def exact_key(record: CorpusRecord) -> str:
    """NFKC, lower-case, whitespace-collapsed serialisation: the exact-duplicate key."""
    folded = unicodedata.normalize("NFKC", text_of(record)).lower()
    return " ".join(folded.split())


def similarity(records: list[CorpusRecord], embedder: Embedder) -> Matrix:
    """Pairwise max(full-serialisation cosine, description cosine); exact-key matches are 1.0.

    A row without a description contributes no description signal (its vector stays zero).
    """
    return _similarities(records, embedder)[0]


def _similarities(records: list[CorpusRecord], embedder: Embedder) -> tuple[Matrix, Matrix]:
    """(the C8 twin similarity, the pre-C8 full-serialisation cosine alone)."""
    full = embedder.embed([text_of(r) for r in records])
    described = [i for i, r in enumerate(records) if r.tool.description]
    desc = np.zeros_like(full)
    if described:
        vectors = embedder.embed([records[i].tool.description or "" for i in described])
        desc = np.zeros((len(records), vectors.shape[1]), dtype=np.float64)
        desc[described] = vectors
    full_sim = np.asarray(full @ full.T, dtype=np.float64)
    sim = np.maximum(full_sim, desc @ desc.T)
    by_key: dict[str, list[int]] = collections.defaultdict(list)
    for i, r in enumerate(records):
        by_key[exact_key(r)].append(i)
    for group in by_key.values():
        for a in group:
            sim[a, group] = 1.0
    return np.asarray(sim, dtype=np.float64), full_sim


def _roots(records: list[CorpusRecord]) -> list[str]:
    """Each row's ultimate host: rows sharing a root are the mutation graph, never compared."""
    by_id = {r.id: r for r in records}
    out = []
    for record in records:
        current, seen = record, set()
        while current.mutation_of and current.mutation_of in by_id and current.id not in seen:
            seen.add(current.id)
            current = by_id[current.mutation_of]
        out.append(current.id)
    return out


@dataclass
class DedupResult:
    kept: list[CorpusRecord]
    dropped_per_stratum: dict[str, list[str]]
    twins: list[tuple[str, str, float]] = field(default_factory=list)
    cross_label: list[dict[str, object]] = field(default_factory=list)
    #: dropped id -> why: near_duplicate (dev twin in its stratum), sealed_twin (a dev row
    #: duplicating a sealed row), host_dropped (a mutation whose host went).
    reasons: dict[str, str] = field(default_factory=dict)


def _stratum(record: CorpusRecord) -> str:
    return f"{record.label}/{record.source}"


def analyse(
    records: list[CorpusRecord],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    embedder: Embedder,
    split_of: dict[str, str] | None = None,
) -> DedupResult:
    """Within-stratum dedup over the whole-corpus mutation graph, and the surviving twins.

    `split_of` maps a host server to its frozen split; a server it does not name is dev.
    """
    n = len(records)
    strata_names = sorted({_stratum(r) for r in records})
    if n < 2:
        return DedupResult(list(records), {name: [] for name in strata_names})
    sim, full_sim = _similarities(records, embedder)
    roots = np.array(_roots(records))
    strata = np.array([_stratum(r) for r in records])
    upper_i, upper_j = np.triu_indices(n, k=1)
    candidate = (sim[upper_i, upper_j] >= threshold) & (roots[upper_i] != roots[upper_j])
    pairs = [(int(a), int(b)) for a, b in zip(upper_i[candidate], upper_j[candidate], strict=True)]

    referenced = {r.mutation_of for r in records if r.mutation_of}
    children: dict[str, list[int]] = collections.defaultdict(list)
    for i, r in enumerate(records):
        if r.mutation_of:
            children[r.mutation_of].append(i)

    servers = {r.id: r.server_name for r in records}
    sealed = [(split_of or {}).get(servers[root], "dev") != "dev" for root in roots]
    hosts = [r.id in referenced for r in records]
    doomed: dict[int, str] = {}
    for a, b in pairs:
        if strata[a] != strata[b]:
            continue
        if sealed[a] and sealed[b]:
            # The sealed split keeps the rule it was frozen under (A4): the full-serialisation
            # cosine alone, later row dropped. The C8 description signal never moves it.
            if full_sim[a, b] >= threshold:
                doomed.setdefault(b, "near_duplicate")
        elif sealed[a] != sealed[b]:
            doomed.setdefault(b if sealed[a] else a, "sealed_twin")
        elif hosts[a] and hosts[b]:
            continue  # both carry mutations; grouping puts the pair in one fold instead
        else:
            # Keep the earlier row, unless only the later one is a host: then the twin goes.
            doomed.setdefault(a if hosts[b] else b, "near_duplicate")
    frontier = list(doomed)
    while frontier:  # a dropped host takes its mutations with it
        for child in children.get(records[frontier.pop()].id, []):
            if child not in doomed:
                doomed[child] = "host_dropped"
                frontier.append(child)

    dropped: dict[str, list[str]] = {name: [] for name in strata_names}
    for i in sorted(doomed):
        dropped[strata[i]].append(records[i].id)
    twins = [
        (records[a].id, records[b].id, round(float(sim[a, b]), 4))
        for a, b in pairs
        if a not in doomed and b not in doomed
    ]
    by_id = {r.id: r for r in records}
    cross_label: list[dict[str, object]] = [
        {"a": a, "b": b, "labels": [by_id[a].label, by_id[b].label], "cosine": cos}
        for a, b, cos in twins
        if by_id[a].label != by_id[b].label
    ]
    kept = [r for i, r in enumerate(records) if i not in doomed]
    reasons = {records[i].id: why for i, why in sorted(doomed.items())}
    return DedupResult(kept, dropped, twins, cross_label, reasons)


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
    split_of: dict[str, str] | None = None,
) -> tuple[list[CorpusRecord], dict[str, list[str]]]:
    """Deduplicate within `(label, source)` strata. Returns kept records and drops per stratum."""
    result = analyse(records, threshold=threshold, embedder=embedder, split_of=split_of)
    for name, ids in result.dropped_per_stratum.items():
        if ids:
            log.info("stratum %s: dropped %d near-duplicates", name, len(ids))
    return result.kept, result.dropped_per_stratum


def dropped_rows(
    records: list[CorpusRecord],
    dropped_per_stratum: dict[str, list[str]],
    reasons: dict[str, str] | None = None,
) -> dict[str, list[dict[str, str | None]]]:
    """The dedup report, with each dropped row's label beside its id.

    `bench.stats` gates on "no poisoned row was dropped". It reads this artefact *after*
    dedup has already removed those rows from the corpus, so an id alone is unresolvable and
    the gate could never fire. The label has to travel with the id.
    """
    by_id = {r.id: r for r in records}
    return {
        stratum: [
            {
                "id": rid,
                "label": by_id[rid].label,
                "source": by_id[rid].source,
                "mutation_of": by_id[rid].mutation_of,
                "reason": (reasons or {}).get(rid, "near_duplicate"),
            }
            for rid in ids
            if rid in by_id
        ]
        for stratum, ids in sorted(dropped_per_stratum.items())
    }


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
    parser.add_argument(
        "--twins",
        type=Path,
        default=Path("bench/data/near_twins.json"),
        help="surviving near-twin pairs across strata; `bench.split.split --near-twins` reads it",
    )
    parser.add_argument(
        "--cross-label",
        type=Path,
        default=Path("bench/results/cross_label_twins.json"),
        help="near-twins carrying different labels, for the review queue",
    )
    parser.add_argument(
        "--split-map",
        type=Path,
        default=None,
        help="the frozen split map (A4): rows of sealed servers are never dropped",
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
    records = _load(args.corpus)
    split_of = None
    if args.split_map:
        servers = json.loads(args.split_map.read_text(encoding="utf-8"))["servers"]
        split_of = {name: entry["split"] for name, entry in servers.items()}
    result = analyse(records, threshold=args.threshold, embedder=embedder, split_of=split_of)
    kept, dropped = result.kept, result.dropped_per_stratum
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(r.model_dump_json() + "\n" for r in kept), encoding="utf-8", newline="\n"
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    # The report carries each dropped row's label: `bench.stats` reads it after these rows
    # are already gone from the corpus, so an id alone would leave its gate unfireable.
    args.report.write_text(
        json.dumps(dropped_rows(records, dropped, result.reasons), indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    twins = {
        "threshold": args.threshold,
        "rule": "max(full-serialisation cosine, description cosine), or an exact NFKC key",
        "pairs": [list(t) for t in result.twins],
    }
    for path, payload in ((args.twins, twins), (args.cross_label, result.cross_label)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(
        f"kept {len(kept)} of {len(records)}; {len(result.twins)} near-twin pairs, "
        f"{len(result.cross_label)} cross-label"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
