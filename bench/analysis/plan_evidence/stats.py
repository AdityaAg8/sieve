"""Small exact statistics behind the plan's numbers: CIs, order statistics, collisions, batching."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from sieve.types import CorpusRecord

REAL_SOURCES = ("public_repo", "wild_fp")


def clopper_pearson(x: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact two-sided binomial interval for x successes in n trials."""
    from scipy.stats import beta  # the model extra's

    lo = 0.0 if x == 0 else float(beta.ppf(alpha / 2, x, n - x + 1))
    hi = 1.0 if x == n else float(beta.ppf(1 - alpha / 2, x + 1, n - x))
    return lo, hi


def cp_table(
    n: int = 104, disagreements: Sequence[int] = (0, 1, 3, 5, 8)
) -> list[tuple[int, float, float, float]]:
    """(k, k/n, lower, upper) for each disagreement count: the §4.3 test-ID table."""
    return [(k, k / n, *clopper_pearson(k, n)) for k in disagreements]


def kappa_half_width(kappa: float, n: int, p_first: float) -> float:
    """Large-sample 95% half-width of Cohen's κ for two raters sharing marginals (p, 1 - p).

    SE(κ) ≈ sqrt(po (1 - po) / (n (1 - pe)²)) with pe = p² + (1 - p)² and po = κ (1 - pe) + pe.
    """
    pe = p_first**2 + (1 - p_first) ** 2
    po = kappa * (1 - pe) + pe
    return 1.96 * math.sqrt(po * (1 - po) / (n * (1 - pe) ** 2))


@dataclass(frozen=True)
class OrderStatistic:
    positives: int
    servers: int
    cluster_sizes: dict[int, int]  # rows per server -> number of servers
    cp_lcb_by_misses: dict[int, float]
    bootstrap_lcb_by_misses: dict[int, float]


def tstat_order(
    dev: Sequence[CorpusRecord], *, max_misses: int = 5, n_boot: int = 2000, seed: int = 0
) -> OrderStatistic:
    """How many pooled-OOF misses `T_low`'s recall lower bound tolerates (plan §6.5, F54).

    Misses are placed in single-row servers (the most favourable placement); the bootstrap
    resamples servers, as `t_low_for_recall_lcb` does.
    """
    per_server = Counter(r.server_name for r in dev if r.label == "poisoned")
    sizes = np.array(sorted(per_server.values(), reverse=True))
    n = int(sizes.sum())
    rng = np.random.default_rng(seed)
    singles = np.flatnonzero(sizes == 1)
    cp = {m: clopper_pearson(n - m, n)[0] for m in range(max_misses + 1)}
    boot: dict[int, float] = {}
    for m in range(max_misses + 1):
        hits = sizes.astype(float)
        hits[singles[:m]] = 0.0
        draws = rng.integers(0, len(sizes), size=(n_boot, len(sizes)))
        recall = hits[draws].sum(axis=1) / sizes[draws].sum(axis=1)
        boot[m] = float(np.quantile(recall, 0.025))
    return OrderStatistic(n, len(sizes), dict(sorted(Counter(sizes.tolist()).items())), cp, boot)


def baseline_collisions(dev: Sequence[CorpusRecord]) -> dict[str, int]:
    """Tool names shared by several dev rows: why joining YARA findings by name is wrong (F58)."""
    by_name: defaultdict[str, list[CorpusRecord]] = defaultdict(list)
    for r in dev:
        by_name[r.tool.name].append(r)
    colliding = [rs for rs in by_name.values() if len(rs) > 1]
    mixed = [rs for rs in colliding if len({r.label == "poisoned" for r in rs}) > 1]
    negatives = [r for rs in mixed for r in rs if r.label != "poisoned"]
    return {
        "tool_names": len(by_name),
        "colliding_names": len(colliding),
        "rows_in_collisions": sum(len(rs) for rs in colliding),
        "mixed_label_names": len(mixed),
        "negatives_sharing_a_poisoned_name": len(negatives),
        "real_negatives_sharing_a_poisoned_name": sum(r.source in REAL_SOURCES for r in negatives),
    }


def bucketing_sim(
    lengths: Sequence[int],
    *,
    batch: int = 16,
    mega: int = 50,
    shuffles: int = 200,
    seed: int = 0,
    max_length: int = 512,
) -> dict[str, float]:
    """Padded tokens per epoch: random batches vs HF-style length grouping (plan §7.3).

    Grouping follows `transformers`' `LengthGroupedSampler`: a random permutation cut into
    megabatches of `mega * batch`, each sorted by length (longest first), then cut into batches.
    """
    lens = np.minimum(np.asarray(lengths), max_length)
    rng = np.random.default_rng(seed)

    def padded(order: np.ndarray) -> int:
        return sum(
            int(lens[order[i : i + batch]].max()) * len(order[i : i + batch])
            for i in range(0, len(order), batch)
        )

    rand, grouped = [], []
    for _ in range(shuffles):
        perm = rng.permutation(len(lens))
        rand.append(padded(perm))
        megas = [perm[i : i + mega * batch] for i in range(0, len(perm), mega * batch)]
        order = np.concatenate([m[np.argsort(-lens[m], kind="stable")] for m in megas])
        grouped.append(padded(order))
    return {
        "real_tokens": int(lens.sum()),
        "random_padded": float(np.mean(rand)),
        "grouped_padded": float(np.mean(grouped)),
        "ratio": float(np.mean(rand) / np.mean(grouped)),
    }
