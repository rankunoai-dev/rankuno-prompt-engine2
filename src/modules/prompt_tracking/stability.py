"""Is a prompt × platform pair settled, or still a coin flip (ADR 0025)?

A pure function over stored snapshots so it can be tested with synthetic
history and reused by anything that holds a series. It deliberately does not
threshold per-crawl rates: with early stop (`audit.py`) a pair that is truly
cited 70% of the time lands on 0.0 or 1.0 in most crawls, so per-crawl spread
would call it volatile forever. The window's samples are pooled instead and the
Wilson band (ADR 0020) is read, which is the same figure the analyst already
sees on every rate.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.core.stats import coin_flip, wilson_interval
from src.modules.prompt_tracking.schemas import CitationSnapshot, StabilityReport, StabilityState

__all__ = ["MIN_OK_SAMPLES", "classify"]

MIN_OK_SAMPLES = 2
"""A crawl with one answer says nothing about agreement; two is the floor."""


def _ok(snapshot: CitationSnapshot) -> int:
    return max(snapshot.samples - snapshot.failed_samples, 0)


def _pct(value: float) -> str:
    return f"{round(value * 100):d}%"


def classify(
    snapshots: list[CitationSnapshot],
    *,
    window: int = 4,
    min_crawls: int = 3,
    min_ok: int = MIN_OK_SAMPLES,
    now: datetime | None = None,
    max_age: timedelta | None = None,
) -> StabilityReport:
    """Judge the pair from its newest crawls.

    Args:
        snapshots: The pair's history, newest first (`TimeSeriesDB.history`).
        window: Crawls pooled for the verdict.
        min_crawls: Usable crawls needed before the pair can leave `unknown`.
        min_ok: Successful samples a crawl needs to be usable.
        now: Current time; with `max_age`, stale evidence yields `unknown`.
        max_age: How old the newest usable crawl may be.

    Only crawls on the newest model count: a model change breaks comparability,
    exactly as the prompt detail already warns.
    """
    if not snapshots:
        return StabilityReport(state=StabilityState.UNKNOWN, crawls=0, reason="Never sampled.")
    model = snapshots[0].model
    same_model = [s for s in snapshots if s.model == model]
    usable = [s for s in same_model if _ok(s) >= min_ok]
    admitted = usable[:window]

    if not admitted:
        recent = same_model[:window]
        if len(recent) >= min_crawls and all(_ok(s) == 0 for s in recent):
            return StabilityReport(
                state=StabilityState.FAILING,
                crawls=len(recent),
                newest_at=recent[0].captured_at,
                reason=f"Every sample failed in the last {len(recent)} crawls; the platform "
                "is not answering, so stability cannot be judged and nothing is spent on it.",
            )
        return StabilityReport(
            state=StabilityState.UNKNOWN,
            crawls=0,
            newest_at=same_model[0].captured_at,
            reason=f"No crawl with at least {min_ok} answers yet; {min_crawls} are needed.",
        )

    if now is not None and max_age is not None and admitted[0].captured_at < now - max_age:
        age = (now - admitted[0].captured_at).days
        return StabilityReport(
            state=StabilityState.UNKNOWN,
            crawls=len(admitted),
            newest_at=admitted[0].captured_at,
            reason=f"Newest usable crawl is {age} days old; the pair is re-judged after fresh "
            "crawls.",
        )
    if len(admitted) < min_crawls:
        return StabilityReport(
            state=StabilityState.UNKNOWN,
            crawls=len(admitted),
            newest_at=admitted[0].captured_at,
            reason=f"{len(admitted)} crawl(s) with at least {min_ok} answers; "
            f"{min_crawls} are needed before a verdict.",
        )

    ok = sum(_ok(s) for s in admitted)
    cited = min(sum(s.client_cited_samples for s in admitted), ok)
    band = wilson_interval(cited, ok)
    assert band is not None  # ok >= min_ok * min_crawls > 0  # noqa: S101
    low, high = band
    rate = cited / ok
    flips = sum(
        1 for a, b in zip(admitted, admitted[1:], strict=False) if a.client_cited != b.client_cited
    )
    streak = 0
    for s in usable:
        if s.client_cited != usable[0].client_cited:
            break
        streak += 1

    # A band that excludes 50% is at most 50 points wide, so "excludes 50%" is the
    # whole test; repeated verdict flips override it because they are what the
    # client sees on the Battleground.
    straddles = low < 0.5 < high
    state = StabilityState.VOLATILE if flips >= 2 or straddles else StabilityState.STABLE

    sentence = (
        f"Across the last {len(admitted)} crawls, {cited} of {ok} answers cited you "
        f"(likely {_pct(low)}–{_pct(high)}): {state.value}."
    )
    if state is StabilityState.VOLATILE and flips >= 2:
        sentence += f" The verdict flipped {flips} times in the window."
    return StabilityReport(
        state=state,
        score=round(1.0 - coin_flip(rate), 4),
        crawls=len(admitted),
        ok_samples=ok,
        cited_samples=cited,
        rate=round(rate, 4),
        rate_low=low,
        rate_high=high,
        flips=flips,
        streak=streak,
        newest_at=admitted[0].captured_at,
        reason=sentence,
    )
