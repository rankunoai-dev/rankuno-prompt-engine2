# ADR 0021 — Sentiment, brand attributes and mention context

**Date**: 2026-09-23
**Status**: Accepted (cycle 0018; operator approved the plan and defaults on 2026-09-23)
**Builds on**: ADR 0014 (capture everything in the single run), ADR 0020 (confidence bands)

## Context

Every crawl already stores each sentence that names the client or a
competitor (`MentionSnippet`), the sentence-to-source claims, and the full
answer text. Nothing scores them. Sentiment is table stakes in every
commercial tracker, and the feasibility review (2026-09-23) ranked it first
to build because it needs only stored data plus a judge. The operator also
asked that a mention inside a listicle sourced from an aggregator be told
apart from a recommendation in prose, for the client and for competitors.

Two invariants constrain the design:

1. **`InsightEngine` never calls a vendor.** Judging is a paid call, so it
   belongs in the crawl, after samples are stored, with its own progress
   phase, ledger rows and spend cap. The Overview only reads.
2. **Every external call goes through `BaseAPIClient`** (coding standard §5)
   and is priced in the ledger. There is an `ANTHROPIC_API_KEY` setting and no
   client; the client is the first deliverable and is shared with prompt
   discovery and content drafts later.

## Decision

### A. Two separate outputs, one judged and one deterministic

| Output | Computed by | When | Stored |
|---|---|---|---|
| **Sentiment**: polarity (positive, neutral, negative, not_about_brand) and up to three attributes per mention sentence, per target entity | LLM judge (Claude Haiku 4.5, temperature 0, structured JSON output) | Post-crawl step in the pipeline | `mention_judgements` table |
| **Mention context**: container (prose, list item, table row, heading), position (first third), sourced-via (the cited domain and its class attached to the sentence), listed-with (other entities in the same block) | Pure functions over `answer_text`, `mentions`, `citation_claims`, `citation_links` | At insight build, like `_placement` today | Not stored; recomputed |

Mention context needs no vendor and is exact, so it is not sent to the judge.
The judge sees the sentence plus one sentence either side, the target entity,
and nothing else.

### B. Storage: `mention_judgements`

One row per (sample, entity, sentence). Key: `prompt_id, run_id, engine,
captured_at, entity, sentence_sha1`. Columns: `term, polarity, attributes
(JSON, max 3), confidence (0 to 1), model, rubric_version, judged_at, status
(ok | unscored | refused)`. Unique index on the key so a re-run is idempotent.
`AnswerSample` has no id on the read path; the four-column natural key is what
`samples_for` already returns, so no schema change to `answer_samples`.

A **judgement cache** keyed by `(sentence_sha1, entity, model,
rubric_version)` short-circuits identical sentences across samples. Engines
repeat themselves; listicle answers are often word-for-word identical across
the three samples. This is the main cost control and it also makes repeated
sentences score identically by construction.

### C. The judge call

- `src/integrations/anthropic_judge.py`, a `BaseAPIClient` subclass over the
  Messages API (raw HTTP through the shared `http` helper, as the other
  connectors do), `service_name="anthropic"`, `rate_limit_key="anthropic"`,
  `RiskClass.read`.
- Batches of up to 40 sentences per request, `output_config.format` with a
  JSON schema listing every item id, `max_tokens` sized to the batch. No
  thinking; the task is classification.
- Rubric is a versioned constant (`RUBRIC_VERSION = "2026-09-23.1"`). The
  rubric and the model id are stored on every row.
- Price card `claude-haiku-4-5: (1.00, 5.00, 0.10)` per million tokens;
  settings `ANTHROPIC_JUDGE_MODEL`, `COST_ANTHROPIC_JUDGE_CALL_USD` (0.005
  default), `SENTIMENT_MAX_SENTENCES_PER_RUN` (400 default). The existing
  session and daily caps see the ledger rows and stop it like any vendor.
