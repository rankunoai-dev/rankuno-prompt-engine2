# UI cycle 0001 — Scaffold and app shell (steps 1–2)

**Date**: 2026-09-17
**Operator instruction**: `docs/UI_SESSION_PROMPT.md` ("read this and start
working after taking the whole context of the code and follow the coding
standard"), which fixes the stack and the delivery order. Treated as the Step 3
sign-off for this cycle; ADR 0015 records the stack decision.

## Scope

Steps 1 and 2 of the session prompt: `ui/` scaffold (Vite, React 18, TS
strict, AntD 5 theme light/dark/system, React Router 6 routes, TanStack Query
5, Zustand, Framer Motion with reduced-motion, OpenAPI type generation, MSW
handlers for every live and planned endpoint with fixtures copied from the
live API, Vitest + RTL + Playwright), and the app shell (collapsible rail, top
bar with project switcher, active-jobs badge, theme toggle, ⌘K command
palette, toasts, completion announcer with browser Notification and tab-title
flash). Acceptance: `npm run lint`, `typecheck`, `test`, `e2e`, `build` green.

## Research findings

- The control plane exposes 18 routes and 32 schemas in `/openapi.json`; two
  routes (`/api/health`, `/api/options`) and the atlas document are declared
  as free-form objects, so their shapes are typed from `app.py` /
  `atlas_export.py` with a comment saying so.
- pydantic fields declared with `default_factory` carry no `default` in the
  OpenAPI document, so `openapi-typescript` emits them as optional even though
  the server always serialises them. A single mapped type `Complete<T>` is
  applied to response models only.
- The existing project (`e42161487b61`, "Test run GEP", 15 prompts) has 25
  snapshots with citation links or mentions; Gemini snapshots are
  `model: "unavailable"` (billing, ADR 0011). The second project has two
  prompts and no crawl. Both are captured as fixtures. No consolidation exists
  yet on either, so the consolidated views will be exercised through the mock
  `POST /consolidate`.
- The backend session began adding the insight routes to `app.py`
  (`InsightsView`, `ActionCard`, `AnswerSample` imports) while this cycle ran;
  they are not in `/openapi.json` yet, so `src/api/planned.ts` stands in.
- Vite 6 binds to `localhost`, which Node 20 resolves to `::1`; Playwright's
  web-server probe polls `127.0.0.1`. The dev server is pinned to
  `127.0.0.1`.
- The installed Playwright browsers (`chromium-1234`) did not match
  `@playwright/test` 1.5x (`chromium_headless_shell-1243`); one
  `npx playwright install chromium` (115 MB) fixed it.

## What was built

- `ui/`: package manifest, `tsconfig` (strict, `noUncheckedIndexedAccess`),
  `vite.config.ts` (proxy `/api`, `/reports`, `/openapi.json` → 8787; vitest
  jsdom config), `playwright.config.ts`, flat ESLint config with
  `typescript-eslint`, Prettier honouring the repo `.editorconfig`.
- `scripts/gen-types.mjs` → `src/api/schema.d.ts` (2 093 lines) from the live
  server, snapshot in `src/api/openapi.json` for CI.
- `src/api/`: `client.ts` (typed fetch, `ApiError` with verbatim `detail` and
  a 422 field map), `endpoints.ts` (one function per route), `queries.ts`
  (key families, hooks with abort signals, job polling at 1 s while active,
  active jobs at 5 s), `mutations.ts` (optimistic prompt updates),
  `planned.ts`.
- `src/app/`: `ThemeProvider` (system/light/dark, `data-theme` stamp),
  `AppShell` with `FrozenOutlet` (see bugs), `Rail`, `TopBar`,
  `CommandPalette`, `JobAnnouncer`, `notify.ts`, `format.ts`, `theme.ts`
  (brand tokens, fixed engine colours).
- `src/mocks/`: handlers for all 18 live routes with a mutable in-memory
  state (create/update/delete/import, jobs that advance one notch per poll,
  consolidate building positions from the latest snapshots) and the three
  planned routes derived deterministically from the stored results
  (`insights.ts`); fixtures copied from the live API.
- Tests: `client.test.ts` (4), `insights.test.ts` (3), `AppShell.test.tsx`
  (4: redirect and rail, active-jobs badge, ⌘K palette navigation, theme
  toggle); Playwright `e2e/smoke.spec.ts` (3, read-only, asserts no
  `POST …/run` or `…/consolidate` is ever issued).
- `ui/README.md`, ADR 0015.

## Bugs found and fixed

- **Every page mounted twice on navigation.** `AnimatePresence mode="wait"`
  keeps the exiting page container alive while it animates out, and the
  `<Outlet>` inside it re-rendered the *new* route during that time, so each
  page mounted first in the exiting container and again in the entering one
  (double fetches, flicker). Fixed with `FrozenOutlet`, which captures the
  outlet element once per route key. Found by a test assertion that observed
  the heading appear and disappear.
- Mock `POST /run` stored the partial request body as the job's request; the
  response type requires every field. Normalised.
- Default vitest timeout (5 s) is too short for a full AntD app render plus
  keyboard interaction in jsdom (4–9 s here); raised to 20 s.

## Corrections

- `docs/UI_SESSION_PROMPT.md` says "Zod schemas generated from the OpenAPI";
  `openapi-typescript` generates TypeScript types, not Zod. Types are
  generated; Zod is used only for form input, as the same document says under
  "Stack".

## Explicitly not done

- Pages other than the shell are placeholders (steps 3–8 follow).
- `ui/dist` is not yet mounted by the control plane (step 9).
- No code splitting yet: the production bundle is 1.0 MB (320 kB gzip); to be
  split per route before step 9.
- Lighthouse accessibility has not been measured; keyboard navigation for the
  palette exists, matrix and tables come with their pages.
- The `planned.ts` shapes will be replaced by generated types as soon as the
  backend publishes the routes.

## Gate output (verbatim)

```
=== lint ===
(eslint: no output)
Checking formatting...
All matched files use Prettier code style!
=== typecheck ===
OK
=== test ===
 ✓ src/mocks/insights.test.ts (3 tests) 12ms
 ✓ src/api/client.test.ts (4 tests) 62ms
 ✓ src/app/AppShell.test.tsx (4 tests) 18898ms
   ✓ app shell > redirects / to /projects and lists rail entries 3985ms
   ✓ app shell > shows the active-jobs badge from /api/jobs?active=true 498ms
   ✓ app shell > opens the command palette with Ctrl+K and jumps to a project 9711ms
   ✓ app shell > switches theme mode through the top bar 4692ms
 Test Files  3 passed (3)
      Tests  11 passed (11)
=== e2e ===
Running 3 tests using 1 worker
  ok 1 [chromium] › e2e\smoke.spec.ts:9:5 › smoke (read-only) › shell loads, rail links work, a project opens (718ms)
  ok 2 [chromium] › e2e\smoke.spec.ts:16:5 › smoke (read-only) › command palette opens with Ctrl+K (723ms)
  ok 3 [chromium] › e2e\smoke.spec.ts:23:5 › smoke (read-only) › no request to /run or /consolidate is ever made (754ms)
  3 passed (30.7s)
=== build ===
vite v6.4.3 building for production...
✓ 3718 modules transformed.
dist/index.html                     0.40 kB │ gzip:   0.27 kB
dist/assets/index-D_gkYcVL.css      0.98 kB │ gzip:   0.53 kB
dist/assets/index-CqQje31Q.js   1,001.08 kB │ gzip: 320.42 kB
✓ built in 16.20s
```

The Python gate (`scripts/verify.ps1`) was not run in this cycle: no file
under `src/`, `tests/` or `scripts/` changed.
