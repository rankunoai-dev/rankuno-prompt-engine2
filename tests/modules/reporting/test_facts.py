"""The fact sheet: pooled rates, bands, deltas and what the brand opts out of."""

from __future__ import annotations

import pytest

from src.core.branding import Brand
from src.integrations.schemas import Engine
from src.modules.reporting.facts import build_fact_sheet, window_label
from src.modules.reporting.schemas import WindowFact


def _kpi(sheet, key):
    return next(k for k in sheet.kpis if k.key == key)


def test_rates_are_pooled_over_counts_not_averaged_over_prompts(project, insights, positions):
    """30 of 60 answers cited, not the mean of two per-prompt rates."""
    sheet = build_fact_sheet(project, insights, positions, brand=project.brand)
    cited = _kpi(sheet, "citation_rate")
    # 18 + 12 + 0 + 0 cited out of 120 answered samples.
    assert cited.value == pytest.approx(30 / 120)
    assert cited.low is not None and cited.high is not None
    assert cited.low < cited.value < cited.high


def test_previous_window_gives_every_rate_a_delta(project, insights, positions, previous_positions):
    """With a prior window the sheet carries the change; without it, nothing."""
    with_prior = build_fact_sheet(
        project, insights, positions, brand=project.brand, previous_positions=previous_positions
    )
    cited = _kpi(with_prior, "citation_rate")
    assert cited.previous == pytest.approx(63 / 120)
    assert cited.delta is not None and cited.delta < 0

    without = build_fact_sheet(project, insights, positions, brand=project.brand)
    assert _kpi(without, "citation_rate").previous is None
    assert _kpi(without, "citation_rate").delta is None


def test_share_of_voice_is_the_client_slice_of_cited_domains(project, insights, positions):
    """The client's domains are marked and the shares are normalised."""
    sheet = build_fact_sheet(project, insights, positions, brand=project.brand)
    assert [c.domain for c in sheet.competitors][0] in {"sap.com", "coupa.com", "gep.com"}
    client = [c for c in sheet.competitors if c.is_client]
    assert [c.domain for c in client] == ["gep.com"]
    share = _kpi(sheet, "share_of_voice")
    assert 0.0 < share.value < 1.0


def test_gemini_is_named_as_the_engine_that_ignores_the_market(project, insights, positions):
    """ADR 0023: the report must not imply a market Gemini cannot honour."""
    sheet = build_fact_sheet(project, insights, positions, brand=project.brand)
    assert sheet.engines_without_locale == ["Gemini"]
    assert {e.engine: e.honours_locale for e in sheet.engines}[Engine.GEMINI] is False


def test_sections_can_be_left_out_and_spend_needs_the_brand_to_opt_in(project, insights, positions):
    """Excluded sections are empty, and cost is never shown unless asked for."""
    sheet = build_fact_sheet(
        project,
        insights,
        positions,
        brand=project.brand,
        spend_usd=12.40,
        include_actions=False,
        include_sentiment=False,
        include_pages=False,
    )
    assert sheet.actions == []
    assert sheet.sentiment == []
    assert sheet.client_pages == [] and sheet.winning_pages == []
    assert sheet.spend_usd is None

    opted_in = build_fact_sheet(
        project,
        insights,
        positions,
        brand=Brand(show_spend=True),
        spend_usd=12.40,
    )
    assert opted_in.spend_usd == 12.40


def test_only_open_actions_and_the_clients_own_sentiment_reach_the_sheet(
    project, insights, positions
):
    """A closed action is not a recommendation, and rivals' tone is not the topic."""
    insights.actions[0].status = "done"
    sheet = build_fact_sheet(project, insights, positions, brand=project.brand)
    assert sheet.actions == []
    assert [s.entity for s in sheet.sentiment] == ["client"]
    assert sheet.sentiment[0].attributes == ["pricing", "support"]


def test_window_label_reads_as_a_date_range():
    """The label the cover and the narrative share."""
    from datetime import UTC, datetime

    window = WindowFact(
        computed_from="consolidation",
        crawls=6,
        prompts=20,
        samples=240,
        first_run_at=datetime(2026, 9, 1, tzinfo=UTC),
        last_run_at=datetime(2026, 9, 14, tzinfo=UTC),
    )
    assert window_label(window) == "1-14 Sep 2026 · 6 crawl(s)"
    assert window_label(WindowFact(computed_from="none", crawls=0, prompts=0, samples=0)) == (
        "0 crawl(s)"
    )


def test_raw_platform_ids_never_reach_a_client_report(project, insights, positions):
    """The insight engine writes CHATGPT_SEARCH; a client reads ChatGPT Search."""
    insights.actions[0].title = "CHATGPT_SEARCH describes the brand negatively"
    insights.actions[0].prescription = "Fix the page PERPLEXITY cites."
    insights.changes[0].text = "GOOGLE_AI_OVERVIEW no longer cites you"
    sheet = build_fact_sheet(project, insights, positions, brand=project.brand)
    assert sheet.actions[0].title == "ChatGPT Search describes the brand negatively"
    assert sheet.actions[0].prescription == "Fix the page Perplexity cites."
    assert sheet.changes[0].text == "Google AI Overview no longer cites you"
