"""Costing report over the usage ledger: volume, spend and parameter proposals.

Reads `api_calls` (every vendor request with its estimated, vendor-reported and
modelled cost), aggregates per vendor, per run and per source, and proposes a
value for each `COST_*` setting from what was actually observed. The proposal
is the mean actual cost per successful call (per unit for Semrush) with a
safety margin, so the ledger's reservation stays a slight over-estimate.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from pydantic import Field

from src.core.config import Settings
from src.core.schemas import StrictModel
from src.integrations.usage import ApiCall, UsageLedger

__all__ = [
    "SAFETY_MARGIN",
    "CostRecommendation",
    "CostReport",
    "RunCost",
    "VendorCost",
    "build_cost_report",
    "format_cost_report",
]

SAFETY_MARGIN = 1.15
"""Proposed parameter = observed mean x this, so reservations err on the high side."""

_SETTING_FOR_VENDOR: dict[str, tuple[str, str]] = {
    "openai": ("cost_openai_search_call_usd", "per call"),
    "perplexity": ("cost_perplexity_call_usd", "per call"),
    "gemini": ("cost_gemini_grounded_call_usd", "per call"),
    "serpapi": ("cost_serpapi_call_usd", "per search"),
    "semrush": ("cost_semrush_unit_usd", "per unit"),
}


class VendorCost(StrictModel):
    """Volume and spend for one vendor."""

    vendor: str
    calls: int = Field(ge=0)
    ok: int = Field(ge=0)
    errors: int = Field(ge=0)
    refused: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    search_calls: int = Field(ge=0)
    units: int = Field(ge=0)
    estimated_usd: float = Field(ge=0.0)
    actual_usd: float = Field(ge=0.0, description="Vendor-reported, else modelled, else estimate.")
    vendor_reported_usd: float = Field(ge=0.0)
    modelled_usd: float = Field(ge=0.0)
    calls_with_vendor_cost: int = Field(ge=0)
    calls_with_modelled_cost: int = Field(ge=0)
    mean_estimated_per_call: float = Field(ge=0.0)
    mean_actual_per_ok_call: float = Field(ge=0.0)
    latency_p50_ms: float = Field(ge=0.0)
    latency_p95_ms: float = Field(ge=0.0)


class RunCost(StrictModel):
    """Spend attributed to one tracker run."""

    run_id: str
    calls: int = Field(ge=0)
    estimated_usd: float = Field(ge=0.0)
    actual_usd: float = Field(ge=0.0)
    by_vendor: dict[str, float] = Field(default_factory=dict)


class CostRecommendation(StrictModel):
    """A proposed value for one `COST_*` setting."""

    setting: str
    vendor: str
    unit: str
    current: float = Field(ge=0.0)
    observed_mean: float = Field(ge=0.0)
    suggested: float = Field(ge=0.0)
    basis_calls: int = Field(ge=0)
    basis: str = Field(description="vendor-reported | modelled | estimate-only")


class CostReport(StrictModel):
    """Everything the CLI, API and UI show about spend."""

    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    since: datetime | None = None
    calls: int = Field(ge=0)
    total_estimated_usd: float = Field(ge=0.0)
    total_actual_usd: float = Field(ge=0.0)
    by_source: dict[str, int] = Field(default_factory=dict)
    vendors: list[VendorCost] = Field(default_factory=list)
    runs: list[RunCost] = Field(default_factory=list)
    recommendations: list[CostRecommendation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    attribution: str = Field(
        default="all",
        pattern="^(all|direct_engine_calls)$",
        description="`direct_engine_calls` when scoped to a prompt: only the engine "
        "samples made inside that prompt's context are counted.",
    )
    unattributed_calls: int = Field(
        default=0,
        ge=0,
        description="Calls in the same runs that carry no prompt id (harvest, keyword "
        "rank, redirect resolution). Shared across every prompt in the run.",
    )
    unattributed_actual_usd: float = Field(default=0.0, ge=0.0)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(pct / 100 * len(ordered)) - 1))
    return round(ordered[index], 1)


def _vendor_cost(vendor: str, calls: list[ApiCall]) -> VendorCost:
    ok = [c for c in calls if c.status == "ok"]
    estimated = sum(c.estimated_cost_usd for c in calls)
    actual = sum(c.actual_cost_usd for c in ok)
    return VendorCost(
        vendor=vendor,
        calls=len(calls),
        ok=len(ok),
        errors=sum(1 for c in calls if c.status == "error"),
        refused=sum(1 for c in calls if c.status == "refused"),
        input_tokens=sum(c.input_tokens or 0 for c in calls),
        output_tokens=sum(c.output_tokens or 0 for c in calls),
        search_calls=sum(c.search_calls for c in calls),
        units=sum(c.units for c in calls),
        estimated_usd=round(estimated, 4),
        actual_usd=round(actual, 4),
        vendor_reported_usd=round(sum(c.vendor_cost_usd or 0.0 for c in ok), 4),
        modelled_usd=round(sum(c.modelled_cost_usd or 0.0 for c in ok), 4),
        calls_with_vendor_cost=sum(1 for c in ok if c.vendor_cost_usd is not None),
        calls_with_modelled_cost=sum(1 for c in ok if c.modelled_cost_usd is not None),
        mean_estimated_per_call=round(estimated / len(calls), 5) if calls else 0.0,
        mean_actual_per_ok_call=round(actual / len(ok), 5) if ok else 0.0,
        latency_p50_ms=_percentile([c.latency_ms for c in ok], 50),
        latency_p95_ms=_percentile([c.latency_ms for c in ok], 95),
    )


def _basis(vendor: VendorCost) -> str:
    """How the vendor's "actual" figure was obtained, for the reader's trust."""
    reported = vendor.calls_with_vendor_cost
    modelled = vendor.calls_with_modelled_cost
    estimate_only = vendor.ok - reported - modelled
    if reported == vendor.ok:
        return "vendor-reported"
    if reported + modelled == vendor.ok:
        return "modelled"
    if reported == 0 and modelled == 0:
        return "estimate-only"
    return f"mixed: {reported} vendor-reported, {modelled} modelled, {estimate_only} estimate-only"


def _recommend(vendor: VendorCost, settings: Settings) -> CostRecommendation | None:
    mapping = _SETTING_FOR_VENDOR.get(vendor.vendor)
    if mapping is None or vendor.ok == 0:
        return None
    setting, unit = mapping
    basis = _basis(vendor)
    if vendor.vendor == "semrush":
        observed = vendor.actual_usd / vendor.units if vendor.units else 0.0
    elif vendor.vendor == "serpapi":
        observed = vendor.actual_usd / vendor.search_calls if vendor.search_calls else 0.0
    else:
        observed = vendor.mean_actual_per_ok_call
    digits = 7 if vendor.vendor == "semrush" else 4
    suggested = math.ceil(observed * SAFETY_MARGIN * 10**digits) / 10**digits
    return CostRecommendation(
        setting=setting,
        vendor=vendor.vendor,
        unit=unit,
        current=float(getattr(settings, setting)),
        observed_mean=round(observed, digits),
        suggested=suggested,
        basis_calls=vendor.ok,
        basis=basis,
    )


def build_cost_report(
    ledger: UsageLedger,
    settings: Settings,
    *,
    since: datetime | None = None,
    days: int | None = None,
    run_ids: list[str] | None = None,
    exclude_sources: list[str] | None = None,
    prompt_id: str | None = None,
) -> CostReport:
    """Aggregate the ledger. `days` is shorthand for `since = now - days`.

    `exclude_sources` drops rows by their `source` tag, e.g. `["demo"]` to
    report real spend while seeded demonstration rows sit in the same ledger.

    `prompt_id` narrows to the engine samples made inside that prompt's context.
    That is a floor, not the prompt's true cost: harvest, keyword-rank and
    redirect calls belong to the run, not to any prompt, so they are reported
    beside the direct figure as `unattributed_*` rather than amortised into it.
    """
    if since is None and days is not None:
        since = datetime.now(UTC) - timedelta(days=days)
    excluded = set(exclude_sources or ())
    calls = [
        c
        for c in ledger.calls(since=since, run_ids=run_ids, prompt_id=prompt_id)
        if c.source not in excluded
    ]
    unattributed: list[ApiCall] = []
    if prompt_id is not None:
        shared_runs = sorted({c.run_id for c in calls if c.run_id})
        if shared_runs:
            unattributed = [
                c
                for c in ledger.calls(since=since, run_ids=shared_runs)
                if c.prompt_id is None and c.source not in excluded
            ]
    by_vendor: dict[str, list[ApiCall]] = {}
    by_run: dict[str, list[ApiCall]] = {}
    by_source: dict[str, int] = {}
    for call in calls:
        by_vendor.setdefault(call.vendor, []).append(call)
        by_source[call.source] = by_source.get(call.source, 0) + 1
        if call.run_id:
            by_run.setdefault(call.run_id, []).append(call)

    vendors = [_vendor_cost(v, cs) for v, cs in sorted(by_vendor.items())]
    runs = [
        RunCost(
            run_id=run_id,
            calls=len(cs),
            estimated_usd=round(sum(c.estimated_cost_usd for c in cs), 4),
            actual_usd=round(sum(c.actual_cost_usd for c in cs if c.status == "ok"), 4),
            by_vendor={
                v: round(
                    sum(c.actual_cost_usd for c in cs if c.vendor == v and c.status == "ok"), 4
                )
                for v in sorted({c.vendor for c in cs})
            },
        )
        for run_id, cs in by_run.items()
    ]
    recommendations = [r for r in (_recommend(v, settings) for v in vendors) if r is not None]
    notes: list[str] = []
    for vendor in vendors:
        if not vendor.ok:
            continue
        estimate_only = vendor.ok - vendor.calls_with_vendor_cost - vendor.calls_with_modelled_cost
        if estimate_only:
            notes.append(
                f"{vendor.vendor}: {estimate_only} of {vendor.ok} ok calls carry no "
                "vendor-reported or modelled cost; their actual equals the configured estimate."
            )
        if vendor.calls_with_modelled_cost and vendor.vendor not in ("serpapi", "semrush"):
            notes.append(
                f"{vendor.vendor}: actual cost is modelled from list prices for "
                f"{vendor.calls_with_modelled_cost} of {vendor.ok} calls; verify against "
                "the invoice."
            )
    if prompt_id is not None and unattributed:
        notes.append(
            f"Scoped to one prompt: {len(unattributed)} call(s) in the same run(s) carry no "
            "prompt (harvest, keyword rank, redirects) and are shared by every prompt in "
            "the run; they are reported separately, not amortised."
        )
    return CostReport(
        since=since,
        calls=len(calls),
        total_estimated_usd=round(sum(c.estimated_cost_usd for c in calls), 4),
        total_actual_usd=round(sum(c.actual_cost_usd for c in calls if c.status == "ok"), 4),
        by_source=dict(sorted(by_source.items())),
        vendors=vendors,
        runs=runs,
        recommendations=recommendations,
        notes=notes,
        attribution="direct_engine_calls" if prompt_id is not None else "all",
        unattributed_calls=len(unattributed),
        unattributed_actual_usd=round(
            sum(c.actual_cost_usd for c in unattributed if c.status == "ok"), 4
        ),
    )


def format_cost_report(report: CostReport) -> str:
    """Plain-text rendering for the CLI."""
    lines = [
        f"Usage ledger: {report.calls} calls"
        + (f" since {report.since:%Y-%m-%d %H:%M} UTC" if report.since else ""),
        f"Estimated ${report.total_estimated_usd:.4f} | actual ${report.total_actual_usd:.4f}",
        "By source: " + ", ".join(f"{k}={v}" for k, v in report.by_source.items()),
        "",
        f"{'vendor':<11}{'calls':>6}{'ok':>5}{'err':>5}{'in tok':>9}{'out tok':>9}"
        f"{'search':>7}{'units':>7}{'est $':>9}{'actual $':>10}{'mean/ok':>10}{'p95 ms':>8}",
    ]
    for v in report.vendors:
        lines.append(
            f"{v.vendor:<11}{v.calls:>6}{v.ok:>5}{v.errors + v.refused:>5}{v.input_tokens:>9}"
            f"{v.output_tokens:>9}{v.search_calls:>7}{v.units:>7}{v.estimated_usd:>9.4f}"
            f"{v.actual_usd:>10.4f}{v.mean_actual_per_ok_call:>10.5f}{v.latency_p95_ms:>8.0f}"
        )
    if report.recommendations:
        lines += ["", "Proposed COST_* settings (observed mean x safety margin):"]
        for r in report.recommendations:
            lines.append(
                f"  {r.setting}: current {r.current:g} -> suggested {r.suggested:g} "
                f"({r.unit}, observed {r.observed_mean:g} over {r.basis_calls} ok calls, {r.basis})"
            )
    if report.runs:
        lines += ["", "By run:"]
        for run in sorted(report.runs, key=lambda r: r.actual_usd, reverse=True)[:20]:
            lines.append(
                f"  {run.run_id}: {run.calls} calls, est ${run.estimated_usd:.4f}, "
                f"actual ${run.actual_usd:.4f}"
            )
    for note in report.notes:
        lines.append(f"Note: {note}")
    return "\n".join(lines)
