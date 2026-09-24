"""The pooled-window stability verdict against synthetic histories (ADR 0025)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.integrations.schemas import Engine
from src.modules.prompt_tracking.schemas import CitationSnapshot, StabilityState
from src.modules.prompt_tracking.stability import classify

NOW = datetime(2026, 9, 23, 9, tzinfo=UTC)
DAY = timedelta(days=1)


def snap(
    days_ago: int, cited: int, samples: int = 3, *, failed: int = 0, model: str = "m"
) -> CitationSnapshot:
    ok = samples - failed
    return CitationSnapshot(
        engine=Engine.CHATGPT_SEARCH,
        model=model,
        captured_at=NOW - days_ago * DAY,
        samples=samples,
        failed_samples=failed,
        web_trigger_rate=1.0,
        client_cited_samples=cited,
        client_citation_rate=(cited / ok) if ok else 0.0,
        client_cited=bool(ok) and cited * 2 >= ok,
    )


def test_never_and_too_few_crawls_are_unknown():
    assert classify([]).state is StabilityState.UNKNOWN
    two = classify([snap(1, 0), snap(2, 0)])
    assert two.state is StabilityState.UNKNOWN and two.crawls == 2 and "3 are needed" in two.reason


def test_settled_pairs_at_both_ends_are_stable_with_a_narrow_band():
    never = classify([snap(1, 0), snap(2, 0), snap(3, 0)])
    assert never.state is StabilityState.STABLE
    assert never.score == 1.0 and never.rate == 0.0 and never.rate_high is not None
    assert never.rate_high < 0.5 and never.streak == 3 and never.flips == 0
    assert (
        never.reason
        == "Across the last 3 crawls, 0 of 9 answers cited you (likely 0%–30%): stable."
    )
    always = classify([snap(1, 3), snap(2, 3), snap(3, 3), snap(4, 3)])
    assert always.state is StabilityState.STABLE and always.rate_low is not None
    assert always.rate_low > 0.5 and always.crawls == 4


def test_early_stop_extremes_do_not_fool_the_pooled_band():
    # A pair cited ~90% of the time: early stop reads 2/2 most crawls, 2/3 once.
    ninety = classify([snap(1, 2, 2), snap(2, 2, 2), snap(3, 2, 3), snap(4, 2, 2)])
    assert ninety.state is StabilityState.STABLE and ninety.flips == 0
    # A genuine 70% pair pools to 7 of 9: the band straddles 50%, so it is uncertain and
    # earns more samples; per-crawl spread (1.0 vs 0.33) is never consulted.
    seventy = classify([snap(1, 2, 2), snap(2, 2, 2), snap(3, 2, 2), snap(4, 1, 3)])
    assert seventy.state is StabilityState.VOLATILE and seventy.flips == 1  # the band, not flips
    assert seventy.rate_low is not None and seventy.rate_low < 0.5


def test_repeated_verdict_flips_override_a_confident_band():
    flippy = classify([snap(1, 3), snap(2, 1), snap(3, 3), snap(4, 3)])  # T F T T -> 2 flips
    assert flippy.rate_low is not None and flippy.rate_low > 0.5  # 10 of 12
    assert flippy.state is StabilityState.VOLATILE and flippy.flips == 2
    assert "flipped 2 times" in flippy.reason and flippy.streak == 1


def test_failing_platform_is_its_own_state():
    dead = classify([snap(1, 0, failed=3), snap(2, 0, failed=3), snap(3, 0, failed=3)])
    assert dead.state is StabilityState.FAILING and dead.crawls == 3
    # Failed crawls between good ones are simply not admitted.
    mixed = classify([snap(1, 0, failed=3), snap(2, 0), snap(3, 0), snap(4, 0)])
    assert mixed.state is StabilityState.STABLE and mixed.crawls == 3


def test_single_sample_crawls_model_changes_and_stale_evidence_are_excluded():
    thin = classify([snap(1, 0, 1), snap(2, 0, 1), snap(3, 0, 1)])
    assert thin.state is StabilityState.UNKNOWN and "at least 2 answers" in thin.reason
    switched = classify([snap(1, 0, model="new"), snap(2, 0), snap(3, 0), snap(4, 0)])
    assert switched.state is StabilityState.UNKNOWN and switched.crawls == 1
    stale = classify([snap(30, 0), snap(31, 0), snap(32, 0)], now=NOW, max_age=4 * DAY)
    assert stale.state is StabilityState.UNKNOWN and "30 days old" in stale.reason
    fresh = classify([snap(1, 0), snap(2, 0), snap(3, 0)], now=NOW, max_age=4 * DAY)
    assert fresh.state is StabilityState.STABLE


def test_streak_counts_beyond_the_window_and_window_caps_the_pool():
    long_run = classify([snap(d, 0) for d in range(1, 9)], window=4)
    assert long_run.crawls == 4 and long_run.ok_samples == 12 and long_run.streak == 8
    broken = classify([snap(1, 0), snap(2, 0), snap(3, 0), snap(4, 0), snap(5, 3)], window=4)
    assert broken.streak == 4 and broken.state is StabilityState.STABLE
