# UI cycle 0002 — Projects list and form, Runs page (steps 3–4)

**Date**: 2026-09-17
**Operator instruction**: `docs/UI_SESSION_PROMPT.md` steps 3 and 4.

## Scope

`/projects` with cards (name, brand, LOB, platforms, interval, next crawl
due, crawls since last consolidation, health verdict, top action title),
search, New/Edit Drawer form with every field the API accepts and 422 mapping,
delete with confirmation. `/projects/:id/runs` with Run due / Run everything
(disabled while a job is active, Popconfirm stating spend), live progress card
polling `GET /api/jobs/{id}` every second (percent, phase, checks, batches,
paid calls, elapsed, message, outcome, warnings, Hide), re-attach on reload,
jobs table with Watch, crawl history, pipeline runs, consolidation panel with
history select and "Consolidate now" with a custom window, cost per crawl.

## Research findings

- The insight routes went live in the backend during this cycle (cycle 0011,
  ADR 0014): `GET …/insights`, `PUT …/actions/{id}`, `GET …/samples`, with
  fields beyond the first draft (`volatility`, `prompts`, `client_covered`,
  `winning_pages`, `client_pages`, `placement`, `freshness`, `metric`,
  `computed_from`). The running server predates that code, so the OpenAPI
  document was dumped from the app in-process (temporary SQLite, no server)
  into `ui/src/api/openapi.json`; `src/api/planned.ts` was deleted and every
  consumer now uses generated types. The backend also took ADR number 0014,
  so the React UI ADR is **0015** (`docs/adr/0015-react-ui.md`).
- Testing Library `*ByRole` queries with a `name` compute accessible names
  over the whole AntD tree on every poll and blocked the event loop for
  seconds between React commits (9 commits in 7.5 s; the mocked fetches were
  all under 60 ms). Tests now find cards by text and scope role queries to a
  container; per-test time fell from 10–20 s to 1–5 s.
- jsdom raises "not implemented" for `getComputedStyle(el, pseudo)`, which
  AntD calls constantly; it is polyfilled in the test setup. AntD's own CSS
  motion is switched off under `prefers-reduced-motion` (a brief requirement)
  and the test matchMedia polyfill reports reduced motion.
- The project name is "Test run" (brand "GEP"); an early fixture summary had
  concatenated the two.

## What was built

- `src/pages/projects/`: `ProjectsPage` (search, cards, delete confirm that
  says history is kept), `ProjectCard` (due summary derived from results,
  consolidation progress from positions, verdict and top action from
  insights), `ProjectForm` (AntD Drawer + Form, Zod for input, tag inputs for
  lists, per-engine model selects from `/api/options.models`, interval preset
  or custom, consolidation window with the inline "run every 2d with 3 →
  every 6 days" hint, server 422 → field errors, unmapped issues surfaced as
  a toast).
- `src/pages/project/`: `RunsPage`, `runs/JobProgressCard` (Framer-eased
  bar, `role=progressbar`), `runs/ConsolidationPanel`; `src/lib/jobs.ts`
  (`useWatchedJob`: analyst choice, else first active job; invalidates the
  project's data the moment the watched job leaves the active state);
  `src/lib/due.ts`; `src/api/mutations.ts` (optimistic prompt updates);
  `components/IntervalPicker`, `EngineCheckboxes`, `EngineTag`, `VerdictTag`.
- Mocks: `insights.ts` rewritten against the generated `InsightsView`
  (health with volatility, actions with `metric`, page inventory, placement,
  freshness); `applyUpdate` keeps action status/owner/note across recomputes
  like the server's `action_states`.
- Tests: `ProjectsPage.test.tsx` (5), `RunsPage.test.tsx` (4).

## Bugs found and fixed

- **Silent form submit.** The per-engine model selects produce `undefined`
  values, which `z.record(z.string(), z.string())` rejected; the error landed
  on `engine_models`, a name no Form.Item renders, so nothing happened on
  Create. Schema made optional and any unmapped issue is now shown as a toast.
- `IntervalPicker` recreated its `presets` array every render, so its effect
  re-ran on each render and looped while the Drawer was open. Memoised.
- The tags inputs had the dropdown forced closed, which stopped Enter from
  committing a tag; the dropdown is now hidden by style instead.
- The jobs table stayed stale after a run finished until the 5 s badge poll
  caught up; `useWatchedJob` now invalidates the project's data on the
  transition.
- Backslashes are halved when files are patched through the shell in this
  environment; a `"\n"` token separator became a literal newline and broke
  the parse. Repaired with the editor tool; noted so later patches avoid it.

## Corrections

- ui-0001 said the insight routes "are not in `/openapi.json` yet"; they are
  in the code as of cycle 0011 and in the committed snapshot, though the
  operator's running server still needs a restart to serve them.
- ui-0001 referred to ADR 0014 for the React stack; it is ADR 0015.

## Explicitly not done

- Steps 5–9 (prompts, battleground, overview/actions, atlas/costs, serving
  `ui/dist`).
- The running control plane has not been restarted; until it is, the UI
  against the real server shows the insight routes as 404 (use
  `VITE_MOCK=planned`).
- Bundle is 1.42 MB (443 kB gzip) before code splitting.

## Gate output (verbatim)

```
=== typecheck ===
OK
=== lint ===
OK
=== test ===
 ✓ src/mocks/insights.test.ts (3 tests) 11ms
 ✓ src/api/client.test.ts (4 tests) 63ms
 ✓ src/app/AppShell.test.tsx (4 tests) 9390ms
 ✓ src/pages/projects/ProjectsPage.test.tsx (5 tests) 18060ms
   ✓ projects page > lists every project with brand, LOB, platforms and consolidation progress 900ms
   ✓ projects page > filters by the search box 710ms
   ✓ projects page > maps a 422 from the API onto the form field 6116ms
   ✓ projects page > creates a project and shows its card 5724ms
   ✓ projects page > deletes a project after confirmation and says history is kept 4605ms
 ✓ src/pages/project/RunsPage.test.tsx (4 tests) 22088ms
   ✓ runs page > shows crawl history, pipeline runs and the consolidation status 3445ms
   ✓ runs page > starts a run, watches it to completion and lists it in the jobs table 10418ms
   ✓ runs page > consolidates on demand with a custom window and updates the panel 4626ms
   ✓ runs page > shows the API detail verbatim when consolidation is refused 3595ms
 Test Files  5 passed (5)
      Tests  20 passed (20)
=== e2e ===
  ok 1 [chromium] › e2e\smoke.spec.ts:9:5 › smoke (read-only) › shell loads, rail links work, a project opens (2.4s)
  ok 2 [chromium] › e2e\smoke.spec.ts:16:5 › smoke (read-only) › command palette opens with Ctrl+K (564ms)
  ok 3 [chromium] › e2e\smoke.spec.ts:23:5 › smoke (read-only) › no request to /run or /consolidate is ever made (1.1s)
  3 passed (7.4s)
=== build ===
dist/index.html                     0.40 kB │ gzip:   0.27 kB
dist/assets/index-D_gkYcVL.css      0.98 kB │ gzip:   0.53 kB
dist/assets/index-B3yejJZN.js   1,420.14 kB │ gzip: 443.25 kB
✓ built in 8.31s
```

The Python gate was not run: no file under `src/`, `tests/` or `scripts/`
changed in this cycle.
