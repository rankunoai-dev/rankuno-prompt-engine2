"""List-price model: card lookup and cost arithmetic."""

from __future__ import annotations

import pytest

from src.integrations.pricing import PRICE_PER_MILLION, modelled_cost, price_card


@pytest.mark.parametrize(
    ("vendor", "model", "expected"),
    [
        ("openai", "gpt-4o-mini", PRICE_PER_MILLION["gpt-4o-mini"]),
        ("openai", "gpt-4o-mini-2024-07-18", PRICE_PER_MILLION["gpt-4o-mini"]),  # date stripped
        ("openai", "GPT-4o", PRICE_PER_MILLION["gpt-4o"]),
        ("openai", "gpt-4.1-mini-2025-04-14", PRICE_PER_MILLION["gpt-4.1-mini"]),
        ("openai", "gpt-5-mini-preview", PRICE_PER_MILLION["gpt-5-mini"]),  # family fallback
        ("gemini", "models/gemini-2.5-flash", PRICE_PER_MILLION["gemini-2.5-flash"]),
        ("gemini", "gemini-3.6-flash", PRICE_PER_MILLION["gemini-2.5-flash"]),  # family fallback
        ("gemini", "gemini-3.1-pro-preview", PRICE_PER_MILLION["gemini-2.5-pro"]),
        ("gemini", "gemini-3.5-flash-lite", PRICE_PER_MILLION["gemini-2.5-flash-lite"]),
        ("perplexity", "perplexity/sonar", None),
        ("openai", None, None),
        ("openai", "", None),
        ("serpapi", "google", None),
    ],
)
def test_price_card_resolution(vendor, model, expected):
    assert price_card(vendor, model) == expected


def test_modelled_cost_arithmetic():
    # gpt-4o-mini: 0.15 in, 0.60 out, 0.075 cached per 1M; $0.01 per search.
    cost = modelled_cost(
        "openai",
        "gpt-4o-mini-2024-07-18",
        input_tokens=10_000,
        output_tokens=2_000,
        cached_tokens=4_000,
        search_calls=2,
    )
    expected = (6_000 * 0.15 + 4_000 * 0.075 + 2_000 * 0.60) / 1_000_000 + 2 * 0.010
    assert cost == pytest.approx(expected, abs=1e-6)


def test_modelled_cost_handles_missing_tokens_and_unknown_model():
    assert modelled_cost("openai", "gpt-4o", input_tokens=None, output_tokens=None) == 0.0
    assert modelled_cost("perplexity", "perplexity/sonar", input_tokens=5, output_tokens=5) is None
    assert modelled_cost(
        "gemini", "gemini-2.5-flash", input_tokens=0, output_tokens=0, search_calls=1
    ) == pytest.approx(0.035)


def test_cached_tokens_never_exceed_input():
    cost = modelled_cost(
        "openai", "gpt-4o-mini", input_tokens=100, output_tokens=0, cached_tokens=500
    )
    assert cost == pytest.approx(500 * 0.075 / 1_000_000, abs=1e-9)
