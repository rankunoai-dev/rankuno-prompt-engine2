# Cycle 0009 — Per-call usage ledger and observed costing parameters

**Date**: 2026-09-17
**Operator request**: "whenever you are calling any API, its volume and number
of calls to multiple platforms, I want you to track the costing, hence in the
end we would have a better idea and can create very better and best costing
parameters" (chat).

## Scope

Record every outbound vendor request with its volume signals (tokens,
searches, units), the configured estimate and the vendor-reported or modelled
cost; tag it with source, run, prompt and engine; aggregate per vendor, run and
source; propose `COST_*` values from observation; expose in CLI, API and UI.
Backfill this session's calls made before the ledger existed. Acceptance:
rows appear for every connector call from any entry point, the report proposes
parameters, gate green.

## Research findings

- `BaseAPIClient.call()` is the single choke point every connector uses, so
  recording there catches ok, error and breaker-refused calls without touching
  connector logic; enrichment (tokens, cost) is the connector's job because
  only it knows the payload shape.
- Only Perplexity reports cost per response (`usage.cost.total_cost`, about
  $0.01 per searched answer). OpenAI and Gemini report tokens; SerpApi and
  Semrush are plan-priced. Hence three cost columns and an explicit precedence.
- `contextvars` do not cross `ThreadPoolExecutor` boundaries; the pipeline must
  copy its context into each submitted task or worker calls lose run/prompt.
- A connector instance is shared by pool threads, so "the call to enrich"
  must be thread-local; the first propagation test caught threads annotating
  each other's rows.

## What was built

- `integrations/usage.py`: `ApiCall`, `UsageLedger` (`api_calls` table in the
  tracker DB, indexes on ts/run/vendor), `usage_context`,
  `current_usage_context`, `get_usage_ledger` (one instance per path).
- `integrations/pricing.py`: list prices per model (tokens) and per-search
  fees for OpenAI and Gemini; family fallback; `modelled_cost`.
- `integrations/base_client.py`: records every call with latency, status and
  estimate; `note_usage()` enriches the thread's last call.
- Connectors: OpenAI (tokens, cached, web-search count, modelled cost),
  Perplexity (tokens, search invocations, vendor cost), Gemini (tokens,
  grounding, modelled cost), SerpApi (one plan search per request),
  Semrush (units, unit price).
- Tags: pipeline sets `run_id` and default source `pipeline`; audit worker
  sets `prompt_id` and `engine`; keyword-rank lookups tagged `KEYWORD_RANK`;
  job manager `control_plane`; CLI `cli`; live check `live_check`.
- `prompt_tracking/costing.py`: `build_cost_report` (per vendor, run, source;
  p50/p95 latency; recommendations = observed mean × 1.15 with basis
  vendor-reported / modelled / estimate-only / mixed) and `format_cost_report`.
