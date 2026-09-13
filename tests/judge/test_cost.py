"""Task 4.3 — cost accounting. The numbers feed the per-scan and per-day spend ledgers."""

from __future__ import annotations

import pytest
from anthropic.types import Usage

from sieve.judge import cost


def test_prices_are_the_published_table() -> None:
    assert cost.PRICES == {
        "claude-haiku-4-5": (1.0, 5.0, 0.10),
        "claude-sonnet-5": (2.0, 10.0, 0.20),
    }


def test_worked_example_from_the_prd_cost_model() -> None:
    usage = Usage(input_tokens=600, output_tokens=250, cache_read_input_tokens=1800)
    assert cost.estimate(usage, "claude-haiku-4-5") == pytest.approx(0.00203, abs=1e-6)


def test_missing_token_counts_are_zero_not_an_error() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=0)
    assert cost.estimate(usage, "claude-haiku-4-5") == pytest.approx(1.0)


def test_cache_writes_are_billed_above_the_input_rate() -> None:
    written = Usage(input_tokens=0, output_tokens=0, cache_creation_input_tokens=1_000_000)
    assert cost.estimate(written, "claude-haiku-4-5") == pytest.approx(1.25)


def test_sonnet_costs_more_than_haiku_for_the_same_usage() -> None:
    usage = Usage(input_tokens=600, output_tokens=250, cache_read_input_tokens=1800)
    assert cost.estimate(usage, "claude-sonnet-5") > cost.estimate(usage, "claude-haiku-4-5")


def test_an_unpriced_model_fails_loudly_rather_than_billing_zero() -> None:
    with pytest.raises(cost.UnpricedModelError, match="claude-opus-5"):
        cost.estimate(Usage(input_tokens=1, output_tokens=1), "claude-opus-5")
