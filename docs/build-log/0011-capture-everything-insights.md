# Cycle 0011 — Capture everything in the single run; insights and action cards

**Date**: 2026-09-17
**Operator request**: "go", after: "we have to make all those things which will
help to bring the bad numbers down … the solution from the same or approx same
API call and costing … if anything else data-capturing feature is needed we
will integrate those also, because we cannot afford separate API calls for
data capture for citation and mentions and separate calls for solution …
everything is captured in a single run only for a project; while implementing,
capture the things which we can implement more which can be stone breaker."

## Scope

No new vendor call. Parse and persist everything the paid responses already
contain that a prescription needs; compute verdicts, changes and action cards
from stored data; expose them to the UI. Acceptance: live check shows the
new fields on real answers at the same per-call cost; insights computed for a
seeded project produce every card type with evidence; gate green.

## Research findings

- Live payloads (cost of the checks: $0.09) confirmed the raw material is
  already there: Perplexity returned 3 fan-out queries and 15 dated source
  snippets; ChatGPT returned its search query and annotation spans; Google's
  AI Overview carries the sentence per inline link and related searches.
- ChatGPT renders a citation as a bare markdown link *after* the sentence it
  supports; naive sentence extraction returned the link text. Resolving a
  link-only or link-leading span to the previous sentence and stripping
  inline link markup gives prose claims ("This integration ensures that
  procurement decisions are informed by real-time financial data …" →
  gep.com/…/procurement).
- Perplexity does not annotate claims unless the prompt asks for `[n]`
  markers; the prompt stays verbatim, so Perplexity claims exist only when
  the model emits markers on its own. Its sources list and snippets are the
  evidence instead.
- The AI Overview follow-up request regularly exceeds 30 s; the SerpApi
  client now uses at least 60 s.
- One connector instance serves many threads (cycle 0009 lesson); claims and
  snippets are per answer object, so no shared state was added.

## What was built

- `integrations/schemas.py`: `CitationClaim`, `SourceSnippet`;
  `EngineAnswer.search_queries/citation_claims/source_snippets`;
  `OrganicResult.snippet`; `SerpSnapshot.related_searches/ai_overview_claims`.
- `integrations/claims.py` (new): `sentence_at`, `sentences_with_marker`,
  `claim_sentence`.
- Connectors: OpenAI (query/queries, annotation offsets across parts),
  Perplexity (queries, snippets/dates, marker claims), Gemini (queries,
  grounding-support claims; redirect resolution remaps claims and snippets),
  SerpApi (block-text claims, related searches, organic snippets, 60 s).
- `prompt_tracking/schemas.py`: `AnswerSample.answer_text/search_queries/
  citation_claims/source_snippets`; `OrganicRankSnapshot.organic_results/
  paa_questions/related_searches`. `assembly.py` and `organic.py` fill them.
- `time_series_db.py`: migration columns, writers, `samples_for`,
  `sample_run_ids`, `prompt_records`, extended organic reader.
- `control_plane/insights.py` (new): `InsightEngine.build` → basis, health
  (winning / present / invisible / losing-to, delta, volatility), changes,
  eight action-card rules with evidence and impact ranking, fan-out map with
  client coverage, claim ledger, trust profile, read-but-rejected pages,
  winning and client pages, placement, freshness; `update_action` with
  baseline and outcome scoring. Copy in `_TITLES` / `_PRESCRIPTIONS`.
- `control_plane/actions.py` (new): `ActionStateStore` (`action_states`).
- `control_plane/positioning.py`: `compute()` (positions without storing);
  `consolidate()` built on it.
- `control_plane/schemas.py`: `ActionState`, `ActionUpdate`, `InsightBasis`,
  `EngineHealth`, `InsightChange`, `EvidenceQuote`, `DomainShare`,
  `ActionEvidence`, `ActionCard`, `FanoutQuery`, `ClaimEntry`, `TrustShare`,
  `RejectedPage`, `PageInventory`, `PlacementProfile`, `FreshnessProfile`,
  `InsightsView`.
- Routes: `GET /api/projects/{id}/insights[?consolidation_id]`,
  `PUT /api/projects/{id}/actions/{action_id}`,
  `GET /api/projects/{id}/samples?prompt_id&engine&run_id`.
