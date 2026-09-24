"""The sampling policy: spend engine calls where the measurement is still noisy.

`plan()` judges every prompt × platform pair of a project (`stability.classify`),
turns the verdicts into per-pair overrides under the project's policy, hands
them to the planner, and totals what the crawl will cost against what the
fixed policy would have spent. It is the single entry point for the run, the
Results tab's "due" markers and the `/sampling` dry run, so the three can never
disagree (ADR 0025).

Policies:
- `fixed`: nothing changes; stability is not even evaluated on the run path.
- `save`: a stable pair's interval is multiplied (2, then 3 after a long streak),
  capped by `STRETCH_MAX` and by the project's consolidation window so the pair
  still appears in every consolidation.
- `reallocate`: `save`, plus `VOLATILE_BOOST` extra samples for volatile pairs,
  granted most-volatile first and only while the calls stretching saved (this
  crawl plus the carry from the cycle's earlier crawls) cover them. Net spend
  never exceeds the fixed baseline.

Never touched: starred prompts (no stretch), explicit interval or sample
overrides, forced or selected-prompt runs, failing pairs (never boosted), and a
pair that just came back from a stretch (one base window before any boost).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from src.core.config import Settings
from src.integrations.schemas import Engine
from src.modules.control_plane.planner import (
    PairKey,
    due_items,
    effective_engines,
    effective_interval,
    effective_samples,
)
from src.modules.control_plane.positioning import PositionStore
from src.modules.control_plane.schemas import (
    PairOverride,
    Project,
    RunRequest,
    SamplingDecision,
    SamplingPlan,
    SamplingSummary,
    TrackedPrompt,
)
from src.modules.prompt_tracking.costing import engine_call_cost
from src.modules.prompt_tracking.schemas import (
    CitationSnapshot,
    StabilityReport,
    StabilityState,
)
from src.modules.prompt_tracking.stability import classify
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

__all__ = ["FIXED", "POLICIES", "REALLOCATE", "SAVE", "plan"]

FIXED = "fixed"
SAVE = "save"
REALLOCATE = "reallocate"
POLICIES = (FIXED, SAVE, REALLOCATE)
_MAX_SAMPLES = 10
_RETURNED_FROM_STRETCH = 1.5
"""A gap this many base intervals wide means the pair was stretched last time."""


@dataclass
class _Pair:
    prompt: TrackedPrompt
    engine: Engine
    interval: timedelta
    base: int
    report: StabilityReport
    newest: datetime | None
    now: datetime
    multiplier: int = 1
    samples: int = 0
    stretched: bool = False
    boosted: bool = False
    boost_eligible: bool = False
    note: str = ""

    @property
    def base_due(self) -> bool:
        return self.newest is None or self.newest <= self.now - self.interval

    @property
    def due(self) -> bool:
        return self.newest is None or self.newest <= self.now - self.interval * self.multiplier

    @property
    def skipped(self) -> bool:
        return self.base_due and not self.due

    def next_due_at(self) -> datetime:
        if self.newest is None:
            return self.now
        return self.newest + self.interval * self.multiplier


def _expected_calls(pair: _Pair, samples: int, settings: Settings) -> int:
    """Calls a crawl of this pair is expected to spend.

    A stable pair reaches consensus and stops at `min_samples`; anything else
    spends its full count. Counting in calls, not samples, is what makes the
    budget guard honest (a skipped stable pair saves 2 calls, not 3).
    """
    if pair.report.state is StabilityState.STABLE and settings.adaptive_sampling:
        return min(samples, settings.min_samples)
    return samples


def _returned_from_stretch(history: list[CitationSnapshot], interval: timedelta) -> bool:
    if len(history) < 2:
        return False
    return history[0].captured_at - history[1].captured_at > interval * _RETURNED_FROM_STRETCH


def _carry(positions: PositionStore, project_id: str, settings: Settings) -> int:
    """Savings not yet spent in the cycle's earlier crawls, never negative."""
    earlier = positions.recent_sampling(project_id, limit=max(settings.stretch_max - 1, 0))
    return max(0, sum(s.calls_saved - s.calls_boosted for s in earlier))


