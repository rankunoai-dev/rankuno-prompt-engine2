"""Deciding what is worth an alert (ADR 0024).

Pure functions from stored analysis to candidate events. Nothing here sends,
stores or reads a clock, so every rule is testable with two windows of data.

The rule that matters is `citation_drop`. Answer engines are non-deterministic:
the same prompt asked three times returns three different answers, so a
citation rate moves a few points between windows on sampling alone. Alerting
on that teaches the reader to ignore the channel. This module therefore fires
only when the two windows' 95% Wilson intervals do not overlap — when the
sampling itself cannot explain the fall (ADR 0020).

The cost of that strictness is stated plainly in the docs: small windows will
miss real drops. A missed alert is recoverable from the dashboard; a channel
nobody reads is not.
"""

from __future__ import annotations

from collections import Counter

from src.core.stats import wilson_interval
from src.integrations.schemas import Engine
from src.modules.alerting.schemas import AlertEvent, AlertRule, AlertSeverity
from src.modules.control_plane.schemas import ConsolidatedPosition, InsightsView

__all__ = ["MIN_TRIALS", "engine_totals", "evaluate"]

MIN_TRIALS = 10
"""Below this many sampled answers an interval is too wide to mean anything,
so the drop rule stays silent rather than reporting on three samples."""

_SURGE_PROMPTS = 2
_MAX_EVENTS = 12


def engine_totals(positions: list[ConsolidatedPosition]) -> dict[Engine, tuple[int, int]]:
    """Cited answers and answered samples per engine, pooled over the window."""
    totals: dict[Engine, tuple[int, int]] = {}
    for position in positions:
        cited, trials = totals.get(position.engine, (0, 0))
        answered = max(position.samples - position.failed_samples, 0)
        totals[position.engine] = (cited + position.cited_samples, trials + answered)
    return {engine: (min(cited, trials), trials) for engine, (cited, trials) in totals.items()}


def _label(engine: Engine) -> str:
    """Platform name for a message."""
    return engine.value.replace("_", " ").title()


def _citation_drops(
    current: list[ConsolidatedPosition], previous: list[ConsolidatedPosition]
) -> list[AlertEvent]:
    """Engines whose citation rate fell beyond what sampling explains."""
    now, before = engine_totals(current), engine_totals(previous)
    events: list[AlertEvent] = []
    for engine, (cited, trials) in sorted(now.items(), key=lambda item: item[0].value):
        if engine not in before:
            continue
        prior_cited, prior_trials = before[engine]
        if trials < MIN_TRIALS or prior_trials < MIN_TRIALS:
            continue
        band_now = wilson_interval(cited, trials)
        band_before = wilson_interval(prior_cited, prior_trials)
        if band_now is None or band_before is None:
            continue
        if band_now[1] >= band_before[0]:
            continue  # the intervals overlap: sampling alone could explain it
        rate, prior_rate = cited / trials, prior_cited / prior_trials
        points = round((prior_rate - rate) * 100)
        events.append(
            AlertEvent(
                rule=AlertRule.CITATION_DROP,
                severity=AlertSeverity.CRITICAL if rate == 0 else AlertSeverity.WARNING,
                title=f"{_label(engine)}: citation rate fell {points} points",
                detail=(
                    f"{round(prior_rate * 100)}% of answers cited the brand in the previous "
                    f"window ({prior_cited} of {prior_trials}); this window it is "
                    f"{round(rate * 100)}% ({cited} of {trials}). The 95% intervals do not "
                    "overlap, so this is not sampling noise."
                ),
                engine=engine,
                subject=engine.value,
                numbers={
                    "rate": round(rate, 4),
                    "previous_rate": round(prior_rate, 4),
                    "points": float(points),
                    "samples": float(trials),
                },
            )
        )
    return events


def _lost_prompts(insights: InsightsView, important: set[str]) -> list[AlertEvent]:
    """Starred prompts that stopped being cited."""
    events: list[AlertEvent] = []
    for change in insights.changes:
        if change.kind != "flip_down" or change.prompt_id not in important:
            continue
        events.append(
            AlertEvent(
                rule=AlertRule.LOST_PROMPT,
                severity=AlertSeverity.WARNING,
                title=f"{_label(change.engine)} stopped citing a tracked prompt",
                detail=(
                    f"“{change.prompt_text[:160]}” went from {change.before} to {change.after} "
                    f"on {_label(change.engine)}."
                ),
                engine=change.engine,
                subject=change.prompt_id,
            )
        )
    return events