- Ledger attribution: one batch spans many prompts, so rows carry `run_id`
  and `engine` but no `prompt_id`. They appear as unattributed calls in a
  per-prompt cost view, which `CostReport.unattributed_*` already models.

### D. Where it runs

`ProjectRunner`, after `record_samples` for every prompt in the crawl and
before consolidation: phase `judging`, progress "n of m sentences", one
ledger row per batch. A judge failure (rate limit after retries, breaker open,
refusal, malformed output) marks the affected sentences `unscored` and the
crawl still succeeds; the Overview shows judged coverage. The judge is skipped
silently, with one log line, when no key is configured.

### E. Aggregation and the band

Per engine, per consolidation window (the same `run_ids` the rest of the
insights use): judged sentences, positive, neutral, negative counts for the
client and for each competitor; negative share with a 95% Wilson band (ADR
0020); top attributes by frequency with an example quote each; the negative
outliers as evidence quotes with the source URL when the sentence carries a
claim. Only rows of the current `rubric_version` aggregate; older rows are
counted as unscored so a rubric change is visible rather than silently mixed.

### F. What the analyst sees

- Overview: a "How engines describe you" strip per platform: negative share
  with band, three attributes, the worst quote. Nothing when unscored.
- Actions: a new card type `negative_claim` when a negative sentence about
  the client carries a source, with the quote, the URL and the engine, and
  `correct_the_record` prescription. Impact from the sentence's frequency
  across samples.
- Inspection Drawer: each mention chip shows polarity and context
  ("negative · list item · via g2.com, aggregator").
- Atlas Share of voice: mention context split for client and competitors:
  recommended in prose versus listed in an aggregator-sourced list.

## Edge cases and breaking points, with the handling

**Judging**