def plan(
    project: Project,
    prompts: list[TrackedPrompt],
    db: TimeSeriesDB,
    positions: PositionStore,
    settings: Settings,
    now: datetime,
    *,
    request: RunRequest | None = None,
    policy: str | None = None,
    classify_all: bool = False,
) -> SamplingPlan:
    """Decide every pair, derive the due items, and total the crawl.

    Args:
        project: The project; its `sampling_policy` applies unless `policy` is given.
        prompts: Its tracked prompts.
        db: Snapshot history.
        positions: Crawl records, for the budget carry.
        settings: Window, floors, caps and per-call costs.
        now: Current time (UTC).
        request: The run request; `force` or `prompt_ids` switch the policy off for
            this run, as the operator asked for exactly that work.
        policy: Simulate a policy other than the project's (the dry-run route).
        classify_all: Evaluate stability even under `fixed`, for the view.
    """
    request = request or RunRequest()
    active = policy or project.sampling_policy
    adaptive = active != FIXED and not request.force and not request.prompt_ids
    evaluate = adaptive or classify_all
    window = settings.stability_window_crawls
    history_limit = max(3 * window, 12)

    pairs: list[_Pair] = []
    for prompt in prompts:
        if not prompt.enabled and not request.force:
            continue
        engines = effective_engines(project, prompt)
        if request.engines:
            engines = [e for e in engines if e in request.engines]
        interval = effective_interval(project, prompt)
        base = effective_samples(project, prompt) or settings.samples_per_engine
        for engine in engines:
            history = db.history(prompt.prompt_id, engine, limit=history_limit if evaluate else 1)
            if evaluate:
                report = classify(
                    history,
                    window=window,
                    min_crawls=settings.stability_min_crawls,
                    now=now,
                    max_age=interval * window,
                )
            else:
                report = StabilityReport(
                    state=StabilityState.UNKNOWN,
                    crawls=len(history),
                    reason="Policy is fixed: stability is not evaluated for this crawl.",
                )
            pair = _Pair(
                prompt=prompt,
                engine=engine,
                interval=interval,
                base=base,
                report=report,
                newest=history[0].captured_at if history else None,
                now=now,
                samples=base,
            )
            _decide_stretch(pair, project, settings, adaptive)
            pair.boost_eligible = (
                adaptive
                and active == REALLOCATE
                and settings.volatile_boost > 0
                and report.state is StabilityState.VOLATILE
                and prompt.samples_per_engine is None
                and pair.due
            )
            if pair.boost_eligible and _returned_from_stretch(history, interval):
                pair.boost_eligible = False
                pair.note = "Back at the base interval after a stretch; boost deferred one window."
            pairs.append(pair)

    saved = sum(_expected_calls(p, p.base, settings) for p in pairs if p.skipped)
    carry = _carry(positions, project.id, settings) if adaptive and active == REALLOCATE else 0
    boosted_calls = _grant_boosts(pairs, saved + carry, settings)

    overrides: dict[PairKey, PairOverride] = {
        (p.prompt.id, p.engine.value): PairOverride(
            multiplier=p.multiplier, samples=p.samples if p.boosted else None, note=p.note
        )
        for p in pairs
        if p.multiplier > 1 or p.boosted
    }
    items = due_items(
        project,
        prompts,
        db,
        now,
        force=request.force,
        only_ids=request.prompt_ids or None,
        engines_filter=request.engines,
        overrides=overrides,
    )

    decisions = [_decision(p, settings) for p in pairs]
    due = [p for p in pairs if p.due]
    planned = sum(_expected_calls(p, p.samples, settings) for p in due)
    baseline = sum(_expected_calls(p, p.base, settings) for p in pairs if p.base_due)
    skipped = [p for p in pairs if p.skipped]
    summary = SamplingSummary(
        policy=active,
        pairs=len(pairs),
        due_pairs=len(due),
        stretched_pairs=sum(1 for p in pairs if p.stretched),
        skipped_pairs=len(skipped),
        boosted_pairs=sum(1 for p in pairs if p.boosted),
        calls_planned=planned,
        calls_baseline=baseline,
        calls_saved=saved,
        calls_boosted=boosted_calls,
        carry_calls=carry,
        est_cost_planned_usd=round(
            sum(
                _expected_calls(p, p.samples, settings) * engine_call_cost(settings, p.engine)
                for p in due
            ),
            4,
        ),
        est_cost_baseline_usd=round(
            sum(
                _expected_calls(p, p.base, settings) * engine_call_cost(settings, p.engine)
                for p in pairs
                if p.base_due
            ),
            4,
        ),
        next_due_at=min((p.next_due_at() for p in skipped), default=None),
    )
    return SamplingPlan(summary=summary, decisions=decisions, items=items)


def _decide_stretch(pair: _Pair, project: Project, settings: Settings, adaptive: bool) -> None:
    """Set the interval multiplier for a stable pair, or explain why not."""
    report = pair.report
    if not adaptive:
        return
    if report.state is not StabilityState.STABLE:
        return
    if pair.prompt.important:
        pair.note = "Starred: sampled every crawl regardless of stability."
        return
    if pair.prompt.interval is not None:
        pair.note = "Prompt has its own interval; the policy leaves it alone."
        return
    tier = 3 if report.streak >= 2 * settings.stability_window_crawls else 2
    multiplier = min(tier, settings.stretch_max, project.consolidation_runs)
    if multiplier <= 1:
        pair.note = "Stable, but the consolidation window is one crawl; nothing to stretch."
        return
    pair.multiplier = multiplier
    pair.stretched = True
    pair.note = f"Stable for {report.streak} crawl(s): sampled every {multiplier} intervals."


def _grant_boosts(pairs: list[_Pair], budget: int, settings: Settings) -> int:
    """Give volatile pairs extra samples, most volatile first, while the budget lasts."""
    spent = 0
    candidates = sorted(
        (p for p in pairs if p.boost_eligible),
        key=lambda p: (p.report.score if p.report.score is not None else 0.0, p.prompt.id),
    )
    for pair in candidates:
        extra = min(settings.volatile_boost, _MAX_SAMPLES - pair.base)
        if extra <= 0:
            pair.note = "Volatile, but already at the maximum sample count."
            continue
        if spent + extra > budget:
            pair.note = "Volatile; boost deferred until stretching has saved enough calls."
            continue
        spent += extra
        pair.samples = pair.base + extra
        pair.boosted = True
        pair.note = f"Volatile: {extra} extra sample(s) this crawl, paid for by stretched pairs."
    return spent


def _decision(pair: _Pair, settings: Settings) -> SamplingDecision:
    return SamplingDecision(
        tracked_id=pair.prompt.id,
        prompt_id=pair.prompt.prompt_id,
        prompt_text=pair.prompt.prompt_text,
        engine=pair.engine,
        stability=pair.report,
        base_samples=pair.base,
        samples=pair.samples,
        multiplier=pair.multiplier,
        stretched=pair.stretched,
        boosted=pair.boosted,
        due=pair.due,
        skipped=pair.skipped,
        expected_calls=_expected_calls(pair, pair.samples, settings) if pair.due else 0,
        baseline_calls=_expected_calls(pair, pair.base, settings) if pair.base_due else 0,
        next_due_at=pair.next_due_at(),
        note=pair.note,
    )
