# ADR 0019 — Per-project owner credentials: everyone reads, only the holder writes

**Date**: 2026-09-21
**Status**: Accepted
**Builds on**: ADR 0018 (site-wide Basic auth for a public deployment)

## Context

ADR 0018 put one Basic credential in front of the whole app. On a shared
deployment everyone on the team holds that credential, so it answers "may this
browser reach the app" and nothing more. Anyone who could open the app could
also edit, run (spend against the vendor keys), consolidate, or hard-delete
**any** project, including ones they had nothing to do with.

The operator asked for credentials set per project at creation time, such that
everyone keeps read access and only the person holding the project's
credential can edit or write.

## Decision

**1. An owner credential per project, set at creation.** `ProjectCreate` takes
an optional `credentials: {owner, password}`. The owner name is public (it is
shown beside the lock); the password is 8 to 128 characters.

**2. The password is never stored.** `credentials.py` derives a salted scrypt
digest (N=2^14, r=8, p=1, 32-byte key, 16-byte random salt) with the standard
library, so there is no new dependency. The cost parameters are stored beside
each digest so they can be raised later without invalidating old rows.

**3. The digest lives in its own table, `project_credentials`.** It is not part
of the project payload, and no API model reads it, so it cannot leak through
`GET /api/projects`, the export route, or a future field added to `Project`.
`Project.protected` and `Project.owner` are **derived by a join on read** and
excluded from the stored payload: a payload cannot claim a project is open.

**4. Reads need nothing. Every mutating route under `/api/projects/{id}` needs
the credential**: update, delete, prompt add/import/update/delete, `/run`,
`/consolidate`, action updates, and credential rotation. The guard is one
FastAPI dependency (`ProjectAccessGuard.require_write`) attached to each route;
`test_every_write_is_refused_without_the_credential_and_changes_nothing` walks
all ten, and the patch script asserted that no POST/PUT/DELETE project route is
left unguarded.

**5. It travels in its own header**, `X-Project-Authorization: Basic
<base64(owner:password)>`, because `Authorization` already carries the site
credential. The two are independent: the site login does not unlock a project,
and a project credential does not get past the site login.

**6. A refusal is 403, never 401.** A 401 makes browsers discard the cached
site credential and prompt again, which would log a reader out of the whole
app for clicking Edit. The body carries a machine-readable `code`
(`project_locked` when nothing was presented, `project_credentials_invalid`
when something wrong was) and the public `owner`.

**7. Wrong guesses are throttled per project**: 8 failures in 5 minutes, then
429 with `Retry-After`, including for a correct credential and for the
`/access` probe, so neither is a free oracle. The counter is in memory, which
is sufficient because ADR 0018 already pins the app to one replica. scrypt
itself costs roughly 50 ms per attempt. Throttling one project never affects
another, and never affects reads.

**8. Projects without a credential stay open.** Stores that predate this cycle
have none, and locking them retroactively would lock everyone out. An open
project can be claimed from its header by anyone who can already write to it
(which, for an open project, is everyone). The UI labels such projects "Open to
everyone".

**9. Optional recovery password.** `PROJECT_ADMIN_PASSWORD` (16+ characters,
refused at boot if shorter; blank means disabled) is accepted as the password
half for any project. It exists because there is no password reset and the
alternative for a lost password is editing SQLite by hand. It is off by
default.

**10. The UI keeps the credential in `sessionStorage`**, per project, for the
tab's lifetime: it survives a reload and is gone when the tab closes, and it is
never written to `localStorage` on a shared machine. It is attached only to
writes and to the `/access` probe. When the API refuses a write, the HTTP
client opens one unlock dialog (shared by concurrent refusals), probes the
typed credential against `/access` before storing it, and retries the original
write once. A stored credential that is refused is dropped as stale.

## Assumptions made without the operator present

- **"Everyone" means everyone who can already reach the app.** The site login
  of ADR 0018 is unchanged. This cycle does **not** open a public deployment to
  anonymous readers; doing so would expose every client profile and captured
  answer to the internet and is a separate decision.
- **Anyone who can reach the app may still create a project** (and so spend
  within the ceilings of ADR 0018 on a project they own). The request was about
  tampering with other people's projects, not about who may start one.
- **The credential is optional at the API and defaulted on in the UI.** The
  create form ticks "Protect it with an owner credential" and warns when it is
  unticked. A local single-user install is not forced to invent a password.

## Consequences

- The hand-written page at `/legacy` sends no project header, so on a protected
  project its edit, run and delete buttons now fail with the 403 message. It
  still reads everything. The React UI is the supported client.
- Unattended runs (`--poll-minutes`) do not pass through HTTP and are
  unaffected: the scheduler is the operator, not a visitor.
- This is access control between cooperating colleagues behind a shared login,
  not multi-tenant isolation: every reader still sees every project's data.
- Over plain HTTP on loopback the header is visible to the local machine only;
  on Railway TLS terminates at the platform edge, as for the site credential.

## Step 5 audit

1. **Hosts and volume**: none. No outbound call is added.
2. **Rate limits**: no vendor quota involved. Inbound unlock attempts are
   limited per project as above.
3. **Spend**: none added. The change *removes* a spend path: `/run` on someone
   else's protected project is now refused.
4. **Idempotency**: credential set/rotate is an upsert keyed by project id;
   repeating it yields the same state.
5. **Circuit breaker**: not applicable, no upstream.
6. **PII**: the owner name is stored and shown; operators should use a handle
   if a real name is unwanted. Passwords are not stored or logged. Logs carry
   the project id, the path and whether a header was presented, never its value.
7. **Input validation**: `ProjectCredentials` is a `StrictModel`; the owner
   refuses a colon (it would split the Basic pair) and leading whitespace. A
   malformed header is a refusal, not an exception. An unreadable stored scheme
   locks rather than crashes.
8. **Secrets**: `PROJECT_ADMIN_PASSWORD` is a `SecretStr` read through
   `get_settings()`. Nothing is committed.
