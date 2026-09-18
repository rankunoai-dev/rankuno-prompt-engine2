# UI cycle 0003 — Prompts, Battleground, Overview, Actions, Atlas, Costs, serving (steps 5–9)

**Date**: 2026-09-17
**Operator instruction**: `docs/UI_SESSION_PROMPT.md` steps 5–9.

## Scope

- `/projects/:id/prompts`: client-side AntD table joined from three sources
  (tracked prompt, latest results, atlas research facts), sort and filters
  (intent, stage, platform, verdict, due, starred, enabled), star and on/off
  (optimistic), overrides editor (interval, platforms, samples, reset), inline
  edit of text/keyword/subtopic, delete with confirm, import by file or paste,
  add one prompt, bulk bar (run selected, star, unstar, set interval, set
  platforms, delete), page size 10/25/50/100 persisted, virtual rows > 200.
- `/projects/:id/battleground`: matrix grouped by subtopic, one badge per cell
  (Linked #rank / Mentioned only / Absent / <competitor> wins, due dot), hover
  basis, consolidated vs point-in-time with a crawl picker, roving-tabindex
  arrow-key navigation, Inspection Drawer (720 px, deep-linked through
  `?prompt&engine&crawl`) with consolidated numbers, rank distribution, domain
  share, per-crawl history, answer text with brand sentences highlighted and
  claims annotated, citation links coloured by role, consulted-but-not-cited
  with "read but rejected" flag, fan-out queries, organic ranks.
- `/projects/:id/overview`: confidence banner, health strip, "what changed",
  top 3 action cards, "show all"; `/projects/:id/actions`: full checklist
  grouped by type, filters, owner/note, done checkbox animating to the Done
  group, outcome tag, `#<action id>` deep link.
- `/atlas`: parity with `docs/prompt-atlas.html` (LOB picker, engine chips,
  filters, as-of run, Overview with tiles/scoreboard/trend/heatmap/domain
  bars, Master sheet, Engines, Share of voice, Content gaps, Claims & fan-out)
  sharing the same Inspection Drawer.
- `/costs`: totals, by source, vendor table, recommendations with a
  copy-to-clipboard `.env` block, per-run table, notes, project scope.
- Serving: `ui/dist` at `/`, `/assets` mounted, SPA fallback, `/legacy`.

## Research findings

- `TrackedPrompt` carries no volume, intent or stage; those live only in the
  atlas dataset. The prompts table joins them by `prompt_id`
  (`src/lib/promptView.ts`), so custom prompts show "—" for volume.
- Point-in-time at matrix level for a past crawl would need one `/samples`
  call per cell; the atlas dataset already carries every snapshot keyed by
  `run_id`, and a crawl record lists its run ids, so past crawls are rendered
  from that with no extra request.
- Testing Library role-with-name queries and per-keystroke typing into a
  20-field form dominate test time; text-scoped queries and single change
  events cut page tests to 1–6 s. A fresh `[]` returned from a Zustand
  selector loops React's external-store hook ("Maximum update depth"); a
  stable constant fixes it.
- `@ant-design/plots` needs canvas; jsdom has none. Every chart has a table
  twin (a dataviz requirement anyway) and tests render the twin alone.
- In this environment a backslash written through the shell is halved, so
  `"\n"` in a shell-patched file became a literal newline. Such edits go
  through the editor tool or `chr(92)` in Python.
- The backend `RunCost` row has no start time; the per-run cost table shows
  run id, calls and spend only.

## What was built

- `src/lib/`: `promptView.ts`, `matrix.ts`, `atlas.ts` (pure, tested),
  `jobs.ts`, `useLenis.ts` (Overview and Atlas only, off under reduced motion).
- `src/pages/project/`: `PromptsPage`, `prompts/ImportCard`,
  `prompts/OverridesEditor`, `BattlegroundPage`, `battleground/CellBadge`,
  `battleground/InspectionDrawer`, `OverviewPage`, `ActionsPage`;
  `components/ActionCardView`, `components/charts/Charts` (lazy plots).
- `src/pages/atlas/`: `AtlasPage`, `AtlasContext`, `AtlasOverview`,
  `AtlasSheet`, `AtlasEngines`, `AtlasVoiceGaps`, `AtlasInsights`.
- `src/pages/costs/CostsPage`; routes are lazy (`App.tsx`), so the bundle is
  split per page (largest page chunk 77 kB, AntD vendor chunk shared).
- `src/modules/control_plane/app.py`: `UI_DIST`, `/` serves the built index
  when present, `/legacy` serves the old page, `/assets` mounted, catch-all
  SPA fallback that never shadows `/api`, `/reports`, `/docs`, `/assets`.
- Tests: `promptView.test.ts` (3), `matrix.test.ts` (4), `atlas.test.ts` (5),
  `PromptsPage.test.tsx` (5), `BattlegroundPage.test.tsx` (4),
  `OverviewPage.test.tsx` (3), `AtlasPage.test.tsx` (3), `CostsPage.test.tsx`
  (1). 48 UI tests in total.

## Bugs found and fixed

- Zod rejected the per-engine model selects' `undefined` values and the error
  landed on a field with no Form.Item, so Create did nothing (ui-0002 scope,
  found here while testing imports).
- The import button's label was split across text nodes and "short" (5
  characters) counted as a prompt; fixed label and test text.
