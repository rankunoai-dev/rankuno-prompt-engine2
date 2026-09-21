# Cycle 0012 — Prompt identity frozen; one endpoint for a prompt's whole story

**Date**: 2026-09-19
**Operator request**: "i want one of the most important classification of the whole
data … that would be the game changer … change of the whole data which is POSSIBLE
on the basis of prompts like when i select the individual prompt the amazing change
in data … and one more important dont blindly follow my instructuion and dont need
to change all the data on my instruction first identify all the edge cases and then
design the plan and then start implementing … also one interface for all the prompts
in the project."

## Scope

Backend half of a two-cycle plan. The prompt workspace UI is cycle 0013; this
cycle makes the data reachable and stops it being destroyed. Approved decisions:
replace the Prompts and Battleground tabs with one workspace, project scope only,
detail leads with verdict-and-why, freeze `prompt_id` at creation, backend first.

## Research findings

The operator asked for edge cases before a plan, so the live store was audited
read-only (4 projects / 46 project prompts / 5,997 samples / 2,634 snapshots /
1,346 organic rows / 48 runs) alongside the code. What the audit changed:

- **`prompt_id` moved under the data.** A pure content hash of `(lob, text)`,
  re-derived on every text edit and on every LOB rename. All 46 live rows matched
  the hash exactly, and 24 orphaned ids already carry history no project owns.
  This was the single largest finding and it inverted two shipped tests.
- **`client_cited` is a ≥50% majority verdict**, not "cited at all" — verified
  with zero mismatches against `client_citation_rate >= 0.5`. 482 snapshots hold
  a real `client_best_rank` while storing `client_cited = 0`, and the React
  `promptView.ts` renders exactly those as "absent" today.
- **`client_citation_rate` excludes failed samples from its denominator** and is
  rounded at write time; 71 partially-failed snapshots disagree with a naive
  recomputation. It must be displayed, not recalculated.
- **Absence has three causes.** 6 of 46 prompts were never sampled, 26 of 46 are
  sampled on fewer platforms than configured, and 41 prompt × engine pairs are
  pure Gemini 429 failures. Failure lives in `snapshots.failed_samples`; a failed
  call writes a snapshot row and no sample row, so counting samples hides it.
- **Capture is bimodal, not partial.** 39 of 63 prompts have *zero* `answer_text`
  and 24 have full coverage; nothing in between, because the cycle-0011 boundary
  is per run. An empty answer tab means "not captured in this era".
- **15 of 48 runs are orphaned** from `project_runs`, and one project's 15 prompts
  have all their data in a run its own crawl list does not contain — so a prompt
  timeline must come from `sample_run_ids`, not the project's crawls.
- **Five readers were already written, tested and unreachable**: `db.history`,
  `db.velocity`, `db.organic_history`, `db.organic_velocity`, `db.sample_run_ids`.
  The endpoint is assembly, not new data modelling.
- `answer_samples` had no `prompt_id` index, so the one per-prompt endpoint in
  the product was a full table scan.

## What was built

- `control_plane/store.py`: `update_prompt` preserves `prompt_id` across a text
  change; `update_project` no longer re-keys prompts on a LOB rename.
- `control_plane/schemas.py`: `EngineStatus` (four states), `PromptEngineDetail`,
  `PromptCapture`, `PromptPosition`, `PromptDetail`.
- `control_plane/runner.py`: `prompt_detail()`; `_engine_detail()` and
  `_engines_with_history()` at module level; `_result_for()` extracted so
  `results()` and `prompt_detail()` cannot drift; `samples()` now checks project
  ownership and clamps `limit`.
- `control_plane/positioning.py`: `positions_for_prompt()` — one join for a
  prompt across every consolidation, instead of one `positions()` call per
  consolidation each carrying the whole project's set; `_maybe_dt` helper
  replacing a twice-inlined conditional; `ix_positions_prompt` index.
- `prompt_tracking/time_series_db.py`: `capture_coverage()` counting the rich
  layers in SQL; `idx_samples_prompt_time` index; `run_id` mapped in
  `_row_to_sample` (the query already selected it).
- `prompt_tracking/schemas.py`: `AnswerSample.run_id`, defaulted so no caller
  breaks.
- Routes: `GET .../prompts/{tracked_id}`, `GET .../prompts/{tracked_id}/detail`,
  and `limit` on `GET .../samples`.
- Tests: `test_prompt_detail.py` (12) covering the four engine states, dropped
  platforms keeping history, minority citation, capture coverage, project
  scoping, limit clamping, positions across consolidations, never-sampled
  prompts, shared-LOB naming, and the two identity-freeze paths; 2 route tests in
  `test_app.py`; 2 existing store tests inverted.
