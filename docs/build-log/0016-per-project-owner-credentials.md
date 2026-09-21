# Cycle 0016 — Per-project owner credentials

**Date**: 2026-09-21
**Operator request**: "implement project wise creds set so that anyone cannot
write and do anything hence at the time of project creation we can set the
creds and hence everyone will have the read access but not the write access to
the project only the person with the creds can edit/write"

Decision record: ADR 0019. This entry is what broke, what was assumed, and what
was left out.

## SDLC note: step 3 was not a stop

The protocol asks for the plan to be shown and approved before implementation.
The operator was not available during this session, so the cycle proceeded on
the most conservative reading of the request and recorded each assumption in
ADR 0019 ("Assumptions made without the operator present"). The one reading
that would have been unsafe to assume — opening a public deployment to
anonymous readers — was **not** taken: the site login of ADR 0018 is untouched.
**Nothing was pushed**, because a push to `main` deploys to Railway; the
operator reviews first.

## Research findings

- Every route was reachable by anyone past the site login; `app.py` had no
  dependency on any route. Nine routes under `/api/projects/{id}` mutate state
  or spend money.
- `Project` is rebuilt from a JSON payload with `extra="forbid"`, and
  `create_project` spread `body.model_dump()` straight into `Project(...)`. A
  credential field on `ProjectCreate` would therefore have been written into
  the payload, returned by every read, and included in `/export`. Hence the
  separate table and the explicit `exclude={"credentials"}`.
- `Authorization` is taken by the site login, and browsers drop cached Basic
  credentials on a 401. Hence a separate header and 403.
- `Settings` has no `env_ignore_empty`, so a blank `PROJECT_ADMIN_PASSWORD=`
  line in `.env` arrives as `SecretStr("")`. A `min_length` on the field would
  have refused to boot on the documented default. Length is checked in
  `model_post_init` and blank is treated as unset.
- The UI's checked-in OpenAPI snapshot was behind the backend (see Corrections).

## What was built

Backend
- `control_plane/credentials.py`: `hash_password`, `verify_password`,
  `CredentialRecord` (scrypt, standard library, parameters stored per row).
- `control_plane/project_access.py`: `AttemptLimiter`, `ProjectAccessGuard`,
  `ProjectLocked`/`TooManyAttempts` with their handlers, and the two routes
  `GET …/access` and `PUT …/credentials`.
- `store.py`: table `project_credentials`; `protected`/`owner` joined on read
  and excluded from the payload; credential written in the same transaction as
  the project; removed with it; `credential_record`, `set_credentials`.
- `schemas.py`: `ProjectCredentials`, `ProjectAccess`, `ProjectCreate.credentials`,
  `Project.protected`, `Project.owner`.
- `auth.py`: `parse_basic` extracted from `credentials_match` and reused.
- `app.py`: `dependencies=owner_only` on the nine mutating project routes.
- `config.py` / `.env.example`: `PROJECT_ADMIN_PASSWORD`.

Frontend
- `lib/projectAuth.ts`: per-project credential in `sessionStorage`, the rule
  for which requests carry it, and the unlock request bus.
- `api/client.ts`: attaches the header; on 403 `project_locked` /
  `project_credentials_invalid` asks for the credential once and retries;
  `ApiError.code`.
- `app/ProjectUnlock.tsx`: unlock dialog (mounted in `AppShell`), lock badge
  (in `ProjectLayout`), set/rotate dialog. `ProjectForm`: owner credential
  section on create, ticked by default, with a warning when unticked.
  `ProjectCard`: lock icon.
- MSW handlers mirror the server guard (`guardHandlers` first in the list,
  `protectMockProject` for tests).

Tests
- `tests/modules/control_plane/test_project_access.py` (21): hashing, corrupt
  records, the contract, the store never persisting the secret, the limiter,
  every write refused and nothing changed, every write allowed for the holder,
  reads open, 404 before 403, malformed headers, claim, rotate, throttle,
  recovery password, and the site login and project credential coexisting.
- UI: `projectAuth.test.ts` (4), `client.test.ts` (+4), `ProjectLock.test.tsx`
  (2, the full dialog flow), `ProjectsPage.test.tsx` updated for the new form
  fields.

Verified against a real server (throwaway database, port 8799, no
`--approve-spend`): create protected → read 200 → edit/run/delete without the
header 403 → wrong password 403 → right password 200; the SQLite file and the
server log contain neither the password nor its base64. Driven through the real
UI with Playwright, the network trace for adding a prompt as a reader was
`POST …/prompts → 403`, `GET …/access → 200` (wrong), `GET …/access → 200`
(right), `POST …/prompts → 201`.

## Bugs found and fixed

- **Unlock dialog prefilled the owner before its form existed.** The first
  version called `form.setFieldsValue` from an effect while the modal content
  (`destroyOnClose`) was not mounted yet. Replaced with `initialValues` on a
  form that mounts with the dialog.
