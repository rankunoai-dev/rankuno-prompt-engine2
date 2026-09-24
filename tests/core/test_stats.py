"""Wilson intervals: the band the UI shows beside every rate."""

from __future__ import annotations

import pytest

from src.core.stats import coin_flip, wilson_interval


def test_no_trials_is_not_measured():
    assert wilson_interval(0, 0) is None


def test_interval_contains_the_point_estimate_and_stays_in_range():
    low, high = wilson_interval(6, 9)  # type: ignore[misc]
    assert 0.0 <= low < 6 / 9 < high <= 1.0
    # Known value: 6/9 at 95% is roughly 35% to 88%.
    assert (low, high) == pytest.approx((0.354, 0.879), abs=0.002)


def test_edges_do_not_collapse_to_certainty():
    # 0 of 9 cited is not "0%": the upper bound is what the client must hear.
    low, high = wilson_interval(0, 9)  # type: ignore[misc]
    assert low == 0.0 and 0.25 < high < 0.35
    low, high = wilson_interval(9, 9)  # type: ignore[misc]
    assert 0.65 < low < 0.75 and high == 1.0


def test_more_samples_narrow_the_band():
    small = wilson_interval(3, 9)
    large = wilson_interval(300, 900)
    assert small is not None and large is not None
    assert (large[1] - large[0]) < (small[1] - small[0]) / 5


def test_confidence_levels_and_bad_input():
    assert wilson_interval(5, 10, 0.90) is not None
    wide = wilson_interval(5, 10, 0.99)
    narrow = wilson_interval(5, 10, 0.90)
    assert wide is not None and narrow is not None
    assert wide[1] - wide[0] > narrow[1] - narrow[0]
    with pytest.raises(ValueError, match="confidence"):
        wilson_interval(5, 10, 0.8)
    with pytest.raises(ValueError, match="successes"):
        wilson_interval(11, 10)


def test_coin_flip_is_zero_at_certainty_and_one_at_even_odds():
    assert coin_flip(0.0) == 0.0 and coin_flip(1.0) == 0.0
    assert coin_flip(0.5) == 1.0
    assert coin_flip(0.25) == coin_flip(0.75) == 0.5
    assert coin_flip(1.7) == 0.0  # clamped, never negative
