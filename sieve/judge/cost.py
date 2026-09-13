"""USD cost of a judge call, from the published per-MTok prices (PRD §12.4).

The same function feeds `scans.judge_cost_usd` and the daily `spend_ledger`, which is what
the budget callback in `client.AnthropicJudge` reads. An unpriced model raises rather than
billing zero: a silently free model would let a misconfiguration spend without limit.
"""

from __future__ import annotations

from anthropic.types import Usage

__all__ = ["CACHE_WRITE_MULTIPLIER", "PRICES", "UnpricedModelError", "estimate"]

#: model -> (input, output, cache_read) USD per million tokens.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0, 0.10),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
}

#: Writing the cached prefix costs more than reading it uncached; the published multiplier
#: is 1.25x the input rate. PRICES carries three rates, so this is applied on top rather
#: than added as a fourth column — dropping it would under-report the first call of every
#: cache window, and the ledger it feeds is a spend cap.
CACHE_WRITE_MULTIPLIER = 1.25

_PER_MTOK = 1_000_000.0


class UnpricedModelError(KeyError):
    """Raised for a model with no published price. Never bill an unknown model as free."""


def estimate(usage: Usage, model: str) -> float:
    """USD for one call. Absent token counts (the SDK leaves them `None`) count as zero.

    Four terms, not three: `PRICES` publishes input, output and cache-read rates, and cache
    *writes* are billed on top at `input_rate * CACHE_WRITE_MULTIPLIER` (1.25x). Dropping that
    term would under-report the first call of every cache window, and the figure feeds a spend
    cap — a ledger that under-counts is a cap that does not hold. It contributes nothing to
    the PRD §12.4 worked example, where `cache_creation_input_tokens` is zero.
    """
    try:
        input_price, output_price, cache_read_price = PRICES[model]
    except KeyError as exc:
        raise UnpricedModelError(
            f"no published price for {model!r}; add it to sieve.judge.cost.PRICES"
        ) from exc
    cache_read = usage.cache_read_input_tokens or 0
    cache_write = usage.cache_creation_input_tokens or 0
    total = (
        usage.input_tokens * input_price
        + usage.output_tokens * output_price
        + cache_read * cache_read_price
        + cache_write * input_price * CACHE_WRITE_MULTIPLIER
    )
    return total / _PER_MTOK
