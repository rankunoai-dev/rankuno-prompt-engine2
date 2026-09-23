# Cycle 0017 — A 95% confidence band on every rate

**Date**: 2026-09-23
**Operator request**: "What we currently lack is calculating the statistical
error margin (e.g. Brand X appears in 85% of answers ± 4% margin of error) …
if not implemented into our engine, implement this."

Decision record: ADR 0020.

## Scope

Wilson score interval at 95% on the consolidated citation rate and mention
rate of every prompt-by-platform position, and on every platform health rate,
computed from the pooled successful samples and stored with the position.
Shown in the UI as "likely 35–88%" beside the point estimate on the
Battleground cell hover, in the Inspection Drawer's consolidated numbers, and
on the Overview health tiles. No verdict or threshold reads the band yet.

## Research findings

- `aggregate_position()` already had `ok` (successful samples) and the two
  counts, so the band is two lines there and two in `_health()`. The maths
  lives in `src/core/stats.py` because nothing about it is control-plane
  specific.
- Positions are persisted as JSON payloads and re-validated on read. New
  required fields would have broken every stored consolidation, so the four
  fields default to `None`, and the UI treats `None` as "no band", which is
  also what point-in-time cells get.
- The UI's `Complete<T>` helper turns optional fields into required ones, so
  every fixture that builds a position or a health tile had to gain the fields.
  The mocks now compute a real Wilson band with the same formula rather than
  `null`, so the Overview tiles show bands in tests and in `VITE_MOCK` mode.

## What was built

- `src/core/stats.py`: `wilson_interval(successes, trials, confidence=0.95)`,
  `None` for zero trials, bounds rounded to four places, three confidence
  levels; `tests/core/test_stats.py` (5).
- `ConsolidatedPosition.{citation,mention}_rate_{low,high}`,
  `EngineHealth.{cited,mention}_rate_{low,high}`; set in
  `positioning.aggregate_position` and `insights._health`.
- UI: `pctRange()` in `format.ts`; `CellView.citedLow/citedHigh`; hover text
  on `CellBadge`; "likely …" lines in `InspectionDrawer` and under the
  `Stat` values on Overview health tiles; regenerated `openapi.json` and
  `schema.d.ts`; `wilson()` in `mocks/insights.ts` used by the position and
  health fixtures.
- Tests: `test_positioning` asserts the exact band for 3 of 5
  (`0.2307–0.8824`); `test_insights` asserts a perfect score still has a lower
  bound below 1; `matrix.test.ts` asserts consolidated cells carry the band
  and point-in-time cells do not; `OverviewPage.test.tsx` asserts every health
  tile shows a "likely" range.

## Bugs found and fixed

- A patch script applied the `pctRange` helper three times because its
  idempotency check keyed on a substring that the new text also contained. The
  typecheck caught it; the script now skips when the new text is present and
  the old is not.
- The same script silently swallowed the OpenAPI dump error (path typo) behind
  `2>/dev/null`, so the first type regeneration produced a snapshot without
  the new fields. Re-run with errors visible.

- The first gate run failed on my own assertion: I hand-computed the lower
  bound for 3 of 5 as 0.2313; the function gives 0.2307. The test now pins the
  computed value, which is also a reminder that the test protects the formula
  from drift, not from my arithmetic.

## Corrections

- The feasibility doc of 2026-09-23 estimated this at "half a cycle to the
  consolidation maths". The maths was half a cycle; the fixtures and the type
  regeneration were the other half.

## Explicitly not done

- Nothing acts on the band. Verdicts, `InsightChange` and the action-card
  thresholds still compare point estimates. "A change counts only when the
  bands do not overlap" is the obvious next step and needs its own decision.
- No band on the Trends charts or on the Atlas, which compute means over
  point-in-time snapshots in the browser.
- No band on share of voice or domain share.
- Existing consolidations are not back-filled; they show no band until the
  next consolidation runs.
- The site-wide "low confidence" banner (fewer crawls than the window) is
  unchanged and independent of the band.

## Gate output (verbatim; progress dots and unrelated coverage rows elided)

`scripts\verify.ps1`:

```
=== Format ===
210 files already formatted
PASSED: Format
=== Lint ===
All checks passed!
PASSED: Lint
=== Type check ===
Success: no issues found in 64 source files
PASSED: Type check
=== Tests ===
============================== warnings summary ===============================
=============================== tests coverage ================================
src\modules\control_plane\positioning.py            172      8     44      3    95%   100-101, 230-232, 452, 465, 477
TOTAL                                              5818     92   1276     54    98%
37 files skipped due to complete coverage.
Required test coverage of 85.0% reached. Total coverage: 97.83%
748 passed, 2 warnings in 147.49s (0:02:27)
PASSED: Tests
ALL GATES PASSED.
```

`scripts\drift_check.py`:

```
--- Drift Audit Results ---
No documentation drift detected.
```

UI (`npm run typecheck`, `npm run lint` clean; `npx vitest run`):

```
✓ src/pages/project/RunsPage.test.tsx (4 tests) 62840ms
 ✓ src/pages/atlas/AtlasPage.test.tsx (3 tests) 67567ms
 ✓ src/pages/project/PromptsPage.test.tsx (5 tests) 89188ms
 ✓ src/pages/project/BattlegroundPage.test.tsx (5 tests) 41850ms
 ✓ src/pages/projects/ProjectsPage.test.tsx (5 tests) 43370ms
 ✓ src/pages/trends/TrendsPage.test.tsx (3 tests) 28117ms
 ✓ src/pages/project/OverviewPage.test.tsx (4 tests) 14934ms
 ✓ src/pages/project/ProjectLock.test.tsx (2 tests) 13509ms
 ✓ src/api/client.test.ts (8 tests) 143ms
 ✓ src/lib/trends.test.ts (3 tests) 28ms
 ✓ src/pages/costs/CostsPage.test.tsx (2 tests) 10306ms
 ✓ src/lib/matrix.test.ts (5 tests) 23ms
 ✓ src/lib/pages.test.ts (4 tests) 18ms
 ✓ src/lib/atlas.test.ts (5 tests) 20ms
 ✓ src/lib/projectAuth.test.ts (4 tests) 17ms
 ✓ src/mocks/insights.test.ts (3 tests) 15ms
 ✓ src/lib/promptView.test.ts (3 tests) 14ms
 ✓ src/app/AppShell.test.tsx (4 tests) 15762ms
 ✓ src/store/ui.test.ts (2 tests) 11ms
 Test Files  19 passed (19)
      Tests  74 passed (74)
```