def _competitor_surges(insights: InsightsView) -> list[AlertEvent]:
    """Rivals that appeared on several prompts in the same window."""
    counts: Counter[str] = Counter()
    engines: dict[str, Engine] = {}
    for change in insights.changes:
        if change.kind != "new_competitor":
            continue
        domain = change.after.strip() or change.text.strip()
        counts[domain] += 1
        engines.setdefault(domain, change.engine)
    events: list[AlertEvent] = []
    for domain, count in counts.most_common():
        if count < _SURGE_PROMPTS:
            continue
        events.append(
            AlertEvent(
                rule=AlertRule.COMPETITOR_SURGE,
                severity=AlertSeverity.WARNING,
                title=f"{domain} is now cited on {count} tracked prompts",
                detail=(
                    f"{domain} crossed the share threshold on {count} prompt(s) in this window, "
                    f"first seen on {_label(engines[domain])}."
                ),
                engine=engines.get(domain),
                subject=domain,
                numbers={"prompts": float(count)},
            )
        )
    return events


def _negative_claims(insights: InsightsView) -> list[AlertEvent]:
    """Sourced negative statements about the brand (ADR 0021)."""
    events: list[AlertEvent] = []
    for card in insights.actions:
        if card.type != "negative_claim" or card.status != "open":
            continue
        quote = card.evidence.quotes[0] if card.evidence.quotes else None
        url = quote.url if quote else (card.evidence.urls[0] if card.evidence.urls else None)
        events.append(
            AlertEvent(
                rule=AlertRule.NEGATIVE_CLAIM,
                severity=AlertSeverity.CRITICAL,
                title=card.title[:200],
                detail=((f"“{quote.text[:220]}”\n\n" if quote else "") + card.prescription[:400]),
                engine=card.engine,
                subject=card.subtopic,
                url=url,
            )
        )
    return events


def _silent_engines(
    insights: InsightsView,
    current: list[ConsolidatedPosition],
    previous: list[ConsolidatedPosition],
) -> list[AlertEvent]:
    """Platforms that used to answer and now do not.

    This is the rule that catches a billing-blocked Gemini or a revoked key
    before a client notices the platform missing from their report.
    """
    now, before = engine_totals(current), engine_totals(previous)
    events: list[AlertEvent] = []
    for health in insights.health:
        cited, trials = now.get(health.engine, (0, 0))
        prior_cited, prior_trials = before.get(health.engine, (0, 0))
        if trials > 0 or prior_trials == 0:
            continue
        events.append(
            AlertEvent(
                rule=AlertRule.ENGINE_SILENT,
                severity=AlertSeverity.CRITICAL,
                title=f"{_label(health.engine)} returned no answers this window",
                detail=(
                    f"The previous window stored {prior_trials} answer(s) from "
                    f"{_label(health.engine)} and this one stored none. Check the vendor key, "
                    "its billing, and the run's warnings."
                ),
                engine=health.engine,
                subject=health.engine.value,
                numbers={
                    "previous_samples": float(prior_trials),
                    "previous_cited": float(prior_cited),
                },
            )
        )
    return events


def evaluate(
    insights: InsightsView,
    current: list[ConsolidatedPosition],
    previous: list[ConsolidatedPosition],
    *,
    rules: set[AlertRule],
    important_prompt_ids: set[str] | None = None,
) -> list[AlertEvent]:
    """Every candidate event for one window, most severe first.

    `rules` filters at the source: a rule that is off costs nothing to evaluate.
    """
    important = important_prompt_ids or set()
    events: list[AlertEvent] = []
    if AlertRule.CITATION_DROP in rules:
        events += _citation_drops(current, previous)
    if AlertRule.LOST_PROMPT in rules:
        events += _lost_prompts(insights, important)
    if AlertRule.COMPETITOR_SURGE in rules:
        events += _competitor_surges(insights)
    if AlertRule.NEGATIVE_CLAIM in rules:
        events += _negative_claims(insights)
    if AlertRule.ENGINE_SILENT in rules:
        events += _silent_engines(insights, current, previous)
    order = {AlertSeverity.CRITICAL: 0, AlertSeverity.WARNING: 1, AlertSeverity.INFO: 2}
    events.sort(key=lambda event: (order[event.severity], event.rule.value, event.subject))
    return events[:_MAX_EVENTS]
