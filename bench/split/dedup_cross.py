"""Eval-vs-dev near-duplicate removal for every OOD set (training plan G4, §5.2).

Each eval set (test-OOD: `real_poisoned`, `mcptox_clean`, `docker`; val-OOD: the six G3
sources) is checked three ways, each counted **on the raw set** so the counts compare with
the §5.2 measurements:

* `exact_within`: a row whose description, under the exact key (NFKC, lower case, collapsed
  whitespace), already appeared earlier in the set;
* `vs_dev`: a row with a dev-side twin, at MiniLM cosine >= 0.92 (`vs_dev_near`, the §5.2
  measurement) or on the exact key (`vs_dev_exact`: the same description under another tool
  name). The dev side is everything a model can train on: the pinned catalog plus the corpus
  `dev` rows;
* `internal_twin_rows` / `internal_twin_groups`: rows with a later cosine twin inside the set
  (the plan-evidence count), and the connected components those twins form.

Removal on a test-OOD set is the union: later exact copies and dev twins go first, then each
remaining component (cosine or exact twins) keeps its first row. A val-OOD set is report-only
and labelled per source, so only its dev twins (a leak) are removed; its other counts are
disclosed. The embedded text is `"<name>. <description>"`, as the §5.2 measurements used.
`bench/results/dedup_cross.json` gets every count and every dropped id, never a range;
downstream scoring drops those ids. Re-run (`make dedup-cross`) after every `make corpus`.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from bench.split.dedup import DEFAULT_THRESHOLD, Embedder, MiniLMEmbedder

RESULTS = Path("bench/results/dedup_cross.json")
TEST_OOD = ("real_poisoned", "docker", "mcptox_clean")
COUNT_KEYS = (
    "raw",
    "exact_within",
    "vs_dev_near",
    "vs_dev_exact",
    "vs_dev",
    "internal_twin_rows",
    "internal_twin_groups",
    "dropped_count",
    "surviving",
)
_BLOCK = 1024


@dataclass(frozen=True)
class Item:
    id: str
    name: str
    description: str

    def text(self) -> str:
        return f"{self.name}. {self.description}"


@dataclass
class SetReport:
    raw: int
    exact_within: int
    vs_dev_near: int
    vs_dev_exact: int
    vs_dev: int
    internal_twin_rows: int
    internal_twin_groups: int
    dropped_count: int
    surviving: int
    removal: str = "full"
    dropped: list[str] = field(default_factory=list)


def exact_key(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().lower()


def _embed(items: Sequence[Item], embedder: Embedder) -> np.ndarray:
    if not items:
        return np.zeros((0, 1))
    return np.asarray(embedder.embed([i.text() for i in items]), dtype=np.float64)


def _near_dev(vectors: np.ndarray, dev: np.ndarray, threshold: float) -> np.ndarray:
    hits = np.zeros(len(vectors), dtype=bool)
    if not len(vectors) or not len(dev):
        return hits
    for start in range(0, len(vectors), _BLOCK):
        sim = vectors[start : start + _BLOCK] @ dev.T
        hits[start : start + _BLOCK] = sim.max(axis=1) >= threshold
    return hits


def _cosine_twins(vectors: np.ndarray, threshold: float) -> np.ndarray:
    if not len(vectors):
        return np.zeros((0, 0), dtype=bool)
    sim = np.asarray(vectors @ vectors.T >= threshold)
    np.fill_diagonal(sim, False)
    return sim


def _with_exact(twins: np.ndarray, items: Sequence[Item]) -> np.ndarray:
    out = twins.copy()
    keys = [exact_key(i.description) for i in items]
    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            if keys[a] == keys[b]:
                out[a, b] = out[b, a] = True
    return out


def _components(adjacent: np.ndarray, members: Sequence[int]) -> list[list[int]]:
    """Connected components among `members`, each sorted, in order of first member."""
    member = set(members)
    seen: set[int] = set()
    out: list[list[int]] = []
    for start in members:
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            node = stack.pop()
            comp.append(node)
            for nxt in np.flatnonzero(adjacent[node]):
                n = int(nxt)
                if n in member and n not in seen:
                    seen.add(n)
                    stack.append(n)
        out.append(sorted(comp))
    return out


def dedup_set(
    items: Sequence[Item],
    dev: Sequence[Item],
    *,
    embedder: Embedder,
    threshold: float = DEFAULT_THRESHOLD,
    dev_vectors: np.ndarray | None = None,
    full: bool = True,
) -> SetReport:
    vectors = _embed(items, embedder)
    dev_vectors = _embed(dev, embedder) if dev_vectors is None else dev_vectors
    dev_keys = {exact_key(d.description) for d in dev}

    seen: set[str] = set()
    exact_later = set()
    for i, item in enumerate(items):
        key = exact_key(item.description)
        if key in seen:
            exact_later.add(i)
        seen.add(key)
    near = _near_dev(vectors, dev_vectors, threshold)
    exact_dev = [exact_key(item.description) in dev_keys for item in items]
    vs_dev = {i for i in range(len(items)) if near[i] or exact_dev[i]}

    twins = _cosine_twins(vectors, threshold)
    later = [i for i in range(len(items)) if twins[i, i + 1 :].any()]
    groups = [c for c in _components(twins, range(len(items))) if len(c) > 1]

    removed = set(vs_dev)
    if full:
        removed |= exact_later
        remaining = [i for i in range(len(items)) if i not in removed]
        for comp in _components(_with_exact(twins, items), remaining):
            removed.update(comp[1:])
    dropped = [items[i].id for i in sorted(removed)]
    return SetReport(
        raw=len(items),
        exact_within=len(exact_later),
        vs_dev_near=int(near.sum()),
        vs_dev_exact=sum(exact_dev),
        vs_dev=len(vs_dev),
        internal_twin_rows=len(later),
        internal_twin_groups=len(groups),
        dropped_count=len(dropped),
        surviving=len(items) - len(dropped),
        removal="full" if full else "dev_twins_only",
        dropped=dropped,
    )


def run_sets(
    sets: Mapping[str, Sequence[Item]],
    dev: Sequence[Item],
    *,
    embedder: Embedder,
    threshold: float = DEFAULT_THRESHOLD,
    reference: Mapping[str, int] | None = None,
    full: Sequence[str] = TEST_OOD,
) -> dict[str, Any]:
    dev_vectors = _embed(dev, embedder)
    reports = {
        name: dedup_set(
            items,
            dev,
            embedder=embedder,
            threshold=threshold,
            dev_vectors=dev_vectors,
            full=name in full,
        )
        for name, items in sets.items()
    }
    return {
        "threshold": threshold,
        "embedded_text": "<name>. <description>",
        "exact_key": "NFKC, lower case, collapsed whitespace, on the description",
        "dev_reference": dict(reference or {"dev": len(dev)}),
        "sets": {name: asdict(r) for name, r in reports.items()},
    }


def write_report(report: Mapping[str, Any], path: Path = RESULTS) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    return path


# ------------------------------------------------------------------------- real inputs


def _catalog_items() -> list[Item]:
    import pyarrow.parquet as pq

    from bench.analysis.plan_evidence.pool import catalog_rows
    from bench.collect.catalog import download

    rows = catalog_rows(pq.read_table(download()[0]).to_pylist())
    return [Item(f"catalog-{i}", r["name"] or "", r["desc"] or "") for i, r in enumerate(rows)]


def _corpus_dev_items(corpus: Path) -> list[Item]:
    from sieve.types import CorpusRecord

    records = [
        CorpusRecord.model_validate_json(line)
        for line in corpus.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [Item(r.id, r.tool.name, r.tool.description or "") for r in records if r.split == "dev"]


def _eval_sets() -> dict[str, list[Item]]:
    from bench.collect import ood_negatives, val_ood
    from bench.collect.real_poisoned import DEFAULT_CACHE, DEFAULT_POINTERS, Pointer, reconstruct

    pointers = [
        Pointer.model_validate(p) for p in json.loads(DEFAULT_POINTERS.read_text(encoding="utf-8"))
    ]
    sets: dict[str, list[Item]] = {
        "real_poisoned": [
            Item(r.id, r.tool.name, r.tool.description or "")
            for r in reconstruct(pointers, DEFAULT_CACHE)
        ]
    }
    pins = ood_negatives.load_pins()
    negatives = ood_negatives.docker_rows(
        Path(pins["docker"].local_dir), commit=pins["docker"].commit, sha256=pins["docker"].sha256
    ) + ood_negatives.mcptox_clean_rows(
        Path(pins["mcptox_clean"].local_dir),
        commit=pins["mcptox_clean"].commit,
        sha256=pins["mcptox_clean"].sha256,
    )
    for row in [*negatives, *val_ood.collect_rows(val_ood.load_pins())]:
        sets.setdefault(row.source, []).append(
            Item(row.id, row.tool["name"], row.tool.get("description") or "")
        )
    return sets


def run_default(
    corpus: Path = Path("bench/data/labelled.jsonl"), embedder: Embedder | None = None
) -> dict[str, Any]:
    catalog, corpus_dev = _catalog_items(), _corpus_dev_items(corpus)
    return run_sets(
        _eval_sets(),
        catalog + corpus_dev,
        embedder=embedder or MiniLMEmbedder(),
        reference={"catalog": len(catalog), "corpus_dev": len(corpus_dev)},
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.split.dedup_cross")
    parser.add_argument("--corpus", type=Path, default=Path("bench/data/labelled.jsonl"))
    parser.add_argument("--out", type=Path, default=RESULTS)
    ns = parser.parse_args(argv)
    report = run_default(ns.corpus)
    write_report(report, ns.out)
    for name, entry in report["sets"].items():
        print(name, {k: entry[k] for k in COUNT_KEYS})
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
