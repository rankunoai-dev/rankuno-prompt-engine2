# UI cycle 0004 — Trends tab: per-run and consolidated graphs per project

**Date**: 2026-09-18
**Operator request**: "a separate tab which will show the graph of individual
project for both consolidated and per run, also on the basis of dropdown
separate for all the platforms, and separately for mentions and citation;
this whole section must be a separate tab like atlas" (chat).

## Scope

New top-level route `/trends` (rail entry next to Atlas, in the ⌘K palette):
project picker; basis toggle **Per run** (every pipeline run on its own date)
/ **Consolidated** (every stored position set); platform dropdown (all
platforms as separate lines, or one platform); metric dropdown (citations and
mentions, or one of them); optional single-prompt filter; citation rate and
mention rate always in separate charts, each with a table twin; the basis
(checks or samples) per point printed under the charts.

## Research findings

- Per-run points come from the atlas dataset (`/reports/prompt-atlas-data.json?lob=`),
  which already carries every snapshot keyed by `run_id`; they are restricted
  to the project's tracked prompt ids so the graph is for the project, not the
  whole line of business. Consolidated points come from
  `GET /positions?consolidation_id=` for each entry in the project's
  consolidation history (`useQueries`, one request per consolidation, only
  while that basis is selected).
- Several runs can start on the same day, so the x label carries the time and
  the first six characters of the run id.
- Cold-load navigation bug (found by the smoke test): starting at `/`, the
  index `<Navigate>` sat inside the shell's animated, frozen outlet and could
  fire again on the next client-side navigation, so clicking a rail link kept
  the URL at `/projects`. The redirect now lives outside the shell route.

## What was built

- `src/lib/trends.ts` (`perRunSeries`, `consolidatedSeries`; tested),
  `src/pages/trends/TrendsPage.tsx`, route in `App.tsx`, rail entry, palette
  page, smoke step that opens Trends from the rail.
- Tests: `trends.test.ts` (3), `TrendsPage.test.tsx` (3: both charts per run
  for every platform; single platform + mentions only; consolidated empty
  state then series after a consolidation).

## Bugs found and fixed

- Root redirect re-firing after the first navigation (above).
- A regex backreference patched through the shell was halved into a control
  character inside `Rail.tsx`; repaired with the editor.

## Corrections

- None.

## Explicitly not done

- No best-rank or share-of-prompts-cited series (only the two rates asked
  for); no cross-project overlay (one project at a time, as requested).
- Consolidated points are fetched per consolidation; a backend route
  returning the whole history in one call would be cheaper for long histories.

## Gate output (verbatim)

```
=== lint ===
(eslint: no output) All matched files use Prettier code style!
=== test ===
 Test Files  15 passed (15)
      Tests  55 passed (55)
=== e2e ===
  ok 1 [chromium] › e2e\smoke.spec.ts:9:5 › smoke (read-only) › shell loads, rail links work, a project opens (2.1s)
  ok 2 [chromium] › e2e\smoke.spec.ts:19:5 › smoke (read-only) › command palette opens with Ctrl+K (582ms)
  ok 3 [chromium] › e2e\smoke.spec.ts:26:5 › smoke (read-only) › no request to /run or /consolidate is ever made (717ms)
  3 passed (4.9s)
=== build ===
dist/assets/TrendsPage-*.js   10.48 kB │ gzip: 4.22 kB
```

No file under `src/`, `tests/` or `scripts/` changed; the Python gate was not
rerun.
