# ADR 0015 — React analyst front end in `ui/`, served by the control plane

**Status**: Accepted (2026-09-17). Operator request: the analyst UI specified in
`docs/UI_BUILD_BRIEF.md` and `docs/UI_SESSION_PROMPT.md`, replacing the two
hand-written pages (`control_plane/static/index.html`, `docs/prompt-atlas.html`).

## Context

ADR 0009 chose a single-file vanilla page to keep the toolchain at zero. Two
cycles later the page carries projects, prompt overrides, a job queue with live
progress, consolidated and point-in-time results, costs, and a second explorer
page exists for the atlas. The brief now asks for verdict-first screens, an
engine matrix, an inspection drawer, action checklists, deep links, keyboard
navigation, and both themes: a component model with routing and server-state
caching is cheaper than growing the vanilla page further. The brief fixes the
stack; this ADR records it and the choices the brief left open.

## Decisions

1. **Stack as briefed, versions pinned to the majors the brief names.** React
   18, TypeScript strict, Vite 6, Ant Design 5 with `ConfigProvider` tokens
   (accent `#1f5eff`, success `#1a8f4d`, warning `#b7791f`, danger `#c0392b`,
   star `#e0a400`), TanStack Query 5, React Router 6, Zustand 5, Framer Motion
   with `MotionConfig reducedMotion="user"`, `@ant-design/plots` for the
   Atlas charts only, Lenis on Overview and Atlas only, Zod for form input only.
   React 19 and antd 6 are current on npm and were deliberately not used.
2. **API shapes are generated, never hand-typed.** `scripts/gen-types.mjs`
   runs `openapi-typescript` against the live `/openapi.json`, falling back to
   the committed snapshot `src/api/openapi.json` so CI needs no server. One
   mapped type, `Complete<T>`, is applied to response models only: pydantic
   always serialises every field, but `default_factory` fields have no
   `default` in OpenAPI and would otherwise read as optional in every
   component. Request bodies keep the generated partial types. The two routes
   FastAPI declares as free-form objects (`/api/health`, `/api/options`) and
   the atlas document are typed from the server source with a comment saying
   so.
3. **Planned routes are typed in `src/api/planned.ts` and served by MSW.**
   `insights`, `actions` and `samples` (cycle 0011) do not exist yet. The mock
   derives them deterministically from the stored results with the rules of
   the brief (§3) where the snapshots carry enough to evaluate them, and leaves
   the rest empty rather than inventing data. The file is deleted when the
   server publishes the routes.
4. **Fixtures are recordings, not inventions.** Every MSW fixture is a copy of
   the live API's response for the existing projects on 17 Sep 2026, so tests
   run against the exact shapes the server emits, including the faulty
   pre-cycle-0008 snapshots (model `google/gemini-3.6-flash` for Perplexity)
   that the brief says must not drive conclusions.
5. **Mutable mocks, one reset.** Handlers keep an in-memory copy of the
   fixtures and mutate it (create, update, delete, import, queue a job that
   advances one notch per poll), so component tests exercise real flows;
   `resetMockState()` runs after every test.
6. **Tests never spend.** Vitest runs entirely on MSW; the Playwright smoke is
   read-only against the local server and asserts that no `POST …/run` or
   `…/consolidate` was made during the session.
7. **UI gate is separate from the Python gate.** `npm run lint`, `typecheck`,
   `test`, `e2e`, `build` must all be green before a step is reported done;
   `scripts/verify.ps1` is unchanged and still governs `src/`.
8. **Repo formatting applies.** Prettier honours the repository
   `.editorconfig` (4-space indent), so the UI follows the same rule as the
   Python code rather than the 2-space React convention.

## Alternatives considered

- **Keep extending the vanilla page**: rejected by the brief; the feature list
  (matrix, drawer, checklists, deep links, keyboard, two themes) is a
  framework's job.
- **Hand-written API types with Zod parsing of every response**: rejected;
  drift from the server would be silent. Generated types fail the typecheck
  when the server changes.
- **SSE/WebSocket for progress**: rejected in ADR 0010; polling with
  `refetchInterval` is kept.

## Consequences

- `ui/` adds a Node toolchain to the repository; `node_modules` and `dist` are
  ignored. The control plane will mount `ui/dist` at `/` (step 9), keeping the
  old page at `/legacy` until parity is confirmed.
- Backend changes to a response model surface as UI type errors on the next
  `npm run gen:types`; regenerate after every backend cycle.
