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
    report = build_cost_report(ledger, settings, days=1)
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


def test_format_cost_report_is_readable(tmp_path, settings):
    ledger = UsageLedger(tmp_path / "u.sqlite")
    _seed(ledger)
    text = format_cost_report(build_cost_report(ledger, settings, days=1))
    assert "Usage ledger: 8 calls" in text
    assert "openai" in text and "perplexity" in text
    assert "cost_openai_search_call_usd: current 0.03 -> suggested 0.0115" in text
    assert "By run:" in text and "r2:" in text
    assert "Note:" in text
