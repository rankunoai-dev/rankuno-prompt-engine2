# Cycle 0010 — Positioning consolidated over a window of crawls

**Date**: 2026-09-17
**Operator request**: "after every three (or four, or any custom input) crawls of
a project we will final-position the data, project-wise. Don't mix the run
interval with positioning: the engine may run every 2 days, positioning every
3 runs, so consolidated data exists at every 6th day, and every run's data is
stored separately on its own date." (chat)

## Scope

Per-project consolidation window in crawls, independent of the run interval;
crawl records; automatic consolidation after every N-th full crawl and on
demand with a custom window; dated, stored position sets per prompt × platform;
Results tab with consolidated (default) and point-in-time views plus history.
Acceptance: the runner records crawls and consolidates at N; positions match a
hand-computed aggregation; API and UI expose both views; gate green.

## Research findings

- A "crawl" must be the project job, not the pipeline run: one job can produce
  several pipeline runs (batches by platform set, generation). `project_runs`
  keeps the job with its pipeline run ids, and consolidation selects raw rows
  by those ids, so no raw data is duplicated or rewritten.
- Runs restricted to selected prompts cover part of the project; counting them
  would make a window of "3 crawls" mean less than three full samples of every
  prompt. They are recorded (`full = false`) but do not count.
- Snapshot totals include reused snapshots (which have no answer samples of
  their own), so totals come from snapshots and distributions from answer
  samples, with a snapshot fallback for the distribution when no samples
  exist in the window.
- Dynamic `IN (...)` SQL trips the injection linter; passing the run ids as a
  JSON array to SQLite's `json_each` needs no string building at all.

## What was built

- `control_plane/schemas.py`: `Project.consolidation_runs` (default 3, 1–50,
  editable), `ProjectRunRecord`, `ConsolidateRequest`, `ConsolidatedPosition`,
  `Consolidation`, `PositionsView`; `RunOutcome.project_run_id` and
  `consolidation_id`.
- `control_plane/positioning.py` (new): `PositionStore` (tables
  `project_runs`, `consolidations`, `positions`), `aggregate_position`
  (samples, failed, cited/mentioned counts and rates, ≥ 50 % verdicts, best
  and cited-weighted mean rank, rank distribution, domain share, competitors'
  best ranks, organic prompt/keyword best and mean, run span),
  `consolidate`, `positions`, `consolidations`, `runs_since_last_consolidation`.
- `control_plane/runner.py`: records each crawl; auto-consolidates when full
  crawls since the last consolidation reach the project window (progress
  phase `consolidating`); `consolidate()`, `positions()`, `crawls()`.
- `control_plane/app.py`: `POST /api/projects/{id}/consolidate`,
  `GET /api/projects/{id}/positions[?consolidation_id]`,
  `GET /api/projects/{id}/crawls`.
- `static/index.html`: settings card "Positioning window"; Results tab with
  Consolidated / Point-in-time toggle, status line (latest consolidation,
  crawls since, next automatic), history selector, "Consolidate now" with a
  custom window, consolidated table (cited/mentioned rates over all samples,
  best/mean rank, samples and runs, rank distribution, domain share,
  competitors, organic best/mean).
- Tests: aggregation (2), store + window semantics (1), runner auto/manual (1),
  API routes (1). Docs: ADR 0013, README, ARCHITECTURE, KNOWN_GAPS, blueprint §7f.

## Bugs found and fixed

- Test fixtures used crawl ids shorter than the contract's minimum; and my
  expected rank distribution was wrong (recounted: 5 × #1, 1 uncited).
- `IN (...)` string building flagged by lint; replaced with `json_each`.

## Corrections

- None to earlier entries.

## Explicitly not done

- Crawls before this cycle are not in `project_runs`; the first consolidation
  of an existing project happens after N new crawls (or on demand after one).
  The earlier snapshots are from the faulty connectors (cycle 0008) anyway.
- Prompt Atlas still shows per-run snapshots only.
- Windows count crawls, not calendar days; a crawl with nothing due is not a
  crawl.

## Local verification

Server restarted. Existing project reports `consolidation_runs = 3`; positions
view empty with `runs_since_last = 0`; `POST …/consolidate` returns 400 "No
completed crawl to consolidate yet" until a crawl is recorded; the page carries
the consolidated view. Runner test: window 2 → first full run no
consolidation, a selected-prompt run recorded but not counted, second full run
consolidates automatically (8 positions, 2 runs, 2 samples each).

## Step 5 audit answers (delta from cycle 0009)

1–2. No vendors involved; consolidation is a read of stored data. 3. No spend.
4. Consolidations are append-only; re-running produces a new dated set.
5. Manual consolidation with no crawl is a 400, not a crash. 6. Positions hold
prompt ids and domains only. 7. All bodies are `StrictModel`s; window 1–50.
8. Unchanged.

## Gate output (verbatim, abridged to changed modules)

```
=== Format ===  PASSED
=== Lint ===    All checks passed!  PASSED
=== Type check === Success: no issues found in 56 source files  PASSED
=== Tests ===
src\modules\control_plane\app.py                    139      3      2      0    98%   103, 114-115
src\modules\control_plane\positioning.py            157      8     42      3    94%   95-96, 215-217, 402, 415, 427
src\modules\control_plane\runner.py                 182     12     38      1    91%   331-343
TOTAL                                              4534     62    954     26    98%
Required test coverage of 85.0% reached. Total coverage: 98.25%
667 passed, 2 warnings in 113.57s (0:01:53)
PASSED: Tests
ALL GATES PASSED.
```

Drift audit: `scripts/drift_check.py` → "No documentation drift detected."
