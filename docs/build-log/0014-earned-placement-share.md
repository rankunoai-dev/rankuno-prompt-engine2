# Cycle 0014 — Earned-placement share bounded to 0–100%

**Date**: 2026-09-19
**Operator request**: "The 338% Share Bug … The old code simply added those
individual percentages together (80+70+60+50+78 = 338%) … artificially inflates
the card's Impact Score, causing those cards to rank higher on your action list
than they should. The Fix: normalize third-party domain share so the percentage
stays accurately between 0% and 100%. solve this problem."

## Scope

One rule in `InsightEngine._actions_for`: the `earned_placement` card's share
and the impact score derived from it. No other card type, no schema change, no
vendor call.

## Research findings

- `_domain_share` returns, per domain, the fraction of *answers* citing it. One
  answer cites several domains, so those fractions legitimately sum past 1.0.
  The card then did `sum(v for _, v in earned)` over every third-party domain
  and used the result as a percentage and as the impact multiplier. On the demo
  project's "Reviews & comparisons" cluster that read **338%**.
- The prescription copy says "*{share:.0%} of {engine}'s sources* here are review
  sites …" — a statement about sources, not a sum of answer shares. The right
  statistic is the fraction of cited sources (one count per domain per answer,
  the same unit `_domain_share` uses) whose class is aggregator, forum,
  marketplace or reference. That is bounded 0..1 by construction.
- A second, pre-existing asymmetry sat behind the first: every other card's
  impact multiplier is a **gap to target** (`_ACTION_TARGET − cited_rate`,
  `rival_share − cited_rate`, `old_rate − cited_rate`, or a fixed 0.4 — all
  ≤ 0.5), while earned placement used the share itself (≤ 1.0) and ignored the
  gap entirely. A topic at 49% cited scored the same as one at 0%. Even after
  bounding the share, earned-placement cards still topped the list on scale
  alone.
- A publisher (`forbes.com`) is *not* an earned class: a brand cannot secure a
  listing there. The test fixture initially assumed otherwise; the code was
  right.

## What was built

- `_EARNED_CLASSES` constant; `InsightEngine._third_party_share()` counting
  third-party sources over all cited sources, per domain per answer.
- `earned_share` now comes from that helper. The per-domain `earned` list is
  kept for the evidence chips, whose individual shares were always correct.
- Impact aligned with the other cards: `weight × earned_share × (_ACTION_TARGET − cited_rate)`.
- Tests: `test_third_party_share_is_bounded_and_counts_sources_not_summed_answer_shares`
  (five third-party domains in every answer → 5/6, where the old arithmetic gave
  500%; duplicates count once; client domain never third-party; empty → 0) and
  `test_earned_placement_card_reports_a_percentage_under_one_hundred` (share in
  (0, 1], the prescription quotes the same number, impact bounded).

## Local verification

Against a **scratch copy** of the live store, demo project "GEP demo - full
capability" (never the live file, no vendor calls):

| | before | after share fix | after impact alignment |
|---|---|---|---|
| ChatGPT · Reviews & comparisons share | **338%** | 89% | 89% |
| that card's impact | ~150 | 39.3 | 12.2 |
| max impact by type | earned 39.3 / others ≤ 14.8 | earned 39.3 / others ≤ 14.8 | earned 12.2 / others ≤ 14.8 |
| #1 open action | earned_placement | earned_placement | **defend · ChatGPT · Reviews & comparisons** |

The new #1 is the card the Overview's "−7% vs previous" on ChatGPT was already
pointing at. All six earned-placement cards now read 51–89%.

## Explicitly not done

- No change to `_domain_share` or to the evidence chips: per-domain answer
  shares are individually correct and useful ("G2 in 80% of answers").
- No change to `_MIN_SHARE` (0.3). Under the new statistic it means "at least
  30% of sources are third-party", a sensible bar; five of six demo cards still
  fire.
- Card ids unchanged, so analyst state on existing earned-placement cards
  survives.

## Step 5 audit answers (delta from cycle 0013)

1–3. No vendors, endpoints or spend. 4. No storage change. 5. Read-side only.
6–8. Unchanged.

## Gate output (verbatim, abridged to the changed module)

```
=== Format ===  PASSED
=== Lint ===    All checks passed!  PASSED
=== Type check === Success: no issues found in 59 source files  PASSED
=== Tests ===
src\modules\control_plane\insights.py         411     17    180     16    94%
TOTAL                                        5433     88   1206     54    98%
Required test coverage of 85.0% reached. Total coverage: 97.80%
711 passed, 2 warnings in 123.04s (0:02:03)
ALL GATES PASSED.
```

Drift audit: `scripts/drift_check.py` → "No documentation drift detected."

