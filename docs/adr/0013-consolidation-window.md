# ADR 0013 — Project positioning consolidated over a window of crawls

**Status**: Accepted (2026-09-17). Operator request: "after every three (or
four, or any custom input given by the analyst) crawls of a project we will
final-position the data, project-wise. Do not mix the run interval with the
positioning: the engine may run every 2 days, and positioning may be every 3
runs, so consolidated data exists every 6th day, while each run's data is
stored separately on its own date."

## Context

One crawl of a prompt on a platform, even with two or three samples, is a
noisy read: citations and brand mentions move day to day. The Results tab
showed the latest snapshot only, so positions swung with every run.

## Decisions

1. **Two independent settings per project.** `interval` (when the engines
   crawl, ADR 0009) and `consolidation_runs` (how many full crawls make one
   position, default 3, analyst-editable 1–50).
2. **A crawl is a project run that produced at least one pipeline run.**
   Recorded in `project_runs` with the pipeline run ids it produced. Runs
   restricted to selected prompts are recorded but flagged `full = false` and
   do not count towards the window, since they cover only part of the project.
3. **Raw data is never touched.** Every run's snapshots, answer samples and
   organic ranks stay on their own date. Consolidation reads them by run id.
4. **A consolidation is a dated, stored position set** (`consolidations` +
   `positions`): per prompt × platform over the window's runs: samples,
   failed, cited samples and rate, mentioned samples and rate, cited /
   mentioned verdicts at ≥ 50 %, best and cited-weighted mean rank, the
   distribution of ranks across samples, share of samples citing each domain,
   competitors' best ranks, and organic best/mean for prompt and keyword.
   Totals come from snapshots (so reused snapshots count); distributions from
   answer samples when present.
5. **Automatic after every N-th full crawl, and on demand.** After a full
   crawl the runner counts full crawls since the last consolidation and, when
   the count reaches N, consolidates the last N and records the trigger as
   `auto`. The analyst can consolidate at any time with a custom window
   (`manual`). Both are kept; the history is browsable.
6. **Results tab shows both.** "Consolidated positioning (last N crawls)" is
   the default view; "Point-in-time (latest crawl)" remains one click away.

## Alternatives considered

- **Rolling average on every run**: hides the analyst's chosen cadence and
  produces a new "position" every crawl, which is what the request rejects.
- **Calendar-based windows (every 6 days)**: coupled to the run interval;
  counting crawls keeps the two settings independent and survives a missed
  run.

## Consequences

- `RunOutcome` gains `project_run_id` and `consolidation_id`.
- Prompt Atlas still reads per-run snapshots; the consolidated sets are in the
  control plane only for now.
