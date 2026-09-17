# ADR 0008 — Full citation links, brand mentions, and unpolluted prompts

**Status**: Accepted (2026-09-16), operator request: links of every citation on
every platform, mentions with the sentence where the client is named, a client
input for the UI that must not influence the engine run.

## Context

Engine answers carried full URLs and consulted pages in memory, but snapshots,
raw sample rows and the CSV kept only domains, so links never reached storage
or a report. Nothing scanned answer text for the brand. And custom prompts were
being capitalised and given a trailing question mark before being sent.

## Decisions

1. **Prompts are sent verbatim.** `PromptGenerator.from_custom` no longer
   normalises text; only whitespace is collapsed at input. Generated prompts
   (ours) may still be normalised. The client profile is used exclusively after
   the answer returns (`citations.build_snapshot`, `mentions.detect_mentions`).
2. **Links are first-class.** `CitationSnapshot.citation_links` (union across
   samples, best position per URL), `client_urls`, `consulted_urls`;
   `AnswerSample.citation_links` / `consulted_urls` per raw answer. Both
   persisted (JSON columns) with idempotent migrations.
3. **Mentions are detected, not inferred.** Exact, case-insensitive,
   word-bounded matching on brand name, aliases and competitor terms
   (`competitor_names`, else the ≥3-character first label of each competitor
   domain). The containing sentence is the snippet (≤600 chars, markdown
   markers stripped). Stored per sample and aggregated per snapshot as
   `mention_detected`, `mention_rate`, up to ten distinct `mention_snippets`,
   and `competitor_mentions` counts.
4. **A UI-ready dataset per run.** `write_ui_dataset` emits one flat JSON row
   per prompt × engine with all of the above plus organic ranks, alongside the
   CSV, which gains "Client URLs" and "Mention Snippet" per engine.

## Alternatives considered

- **LLM-based mention/sentiment extraction**: richer, but non-deterministic and
  a second metered call per answer. Exact matching is auditable and free;
  sentiment is a candidate for a later cycle.
- **Storing links only in the dataset file**: rejected; history must be
  queryable from SQLite.

## Consequences

- Snapshot rows grow (JSON columns); acceptable at tracker volumes.
- Analysts should supply `competitor_names` when a competitor's brand differs
  from its domain label.
