"""Turns stored analysis into the one object a report may state (ADR 0024).

Pure functions over read models. Nothing here calls a vendor, opens a file or
touches the clock beyond the timestamp it stamps on the sheet, so the whole
document is reproducible from the same window.

The aggregation rule throughout: counts first, rates second. A project-wide
citation rate is `sum(cited_samples) / sum(ok_samples)` across the window's
positions, never the mean of per-prompt rates — averaging rates would weight a
prompt sampled three times the same as one sampled thirty, and the confidence
band computed from the pooled counts would then be a lie.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from src.core.branding import Brand
from src.core.domains import registrable_domain
from src.core.locale import Locale
from src.core.stats import wilson_interval
from src.integrations.schemas import Engine
from src.modules.control_plane.schemas import (
    ConsolidatedPosition,
    InsightsView,
    PositionsView,
    Project,
)
from src.modules.reporting.schemas import (
    MAX_ACTIONS,
    MAX_CHANGES,
    MAX_COMPETITORS,
    MAX_PAGES,
    ActionFact,
    ChangeFact,
    CompetitorFact,
    EngineFact,
    FactSheet,
    Kpi,
    PageFact,
    SentimentFact,
    WindowFact,
)

__all__ = ["ENGINE_LABELS", "build_fact_sheet", "window_label"]

ENGINE_LABELS: dict[Engine, str] = {
    Engine.GOOGLE_AI_OVERVIEW: "Google AI Overview",
    Engine.CHATGPT_SEARCH: "ChatGPT Search",
    Engine.PERPLEXITY: "Perplexity",
    Engine.GEMINI: "Gemini",
}

_MIN_COMPETITOR_SHARE = 0.02


def window_label(window: WindowFact) -> str:
    """Human label for the covered period, e.g. '1-14 Sep 2026 · 6 crawls'."""
    first, last = window.first_run_at, window.last_run_at
    if first is None or last is None:
        return f"{window.crawls} crawl(s)"
    if first.date() == last.date():
        span = first.strftime("%d %b %Y")
    elif (first.year, first.month) == (last.year, last.month):
        span = f"{first.day}-{last.strftime('%d %b %Y')}"
    else:
        span = f"{first.strftime('%d %b')} - {last.strftime('%d %b %Y')}"
    return f"{span} · {window.crawls} crawl(s)"


def _pooled(positions: list[ConsolidatedPosition], attribute: str) -> tuple[int, int]:
    """Successes and trials for `attribute` ('cited_samples' or 'mention_samples')."""
    trials = sum(max(p.samples - p.failed_samples, 0) for p in positions)
    successes = sum(getattr(p, attribute) for p in positions)
    return min(successes, trials), trials


def _rate_kpi(
    key: str,
    label: str,
    positions: list[ConsolidatedPosition],
    previous: list[ConsolidatedPosition],
    attribute: str,
    note: str = "",
) -> Kpi:
    """A pooled rate with its Wilson band and the same rate a window earlier."""
    successes, trials = _pooled(positions, attribute)
    value = successes / trials if trials else 0.0
    band = wilson_interval(successes, trials)
    prior: float | None = None
    if previous:
        prior_successes, prior_trials = _pooled(previous, attribute)
        prior = prior_successes / prior_trials if prior_trials else None
    return Kpi(
        key=key,
        label=label,
        value=value,
        unit="percent",
        low=band[0] if band else None,
        high=band[1] if band else None,
        previous=prior,
        delta=None if prior is None else value - prior,
        note=note,
    )


def _domain_shares(positions: list[ConsolidatedPosition]) -> dict[str, float]:
    """Mean share of cited sources per domain across the window's positions."""
    totals: dict[str, float] = defaultdict(float)
    for position in positions:
        for domain, share in position.cited_domain_share.items():
            totals[registrable_domain(domain) or domain] += share
    if not positions:
        return {}
    return {domain: total / len(positions) for domain, total in totals.items()}


def _competitors(
    positions: list[ConsolidatedPosition], client_domains: list[str]
) -> list[CompetitorFact]:
    """Who holds the citations, client included, biggest share first."""
    client = {registrable_domain(d) or d for d in client_domains}
    shares = _domain_shares(positions)
    ranked = sorted(shares.items(), key=lambda item: item[1], reverse=True)
    out = [
        CompetitorFact(domain=domain, share=min(share, 1.0), is_client=domain in client)
        for domain, share in ranked
        if share >= _MIN_COMPETITOR_SHARE
    ]
    return out[:MAX_COMPETITORS]


def _share_of_voice(competitors: list[CompetitorFact]) -> float:
    """The client's slice of the cited sources, 0 when never cited."""
    total = sum(c.share for c in competitors)
    if total <= 0:
        return 0.0
    client = sum(c.share for c in competitors if c.is_client)
    return client / total


def _engine_facts(
    insights: InsightsView, positions: list[ConsolidatedPosition]
) -> list[EngineFact]:
    """One row per platform the project ran, in the tracker's canonical order."""
    prompts_by_engine: dict[Engine, set[str]] = defaultdict(set)
    for position in positions:
        prompts_by_engine[position.engine].add(position.prompt_id)
    out: list[EngineFact] = []
    for health in insights.health:
        out.append(
            EngineFact(
                engine=health.engine,
                label=ENGINE_LABELS.get(health.engine, health.engine.value),
                verdict=health.verdict,
                losing_to=health.losing_to,
                cited_rate=health.cited_rate,
                cited_rate_low=health.cited_rate_low,
                cited_rate_high=health.cited_rate_high,
                mention_rate=health.mention_rate,
                best_rank=health.best_rank,
                delta_cited_rate=health.delta_cited_rate,
                volatility=health.volatility,
                prompts=health.prompts or len(prompts_by_engine.get(health.engine, ())),
                samples=health.samples,
                crawls=health.crawls,
                honours_locale=health.engine.honours_locale,
            )
        )
    return out


