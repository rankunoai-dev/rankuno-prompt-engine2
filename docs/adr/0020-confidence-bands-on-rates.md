# ADR 0020 — Every rate carries a 95% confidence band

**Date**: 2026-09-23
**Status**: Accepted
**Builds on**: ADR 0010 (consolidation over a window of crawls)

## Context

The tracker already samples each prompt three times per engine per crawl and
consolidates over three crawls (ADR 0010), which is more than most commercial
trackers run. But it reported the result as a bare proportion: "cited 67%" from
six of nine samples. That number is an estimate with a wide band, and the
competitive review of 2026-09-22 found that credible practitioners now ask for
the band first: SparkToro measured a less than one-in-a-hundred chance of the
same brand list in two runs, and HOTH showed that twenty prompts at a 20%
citation rate give a true range of 2.5% to 37.5%. A client shown "67%" without
the band will read a move to 56% next month as a loss when it is noise.

## Decision

1. **Wilson score interval at 95%**, computed in `src/core/stats.py`, on every
   consolidated citation rate and mention rate (`ConsolidatedPosition`) and on
   every platform health rate (`EngineHealth`), from the pooled successful
   samples behind the rate. Wilson rather than the normal approximation because
   it behaves at the edges: zero of nine cited gives an upper bound near 30%
   instead of a false 0%, and it never leaves [0, 1].
2. **The band is stored with the position**, not recomputed in the browser, so
   a consolidation made today reads the same in a year. Rows consolidated
   before this cycle carry `None` and the UI shows nothing rather than a guess.
3. **The UI shows it as a range in words**: "cited 67%, likely 35–88%" on the
   Battleground cell hover, under the consolidated numbers in the Inspection
   Drawer, and under each rate on the Overview health tiles. Point-in-time
   cells (one crawl) show no band, because one crawl is not a sample of
   anything.
4. **No behaviour keys off the band yet.** Verdicts, action-card thresholds and
   "what changed" still use the point estimate. Making a change count only when
   two bands do not overlap is the natural next step and a separate decision.

## Consequences

- Nine samples is honest but wide: a 35 to 88 point band on a 67% rate. The
  band is the argument for more samples per prompt, and the number a client
  can now see move as samples are added.
- The band is per prompt per engine. A project-wide band would need the
  prompts to be a random sample of something, which they are not.
- Sentiment (feature 2 of the roadmap) will inherit the same treatment when it
  lands, because it aggregates through the same consolidation path.
