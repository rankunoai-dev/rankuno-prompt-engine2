# ADR 0010 — Background run queue with live progress

**Status**: Accepted (2026-09-17), operator request: "when I am running the
engine there is no progress bar and nothing that notifies me the job/run is
complete; I need the progress bar."

## Context

ADR 0009 executed a run inside the HTTP request. A project of a few dozen
prompts across four platforms takes minutes; the page showed one static toast
and then either a result or a browser timeout. Nothing told the operator how far
along the run was, whether it was still alive, or when it finished.

## Decisions

1. **The pipeline reports progress; it does not know about the UI.**
   `PromptTrackerPipeline` accepts an optional `progress` callback and emits
   `PipelineProgress(phase, done, total, calls, message)` events: one when
   harvesting starts, one when the audit plan is known (reused snapshots count
   as done immediately), one per finished prompt × platform check, then
   keyword-rank, report and done. A raising callback is logged and ignored: a
   UI going away must never abort a paid run.
2. **Checks, not calls, are the unit of progress.** A check is one prompt on
   one platform. Its size is known before the run starts, whereas the number of
   engine calls depends on adaptive early-stop, so a call-based bar would jump.
   Paid calls are still shown as a live counter.
3. **The runner folds batches into one run.** `ProjectRunner.run(...,
   progress=sink)` sums the planned checks of every batch up front, offsets each
   batch's events, and grows the total when a Semrush-generated batch (unknown
   size until harvested) reports its plan. The sink receives `RunProgress`
   (checks, batches, calls, percent, phase, message).
4. **Runs are jobs on one worker thread.** `JobManager.submit()` records a
   `RunJob` and returns immediately; `POST /api/projects/{id}/run` answers
   `202` with it. One daemon worker executes jobs in order so two runs never
   compete for the ledger, the SQLite file or vendor rate buckets. An identical
   request already queued or running is returned instead of queued twice, so a
   double click or an overlapping poller cycle cannot double spend. The poller
   now queues jobs too (`JobManager.run_due_all()`), so every run — manual or
   scheduled — goes through the same worker and is visible in the UI.
5. **Polling, not push.** The page polls `GET /api/jobs/{id}` every second
   while a job is active and `GET /api/jobs?active=true` every five seconds for
   the header badge and sidebar dots. Server-sent events or WebSockets would
   need a second connection model for a single-user loopback app; polling one
   small JSON document is simpler and survives a page refresh (the page finds
   the active job on load and resumes watching it).
6. **Completion is announced three ways.** A sticky toast with the outcome, a
   browser `Notification` when the tab is hidden and permission was granted
   (asked on the first Run click), and a flashing tab title until the page is
   focused. The results tab is refreshed automatically.
7. **Job state is in memory.** The durable record of a run is the `runs` table
   the pipeline already writes; the queue is process state (last 200 jobs). A
   restart forgets queued jobs and their progress, never results.

## Alternatives considered

- **Server-sent events**: real-time, but adds connection lifecycle handling to
  both sides for a gain of under a second of latency on a minutes-long run.
- **Per-call progress inside `audit_engine`**: finer, but the total is unknown
  with adaptive sampling, so the bar would be either pessimistic or jumpy.
- **Persisting jobs in SQLite**: would survive restarts, but a queued job from
  a dead process should not silently run on the next start with spend attached.
  Left as a known gap.
- **Cancellation**: not requested; would require cooperative checks between
  batches. Left as a known gap.

## Consequences

- `PipelineRunner` (injected in tests) now takes `(payload, progress)`.
- The CLI (`python -m src.modules.prompt_tracking`) is unchanged and passes no
  callback; it still prints the summary at the end.
- `RunOutcome` is unchanged and is embedded in the finished `RunJob`.
