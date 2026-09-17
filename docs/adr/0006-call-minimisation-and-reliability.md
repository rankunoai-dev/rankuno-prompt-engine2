# ADR 0006 — Minimising engine calls without losing reliability

**Status**: Accepted (2026-09-16), operator decision: "implement every problem and
try to minimise the maximum API calls, make a very reliable system".

## Context

At 100 prompts × 4 engines × 3 samples a daily run is 1,200 metered calls.
Three costs compound: money per call, wall-clock time when sequential, and
wasted calls when a vendor is down or a run crashes half-way and restarts.

## Decisions

1. **Adaptive sampling** (`ADAPTIVE_SAMPLING`, `MIN_SAMPLES`, default on / 2).
   Sample at least `MIN_SAMPLES`; stop early once all answers agree on whether
   the client is cited; otherwise continue to `SAMPLES_PER_ENGINE`. Stable
   prompts cost 2 calls instead of 3; unstable ones still get the full count,
   which is exactly where extra samples carry information.
2. **Run plan with call cap** (`MAX_ENGINE_CALLS_PER_RUN`). Every call is
   reserved under one lock against both the cap and the `CostLedger` *before*
   it is made. A refusal after at least one paid sample ends sampling and
   returns a partial outcome — paid answers are never discarded.
3. **Snapshot reuse** (`REUSE_WITHIN_HOURS`). A prompt/engine snapshot younger
   than the window is copied (flagged `reused`) and not re-recorded. A crashed
   or duplicated run re-runs for free.
4. **Circuit breaker per vendor** (core). Consecutive transient failures open
   the breaker; calls fail fast at no cost until a cooldown, then one trial.
   4xx responses never trip it. The audit step skips an engine whose breaker is
   open and counts the refused samples.
5. **Bounded thread pool** (`PIPELINE_MAX_WORKERS`). Connectors are synchronous
   and thread-safe (httpx clients, token buckets, ledger, breakers), so
   parallelism needs no async rewrite. Persistence stays on the main thread.
6. **Model-shift audit trail.** Every raw answer is stored (`answer_samples`)
   with its model string and response id; a run reports engines whose model
   changed since the previous run.

## Alternatives considered

- **asyncio + httpx.AsyncClient**: more throughput ceiling, but a rewrite of
  every connector and the governed base classes for a workload that is
  rate-limit-bound anyway. Deferred.
- **Sequential-probability-ratio sampling**: statistically stronger early stop;
  rejected for now as harder to explain to analysts than "two agreeing samples".

## Consequences

- Default per-run cost drops by roughly a third on stable prompts.
- `engine_calls` in the summary is the number of *paid* calls; refused and
  reused counts are reported separately.
- Adaptive sampling changes `CitationSnapshot.samples` from a constant to a
  per-prompt value; rate fields remain comparable across runs.
