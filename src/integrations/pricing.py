"""List-price model for vendors that do not report cost per response.

These are vendor price-list figures (USD) kept in one place so a recorded call's
tokens and search count can be turned into a modelled cost. They are a
starting point, not truth: the usage ledger exists precisely so observed spend
can replace them. Perplexity reports its own cost per response and needs no
card; SerpApi and Semrush are priced by plan and use the configured per-search
and per-unit settings instead.

Prices are per 1,000,000 tokens: (input, output, cached input). Search fees are
per invocation.
"""

from __future__ import annotations

import re

__all__ = ["PRICE_PER_MILLION", "SEARCH_FEE_USD", "modelled_cost", "price_card"]

PRICE_PER_MILLION: dict[str, tuple[float, float, float]] = {
    # OpenAI (Responses API text tokens).
    "gpt-4o-mini": (0.15, 0.60, 0.075),
    "gpt-4o": (2.50, 10.00, 1.25),
    "gpt-4.1-nano": (0.10, 0.40, 0.025),
    "gpt-4.1-mini": (0.40, 1.60, 0.10),
    "gpt-4.1": (2.00, 8.00, 0.50),
    "gpt-5-nano": (0.05, 0.40, 0.005),
    "gpt-5-mini": (0.25, 2.00, 0.025),
    "gpt-5": (1.25, 10.00, 0.125),
    # Google Gemini (paid tier, prompts <= 200k tokens).
    "gemini-2.0-flash": (0.10, 0.40, 0.025),
    "gemini-2.5-flash-lite": (0.10, 0.40, 0.025),
    "gemini-2.5-flash": (0.30, 2.50, 0.075),
    "gemini-2.5-pro": (1.25, 10.00, 0.31),
    # Anthropic Messages API (first-party rates; cache reads at 10% of input).
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
}
"""Exact model ids (date suffixes stripped) to (input, output, cached) USD per 1M tokens."""

SEARCH_FEE_USD: dict[str, float] = {
    "openai": 0.010,  # web_search tool: $10 per 1,000 calls (Responses API)
    "gemini": 0.035,  # Grounding with Google Search: $35 per 1,000 grounded prompts
}
"""Per-search-invocation fee by vendor, for vendors that bill searches separately."""

_DATE_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")
_FAMILY_FALLBACKS: tuple[tuple[str, str], ...] = (
    # Newer or preview ids without a card borrow the nearest family card.
    ("gpt-4o-mini", "gpt-4o-mini"),
    ("gpt-4o", "gpt-4o"),
    ("gpt-4.1-nano", "gpt-4.1-nano"),
    ("gpt-4.1-mini", "gpt-4.1-mini"),
    ("gpt-4.1", "gpt-4.1"),
    ("gpt-5-nano", "gpt-5-nano"),
    ("gpt-5-mini", "gpt-5-mini"),
    ("gpt-5", "gpt-5"),
    ("flash-lite", "gemini-2.5-flash-lite"),
    ("flash", "gemini-2.5-flash"),
    ("haiku", "claude-haiku-4-5"),
    ("sonnet", "claude-sonnet-5"),
    ("opus", "claude-opus-5"),
    ("pro", "gemini-2.5-pro"),
)


def _normalise(model: str) -> str:
    name = model.strip().lower()
    if name.startswith("models/"):
        name = name[len("models/") :]
    return _DATE_SUFFIX.sub("", name)


def price_card(vendor: str, model: str | None) -> tuple[float, float, float] | None:
    """Price card for `model`, exact first, then by family; None if unknown."""
    if not model:
        return None
    name = _normalise(model)
    card = PRICE_PER_MILLION.get(name)
    if card is not None:
        return card
    families = (
        [f for f in _FAMILY_FALLBACKS if f[1].startswith("gemini")]
        if vendor == "gemini"
        else [f for f in _FAMILY_FALLBACKS if f[1].startswith("gpt")]
        if vendor == "openai"
        else []
    )
    for needle, target in families:
        if needle in name:
            return PRICE_PER_MILLION[target]
    return None


def modelled_cost(
    vendor: str,
    model: str | None,
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_tokens: int | None = None,
    search_calls: int = 0,
) -> float | None:
    """USD for one call from list prices; None when the model has no card."""
    card = price_card(vendor, model)
    if card is None:
        return None
    in_price, out_price, cached_price = card
    cached = cached_tokens or 0
    billable_input = max((input_tokens or 0) - cached, 0)
    tokens_usd = (
        billable_input * in_price + cached * cached_price + (output_tokens or 0) * out_price
    ) / 1_000_000
    search_usd = search_calls * SEARCH_FEE_USD.get(vendor, 0.0)
    return round(tokens_usd + search_usd, 8)
