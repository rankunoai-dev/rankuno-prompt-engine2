# Cycle 0008 — Connectors corrected against live behaviour; logger extras; spend cap

**Date**: 2026-09-17
**Operator request**: "you are not fetching the citation properly at all … for a
brand prompt citations are not arriving … don't spend more than 0.5 $ when you
test from here, just put a cap" (chat, with the drawer of "How do I implement
GEP procurement software and integrate it with my ERP?" showing 0% citation on
all four engines while ChatGPT and Perplexity visibly cite gep.com).

## Scope

Find why a branded prompt reads "not cited" everywhere, fix each engine, and
cap test spend. Acceptance: one live call per engine on that prompt returns
gep.com among the sources for every engine whose vendor account is funded;
gate green; no test run above $0.50.

## Research findings (one live call per engine, $0.10)

| Engine | Stored | Live diagnosis |
| :-- | :-- | :-- |
| ChatGPT Search | `gpt-4o-mini-2024-07-18`, web 0%, 0 citations | The `web_search` tool was offered with `tool_choice: auto`; the model answered from memory. |
| Perplexity | `google/gemini-3.6-flash`, 0 citations, empty text | `/chat/completions` retired by the vendor (HTTP 403 "Sonar is now the Agent API"). A parallel session had moved to `/v1/responses` but hard-swapped the model to Gemini, sent no tools (the API then returns no sources at all), and the parser did not read the Agent API shape. `sonar-pro` is rejected there (400); Perplexity's own model is `perplexity/sonar`. With `tool_choice: auto` the model still skipped the search on this prompt. |
| Google AI Overview | present, 0 references; earlier samples "failed" | Sources are now inline `snippet_links` inside `text_blocks`; the `references` list the parser read is absent. Earlier failures were `serpapi_reported_error` whose text had been dropped by the logger. |
| Gemini | "unavailable", samples failed | HTTP 429 `RESOURCE_EXHAUSTED`: "Your prepayment credits are depleted" on the operator's Google AI Studio project. Not a code fault. |
| Logging | audit lines had only ts/level/logger/message | `logging.LoggerAdapter(logger, {})` replaces caller `extra=` on Python < 3.13. Every structured field since cycle 0001 was dropped. |

Perplexity's Agent API takes 45–60 s per answer and reports the real cost in
`usage.cost.total_cost` (~$0.01 with search).

## What was built

- `integrations/openai_search.py`: `tool_choice: {"type": "web_search"}`.
- `integrations/perplexity.py`: rewritten for `/v1/responses` with
  `perplexity/sonar`, `web_search` tool forced, 120 s timeout; sources from the
  `search_results` item ordered by `[n]` markers when present; retired names
  `sonar`/`sonar-pro`/`sonar-reasoning*` map to `perplexity/sonar`; legacy
  chat-completions payloads still parse. Prompt sent verbatim (no citation
  instruction appended). Config default and `.env`/`.env.example` updated.
- `integrations/serp_api.py`: inline `snippet_links` (blocks and list items)
  become references after any explicit `references`, in reading order.
- `core/logger.py`: `_MergingAdapter` keeps caller extras and renames keys that
  shadow `LogRecord` attributes (`name` → `ctx_name`), which surfaced as soon
  as extras started flowing (project creation raised `KeyError`).
- `scripts/live_check.py`: one call per engine under a `CostLedger` ceiling
  (default $0.50); exit 1 on any failure or empty source list.
- `.env`: `MAX_SESSION_SPEND_USD=0.5`.
- Tests: Perplexity suite rewritten (20 tests), OpenAI request test, two
  SerpApi inline-link tests, three logger tests.
- Docs: ADR 0011, README and ARCHITECTURE engine tables, blueprint §2,
  KNOWN_GAPS (closed two; added Gemini billing, real-cost wiring, model list,
  pre-0008 snapshot caveat).

## Bugs found and fixed

- Adapter rename of reserved keys (above): first gate run failed 6 tests and
  38 setups with `Attempt to overwrite 'name' in LogRecord`.
- My SerpApi patch placed the inline-link block outside the overview branch
  on first attempt (parse error); moved.
- Perplexity legacy path lost titles when the same URL appeared in both
  `citations` and `search_results`; entries are now merged.
