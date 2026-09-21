# Prompt Engine — analyst UI

React 18 + TypeScript front end for the control plane in
`src/modules/control_plane`. The brief is `docs/UI_BUILD_BRIEF.md`; the stack
decision is ADR 0015.

## Run it

```powershell
# backend (port 8787); never click Run on a real project while testing
.\.venv\Scripts\python.exe -m src.modules.control_plane --approve-spend

cd ui
npm install
npm run dev            # http://127.0.0.1:5173, /api and /reports proxied to 8787
```

`VITE_MOCK=1 npm run dev` runs the app entirely on MSW fixtures (no server).
`VITE_MOCK=planned npm run dev` uses the real server and mocks only the
cycle-0011 insight routes that do not exist yet.

## Gate (all must be green before a step is reported done)

```powershell
npm run lint        # eslint + prettier --check
npm run typecheck   # tsc --strict
npm test            # vitest + Testing Library, MSW for every endpoint
npm run e2e         # Playwright, read-only, against the local server
npm run build       # typecheck + vite build → ui/dist
```

## Owner credentials (ADR 0019)

Everyone reads every project; changing, running or deleting a protected one needs
its owner credential. The create form asks for it (ticked by default). The project
header shows the lock state: *Read-only · owner X* with *Unlock to edit*,
*Unlocked · you can edit* with *Lock* and a rotate button, or *Open to everyone*
with *Protect*. Nothing else in the UI checks the lock: `api/client.ts` attaches the
stored credential to writes, and when the API answers 403 `project_locked` it opens
the unlock dialog and retries the write once. The credential lives in
`sessionStorage` (`lib/projectAuth.ts`), per project, for the tab's lifetime. The
MSW handlers mirror the server guard; `protectMockProject()` locks a fixture project
in a test.

## Serving the built app

`npm run build` writes `ui/dist`. The control plane serves it at `/` when the
folder exists (assets under `/assets`, React Router deep links fall back to
the shell); the hand-written page stays at `/legacy`. Restart the server after
the first build so the asset mount is registered. Without a build, `/` keeps
serving the legacy page.

## Layout

```
src/api/        schema.d.ts (generated: npm run gen:types), client.ts (typed fetch,
                ApiError with 422 field map), endpoints.ts (one function per route),
                queries.ts (TanStack Query keys and hooks), planned.ts (cycle-0011
                shapes until they appear in /openapi.json)
src/app/        shell: ThemeProvider (AntD tokens, light/dark/system), AppShell, Rail,
                TopBar (project switcher, active-jobs badge, theme, ⌘K), CommandPalette,
                JobAnnouncer (toast + Notification + title flash), notify.ts, format.ts
src/store/      Zustand UI-only state (theme, rail, page size, selection)
src/pages/      one folder per route group (projects, project tabs, atlas, trends, costs)
src/mocks/      MSW handlers for every live and planned route; fixtures are copies of
                the live API responses for the existing projects (17 Sep 2026)
src/test/       vitest setup (MSW server, jsdom polyfills) and the render helper
e2e/            Playwright smoke; asserts no POST to /run or /consolidate is made
```

## Rules baked in

- API shapes come from OpenAPI. `Complete<T>` in `endpoints.ts` only removes
  the optionality OpenAPI puts on pydantic `default_factory` fields of
  response models; request bodies stay partial.
- Every fetch takes the query's abort signal, so leaving a route cancels it.
- Mutations invalidate the minimal key family (`qk` in `queries.ts`).
- The browser never calls a vendor; tests never trigger a crawl.
- `MotionConfig reducedMotion="user"` disables animation under
  `prefers-reduced-motion`.
