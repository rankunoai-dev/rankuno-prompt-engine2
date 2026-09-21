"""Costing report: aggregation, recommendations, filters and text rendering."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.integrations.usage import ApiCall, UsageLedger
from src.modules.prompt_tracking.costing import (
    SAFETY_MARGIN,
    build_cost_report,
    format_cost_report,
)

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)


def _seed(ledger: UsageLedger) -> None:
    rows = [
        # OpenAI: modelled costs, one error.
        ApiCall(
            vendor="openai",
            operation="responses.create",
            ts=NOW,
            run_id="r1",
            source="cli",
            estimated_cost_usd=0.03,
            modelled_cost_usd=0.012,
            input_tokens=1000,
            output_tokens=500,
            search_calls=1,
            latency_ms=4000,
        ),
        ApiCall(
            vendor="openai",
            operation="responses.create",
            ts=NOW,
            run_id="r1",
            source="cli",
            estimated_cost_usd=0.03,
            modelled_cost_usd=0.008,
            input_tokens=800,
            output_tokens=300,
            search_calls=1,
            latency_ms=6000,
        ),
        ApiCall(
            vendor="openai",
            operation="responses.create",
            ts=NOW,
            run_id="r1",
            source="cli",
            estimated_cost_usd=0.03,
            status="error",
            error="HTTP 500",
            latency_ms=100,
        ),
        # Perplexity: vendor-reported.
        ApiCall(
            vendor="perplexity",
            operation="v1.responses",
            ts=NOW,
            run_id="r2",
            source="control_plane",
            estimated_cost_usd=0.02,
            vendor_cost_usd=0.0098,
            modelled_cost_usd=None,
            input_tokens=4663,
            output_tokens=2452,
            search_calls=1,
            latency_ms=42000,
        ),
        # SerpApi: two searches priced by plan.
        ApiCall(
            vendor="serpapi",
            operation="google_search",
            ts=NOW,
            run_id="r2",
            source="control_plane",
            estimated_cost_usd=0.01,
            modelled_cost_usd=0.01,
            search_calls=1,
            latency_ms=150,
        ),
        ApiCall(
            vendor="serpapi",
            operation="google_ai_overview",
            ts=NOW,
            run_id="r2",
            source="control_plane",
            estimated_cost_usd=0.01,
            modelled_cost_usd=0.01,
            search_calls=1,
            latency_ms=7000,
        ),
        # Semrush: units.
        ApiCall(
            vendor="semrush",
            operation="phrase_questions",
            ts=NOW,
            run_id="r2",
            source="control_plane",
            estimated_cost_usd=0.0025,
            modelled_cost_usd=0.002,
            units=400,
            latency_ms=900,
        ),
        # Gemini: refused by breaker, estimate only.
        ApiCall(
            vendor="gemini",
            operation="generate_content",
            ts=NOW,
            source="live_check",
            estimated_cost_usd=0.04,
            status="refused",
            error="circuit open",
        ),
        # Old call outside the window.
        ApiCall(
            vendor="openai",
            operation="responses.create",
            ts=NOW - timedelta(days=10),
            run_id="old",
            source="cli",
            estimated_cost_usd=0.03,
            modelled_cost_usd=0.02,
        ),
    ]
    for row in rows:
        ledger.record(row)


def test_report_aggregates_per_vendor_run_and_source(tmp_path, settings):
    ledger = UsageLedger(tmp_path / "u.sqlite")
    _seed(ledger)
    report = build_cost_report(ledger, settings, since=NOW - timedelta(days=1))

    assert report.calls == 8  # the 10-day-old call is excluded
    assert report.by_source == {"cli": 3, "control_plane": 4, "live_check": 1}
    by = {v.vendor: v for v in report.vendors}
    openai = by["openai"]
    assert (openai.calls, openai.ok, openai.errors, openai.refused) == (3, 2, 1, 0)
    assert openai.input_tokens == 1800 and openai.output_tokens == 800 and openai.search_calls == 2
    assert openai.estimated_usd == 0.09 and openai.actual_usd == 0.02
    assert openai.mean_actual_per_ok_call == 0.01
    assert openai.latency_p50_ms == 4000 and openai.latency_p95_ms == 6000
    assert by["perplexity"].vendor_reported_usd == 0.0098 and by["perplexity"].actual_usd == 0.0098
    assert by["gemini"].refused == 1 and by["gemini"].actual_usd == 0.0
    assert by["semrush"].units == 400
    assert report.total_estimated_usd == round(0.09 + 0.02 + 0.02 + 0.0025 + 0.04, 4)
    assert report.total_actual_usd == round(0.02 + 0.0098 + 0.02 + 0.002, 4)

    runs = {r.run_id: r for r in report.runs}
    assert runs["r1"].calls == 3 and runs["r1"].actual_usd == 0.02
    assert runs["r2"].by_vendor == {"perplexity": 0.0098, "semrush": 0.002, "serpapi": 0.02}


def test_recommendations_use_observed_means_with_margin(tmp_path, settings):
    ledger = UsageLedger(tmp_path / "u.sqlite")
    _seed(ledger)
    # `since` rather than `days`: the rows are seeded at a frozen NOW, so a window
    # measured from the real clock empties out the day after NOW.
    report = build_cost_report(ledger, settings, since=NOW - timedelta(days=1))
    recs = {r.setting: r for r in report.recommendations}
    assert set(recs) == {
        "cost_openai_search_call_usd",
        "cost_perplexity_call_usd",
        "cost_serpapi_call_usd",
        "cost_semrush_unit_usd",
    }  # gemini had no ok call
    openai = recs["cost_openai_search_call_usd"]
    assert openai.current == settings.cost_openai_search_call_usd
    assert openai.observed_mean == 0.01 and openai.basis == "modelled" and openai.basis_calls == 2
    assert openai.suggested == 0.0115  # ceil(0.01 * 1.15 * 1e4) / 1e4
    pplx = recs["cost_perplexity_call_usd"]
    assert pplx.basis == "vendor-reported" and pplx.suggested >= 0.0098 * SAFETY_MARGIN
    serp = recs["cost_serpapi_call_usd"]
    assert serp.unit == "per search" and serp.observed_mean == 0.01
    semrush = recs["cost_semrush_unit_usd"]
    assert semrush.unit == "per unit" and semrush.observed_mean == 0.000005
    assert any("openai" in n for n in report.notes)


def test_run_filter_and_empty_ledger(tmp_path, settings):
    ledger = UsageLedger(tmp_path / "u.sqlite")
    _seed(ledger)
    only = build_cost_report(ledger, settings, run_ids=["r2"])
    assert only.calls == 4 and [r.run_id for r in only.runs] == ["r2"]
    empty = build_cost_report(UsageLedger(tmp_path / "e.sqlite"), settings)
    assert empty.calls == 0 and empty.vendors == [] and empty.recommendations == []


def test_days_is_shorthand_for_a_window_ending_now(tmp_path, settings):
    """`days` resolves against the real clock, so only wide windows are stable."""
    ledger = UsageLedger(tmp_path / "u.sqlite")
    _seed(ledger)
    assert build_cost_report(ledger, settings, days=36_500).calls == 9
    assert build_cost_report(ledger, settings, days=0).calls == 0


def test_format_cost_report_is_readable(tmp_path, settings):
    ledger = UsageLedger(tmp_path / "u.sqlite")
    _seed(ledger)
    text = format_cost_report(build_cost_report(ledger, settings, since=NOW - timedelta(days=1)))
    assert "Usage ledger: 8 calls" in text
    assert "openai" in text and "perplexity" in text
    assert "cost_openai_search_call_usd: current 0.03 -> suggested 0.0115" in text
    assert "By run:" in text and "r2:" in text
    assert "Note:" in text


def test_exclude_sources_drops_tagged_rows(tmp_path, settings):
    ledger = UsageLedger(tmp_path / "x.sqlite")
    ledger.record(
        ApiCall(
            vendor="openai", operation="responses", source="control_plane", estimated_cost_usd=0.03
        )
    )
    ledger.record(
        ApiCall(vendor="openai", operation="responses", source="demo", estimated_cost_usd=0.03)
    )
    ledger.record(
        ApiCall(vendor="serpapi", operation="search", source="demo", estimated_cost_usd=0.01)
    )
    report = build_cost_report(ledger, settings, exclude_sources=["demo"])
    assert report.calls == 1
    assert report.by_source == {"control_plane": 1}
    assert [v.vendor for v in report.vendors] == ["openai"]
    assert build_cost_report(ledger, settings).calls == 3


def test_prompt_scope_counts_direct_engine_calls_and_reports_the_shared_remainder(
    tmp_path, settings
):
    """Per-prompt spend is a floor: harvest and keyword-rank rows carry no prompt id."""
    ledger = UsageLedger(tmp_path / "u.sqlite")
    rows = [
        ApiCall(
            vendor="openai",
            operation="responses.create",
            ts=NOW,
            run_id="r9",
            source="control_plane",
            prompt_id="aaaaaaaaaaaaaaaa",
            engine="CHATGPT_SEARCH",
            estimated_cost_usd=0.03,
            modelled_cost_usd=0.01,
        ),
        ApiCall(
            vendor="perplexity",
            operation="v1.responses",
            ts=NOW,
            run_id="r9",
            source="control_plane",
            prompt_id="aaaaaaaaaaaaaaaa",
            engine="PERPLEXITY",
            estimated_cost_usd=0.02,
            vendor_cost_usd=0.009,
        ),
        ApiCall(
            vendor="openai",
            operation="responses.create",
            ts=NOW,
            run_id="r9",
            source="control_plane",
            prompt_id="bbbbbbbbbbbbbbbb",
            engine="CHATGPT_SEARCH",
            estimated_cost_usd=0.03,
            modelled_cost_usd=0.011,
        ),
        # shared by every prompt in the run: no prompt id by construction
        ApiCall(
            vendor="semrush",
            operation="phrase_all",
            ts=NOW,
            run_id="r9",
            source="control_plane",
            units=400,
            estimated_cost_usd=0.002,
            modelled_cost_usd=0.002,
        ),
        ApiCall(
            vendor="serpapi",
            operation="search",
            ts=NOW,
            run_id="r9",
            source="control_plane",
            engine="KEYWORD_RANK",
            estimated_cost_usd=0.01,
            modelled_cost_usd=0.01,
        ),
        # a different run entirely: must not leak into the remainder
        ApiCall(
            vendor="serpapi",
            operation="search",
            ts=NOW,
            run_id="r10",
            source="control_plane",
            estimated_cost_usd=0.01,
            modelled_cost_usd=0.01,
        ),
    ]
    for row in rows:
        ledger.record(row)

    report = build_cost_report(
        ledger, settings, since=NOW - timedelta(days=1), prompt_id="aaaaaaaaaaaaaaaa"
    )
    assert report.attribution == "direct_engine_calls"
    assert report.calls == 2 and {v.vendor for v in report.vendors} == {"openai", "perplexity"}
    assert report.total_actual_usd == 0.019
    assert report.unattributed_calls == 2  # semrush + keyword rank in r9, not the r10 row
    assert report.unattributed_actual_usd == 0.012
    assert any("shared by every prompt" in n for n in report.notes)

    whole = build_cost_report(ledger, settings, since=NOW - timedelta(days=1))
    assert whole.attribution == "all" and whole.unattributed_calls == 0 and whole.calls == 6


def test_usage_context_none_clears_an_inherited_prompt(tmp_path):
    """A keyword-rank lookup nested in a prompt's context must not be charged to it."""
    from src.integrations.usage import current_usage_context, usage_context

    with usage_context(source="t", prompt_id="aaaaaaaaaaaaaaaa", engine="CHATGPT_SEARCH"):
        assert current_usage_context()["prompt_id"] == "aaaaaaaaaaaaaaaa"
        with usage_context(engine="KEYWORD_RANK", prompt_id=None):
            inner = current_usage_context()
            assert "prompt_id" not in inner and inner["engine"] == "KEYWORD_RANK"
        assert current_usage_context()["prompt_id"] == "aaaaaaaaaaaaaaaa"  # restored