- A parallel session's line in `app.py` exceeded the limit; formatted only.
- `test_engine_calls_run_on_multiple_threads` failed once while the server
  and live calls were running concurrently; passed on the re-run.

## Corrections

- ADR 0004 said Perplexity is `/chat/completions` with `sonar-pro`; that
  endpoint no longer exists. Amended by ADR 0011.
- Cycle 0006 recorded a forced full-project smoke run (24 paid calls). Under
  the new rule that run would not have been made; diagnostics are now single
  calls under a ceiling.

## Explicitly not done

- Gemini remains blocked until the Google AI Studio project is topped up.
- Actual Perplexity cost is not charged to the ledger (estimate still used).
- Snapshots taken earlier on 2026-09-17 are not rewritten; they under-report
  citations for three engines and are identifiable by run id and by model
  `google/gemini-3.6-flash` for Perplexity.
- The control plane's per-project model list (parallel session) still shows
  retired names; they are mapped, not removed.

## Local verification

`scripts/live_check.py` on the branded prompt after the changes:

```
CHATGPT_SEARCH   gpt-4o-mini-2024-07-18  web_triggered=True  citations=2   #1 gep.com  #2 gep.com
PERPLEXITY       perplexity/sonar        web_triggered=True  citations=15  gep.com at #5, #6 (15.7 s)
GOOGLE_AI_OVERVIEW google_ai_overview    web_triggered=True  citations=1   #1 gep.com
GEMINI           ERROR HTTP 429 RESOURCE_EXHAUSTED (prepayment credits depleted)
estimated spend this cycle: $0.20 across all diagnostics (ceiling $0.50)
```

Server restarted; `POST /api/projects` 201, `DELETE` 204; the audit line for
the creation now carries `project_id` and `ctx_name`.

## Step 5 audit answers (delta from cycle 0007)

1. Same vendors; Perplexity endpoint changed to the vendor's current one.
2. Rate key for Perplexity renamed to `perplexity.responses`.
3. Spend: session ceiling lowered to $0.50; diagnostics carry their own
   ledger; nothing bypasses `CostLedger`.
4. Unchanged. 5. Connector errors are now visible in the audit log with text.
6. No new data classes. 7. Unchanged contracts. 8. Unchanged.

## Gate output (verbatim)

```
=== Format ===  PASSED
=== Lint ===    All checks passed!  PASSED
=== Type check === Success: no issues found in 52 source files  PASSED
=== Tests ===
Name                                              Stmts   Miss Branch BrPart  Cover   Missing
---------------------------------------------------------------------------------------------
src\core\base_tool.py                                82      6     16      2    92%   68, 108, 193-194, 226-228
src\core\circuit_breaker.py                          86      0     20      1    99%   115->120
src\core\guardrails.py                               86      2     20      2    96%   197, 213
src\core\rate_limiter.py                            113      4     26      2    96%   77-78, 133->138, 237-238
src\core\retry.py                                    20      2      0      0    90%   47-48
src\core\url_safety.py                               71      1     18      0    99%   64
src\integrations\perplexity.py                       96      0     44      3    98%   129->128, 138->137, 141->140
src\integrations\url_resolver.py                     97      2     18      0    98%   105-106
src\modules\control_plane\__main__.py                70      2     10      1    96%   72-73, 120->128
src\modules\control_plane\app.py                    117      4      0      0    97%   79, 89, 100-101
src\modules\control_plane\planner.py                 57      1     26      1    98%   73
src\modules\control_plane\runner.py                 158     12     34      1    90%   260-272
src\modules\control_plane\store.py                  117      3     16      0    98%   81-83
src\modules\prompt_tracking\__main__.py             157      4     46      2    97%   157, 159, 298-299
src\modules\prompt_tracking\pipeline.py              255      3     70      2    98%   268, 429-430
src\modules\prompt_tracking\prompt_generator.py      73      1     24      1    98%   119
src\modules\prompt_tracking\scheduler.py            132      0     20      1    99%   266->275
---------------------------------------------------------------------------------------------
TOTAL                                              3874     47    824     19    98%
Required test coverage of 85.0% reached. Total coverage: 98.47%
626 passed, 2 warnings in 136.43s (0:02:16)
PASSED: Tests
ALL GATES PASSED.
```

Drift audit: `scripts/drift_check.py` → "No documentation drift detected."
