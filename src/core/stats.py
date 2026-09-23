"""Uncertainty for the rates the tracker reports.

An AI answer is a coin flip that lands the same way most of the time. A rate
built from nine samples is not the truth, it is an estimate with a wide band,
and every credible measurement guide for this category now asks for that band
to be shown. The Wilson score interval is used because it stays sane at the
edges: 0 of 9 cited gives an upper bound near 30%, not the 0% a plain
proportion would suggest, and it never leaves [0, 1].
"""

from __future__ import annotations

import math

__all__ = ["wilson_interval"]

# Two-sided z for the confidence levels the app offers. 95% is the default and
# the only one the UI shows; the others exist so a caller can ask for them.
_Z = {0.90: 1.6449, 0.95: 1.9600, 0.99: 2.5758}


def wilson_interval(
    successes: int, trials: int, confidence: float = 0.95
) -> tuple[float, float] | None:
    """Wilson score interval for `successes` out of `trials`, rounded to 4 places.

    Returns `None` when there are no trials: an interval of [0, 1] would be
    true but useless, and callers treat `None` as "not measured".
    """
    if trials <= 0:
        return None
    if not 0 <= successes <= trials:
        msg = f"successes must be within [0, trials]; got {successes} of {trials}"
        raise ValueError(msg)
    try:
        z = _Z[confidence]
    except KeyError:
        msg = f"confidence must be one of {sorted(_Z)}; got {confidence}"
        raise ValueError(msg) from None
    n = float(trials)
    p = successes / n
    z2 = z * z
    denominator = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denominator
    half = (z / denominator) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    low = max(0.0, centre - half)
    high = min(1.0, centre + half)
    return round(low, 4), round(high, 4)