- `scripts/live_check.py` prints queries, claims and snippets.
- Tests: claims (4), connector captures (5), capture round trip and
  migration (4), insights (5), API routes (1).
- Docs: ADR 0014, README, ARCHITECTURE, KNOWN_GAPS, blueprint §7g,
  UI brief §5 and UI session prompt updated to the live contract (UI ADR is
  now 0015).

## Bugs found and fixed

- Claims were deduplicated across engines, so only the first engine kept its
  attributions; the key now includes the engine.
- Test fixtures consolidated twice over the same crawl (no movement to
  detect); restructured to seed crawl 1 between consolidations.
- Regex-based patches on formatter-rewrapped code failed twice; replaced by
  anchored rewrites and, for card copy, by module-level templates (also the
  fix for over-long lines).
- `PromptCandidate` in a new test used a stale field name; aligned with the
  contract (`intent=IntentDecision(...)`).

## Corrections

- The UI brief said the insight endpoints were "planned"; they are live, and
  the contract gained `volatility`, `prompts`, `client_covered`,
  `winning_pages`, `client_pages`, `placement`, `freshness`, `metric`,
  `basis.computed_from`.

## Explicitly not done

- Rules are fixed thresholds; no learning from outcomes.
- Perplexity claims depend on the model emitting `[n]` markers.
- Pre-0011 samples carry empty new fields; cards over them are sparse until
  new crawls land (the existing project shows only the landing-page card
  until its next crawl).
- No UI for insights in the legacy HTML page; the React UI is the consumer.

## Local verification

Live check (`scripts/live_check.py`, $0.09 total): ChatGPT — search query
captured, 2 claims now prose sentences attributed to two gep.com pages;
Perplexity — 3 fan-out queries ("GEP procurement software ERP integration
official documentation", "GEP SMART integration ERP API official", "GEP
procurement implementation guide ERP"), 15 source snippets with dates, 15
citations with gep.com first; Google AI Overview — one follow-up timed out at
30 s (fixed to 60 s afterwards). Server restarted: `GET /api/projects/{id}/insights`
returns basis `none` for the existing project (no crawl recorded since cycle
0010) with one landing-page card; `/openapi.json` 200.

## Step 5 audit answers (delta from cycle 0010)

1–2. No new vendors, endpoints or rate keys; the same requests are parsed
further. 3. Spend per call unchanged; SerpApi timeout raised only. 4. Claims
and snippets are per sample; stores are append-only. 5. Insights never raise
into a run; they are read-side. 6. Full answer text is stored (already
returned to us); no PII beyond what answers contain. 7. All new bodies are
`StrictModel`s; action status is constrained to open/done. 8. Unchanged.

## Gate output (verbatim, abridged to changed modules)

```
=== Format ===  PASSED
=== Lint ===    All checks passed!  PASSED
=== Type check === Success: no issues found in 59 source files  PASSED
=== Tests ===
src\integrations\claims.py                           40      1     10      1    96%   54
src\integrations\gemini_search.py                   114      0     36      1    99%   180->175
src\integrations\openai_search.py                   107      0     40      2    99%   139->138, 176->174
src\integrations\perplexity.py                      120      0     54      4    98%   160->159, 164->163, 173->172, 176->175
src\integrations\serp_api.py                        121      1     46      2    98%   224, 225->227
src\modules\control_plane\actions.py                 37      3      0      0    92%   54-56
src\modules\control_plane\insights.py               393     17    168     16    94%   167-168, 382-383, 432, 445, 456-457, 481, 539, 614->612, 653, 682, 697, 707, 709, 710->679, 737->730, 913, 1078
src\modules\control_plane\positioning.py            163      8     44      3    95%   95-96, 215-217, 412, 425, 437
TOTAL                                              5275     86   1178     51    98%
Required test coverage of 85.0% reached. Total coverage: 97.72%
686 passed, 2 warnings in 218.36s (0:03:38)
PASSED: Tests
ALL GATES PASSED.
```

Drift audit: `scripts/drift_check.py` → "No documentation drift detected."
