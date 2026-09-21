# Cycle 0013 — Prompt scope: every project screen, for all prompts or one

**Date**: 2026-09-19
**Operator request**: "i dont want the tabs to be one, i just want that most of
the tabs or screen is showing the data on the basis of whole analysis of all the
prompts in a project … i want the present data representation … of all the
section is absolutely fine right now also but i want the UI and information in
the screen must change on the basis of individual prompts also when selected
from the dropdown just like the trends tab … i know the schema or whatever the
distinction data representation and distinction would be tedious but if we
implement this feature with great cautiousness and with great plan and
identifying all the edge cases and failing points we can hit the bulls eye."

## Scope

Backend half. The operator withdrew the earlier "one workspace replacing two
tabs" idea; this cycle keeps every tab and adds a server-side prompt scope that
each tab can request. The front end is specified for the parallel UI session in
`docs/UI_SCOPE_BRIEF.md` and is not touched here — that session is live in the
exact files involved. Approved decisions: selector in the project header,
project tabs only; swap degenerate stats for a prompt equivalent where one
exists, dim the rest with a "project-wide" note; backend first.

## Research findings

Two audits — one over `InsightsView` field by field, one over every visible stat
in the UI — before any design:

- **The cycle-0012 guidance was wrong.** `PromptDetail` told callers to filter
  the cached project-wide `/insights` by `prompt_id`. Every list in that view is
  capped project-wide *before* serialisation (`changes[:50]`, `fanout[:200]`,
  `claims[:500]`, pages `[:50]`), and the claims dedup key `(sentence, url,
  engine)` has no prompt component. Client-side filtering silently loses rows
  for any prompt outside the top-N and mis-attributes shared claims. Both are
  now proven by tests.
- Of the 13 `InsightsView` fields, only `changes` and `claims` carry a prompt
  id at all. `fanout`, `winning_pages`, `client_pages` collapse the set to a
  count; `health`, `read_but_rejected`, `trust_profile`, `placement`,
  `freshness` never enter the prompt axis. Two action-card types
  (`read_but_rejected`, `freshness`) ship `prompt_ids=[]`.
- `_health`'s `losing_to` uses `_domain_share` over *all* samples — filtering
  positions alone would leave it project-wide. Scoping at the top fixes this
  for free.
- `CostReport` has no prompt dimension. `api_calls.prompt_id` is set only in
  `audit.py`'s `usage_context` (engine samples); Semrush, keyword rank and the
  redirect resolver (which runs *outside* the context block) are null. And
  `usage_context(prompt_id=None)` at the keyword-rank call site was a no-op —
  `None` was ignored rather than clearing.
- `?prompt=` is already the inspection-drawer deep link, written by four call
  sites; a URL-borne scope needs its own name.
- `ProjectLayout`, `TopBar` and `ProjectCard` all rebuild the path and drop the
  query string; `AppShell` animates on pathname only, so a query-string scope
  will not remount pages.
- A project guard on `sample_run_ids` would hide real data: 15 of 48 runs are
  orphaned from `project_runs`, including one project's entire dataset.

## What was built

- `control_plane/insights.py`: `build(prompt_id=)` reduces `prompts` to the one
  tracked prompt (`KeyError` if not tracked → 404) and filters current and
  previous positions through `_scoped()` before anything is aggregated.
  Nothing downstream changed; the caps now apply to the scoped set.
- `control_plane/runner.py`: `insights(prompt_id=)`. `app.py`:
  `GET …/insights?prompt_id=`; `GET /api/costs?project_id=&prompt_id=` with
  ownership check (404) and a 400 when `prompt_id` arrives without a project.
- `integrations/usage.py`: `calls(prompt_id=)`, `ix_api_calls_prompt` index,
  and `usage_context(key=None)` now **clears** an inherited value.
- `prompt_tracking/costing.py`: `build_cost_report(prompt_id=)`; `CostReport`
  gains `attribution`, `unattributed_calls`, `unattributed_actual_usd` and a
  note. Shared spend is reported beside the direct figure, never amortised.
- Docstrings on `PromptDetail` and `prompt_detail` corrected; ADR 0016 amended.
- Tests: 4 in `test_insights.py` (scoped fields, foreign id, the shared-claims
  proof, the 52-prompt cap proof), 2 in `test_costing.py` (direct-vs-shared
  spend, `None` clears context), 1 route test; 1 existing usage-context test
  inverted to the new semantics.
- Docs: ADR 0017, KNOWN_GAPS, README, ARCHITECTURE, and **`docs/UI_SCOPE_BRIEF.md`**
  — the control to copy, URL/state rules, query-key rules, a per-tab table of
  what swaps and what dims, exact replacement copy, and ten vitest cases.

## Bugs found and fixed

- `usage_context(prompt_id=None)` did nothing. Harmless today only because no
  prompt context enclosed the keyword-rank call; the new semantics make it do
  what it reads as doing. One existing test asserted the old behaviour and was
  updated.

## Deviations from the approved plan

- None of substance. The plan's cap proof called for 60 prompts; 52 suffices
  (the cap is 50) and keeps the test at one engine per prompt.

## Explicitly not done

- No UI edits. `docs/UI_SCOPE_BRIEF.md` is the handoff.
- No project guard on `sample_run_ids` (would hide orphaned-run data).
- Action-card ids unchanged; scoped and project cards share state by design.
- Semrush / keyword-rank / redirect spend stays unattributable.
- Atlas, Trends and Costs pages untouched.

## Step 5 audit answers (delta from cycle 0012)

1–3. No new vendors, endpoints that spend, or spend: two read-side query
parameters. 4. One new ledger index, `IF NOT EXISTS` in `_SCHEMA`. 5. Scope is
read-side; a foreign id raises before any work. 6. No new PII. 7. `CostReport.attribution`
is pattern-constrained. 8. `/costs?prompt_id=` is refused without a project and
checks ownership, matching `/samples`.

## Local verification

Ledger index on a **scratch copy** of the live store (never the live file):
dropped `ix_api_calls_prompt` by hand, re-opened `UsageLedger`, index present
again — the `IF NOT EXISTS` in `_SCHEMA` applies on open. `EXPLAIN QUERY PLAN`
for the per-prompt shape reports `SEARCH api_calls USING INDEX ix_api_calls_prompt`.
5,704 of 5,767 ledger rows carry a `prompt_id`; the 63 that do not are the
harvest / keyword-rank / redirect calls the report now names as unattributed.

No vendor call was made and no live run was touched.

## Gate output (verbatim, abridged to changed modules)

```
=== Format ===  PASSED
=== Lint ===    All checks passed!  PASSED
=== Type check === Success: no issues found in 59 source files  PASSED
=== Tests ===
src\integrations\usage.py                     144      3     24      0    98%   164-166
src\modules\control_plane\insights.py         401     17    174     16    94%
src\modules\prompt_tracking\costing.py        144      1     46      1    99%   164
TOTAL                                        5423     88   1200     54    98%
Required test coverage of 85.0% reached. Total coverage: 97.79%
709 passed, 2 warnings in 123.75s (0:02:03)
ALL GATES PASSED.
```

Drift audit: `scripts/drift_check.py` → "No documentation drift detected."

