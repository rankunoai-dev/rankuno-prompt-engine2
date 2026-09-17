# Cycle 0006 — Background runs with progress bar and completion notifications

**Date**: 2026-09-17
**Operator request**: "when I am running the engine there is no progress bar and
toggler and anything that notifies the job/run is complete, hence I need the
progress bar" (chat).

## Scope

Make a UI run non-blocking and observable: queue it, show live progress
(checks done, batches, paid calls, phase, elapsed), and announce completion.
Acceptance: `POST …/run` returns at once; the page shows a moving bar and a
completion toast/notification; refresh re-attaches to the running job; gate
green.

## Research findings

- The pipeline already knows its work plan before spending: tasks = prompts ×
  platforms minus reusable snapshots. That count is stable under adaptive
  sampling, whereas engine-call counts are not, so checks are the progress unit
  and calls are a secondary live counter.
- The Semrush-generated batch's size is unknown until harvested; the runner
  grows the total when that batch reports its plan and caps the bar at 99%
  until the run is done so it never reads 100% early (seen in the live smoke
  test before the cap was added).
- The poller and the UI previously ran through different paths; routing both
  through one worker removes any chance of two concurrent runs sharing the
  ledger and the SQLite file, and makes scheduled runs visible in the UI.

## What was built

- `prompt_tracking/schemas.py`: `PipelinePhase`, `PipelineProgress`.
- `prompt_tracking/pipeline.py`: optional `progress` callback; events at
  harvest start, audit plan, each finished prompt × platform check, keyword
  ranks, report, done. Callback exceptions are logged, never raised.
- `control_plane/schemas.py`: `RunProgress`, `JobState`, `RunJob`.
- `control_plane/runner.py`: `ProjectRunner.run(..., progress=sink)`;
  `_ProgressTracker` folds per-batch events into run totals; `PipelineRunner`
  now takes `(payload, progress)`; generation cadence check extracted to
  `_generation_due` so batch totals are known before the run starts.
- `control_plane/jobs.py` (new): `JobManager` — submit (dedupes identical
  active requests), one daemon worker, `run_pending()` for tests, `get`,
  `list_jobs`, `active`, `run_due_all` for the poller, in-memory retention.
- `control_plane/app.py`: `POST …/run` → 202 `RunJob`; `GET /api/jobs`,
  `GET /api/jobs/{id}`, `GET /api/projects/{id}/jobs`; health reports
  `active_jobs`.
- `control_plane/__main__.py`: builds the `JobManager`; poller queues through it.
- `static/index.html`: progress card (bar, phase, checks/batches/calls/elapsed,
  message, outcome and warnings), 1 s job polling, sticky completion toast with
  dismiss, browser `Notification` (permission asked on first Run click),
  flashing tab title, header badge and sidebar dots from a 5 s active-jobs
  poll, Run buttons disabled while a job is active for the project, re-attach
  on page load, jobs table in the Runs tab.
- Docs: README, ARCHITECTURE, KNOWN_GAPS (closed the synchronous-run gap; added
  in-memory jobs / no cancel / polled progress), ADR 0010, blueprint §7d.

## Bugs found and fixed

- `JobManager.list` shadowed the builtin inside the class body, so a later
  `list[RunJob]` annotation referred to the method (mypy). Renamed `list_jobs`.
- Retention only ran on submit, so finished jobs piled up until the next
  submission; now trimmed on completion too (caught by the trim test).
- Progress read 100% while the generation batch was still harvesting (live
  smoke test). Percent is now capped at 99 until the `done` phase.
- New progress test module imported fixtures from a sibling test module, which
  ruff flags as redefinition; fixtures are defined locally instead.

## Corrections

- Cycle 0005 stated "Runs execute synchronously in the request; no queue or
  progress stream" — closed this cycle.
- Cycle 0005 stated no vendor keys were present. `.env` now contains all five
  vendor keys (added outside this session); the smoke test below therefore
  made real, paid engine calls under `--approve-spend`.

## Explicitly not done

- No cancellation of a queued or running job.
- Job state is not persisted; a restart forgets the queue (the `runs` table is
  the durable record).
- Progress is polled, not pushed; granularity is per finished check, not per
  engine call.
- Still no authentication; loopback only.

## Local verification

Server restarted with `--approve-spend --port 8787`. `POST /api/projects/
e42161487b61/run {"force":true}` returned `202` at once. Polling
`GET /api/jobs/{id}` showed `running · audit 134/140 · 95.7% · 17 calls`, then
`finished · done 140/140 · 100% · 24 calls`, outcome: 2 batches, 15 prompts,
statuses `success, success`, 0 warnings, run ids `ebacc13302634d8b`,
`2e87b4d46a5d4edd`. `GET /api/jobs?active=true` listed the job while running and
`/api/health` reported `active_jobs` accordingly. After the percent cap fix the
server was restarted again: health ok, UI 200, jobs list empty.

## Step 5 audit answers (delta from cycle 0005)

1–2. No new vendors or rate keys.
3. Spend path unchanged (approval + ledger + call cap); dedupe of identical
   active requests prevents a double click or an overlapping poller cycle from
   queuing the same run twice.
4. Job submission is idempotent for identical active requests; job ids are
   random 16-hex.
5. Worker exceptions mark the job failed and the worker keeps serving.
6. Job payloads hold project ids, request flags and counters only.
7. `RunJob`/`RunProgress` are `StrictModel`s; query params are clamped.
8. Unchanged; loopback default.

## Gate output (verbatim)

```
=== Format ===
146 files already formatted
PASSED: Format
=== Lint ===
All checks passed!
PASSED: Lint
=== Type check ===
Success: no issues found in 51 source files
PASSED: Type check
=== Tests ===
Name                                              Stmts   Miss Branch BrPart  Cover   Missing
---------------------------------------------------------------------------------------------
src\core\base_tool.py                                82      6     16      2    92%   68, 108, 193-194, 226-228
src\core\circuit_breaker.py                          86      0     20      1    99%   115->120
src\core\guardrails.py                               86      2     20      2    96%   197, 213
src\core\logger.py                                   59      1     10      1    97%   107
src\core\rate_limiter.py                            113      4     26      2    96%   77-78, 133->138, 237-238
src\core\retry.py                                    20      2      0      0    90%   47-48
src\core\url_safety.py                               71      1     18      0    99%   64
src\integrations\perplexity.py                       75      7     32      2    84%   67->69, 93-99
src\integrations\url_resolver.py                     97      2     18      0    98%   105-106
src\modules\control_plane\__main__.py                70      2     10      1    96%   72-73, 120->128
src\modules\control_plane\app.py                    109      3      0      0    97%   78, 88, 93
src\modules\control_plane\planner.py                 57      1     26      1    98%   73
src\modules\control_plane\store.py                  117      3     16      0    98%   81-83
src\modules\prompt_tracking\__main__.py             157      4     46      2    97%   157, 159, 298-299
src\modules\prompt_tracking\pipeline.py             255      3     70      2    98%   268, 429-430
src\modules\prompt_tracking\prompt_generator.py      73      1     24      1    98%   119
src\modules\prompt_tracking\scheduler.py            132      0     20      1    99%   266->275
---------------------------------------------------------------------------------------------
TOTAL                                              3763     42    782     18    99%
34 files skipped due to complete coverage.
Required test coverage of 85.0% reached. Total coverage: 98.50%
611 passed, 2 warnings in 124.55s (0:02:04)
PASSED: Tests
ALL GATES PASSED.
```

Drift audit: `scripts/drift_check.py` → "No documentation drift detected."
