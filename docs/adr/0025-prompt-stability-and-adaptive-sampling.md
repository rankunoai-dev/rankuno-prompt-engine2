# ADR 0025 — Prompt stability from the pooled band, and a sampling policy that spends where the band is wide

**Date**: 2026-09-24
**Status**: Accepted (cycle 0022; the operator asked for "Prompt Variance & Adaptive Sampling Control (calculating prompt stability over time to automatically optimize crawl costs)" and chose, when asked, `fixed` as the default for existing projects and both `save` and `reallocate` in this cycle)
**Builds on**: ADR 0010 / 0013 (consolidation over a window of crawls), ADR 0020 (Wilson band on every rate), ADR 0006 (call minimisation: the within-crawl early stop)

## Context

Every prompt × platform pair is sampled at the same cadence with the same
sample count, whatever its history shows. A pair that has read "0 of 9 cited"
for a month costs the same as one that flips every crawl. The within-crawl
early stop (`audit.py`: stop at `MIN_SAMPLES` once the answers agree) already
saves about a third on settled pairs, but nothing looks across crawls.

The competitive review was blunt: "adaptive sampling is a cost feature, not a
moat. Buyers are asking for more samples with confidence intervals, not
fewer." So the goal is not fewer samples. It is the same budget spent where the
measurement is still noisy, with the analyst able to see exactly what the
policy did and why.

A design review before implementation found three things that shaped the
result:

1. **Per-crawl rates cannot be thresholded.** Early stop pushes each crawl's
   rate to 0.0 or 1.0; a pair truly cited 70% of the time reads 1.0 in about
   half its crawls and 0.33 or 0.67 in the rest. Any "spread across crawls"
   statistic calls it volatile forever, and calls a 0% or 100% pair perfectly
   stable, which is just `coin_flip` of the true rate in disguise.
2. **Consolidation pools by run id.** A pair that skips a whole consolidation
   window vanishes from the position set and is reported as a lost platform.
3. **Budget arithmetic must be in calls, not samples.** A skipped stable pair
   saves the two calls it would have early-stopped at, not three; a boost per
   prompt across four platforms costs four times what a boost per pair costs.

## Decision

### A. Stability is the window's pooled Wilson band

`prompt_tracking/stability.classify()` is a pure function over the pair's
snapshots, newest first. It admits the newest `STABILITY_WINDOW_CRAWLS` (4)
crawls that ran on the newest model and had at least two successful samples,
pools their cited and successful samples, and reads the 95% band the product
already shows on every rate:

| State | Rule |
| :-- | :-- |
| `stable` | the band excludes 50% and the stored verdict flipped at most once in the window |
| `volatile` | the band straddles 50%, or the verdict flipped twice or more |
| `failing` | at least `STABILITY_MIN_CRAWLS` recent crawls and no successful sample in any of them |
| `unknown` | fewer than `STABILITY_MIN_CRAWLS` (3) usable crawls, or the newest usable crawl is older than the window (`interval × window`) |

A band that excludes 50% is at most 50 points wide, so "excludes 50%" is the
whole test. The report carries the sentence the UI shows: "Across the last 4
crawls, 0 of 12 answers cited you (likely 0%–24%): stable." A `score` of
`1 − coin_flip(pooled rate)` orders pairs for the boost queue; `coin_flip`
moved to `core/stats.py` and the Overview's per-platform volatility now calls
the same function, so the two figures cannot disagree.

Consequence to accept: a genuine 70% pair pools 7 of 9 and straddles 50%, so
it is `volatile` and stays so until roughly thirty samples have narrowed the
band. That is honest. Such a pair is uncertain, and under `reallocate` it is
exactly where the saved calls go.

### B. Three policies per project, `fixed` by default

`Project.sampling_policy` is `fixed | save | reallocate`, default `fixed` for
every project including the ones already deployed, so nothing changes on
deploy. The analyst turns it on per project.

- **`save`**: a stable pair's interval is multiplied by 2, and by 3 once its
  agreeing streak reaches twice the window. The multiplier is capped by
  `STRETCH_MAX` (3) **and by the project's consolidation window**, so a
  stretched pair still appears in every consolidation and is never reported as
  lost.
- **`reallocate`**: `save`, plus `VOLATILE_BOOST` (2) extra samples for volatile
  pairs, granted most-volatile first, per platform, and only while the calls
  stretching saved cover them. Savings are counted in expected calls (a stable
  pair early-stops at `MIN_SAMPLES`) and carried across the stretch cycle from
  the `sampling` summary persisted on the previous crawl records, so the boost
  does not oscillate on the crawls when stretched pairs come due. Net spend
  never exceeds what `fixed` would have spent.

