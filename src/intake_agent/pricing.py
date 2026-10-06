"""Per-token prices and cost estimates computed from the API's usage figures.

Prices are US dollars per million tokens for the Claude API, as published in October 2026.  The
invoice is the authority.  A cost here is an estimate from `usage`, never a bill.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write_5m: float


PRICES_PER_MTOK: dict[str, Price] = {
    "claude-opus-5-5": Price(input=4.00, output=20.00, cache_read=0.20, cache_write_5m=5.00),
    "claude-opus-5": Price(input=5.00, output=25.00, cache_read=0.50, cache_write_5m=6.25),
    "claude-opus-4-8": Price(input=5.00, output=25.00, cache_read=0.50, cache_write_5m=6.25),
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.20, cache_write_5m=2.50),
    "claude-haiku-4-5": Price(input=1.00, output=5.00, cache_read=0.10, cache_write_5m=1.25),
}


@dataclass(frozen=True)
class TokenCounts:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


def cost_usd(counts: TokenCounts) -> float | None:
    """Estimated cost of one request or attempt, or None for a model with no listed price."""
    price = PRICES_PER_MTOK.get(counts.model)
    if price is None:
        return None
    return (
        counts.input_tokens * price.input
        + counts.output_tokens * price.output
        + counts.cache_creation_input_tokens * price.cache_write_5m
        + counts.cache_read_input_tokens * price.cache_read
    ) / 1_000_000
