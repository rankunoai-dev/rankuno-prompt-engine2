# ADR 0014 — Capture everything in the single run; insights and action cards from stored data

**Status**: Accepted (2026-09-17). Operator rule: "we cannot afford separate
API calls for data capture and separate calls for the solution; everything is
captured in the single run of a project, and whatever brings the bad numbers
down must come from the same or approximately the same calls and cost."

## Context

Until cycle 0010 the engine measured (cited, mentioned, rank, links,
competitors, organic rank, consolidated positions) but discarded most of what
the paid responses contain, and nothing turned measurement into a
prescription. Every vendor response already carries the engine's own search
queries, which sentence each source supports, the source passages and dates,
the pages the engine read but did not cite, and the SERP's questions.

## Decisions

1. **Same calls, zero extra spend.** No new request is made anywhere. The
   connectors parse more of the responses they already receive:
   - ChatGPT Search: `web_search_call.action.query`/`queries` (fan-out),
     `url_citation` indices mapped to the sentence they annotate (claims).
   - Perplexity: `search_results.queries` (fan-out), `results[].snippet/date/
     last_updated` (source snippets), `[n]` markers mapped to sentences.
   - Gemini: `webSearchQueries` (fan-out), `groundingSupports` segments mapped
     to chunk URIs (claims), redirect resolution remaps claims too.
   - Google AI Overview: each text block's inline links as claims (block text
     is the claim), `related_searches`, organic titles and snippets, PAA.
   - Full answer text is stored per sample (the 300-char excerpt stays for
     the drawer preview).
2. **Stored where samples already live.** `answer_samples` gains
   `answer_text`, `search_queries`, `citation_claims`, `source_snippets`;
   `organic_snapshots` gains `organic_results`, `paa_questions`,
   `related_searches`; all via the idempotent migration helper. Readers:
   `samples_for`, `sample_run_ids`, `prompt_records`.
3. **Insights are a pure function of stored data** (`control_plane/insights.py`).
   Basis: the latest consolidation (ADR 0013), else the latest crawl labelled
   low-confidence. Outputs: per-platform health verdicts (winning / present /
   invisible / losing to X, with delta and volatility), changes since the
   previous consolidation, action cards, the fan-out map with client coverage,
   the claim ledger, the engine trust profile, read-but-rejected client
   pages, winning competitor pages, client pages that do the work, brand
   placement inside answers, and source freshness.
4. **Action cards are deterministic and stable.** Eight rule types (convert
   mention, own claim, read-but-rejected, AI Overview gap, earned placement,
   freshness, defend, landing page), each with evidence (quotes, domains,
   URLs, the engine's queries, numbers) and an impact score (cluster search
   volume × gap). The id hashes the trigger, so status, owner and note kept in
   `action_states` survive recomputation; when a card is marked done the
   metric is stored as a baseline and scored improved / unchanged /
   regressed once a later consolidation exists.
5. **Copy is data, not code.** Titles and prescriptions are templates in one
   table so wording can be tuned without touching rules.

## Consequences

- Rows are larger (full text per sample); SQLite handles it at this scale.
- Pre-0011 samples have empty new fields; insights over them show fewer
  cards until the next crawls land.
- The UI reads `GET /api/projects/{id}/insights`, `PUT …/actions/{id}` and
  `GET …/samples`; contracts in `docs/UI_BUILD_BRIEF.md` §5.