Never touched, under any policy: starred prompts (no stretch), prompts with an
explicit interval (no stretch) or explicit sample count (no boost), forced
runs, runs restricted to selected prompts, `failing` pairs (never boosted; the
platform is not answering), pairs already at ten samples, and a pair whose two
newest crawls are more than 1.5 intervals apart (it just returned from a
stretch; one base window before any boost, so it cannot ping-pong).

### C. One plan for the run, the Results tab and the dry run

`control_plane/sampling.plan()` judges every pair, converts verdicts into
per-pair `PairOverride`s (an interval multiplier, a boosted sample count) and
passes them to `planner.due_items()`, which keeps the single definition of
"due". The runner, `results()` and `GET /api/projects/{id}/sampling` all call
`plan()`, so the Results tab never marks a pair "due" that the next crawl will
skip. The route is read-only and accepts `?policy=` to simulate a policy the
project does not have, which is how an analyst decides whether to turn one on.

`DueItem.boosted` carries per-platform sample counts and `planner.batches()`
splits a prompt whose platforms differ, because a pipeline call has one count.

### D. The policy explains itself

- Every `SamplingDecision` has the stability report, the multiplier, the
  sample count, whether the pair is due, skipped or boosted, `next_due_at`, and
  a one-line note ("Stable for 6 crawl(s): sampled every 2 intervals";
  "Volatile; boost deferred until stretching has saved enough calls").
- `SamplingSummary` (pairs, stretched, skipped, boosted, calls planned versus
  baseline, saved, boosted, carry, estimated cost both ways, earliest next due)
  is on `RunOutcome`, persisted on `ProjectRunRecord.sampling` (new nullable
  column, idempotent migration), and returned by the route.
- An idle crawl's reason reads "nothing due; 3 stable pair(s) stretched, next
  due 2026-09-26" instead of a bare "nothing due", so a quiet stable project
  does not look broken on the Runs page.
- `PromptEngineDetail.stability` gives the drawer the same report.

### E. A pre-existing bug fixed alongside

A run restricted to selected platforms (`RunRequest.engines`) without selected
prompts was recorded as a full crawl and advanced the consolidation window.
`full` is now false whenever prompts or platforms were restricted.

## Alternatives considered

- **Per-crawl spread and flip counting** as the statistic: rejected for the
  reason in Context §1; flips survive only as a secondary trigger at two or
  more.
- **A persisted per-pair schedule state**: rejected. The planner's rule is that
  the history is the schedule state; the stretch is recomputed from history on
  every plan, and switching a project back to `fixed` takes effect on the next
  crawl with nothing to migrate.
- **Boost per prompt** (one count for all its platforms): rejected; it quadruples
  the cost of a boost and broke the budget arithmetic.
- **Defaulting existing projects to `save`**: rejected by the operator; the trend
  cadence of a live client project should not change without someone choosing it.
- **Recording a zero-batch crawl** when everything was stretched: rejected;
  `ProjectRunRecord` means "a job that produced at least one pipeline run" and
  the consolidation clock should not advance on a crawl with no data. The
  outcome's reason and summary explain the idle crawl instead.

## Consequences

- New: `prompt_tracking/stability.py`, `control_plane/sampling.py`,
  `control_plane/sampling_routes.py`; `StabilityReport`/`StabilityState` in
  `prompt_tracking/schemas.py`; `PairOverride`, `SamplingDecision`,
  `SamplingSummary`, `SamplingPlan`, `SamplingView` in `control_plane/schemas.py`;
  `core/stats.coin_flip`; `prompt_tracking/costing.engine_call_cost` (the
  pipeline's private cost map, now shared).
- Settings: `STABILITY_WINDOW_CRAWLS`, `STABILITY_MIN_CRAWLS`, `STRETCH_MAX`,
  `VOLATILE_BOOST`. Vendor spend for the feature: none; it only reads history.
- A stretched pair pools fewer samples per consolidation, so its band widens.
  The dry run shows expected calls per pair so the trade is visible before a
  policy is turned on; band-aware change detection (ADR 0020 §4) remains the
  natural next step.
- Prompt history is shared by `(lob, text)` across projects. A second project
  on the same line of business with the same prompt writes snapshots that the
  first project's stability window reads. Documented in `KNOWN_GAPS.md`; the
  fix (restricting `classify()` to the project's own run ids) is a later cycle.
- The UI work is specified in `docs/UI_SAMPLING_BRIEF.md`.