- `test_index_health_and_options` asserted the legacy page at `/`; with the
  sanctioned mount that is impossible, so its one request line now targets
  `/legacy`. This is the only edit outside `app.py` in the backend tree, made
  because the session prompt requires the repo gate to pass after the mount.
- A first full-suite run reported 11 control-plane errors; rerunning the
  folder and then the full suite passed with no change. The backend session
  was editing `app.py` at the time; the failures were not reproducible.

## Corrections

- ui-0002 said the bundle would be split "before step 9"; done in this cycle.

## Explicitly not done

- Lighthouse accessibility has not been measured (no browser audit tooling in
  this session); keyboard navigation exists for the matrix, tables, palette
  and forms, and every control has an accessible name.
- Fan-out, claims, source snippets and freshness render from the live
  insight routes but are empty for samples captured before cycle 0011; the
  mock mirrors that.
- The running control plane was not restarted, so the served `/` and the
  insight routes were verified through tests and the Vite proxy, not through
  the operator's live process. Restart it to serve `ui/dist`.
- The UI gate is not yet wired into `.github/workflows/ci.yml` (Python gate
  only); a `node` job running `npm ci && npm run lint && npm run typecheck &&
  npm test && npm run build` is the next step for CI.
- `docs/prompt-atlas.html` and `static/index.html` are kept, reachable at
  `/docs/prompt-atlas.html` and `/legacy`, until the operator confirms parity.

## Gate output (verbatim)

UI (`ui/`):

```
=== typecheck ===
(tsc: no output)
=== lint ===
(eslint: no output; prettier: All matched files use Prettier code style!)
=== test ===
 Test Files  13 passed (13)
      Tests  48 passed (48)
=== e2e ===
  ok 1 [chromium] › e2e\smoke.spec.ts:9:5 › smoke (read-only) › shell loads, rail links work, a project opens (568ms)
  ok 2 [chromium] › e2e\smoke.spec.ts:16:5 › smoke (read-only) › command palette opens with Ctrl+K (544ms)
  ok 3 [chromium] › e2e\smoke.spec.ts:23:5 › smoke (read-only) › no request to /run or /consolidate is ever made (928ms)
  3 passed (4.8s)
=== build ===
dist/assets/PromptsPage-DvgMP42Z.js       76.82 kB │ gzip:  25.20 kB   (largest page chunk)
dist/assets/AtlasPage-Bc_rTdVx.js         38.43 kB │ gzip:  12.41 kB
dist/assets/InspectionDrawer-DD7B1f_1.js  21.46 kB │ gzip:   6.79 kB
(29 chunks in total; AntD vendor chunk shared)
```

Python (`scripts/verify.ps1` steps, run through the venv):

```
=== Format ===
181 files already formatted
=== Lint ===
All checks passed!
=== Type check ===
Success: no issues found in 59 source files
=== Tests ===
TOTAL                                              5292     89   1182     53    98%
Required test coverage of 85.0% reached. Total coverage: 97.65%
686 passed, 2 warnings in 115.74s (0:01:55)
=== Drift ===
--- Drift Audit Results ---
No documentation drift detected.
```
