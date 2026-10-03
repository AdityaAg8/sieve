"""Near-duplicates between sources (plan §5.2): MiniLM cosine ≥ 0.92 and an exact NFKC key.

Corpus rows enter only as dev rows (`corpus_dev_real`, `corpus_dev_syn`). The definitive
eval-vs-dev removal with exact surviving counts is G4's `bench/split/dedup_cross.py`.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence

import numpy as np

from bench.analysis.plan_evidence.pool import Row

THRESHOLD = 0.92

PAIRS: tuple[tuple[str, str], ...] = (
    ("mcptox", "corpus_dev_syn"),
    ("mcptox", "corpus_dev_real"),
    ("mcptox", "catalog"),
    ("mcptox", "mcptox"),
    ("mcptox_clean", "catalog"),
    ("mcptox_clean", "corpus_dev_real"),
    ("mcptox_clean", "mcptox_clean"),
    ("guardbench", "catalog"),
    ("guardbench", "corpus_dev_real"),
    ("msb_clean", "catalog"),
    ("docker", "docker"),
    ("docker", "catalog"),
    ("docker", "corpus_dev_real"),
    ("scannerbench_en", "corpus_dev_real"),
    ("scannerbench_tr", "corpus_dev_real"),
    ("mtp", "corpus_dev_real"),
    ("catalog", "catalog"),
)


def group(row: Row) -> str | None:
    """The comparison group of a pool row; None for corpus rows outside dev."""
    if row["src"].startswith("corpus:"):
        if row["split"] != "dev":
            return None
        return "corpus_dev_syn" if row["src"] == "corpus:synthetic_mutation" else "corpus_dev_real"
    return str(row["src"])


def exact_key(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().lower()


def embed_text(row: Row) -> str:
    return f"{row['name'] or ''}. {row['desc'] or ''}"


def exact_duplicates(rows: Sequence[Row]) -> int:
    """Rows whose description, under the exact key, already appeared earlier in `rows`."""
    seen: set[str] = set()
    dups = 0
    for r in rows:
        k = exact_key(r["desc"] or "")
        dups += k in seen
        seen.add(k)
    return dups


def rows_with_neighbour(
    a: np.ndarray, b: np.ndarray, *, same: bool, threshold: float = THRESHOLD
) -> int:
    """Rows of `a` with a cosine ≥ threshold neighbour in `b` (unit vectors).

    With `same` (a is b) a row counts only when it has a twin LATER in the order (keep the
    last of each chain), so the count is how many rows a dedup removes, not the twin-group size.
    """
    hits = 0
    for start in range(0, len(a), 1024):
        sim = a[start : start + 1024] @ b.T
        if same:
            for r in range(sim.shape[0]):
                sim[r, : start + r + 1] = -1.0
        hits += int((sim.max(axis=1) >= threshold).sum()) if sim.size else 0
    return hits


def cross_counts(
    rows: Sequence[Row], embeddings: np.ndarray, pairs: Sequence[tuple[str, str]] = PAIRS
) -> dict[str, int]:
    idx: defaultdict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        g = group(r)
        if g is not None:
            idx[g].append(i)
    out: dict[str, int] = {}
    for a, b in pairs:
        if not idx[a] or not idx[b]:
            continue
        out[f"{a} (n={len(idx[a])}) vs {b}"] = rows_with_neighbour(
            embeddings[idx[a]], embeddings[idx[b]], same=a == b
        )
    for name in ("mcptox", "mcptox_clean", "docker"):
        out[f"{name} exact duplicate descriptions"] = exact_duplicates([rows[i] for i in idx[name]])
    return out