- Docs: ADR 0016, KNOWN_GAPS (shared-LOB merge, majority verdict, detail cost).

## Bugs found and fixed

- **Two costing tests were date-dependent and broke overnight.**
  `test_costing.py` seeds ledger rows at a frozen `NOW = 2026-09-17 12:00` but
  filtered them with `days=1`, which `build_cost_report` resolves against the
  real clock. They passed on 2026-09-18 and failed on 2026-09-19 with "0 calls".
  Both now use `since=NOW - timedelta(days=1)`, matching their sibling test, and
  a new `test_days_is_shorthand_for_a_window_ending_now` keeps the `days=`
  branch covered with windows wide and narrow enough to be stable. Unrelated to
  this cycle's changes; it would have broken the gate on any later day.
- A test reached into `runner._positions`; `ProjectRunner` already accepts an
  injected `PositionStore`, so the fixture supplies one.

## Deviations from the approved plan

- **Insights are not assembled into `PromptDetail`.** The plan listed "the
  prompt's slice of changes/claims/actions", but the same plan forbids per-prompt
  variants of cached whole-project endpoints. `changes` and `claims` carry
  `prompt_id` and action cards carry `prompt_ids`, so the client filters its
  cached `/insights` response; building it server-side would cost `~6 + P`
  queries per request for data the client already holds.
- **Platforms dropped from a project still appear**, as `not_configured` with
  their history intact. Hiding crawls the operator already paid for would look
  like the data never existed.

## Explicitly not done

- No lineage table for the 24 pre-existing orphans. The freeze prevents new ones;
  back-linking old ones needs a migration across three FK-bearing tables for data
  no project references.
- The shared-LOB history merge is unfixed — `prompt_id` still has no project
  component. `shared_lob_projects` warns; it does not prevent.
- `client_cited`'s threshold is unchanged and no stored data was rewritten. The
  counts are exposed so the UI can stop misreading it in cycle 0013.
- No cursor on `/samples`, only a clamped `limit`. 1000 covers ~80 crawls.

## Step 5 audit answers (delta from cycle 0011)

1–3. No new vendors, endpoints or spend: every addition is a read over stored
data. 4. Two new indexes, both `IF NOT EXISTS` inside `_SCHEMA`, which is re-run
on open, so live databases upgrade without a migration step. 5. `prompt_detail`
is read-side and cannot raise into a run. 6. No new PII; `PromptDetail` returns
answer text the store already holds. 7. All new bodies are `StrictModel`s;
`EngineStatus` is a closed enum. 8. `/samples` is now scoped to the project,
closing a route that served any prompt's samples under any project id.

## Local verification

Indexes confirmed against a **scratch copy** of the live store (never the live
file): `answer_samples` went from `['idx_samples_engine_time']` to include
`idx_samples_prompt_time`, `positions` gained `ix_positions_prompt`, both purely
by opening the database. `EXPLAIN QUERY PLAN` for the `samples_for` shape now
reports `SEARCH answer_samples USING COVERING INDEX idx_samples_prompt_time`
where it previously scanned.

OpenAPI generated in-process (the running server predates this code, so its
`/openapi.json` would be stale): `/api/projects/{project_id}/prompts/{tracked_id}`
now carries `get` alongside `put`/`delete`, `…/detail` is present, and
`EngineStatus` serialises as `['has_data', 'asked_failed', 'never_asked',
'not_configured']`. Cycle 0013 should re-run `npm run gen:types` in `ui/` before
consuming it — left undone here to avoid colliding with the parallel UI session.

No vendor call was made and no live run was disturbed; every check above is a
read or runs against a temporary database.

## Gate output (verbatim, abridged to changed modules)

```
=== Format ===  PASSED
=== Lint ===    All checks passed!  PASSED
=== Type check === Success: no issues found in 59 source files  PASSED
=== Tests ===
src\modules\control_plane\app.py            173      6      6      2    96%
src\modules\control_plane\positioning.py    169      8     44      3    95%   98-99, 223-225, 445, 458, 470
src\modules\control_plane\runner.py         230     12     48      1    93%   475-487
src\modules\control_plane\store.py          112      3     10      0    98%   81-83
TOTAL                                      5393     87   1188     53    98%
Required test coverage of 85.0% reached. Total coverage: 97.78%
702 passed, 2 warnings in 150.74s (0:02:30)
ALL GATES PASSED.
```

`runner.py` 475-487 is the parallel session's `engine_models` override block,
untouched and untested by this cycle.

Drift audit: `scripts/drift_check.py` → "No documentation drift detected."

