# ADR 0007 — Interval scheduling and analyst-supplied prompts

**Status**: Accepted (2026-09-16), operator decision: "an input field where we
can feed the prompts and customise the engine running with intervals — daily,
weekly, monthly or any interval".

## Context

The tracker generated its own prompts from Semrush and ran once per
invocation. Analysts need to track prompts they wrote themselves, and the
cadence must be configurable without an external cron expression per client.

## Decisions

1. **Custom prompts are first-class input.** `PipelineInput.custom_prompts`
   (CLI `--prompt`, `--prompts-file`; job file `custom_prompts`). Each may pin a
   keyword and subtopic. They are always tracked; the intent gate runs but is
   advisory (its verdict is recorded in the reason). With custom prompts
   supplied, Semrush harvesting is off unless `--also-generate` /
   `generate_prompts: true`.
2. **Jobs file + stateless scheduler.** A JSON file lists `TrackingJob`s (client,
   prompts, engines, samples, interval). `Scheduler.run_due()` reads each job's
   `last_run_at` from the `jobs` table, runs those whose interval elapsed, and
   records the outcome. It is idempotent and safe to invoke hourly from Windows
   Task Scheduler or cron. `daemon` polls in-process for hosts without a
   scheduler; `status` reports last/next; `install-task` prints (never runs) the
   `schtasks` registration.
3. **Interval grammar.** `hourly`, `daily`, `weekly`, `monthly` (30 days) or
   `<n>min|h|d|w|mo`. Validated at job-load time.
4. **Approval for scheduled runs** is by budget (`UNATTENDED_SPEND_CAP_USD`),
   per ADR 0002. `--approve-spend` on `run-due`/`daemon` is a human's approval
   of that invocation.

## Alternatives considered

- **Cron expressions**: precise calendar anchoring, but a second grammar to
  learn and validate. Elapsed-interval scheduling plus an external hourly
  trigger covers the stated need (daily / weekly / monthly / any interval).
- **Storing jobs in the database**: rejected; a reviewable file in version
  control is the right home for tracking configuration.

## Consequences

- Cadence is elapsed-time based, not calendar-anchored (documented gap).
- One prompt tracked by two jobs shares its `prompt_id` and history.