- CLI `costs [--days N] [--run-id …] [--json]`; `GET /api/costs?project_id=&days=`
  (uses injected settings; project narrows to its LOB's runs); Costs tab in
  the control plane with scope and window selectors.
- Backfill of 40 calls from this session (`source=backfill`, notes per row).
- Tests: usage (7), pricing (4 + 13 params), costing (4), pipeline propagation
  (2), base-client/connector recording (5), CLI costs (1), API costs (1).
- Docs: ADR 0012, README "Cost tracking", ARCHITECTURE, KNOWN_GAPS, blueprint §7e.

## Bugs found and fixed

- Thread-shared `last_call_id` (above) → `threading.local()`.
- `/api/costs` initially read the ledger from global settings, which in tests
  would have pointed at the real `.env` database; now uses injected settings.
- Cost test assumed seeded rows were "older than a day" on the same calendar
  day; moved a week back.
- Backfill rows for the cycle 0006 smoke run lacked per-search counts, which
  skewed the SerpApi proposal to double; corrected and reloaded.
- Mixed-basis vendors (Perplexity: 3 vendor-reported, 8 estimate-only) were
  labelled "estimate-only"; basis is now explicit.

## Corrections

- Cycle 0008 said Perplexity's real cost "is not charged to the ledger"; it is
  now recorded per call and drives the proposal, though reservation still
  uses the setting (gap kept).

## Explicitly not done

- Actual cost is not charged live to the spend ceiling; the operator copies
  proposed values into `.env`.
- List prices are hand-maintained; OpenAI/Gemini "actual" is modelled until
  checked against an invoice.
- Semrush harvest calls of the cycle 0006 smoke run are not in the backfill
  (unit counts unknown); one Perplexity probe that timed out client-side has
  unknown billing.

## Local verification

Real ledger after backfill (`python -m src.modules.prompt_tracking costs`):

```
Usage ledger: 40 calls | estimated $0.8900 | actual $0.7575 | by source: backfill=40
vendor      calls  ok  err  in tok  out tok  search  est $   actual $  mean/ok
gemini          7   6    1       0        0       0  0.2800  0.2400    0.04000
openai          7   7    0       0        0       1  0.2100  0.2100    0.03000
perplexity     14  11    3    4703     5735       3  0.2800  0.1875    0.01705
serpapi        12  12    0       0        0      12  0.1200  0.1200    0.01000
Proposed: gemini 0.04 -> 0.046 (estimate-only); openai 0.03 -> 0.0345 (estimate-only);
          perplexity 0.02 -> 0.0197 (mixed: 3 vendor-reported, 0 modelled, 8 estimate-only);
          serpapi 0.01 -> 0.0115 per search (modelled)
```

Server restarted; `GET /api/costs` returns the same figures; the page contains
the Costs tab. From this point every call records itself with tokens and cost.

## Step 5 audit answers (delta from cycle 0008)

1–2. No new vendors or rate keys. 3. Spend controls unchanged; recording is
observation only and never raises into a paid call. 4. Ledger inserts use
`INSERT OR REPLACE` by call id. 5. Ledger faults are logged, never fatal.
6. Rows hold prompt ids, not prompt text; no PII. 7. `ApiCall` is a
`StrictModel`; `update()` rejects unknown fields. 8. Unchanged.

## Gate output (verbatim)

```
=== Format ===  PASSED
=== Lint ===    All checks passed!  PASSED
=== Type check === Success: no issues found in 55 source files  PASSED
=== Tests ===
Name                                              Stmts   Miss Branch BrPart  Cover   Missing
---------------------------------------------------------------------------------------------
src\core\base_tool.py                                82      6     16      2    92%   68, 108, 193-194, 226-228
src\core\circuit_breaker.py                          86      0     20      1    99%   115->120
src\core\guardrails.py                               86      2     20      2    96%   197, 213
src\core\rate_limiter.py                            113      4     26      2    96%   77-78, 133->138, 237-238
src\core\retry.py                                    20      2      0      0    90%   47-48
src\core\url_safety.py                               71      1     18      0    99%   64
src\integrations\base_client.py                      85      1     10      2    97%   188, 190->exit
src\integrations\perplexity.py                      110      0     46      3    98%   159->158, 168->167, 171->170
src\integrations\url_resolver.py                     97      2     18      0    98%   105-106
src\integrations\usage.py                           140      3     22      0    98%   157-159
src\modules\control_plane\__main__.py                70      2     10      1    96%   72-73, 120->128
src\modules\control_plane\app.py                    130      4      2      0    97%   89, 99, 110-111
src\modules\control_plane\planner.py                 57      1     26      1    98%   73
src\modules\control_plane\runner.py                 158     12     34      1    90%   260-272
src\modules\control_plane\store.py                  117      3     16      0    98%   81-83
src\modules\prompt_tracking\__main__.py             172      4     48      2    97%   177, 179, 318-319
src\modules\prompt_tracking\costing.py              133      4     40      2    95%   149-151, 224
src\modules\prompt_tracking\pipeline.py             263      3     70      2    98%   277, 438-439
src\modules\prompt_tracking\prompt_generator.py      73      1     24      1    98%   119
src\modules\prompt_tracking\scheduler.py            132      0     20      1    99%   266->275
---------------------------------------------------------------------------------------------
TOTAL                                              4286     55    908     23    98%
Required test coverage of 85.0% reached. Total coverage: 98.34%
662 passed, 2 warnings in 111.03s (0:01:51)
PASSED: Tests
ALL GATES PASSED.
```

Drift audit: `scripts/drift_check.py` → "No documentation drift detected."