- *Same sentence names client and competitor* ("Coupa is stronger than GEP on
  risk"). Judged once per target entity; the target is explicit in the prompt
  and stored on the row. Never inferred from the text afterwards.
- *False-positive brand match* (a short alias, an acronym). The judge may
  answer `not_about_brand`; the row is stored with that polarity and excluded
  from every share. It also feeds a "noisy alias" count per term so the
  client profile can be fixed.
- *Prompt injection inside answer text.* Sentences are untrusted data. They
  travel inside delimited, numbered items, the system prompt says they are
  data, and the output is a fixed JSON schema validated by a `StrictModel`.
  An item whose id is missing or whose enum is wrong is `unscored`.
- *Refusal stop reason.* Treated as `refused` for the whole batch; retried
  once as smaller batches; then `unscored`.
- *Non-determinism.* Temperature 0 plus the cache make repeats identical for
  identical input. Across model versions nothing is promised; the model id is
  on every row and the health strip states "scored by <model>".
- *Rubric drift.* Bumping `RUBRIC_VERSION` does not re-score history.
  Aggregates read the current version; earlier rows count as unscored. A
  backfill script with a spend budget is optional and separate.
- *Old samples without `answer_text`* (before cycle 0011): judged on the
  sentence alone, no neighbours; `confidence` is capped and the row says so.
- *Non-English answers.* Judged as is; attributes returned in the answer's
  language. No translation.
- *Listicle answers with dozens of mentions.* Per sample, at most 12 client
  sentences and 6 per competitor, chosen by position (earliest first); the
  run-wide cap is 400 sentences. Beyond the cap, sentences are `unscored`,
  never silently dropped from the count.
- *Truncated snippets* (600-character cap). Neighbours are taken from
  `answer_text` by locating the first 40 characters of the snippet, the same
  trick `_placement` uses; when not found, the snippet alone is judged.
- *Model output longer than `max_tokens`.* Parse what is complete, mark the
  rest `unscored`, halve the batch size for the rest of the run.

**Pipeline and platform**

- *Crawl must never fail because the judge failed.* Judge errors are caught
  at the phase boundary; the crawl's status is unaffected; coverage is shown.
- *Spend.* Worst case at the run cap: 400 sentences, roughly 90k input and 20k
  output tokens, about $0.19 at Haiku prices; typical crawls are a few cents.
  The estimate setting is the per-batch figure the ledger records before the
  real token counts arrive.
- *Reused snapshots* (`reuse_within_hours`) produce no new samples and no new
  judgements; nothing to do.
- *Gemini billing-blocked.* Fewer sentences, no other effect.
- *No key in production.* The Railway variables gain `ANTHROPIC_API_KEY`;
  without it the feature is off and the strip shows "not configured", not an
  error, and the health check is unaffected.
- *Concurrency.* The judge runs after the four-thread engine pool, serially,
  inside the single job worker. It shares the rate limiter and breaker
  registry, so a future second Anthropic use (drafts) throttles jointly.
- *Data sent to Anthropic.* Public engine output only, no client data beyond
  brand and competitor names, no personal data. Anthropic's 30-day retention
  applies; noted in the runbook.

**Mention context**

- *Citation markers are stripped before sentence splitting*, so the sentence
  cannot be matched to `[n]` directly. The match goes through
  `citation_claims` (sentence to URL) with whitespace-normalised, case-folded
  comparison, then falls back to the nearest marker on the same line of
  `answer_text`, then to none.
- *Markdown tables* split into one "sentence" per row; the container is
  detected from the line, not the sentence, so a row still reads as `table`.
- *Numbered lists* ("1. GEP SMART") versus prose that starts with a digit
  ("2024 saw"): container requires the marker and a following space or
  period, and the line to be under 300 characters.
- *Competitor named only through its domain* (coupa.com in a citation but
  not in the text). That is a citation, not a mention; it stays in the
  citation metrics and does not appear in mention context.
- *Client listed alongside twenty vendors from a G2 page.* Context reads
  "list item · via g2.com, aggregator · listed with 19 others", which is the
  insight the operator asked for, and the `earned_placement` card already
  covers the action.

**UI**

- Type generation: the snapshot is dumped in-process, not from the running
  server, as in cycle 0016.
- Mocks compute sentiment deterministically from fixture text so tests and
  `VITE_MOCK` mode show the strip without a key.
- The band on negative share reuses `pctRange`; unscored counts are shown,
  never hidden.

## Plan

| Step | Deliverable | Tests |
|---|---|---|
| 1 | `anthropic_judge.py` client, price card, settings, `.env.example` | transport mocked with `httpx.MockTransport`; refusal, 429, malformed JSON, cap |
| 2 | `mention_judgements` table and read/write in `time_series_db.py`; cache | idempotent re-run, cache hit, old-rubric exclusion |
| 3 | `prompt_tracking/sentiment.py`: sentence selection, neighbours, batching, rubric, parsing | selection caps, neighbour lookup, injection-shaped input, partial output |
| 4 | Runner phase `judging`; failure isolation; progress | crawl succeeds when judge fails; ledger rows carry run and engine |
| 5 | `insights.py`: `SentimentProfile`, `MentionContext`, `negative_claim` card; schemas | aggregation with band, current-rubric-only, context matching fallbacks |
| 6 | UI: Overview strip, card type, drawer chips, Atlas split; mocks; types | strip renders with band and unscored count; card appears for a negative sourced claim |
| 7 | Docs: this ADR to Accepted, build log 0018, README, ARCHITECTURE, KNOWN_GAPS, runbook variable | drift check |

Effort: one and a half cycles. Steps 1 to 4 are backend and can be gated on
their own before the UI.

## Defaults assumed unless the operator says otherwise

- Model `claude-haiku-4-5` for judging; cost cap through the existing session
  and daily ceilings plus `SENTIMENT_MAX_SENTENCES_PER_RUN=400`.
- Competitors are scored as well as the client.
- Sentiment is on whenever `ANTHROPIC_API_KEY` is set; there is also a
  per-project `sentiment: bool` (default true) so a project can opt out.

## Explicitly out of scope

Narrative drivers over time; a Perception report across competitors;
translation; re-scoring history on a rubric change; sentiment on citations
without a mention; anything that acts on sentiment automatically.
