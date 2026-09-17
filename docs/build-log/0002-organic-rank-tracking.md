# Cycle 0002 — Google organic rank tracking

**Date**: 2026-09-16
**Operator approval**: "do it both" (chat, 2026-09-16) — track organic rank for the
conversational prompt text (free, same SerpApi call) **and** for the short seed
keyword (one extra SerpApi call per distinct keyword per run).

## Scope

Answer "is Google ranking for a prompt tracked?" — it was not. The SerpApi
connector fetched organic results and discarded them; nothing stored or reported
organic position. Acceptance: both rank kinds captured, persisted with history,
velocity available, reported in the CSV, `--no-keyword-rank` switch, gate green.

## Research findings

- `SerpApiClient.search()` already parsed organic domains; positions, URLs and
  titles were dropped. `ask()` threw the SERP away after building the AI Overview
  answer, so the pipeline could not see it.
- Organic ranks vary by device as well as locale; the connector fixed `gl/hl/
  location` but sent no `device`. Added `SERP_DEVICE` (desktop|mobile|tablet).
- A conversational prompt and its seed keyword produce different SERPs; neither
  measurement substitutes for the other (ADR 0005).

## What was built

- `integrations/schemas.py`: `OrganicResult`; `SerpSnapshot.organic_results`,
  `device`; `organic_domains` is now a derived property.
- `integrations/serp_api.py`: parses organic results (Google's `position`, list
  index fallback), sends `device`, new `search_and_ask()` returning snapshot +
  engine answer from one call; `ask()` delegates to it.
- `modules/prompt_tracking/schemas.py`: `RankQueryKind`, `OrganicRankSnapshot`,
  `OrganicVelocityReport`; `MasterPromptRecord.organic_history` + `latest_rank()`;
  `PipelineInput.track_keyword_rank`; `TrackerRunSummary.keyword_rank_calls`.
- `modules/prompt_tracking/organic.py`: `build_organic_snapshot()` (best client
  position across samples, ranking URL, top-10 domains, competitor best positions).
- `time_series_db.py`: `organic_snapshots` table, `record_organic()`,
  `organic_history()`, `organic_velocity()` (window-over-window best position).
- `pipeline.py`: audit outcome carries SERP samples → PROMPT rank; per-run cache
  of KEYWORD rank, one SerpApi call per distinct keyword, charged to the ledger
  before the call; `describe_invocation()` projects the keyword-call spend.
- `report.py`: five new trailing columns (rank + URL for prompt and keyword,
  organic competitors). `__main__.py`: `--no-keyword-rank`; keyword calls in output.
- Docs: README, ARCHITECTURE, blueprint v2 §6–7, ADR 0005, `.env.example`.

## Bugs found and fixed

- Test isolation: with keyword tracking on by default, a pipeline test that
  injected only two engine stubs made the pipeline lazily build a real
  `SerpApiClient` and attempt the network. The shared test helper now injects a
  stub for the Google engine; the lookup is skipped (not faked) for stubs, and
  that skip path is itself tested.
- No source bugs found by the new tests.

## Corrections

- Cycle 0001's blueprint §6 step 5 described the audit as citation-only; it now
  records organic rank as part of the same step.
- README claimed the SQLite store held "prompts, snapshots, runs"; it now also
  holds `organic_snapshots`.

## Explicitly not done

- No Google Search Console position data (average position for impressions
  received is a different measure; complementary, not built).
- No mobile/desktop dual tracking in one run — `SERP_DEVICE` is one value per
  tracker configuration.
- Organic rank is captured only when the run audits engines; `--skip-engine-audit`
  skips it too, by design (no SerpApi spend in research-only mode).
- `docs/prompt-atlas.html` and `scripts/export_dashboard.py` were added to the
  repository by a parallel session during this cycle. They lint clean and are not
  part of this cycle's scope; the exporter does not yet read `organic_snapshots`.

## Step 5 audit answers (delta from cycle 0001)

1. Hosts unchanged. Volume: +1 SerpApi call per distinct seed keyword per run.
2. Rate key `serpapi.search` shared with the AI Overview audit.
3. Worst case +N×`COST_SERPAPI_CALL_USD` where N = seed keywords; charged before
   the call; bounded by `MAX_SESSION_SPEND_USD`.
4. Reads only. Cached per run; failure cached too (no per-prompt retry).
5–8. Unchanged.

## Gate output (verbatim)

```
=== Format ===
102 files already formatted
PASSED: Format
=== Lint ===
All checks passed!
PASSED: Lint
=== Type check ===
Success: no issues found in 37 source files
PASSED: Type check
=== Tests ===
Name                               Stmts   Miss Branch BrPart  Cover   Missing
------------------------------------------------------------------------------
src\core\base_tool.py                 82      6     16      2    92%   68, 108, 193-194, 226-228
src\core\guardrails.py                86      2     20      2    96%   197, 213
src\core\logger.py                    59      1     10      1    97%   107
src\core\rate_limiter.py             113      4     26      2    96%   77-78, 133->138, 237-238
src\core\retry.py                     20      2      0      0    90%   47-48
src\core\url_safety.py                71      1     18      0    99%   64
src\integrations\url_resolver.py      97      2     18      0    98%   105-106
------------------------------------------------------------------------------
TOTAL                               2259     18    486      7    99%
30 files skipped due to complete coverage.
Required test coverage of 85.0% reached. Total coverage: 99.09%
446 passed in 94.39s (0:01:34)
PASSED: Tests
ALL GATES PASSED.
```

Drift audit: `scripts/drift_check.py` → "No documentation drift detected."
