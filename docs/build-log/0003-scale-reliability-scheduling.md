# Cycle 0003 — Scale, reliability, custom prompts, interval scheduling

**Date**: 2026-09-16
**Operator approval**: "lets implement every problem and try to minimize the
maximum api calls hence make a very reliable system and an input field where we
can feed the prompts and also customize the engine running with intervals"
(chat, 2026-09-16).

## Scope

The three vulnerabilities named in the operator's comparison (API cost at
scale, sequential execution speed, vendor model shifts) plus two inputs:
analyst-supplied prompts and configurable run intervals. Acceptance: fewer
paid calls per run at equal or better reliability, parallel audit, model-shift
detection, prompts from CLI/file/job file, `daily|weekly|monthly|<n>x`
scheduling, gate green.

## Research findings

- Connectors, token buckets, `CostLedger` and the new breakers are thread-safe,
  so a bounded thread pool gives the speed-up without an asyncio rewrite.
- Budget/cap refusals mid-sampling originally discarded already-paid answers;
  caught by the first draft of the audit tests and redesigned (partial outcome
  with `stop_reason`).
- Argparse subcommands break `build_parser().parse_args([...flags])` callers;
  `main()` prepends `run` when the first token is a flag so existing invocations
  keep working.

## What was built

- `core/circuit_breaker.py` (+ registry) wired into `BaseAPIClient.call()`;
  `available` / `breaker` on every connector. 4xx never trips it.
- Settings: `CIRCUIT_*`, `ADAPTIVE_SAMPLING`, `MIN_SAMPLES`, `PIPELINE_MAX_WORKERS`,
  `MAX_ENGINE_CALLS_PER_RUN`, `REUSE_WITHIN_HOURS`.
- `prompt_tracking/audit.py`: `RunPlan` (locked reservation against cap +
  ledger), `audit_engine` (adaptive early-stop, breaker-aware, partial outcome),
  `consensus`.
- `prompt_tracking/assembly.py`: `build_record`, `answer_samples`, `model_shifts`
  (split from the pipeline to keep it under the 400-line target).
- `pipeline.py`: thread-pool audit, snapshot reuse, per-run cap, custom prompts,
  optional generation, raw samples, model-shift warnings; new summary counters
  `reused_snapshots`, `calls_refused_by_circuit`, `custom_prompts`, `model_shifts`.
- `inputs.py` (prompt file parsing), `PromptGenerator.from_custom`,
  `CustomPrompt`, `AnswerSample` schemas; `CitationSnapshot.response_ids/reused`.
- `time_series_db.py`: `answer_samples` and `jobs` tables, `record_samples`,
  `last_model`, `job_state`, `record_job_run`, idempotent `_migrate()` for the
  new `snapshots.response_ids` column.
- `scheduler.py`: `TrackingJob`, `parse_interval`, `load_jobs`, `Scheduler`
  (`status`, `run_due`, `daemon`).
- CLI: `run` (+ `--prompt`, `--prompts-file`, `--also-generate`, `--no-adaptive`,
  `--max-calls`, `--reuse-hours`, `--workers`) and `schedule run-due|daemon|
  status|install-task`. Examples in `docs/examples/`.
- Docs: README, ARCHITECTURE, KNOWN_GAPS, ADR 0006–0007, blueprint §7a.

## Bugs found and fixed

- Audit discarded paid samples when the budget/cap refused the next reservation
  (design flaw in this cycle's first draft). Fixed: partial outcome + reason.
- `EngineLike` was a concrete class, so `BaseAPIClient` connectors failed the
  union type; made it a `Protocol`, availability read via `getattr`.
- A variable-name collision (`snapshot` reused across the keyword-rank loop and
  the persistence loop) caught by mypy; renamed.
- Test isolation: process-wide breakers now reset around every test
  (`tests/conftest.py`); the shared `settings` fixture pins one worker because
  the duck-typed stubs count calls without locks. Concurrency has its own test
  with a locked stub.

## Corrections

- Cycle 0001 build log listed "no circuit breaker" and "runs sequential, no
  scheduler" as gaps; both closed (moved to KNOWN_GAPS "Closed").
- `describe_invocation()` now says "up to N samples" (adaptive), and counts
  custom prompts; the cycle 0001 text said "N samples".
- The CLI's text output line for engine calls changed shape (adds reused /
  refused / keyword-call counters).

## Explicitly not done

- No asyncio; throughput is bounded by `PIPELINE_MAX_WORKERS` and vendor rate
  buckets. Tracked in KNOWN_GAPS.
- Intervals are elapsed-time (`daily` = 24 h since last run), not calendar-
  anchored; `monthly` = 30 days. Anchor with Task Scheduler / cron if needed.
- Adaptive early-stop tests agreement on *cited or not*, not on rank position.
- `install-task` prints the `schtasks` command; it never registers the task.
- The other session's `docs/prompt-atlas.html` / `scripts/export_dashboard.py`
  were not touched and do not yet read `answer_samples` or `jobs`.
- Still no live API call from this repository.

## Step 5 audit answers (delta from cycle 0002)

1. Hosts unchanged. Volume: **down** by adaptive sampling (≈ −33% on stable
   prompts), reuse (re-runs free), cap; up to `PIPELINE_MAX_WORKERS` calls
   in flight at once, still under each vendor's token bucket.
2. Rate keys unchanged; breakers share the same keys.
3. Worst case unchanged in dollars (`MAX_SESSION_SPEND_USD` is the stop) and
   now also bounded in calls (`MAX_ENGINE_CALLS_PER_RUN`). Every reservation
   is taken under one lock before the call.
4. Reads only; `prompt_id` upsert; job state upsert by name; reuse is
   idempotent.
5. Circuit breaker: **present** (closed → open → half-open, per vendor).
6–8. Unchanged. Raw answer excerpts (≤300 chars) are stored; they contain no
   PII by construction (vendor answers to product prompts).

## Gate output (verbatim)

```
=== Format ===
118 files already formatted
PASSED: Format
=== Lint ===
All checks passed!
PASSED: Lint
=== Type check ===
Success: no issues found in 42 source files
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
src\integrations\url_resolver.py                     97      2     18      0    98%   105-106
src\modules\prompt_tracking\pipeline.py             232      3     68      2    98%   232, 380-381
src\modules\prompt_tracking\prompt_generator.py      73      1     24      1    98%   115
src\modules\prompt_tracking\scheduler.py            132      0     20      1    99%   266->275
---------------------------------------------------------------------------------------------
TOTAL                                              2828     22    632     12    99%
31 files skipped due to complete coverage.
Required test coverage of 85.0% reached. Total coverage: 99.02%
532 passed in 113.79s (0:01:53)
PASSED: Tests
ALL GATES PASSED.
```

Drift audit: `scripts/drift_check.py` → "No documentation drift detected."