- **`clearToken` tripped `no-unused-vars`** through a rest-destructuring discard;
  rewritten as copy-and-delete.
- **My own patch script asserted on an import that does not exist** in `app.py`
  (`positioning`); the assertion caught it before anything was written.

## Corrections

- **`ui/src/api/openapi.json` was stale.** Regenerating it in-process from the
  current app added, besides this cycle's two routes and two schemas,
  `…/prompts/{tracked_id}/detail`, `PromptDetail` and five related schemas,
  `CostReport.attribution` / `unattributed_*`, and `AnswerSample.run_id` — all
  from backend cycles 0012 to 0014. Two mocks did not satisfy the real contract
  and were fixed (`handlers.ts` cost report defaults, `insights.ts` `run_id`).
  Earlier UI logs that describe the snapshot as current were wrong from cycle
  0012 onward. The running dev server on 8787 predates this cycle, so
  `npm run gen:types` against it would have written a *staler* file; the
  snapshot was dumped from `create_app(...).openapi()` instead.
- ui-0005 and ui-0006 had been written but never committed; they were committed
  as-is at the start of this cycle (two files needed `prettier --write`).

## Explicitly not done

- **No anonymous public read.** "Everyone" is everyone past the site login.
- **No restriction on who may create a project**, and the API still accepts a
  project without a credential. Only the UI defaults protection on.
- **Existing projects are not locked.** They show "Open to everyone" until
  someone protects them; the first person to do so becomes the owner.
- **No password reset, no removal of protection, no second owner, no roles.**
  One credential per project; recovery is `PROJECT_ADMIN_PASSWORD` or nothing.
- **Write controls are not greyed out for readers.** A reader can click Edit;
  the API refuses and the unlock dialog opens. Optimistic updates (starring a
  prompt) flash and roll back if the dialog is cancelled.
- **The `/legacy` page cannot unlock a project**; its write buttons fail with
  the 403 message on protected projects.
- **The throttle is per process and in memory**: a restart clears it, and it
  does not limit by IP.
- `app.py` is now 418 lines, over the 400-line target, because ruff wraps the
  nine decorated routes. Splitting the routes into routers is a separate change.
- Playwright `e2e` was not extended; it runs read-only against the operator's
  server, which still runs pre-cycle code until restarted.

## Gate output (verbatim; progress dots and unrelated coverage rows elided)

`scripts\verify.ps1`:

```
=== Format ===
204 files already formatted
PASSED: Format
=== Lint ===
All checks passed!
PASSED: Lint
=== Type check ===
Success: no issues found in 63 source files
PASSED: Type check
=== Tests ===
============================== warnings summary ===============================
=============================== tests coverage ================================
src\modules\control_plane\store.py                  134      3     14      0    98%   96-98
TOTAL                                              5778     92   1272     54    98%
36 files skipped due to complete coverage.
Required test coverage of 85.0% reached. Total coverage: 97.82%
743 passed, 2 warnings in 179.62s (0:02:59)
PASSED: Tests
ALL GATES PASSED.
```

`scripts\drift_check.py`:

```
--- Drift Audit Results ---
No documentation drift detected.
```

UI (`npm run typecheck`, `npm run lint`, `npm run build` clean; `npx vitest run`):

```
✓ src/pages/atlas/AtlasPage.test.tsx (3 tests) 22430ms
 ✓ src/pages/project/RunsPage.test.tsx (4 tests) 22961ms
 ✓ src/pages/project/PromptsPage.test.tsx (5 tests) 28863ms
 ✓ src/pages/trends/TrendsPage.test.tsx (3 tests) 18475ms
 ✓ src/pages/projects/ProjectsPage.test.tsx (5 tests) 20024ms
 ✓ src/pages/project/BattlegroundPage.test.tsx (5 tests) 16050ms
 ✓ src/pages/project/OverviewPage.test.tsx (4 tests) 12218ms
 ✓ src/app/AppShell.test.tsx (4 tests) 14138ms
 ✓ src/pages/project/ProjectLock.test.tsx (2 tests) 11169ms
 ✓ src/api/client.test.ts (8 tests) 112ms
 ✓ src/lib/trends.test.ts (3 tests) 22ms
 ✓ src/lib/atlas.test.ts (5 tests) 17ms
 ✓ src/lib/pages.test.ts (4 tests) 15ms
 ✓ src/lib/projectAuth.test.ts (4 tests) 14ms
 ✓ src/lib/matrix.test.ts (4 tests) 21ms
 ✓ src/lib/promptView.test.ts (3 tests) 13ms
 ✓ src/pages/costs/CostsPage.test.tsx (2 tests) 8037ms
 ✓ src/mocks/insights.test.ts (3 tests) 11ms
 Test Files  18 passed (18)
      Tests  71 passed (71)
```
