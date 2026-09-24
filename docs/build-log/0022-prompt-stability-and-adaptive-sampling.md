# Cycle 0022 — Prompt stability and the adaptive sampling policy

**Date**: 2026-09-24
**Operator request**: "Cycle 5: Prompt Variance & Adaptive Sampling Control
(calculating prompt stability over time to automatically optimize crawl costs).
lets start". At the step-3 stop the operator chose `fixed` as the default for
existing projects and asked for both `save` and `reallocate` in this cycle.

Decision record: ADR 0025. Vendor spend for the build and the feature: $0; it
reads stored history only. The plan was stress-tested by a review agent before
implementation; its corrections are the "Corrections" section below.

## Scope

A per-pair stability verdict (`prompt_tracking/stability.py`), a per-project
`sampling_policy` applied by one `plan()` (`control_plane/sampling.py`) that the
run, the Results tab and a new dry-run route all share, per-pair overrides in
the planner, a `sampling` summary on every crawl record and run outcome,
`PromptEngineDetail.stability`, four settings, docs and
`docs/UI_SAMPLING_BRIEF.md`. No `ui/` edits.

## Research findings

- The within-crawl early stop (`audit.py`) already saves about a third of
  calls on settled pairs. Nothing looked across crawls; the planner's only rule
  was "newest snapshot older than the interval".
- `EngineHealth.volatility` was `mean(min(r, 1−r)·2)`: the coin-flip distance
  the new score needed. It is now `core/stats.coin_flip` and both call it.
- Consolidation pools by run id (`positioning.compute`), so a pair absent from
  every crawl of a window disappears from the position set and is reported as
  a lost platform. The stretch multiplier is therefore capped by the project's
  `consolidation_runs`.
- `PipelineInput.samples_per_engine` is one number per pipeline call, so a
  per-platform boost needs the planner to split a prompt into two batches.
- Prompt history is shared by `(lob, text)` across projects; stability reads
  it as-is (documented gap).
- A run restricted to platforms but not prompts was recorded as a full crawl
  and advanced the consolidation window. Fixed here.

## What was built

- `prompt_tracking/stability.py`: `classify()` admits the newest
  `STABILITY_WINDOW_CRAWLS` crawls on the newest model with ≥ 2 successful
  samples, pools them, reads the Wilson band: stable when it excludes 50% with
  at most one verdict flip, volatile when it straddles 50% or flips twice or
  more, failing when nothing succeeded, unknown below `STABILITY_MIN_CRAWLS`
  or when the newest usable crawl is older than the window. Carries the
  sentence, score, flips, streak.
- `control_plane/sampling.py`: `plan()` with policies `fixed | save |
  reallocate`; stretch tiers 2 then 3 (streak ≥ 2 × window), capped by
  `STRETCH_MAX` and `consolidation_runs`; boosts most-volatile first in calls
  (a stable pair is expected to early-stop at `MIN_SAMPLES`), within savings
  plus the carry read from the previous crawl records; exemptions for starred,
  overridden, forced, selected, failing, maxed-out and just-unstretched pairs;
  `SamplingSummary` with expected calls and cost both ways.
- `planner.py`: `overrides` on `due_items()`, `DueItem.boosted`, `batches()`
  splitting per sample count. `positioning.py`: `project_runs.sampling`
  column with an idempotent migration, `recent_sampling()`. `runner.py`: one
  plan per run, `results()` and `_result_for()` on the same plan, the idle
  reason with the stretched count and next due date, `sampling_view()`,
  stability on the prompt detail, the `full` fix. `sampling_routes.py`:
  `GET /api/projects/{id}/sampling[?policy=]`.
- Schemas: `StabilityReport`, `StabilityState` (prompt_tracking);
  `PairOverride`, `SamplingDecision`, `SamplingSummary`, `SamplingPlan`,
  `SamplingView`, `ProjectBase.sampling_policy`, `RunOutcome.sampling`,
  `ProjectRunRecord.sampling`, `PromptEngineDetail.stability`.
- `costing.engine_call_cost()` replaces the pipeline's private cost map.
- Settings `STABILITY_WINDOW_CRAWLS=4`, `STABILITY_MIN_CRAWLS=3`,
  `STRETCH_MAX=3`, `VOLATILE_BOOST=2`.

## Bugs found and fixed

- First draft thresholded per-crawl rates (spread ≤ 0.34, coin flip); the
  review showed early stop pushes per-crawl rates to 0 or 1, so a 70% pair
  would read volatile forever and `client_citation_rate` is not rounded at
  write time, making a 0.34 threshold on floats knife-edge. Replaced by the
  pooled band.
- The "band excludes 50% but is too wide" branch was unreachable (a band that
  excludes 50% is at most 50 points wide). Removed.
- `batches()` reused the inner-loop variable for the group key (mypy caught
  the list/tuple clash).
- The runner test first ran a selected-prompt crawl, which wrote a fresh
  Perplexity snapshot and made the later "volatile pair runs" assertion
  impossible; the test was wrong, not the code. Reordered.
- The 70% fixture had two verdict flips, so it was volatile by flips rather
  than by the band the test meant to exercise; fixture changed.
- `full` was true for a platform-restricted run (pre-existing).

## Corrections

- The plan named per-crawl spread as the statistic and boosted per prompt;
  both were replaced (pooled band; per-platform boosts with `batches()`
  splitting).
- The plan's savings arithmetic counted samples; it now counts expected calls,
  and the "neutral per crawl" rule became "neutral over the stretch cycle"
  via the carry.
- Settings were going to be `ADAPTIVE_*`, which collides with the existing
  `ADAPTIVE_SAMPLING` (the early stop). Renamed.
- Commit `03a85da` ("wip(reporting)…", author unknown, not made by either
  session's own commit step) swept in half-finished versions of this cycle's
  `stability.py`, `stats.py`, `costing.py`, `pipeline.py`,
  `prompt_tracking/schemas.py`, `config.py` and `.env.example` alongside the
  other session's reporting work. This cycle's commit completes them; the
  history is not rewritten.

## Explicitly not done

- No UI: the policy control, the Runs-tab sampling section, the drawer line
  and the prompts column are in `docs/UI_SAMPLING_BRIEF.md`.
- Stability does not restrict itself to the project's own run ids (shared-LOB
  history leaks in); rank movement is not part of the verdict; band-aware
  change detection (ADR 0020 §4) is still open; no zero-batch crawl record is
  written when everything was stretched.
- `STABILITY_*` are global settings; there is no per-project window or boost.

## Gate output

```
=== Format ===       PASSED
=== Lint ===         All checks passed!   PASSED
=== Type check ===   Success: no issues found in 97 source files   PASSED
=== Tests ===
src\modules\control_plane\planner.py                 71      1     32      1    98%   97
src\modules\control_plane\positioning.py            184      9     50      4    94%
src\modules\control_plane\sampling.py               137      1     34      1    99%   117
TOTAL                                              9496    245   2148    145    96%
Required test coverage of 85.0% reached. Total coverage: 96.43%
986 passed, 2 warnings in 380.78s (0:06:20)
PASSED: Tests
ALL GATES PASSED.

--- Drift Audit Results ---
No documentation drift detected.
```

The tree at gate time also held the other session's committed cycle 0021
(reports and alerting), so the totals include its tests.
