# ADR 0005 — Google organic rank: track the prompt text and the seed keyword

**Status**: Accepted (2026-09-16), operator decision: "do it both".

## Context

The tracker measured AI citations only. The SerpApi call that fetches the AI
Overview already returns the classic organic results, which the connector was
discarding. Analysts also want conventional keyword rank tracking. A
conversational prompt ("How does GEP compare to other procurement software
vendors?") and its seed keyword ("procurement software") produce different SERPs,
so one measurement cannot stand in for the other.

## Decision

Record two `OrganicRankSnapshot`s per prompt per run:

| Kind | Query | Cost | Samples |
| :-- | :-- | :-- | :-- |
| `PROMPT` | the prompt text | none — parsed from the AI Overview call | `SAMPLES_PER_ENGINE` |
| `KEYWORD` | `core_keyword` | one SerpApi call per **distinct** keyword per run, cached | 1 |

Both are stored in `organic_snapshots`, reported as "Google Organic Rank /
Ranking URL" columns, and given window-over-window velocity. Device is fixed
by `SERP_DEVICE` (default `desktop`) and stored on every snapshot, because
organic ranks differ by device and a series that mixes them is not comparable.

Keyword lookups always use the real Google connector, even when the audited
engines exclude `GOOGLE_AI_OVERVIEW`. A failed lookup is a warning, is charged
(the call was made) and is cached so it is not retried per prompt.
`--no-keyword-rank` / `track_keyword_rank=False` disables the extra calls.

## Alternatives considered

- **Prompt-text rank only.** Free, but not what SEO reporting calls "rank".
- **Keyword rank only.** Ignores the SERP the AI Overview was actually built on.
- **Google Search Console for rank.** Gives *average* position for *impressions
  received*, not the live SERP; complementary, not a substitute. Not built.

## Consequences

- Default run cost rises by one SerpApi call per seed keyword (typically 2–6).
- `SerpSnapshot.organic_domains` became a derived property; consumers should
  read `organic_results` for positions and URLs.