def _pages(insights: InsightsView, *, client: bool) -> list[PageFact]:
    """Cited pages, most-cited first, with their exact URLs (cycle ui-0006)."""
    source = insights.client_pages if client else insights.winning_pages
    return [
        PageFact(
            url=page.url,
            domain=page.domain,
            title=page.title,
            citations=page.citations,
            engines=list(page.engines),
            is_client=page.is_client,
        )
        for page in source[:MAX_PAGES]
    ]


def _sentiment(insights: InsightsView, brand: str) -> list[SentimentFact]:
    """The client's own sentiment rows; competitors are not a client-report topic."""
    out: list[SentimentFact] = []
    for profile in insights.sentiment:
        if profile.entity not in {"client", brand}:
            continue
        if profile.judged <= 0:
            continue
        worst = profile.worst[0] if profile.worst else None
        out.append(
            SentimentFact(
                engine=profile.engine,
                entity=profile.entity,
                judged=profile.judged,
                negative_share=profile.negative_share,
                negative_share_low=profile.negative_share_low,
                negative_share_high=profile.negative_share_high,
                attributes=[a.attribute for a in profile.attributes[:3]],
                worst_quote=worst.text if worst else None,
                worst_url=worst.url if worst else None,
            )
        )
    return out


def _locale_label(locale: Locale | None) -> str:
    """Market label for the cover; the server default when the project has none."""
    return locale.label if locale else "Server default"


def build_fact_sheet(
    project: Project,
    insights: InsightsView,
    positions: PositionsView,
    *,
    brand: Brand,
    previous_positions: list[ConsolidatedPosition] | None = None,
    spend_usd: float | None = None,
    include_actions: bool = True,
    include_sentiment: bool = True,
    include_pages: bool = True,
    now: datetime | None = None,
) -> FactSheet:
    """Everything the report may state, computed from one window.

    `previous_positions` are the consolidated positions of the window before
    this one; without them the sheet simply carries no deltas rather than
    inventing a baseline.
    """
    current = positions.positions
    prior = previous_positions or []
    consolidation = positions.consolidation
    window = WindowFact(
        consolidation_id=insights.basis.consolidation_id,
        computed_from=insights.basis.computed_from,
        first_run_at=consolidation.first_run_at if consolidation else None,
        last_run_at=consolidation.last_run_at if consolidation else None,
        crawls=insights.basis.crawls,
        prompts=len({p.prompt_id for p in current}),
        samples=insights.basis.samples,
        low_confidence=insights.basis.low_confidence,
        previous_consolidation_id=positions.history[1].id if len(positions.history) > 1 else None,
    )
    competitors = _competitors(current, project.client.domains)
    prior_competitors = _competitors(prior, project.client.domains) if prior else []
    share = _share_of_voice(competitors)
    prior_share = _share_of_voice(prior_competitors) if prior_competitors else None
    kpis = [
        _rate_kpi(
            "citation_rate",
            "Citation rate",
            current,
            prior,
            "cited_samples",
            note="Answers that linked the brand.",
        ),
        _rate_kpi(
            "mention_rate",
            "Mention rate",
            current,
            prior,
            "mention_samples",
            note="Answers that named the brand, linked or not.",
        ),
        Kpi(
            key="share_of_voice",
            label="Share of citations",
            value=share,
            unit="percent",
            previous=prior_share,
            delta=None if prior_share is None else share - prior_share,
            note="The brand's slice of every source these answers cited.",
        ),
        Kpi(
            key="prompts",
            label="Prompts tracked",
            value=float(window.prompts),
            unit="count",
            note=f"Across {len({p.engine for p in current})} platform(s).",
        ),
    ]
    actions = (
        [
            ActionFact(
                title=card.title,
                prescription=card.prescription,
                impact_score=card.impact_score,
                engine=card.engine,
                subtopic=card.subtopic,
                metric=card.metric,
                urls=list(card.evidence.urls[:4]),
                status=card.status,
            )
            for card in insights.actions
            if card.status == "open"
        ][:MAX_ACTIONS]
        if include_actions
        else []
    )
    return FactSheet(
        generated_at=now or datetime.now(UTC),
        project_id=project.id,
        project_name=project.name,
        client_brand=brand.client_name or project.client.brand_name,
        client_domain=project.client.domains[0],
        lob=project.client.lob,
        locale=project.locale,
        locale_label=_locale_label(project.locale),
        engines_without_locale=[
            ENGINE_LABELS.get(e, e.value) for e in project.engines if not e.honours_locale
        ],
        window=window,
        kpis=kpis,
        engines=_engine_facts(insights, current),
        changes=[
            ChangeFact(
                kind=change.kind,
                prompt_text=change.prompt_text,
                engine=change.engine,
                before=change.before,
                after=change.after,
                text=change.text,
            )
            for change in insights.changes[:MAX_CHANGES]
        ],
        actions=actions,
        competitors=competitors,
        client_pages=_pages(insights, client=True) if include_pages else [],
        winning_pages=_pages(insights, client=False) if include_pages else [],
        sentiment=_sentiment(insights, project.client.brand_name) if include_sentiment else [],
        sentiment_configured=insights.sentiment_coverage.configured,
        spend_usd=spend_usd if brand.show_spend else None,
    )
