# ADR 0018 — Posture for a public deployment

**Date**: 2026-09-21
**Status**: Accepted
**Amends**: ADR 0009 (control-plane UI: "bind to 127.0.0.1 only")

## Context

The operator asked whether the project could be deployed to Railway. Three
audits — backend, frontend, and the exposure a public URL creates — said no,
for reasons that were structural rather than cosmetic:

- The server bound `127.0.0.1` and nothing read the platform's `$PORT`. That
  loopback bind was also **the only access control the application had**: no
  route carried a dependency, there was no middleware, and `--approve-spend` —
  required for the UI's Run button to do anything — is literally `return True`
  for whoever sends the request. On a public domain, anyone could enumerate
  every client profile, read every captured answer, hard-delete a project, or
  spend against the vendor keys.
- The spend ceiling was an in-memory float. Railway restarts on deploy, crash
  and out-of-memory, and each restart re-armed the full budget. Worse, Semrush
  spend was charged to that ledger *after* the whole harvest loop, on a client
  constructed fresh per run, so it never met the ceiling at all.
- There was no build manifest: `fastapi`/`uvicorn` were an optional extra,
  `ui/dist` is gitignored and needs Node, and `config.py` derives `REPO_ROOT`
  from its own `__file__`, so a plain `pip install .` would have relocated the
  database into site-packages.

The frontend source needed no change: relative `/api` paths, no `localhost`,
MSW proven tree-shaken out of `dist`, SPA fallback already in the backend.

## Decision

**1. HTTP Basic auth on every request except `/api/health`**, as a pure-ASGI
middleware (`control_plane/auth.py`) so the served UI, `/assets`, `/docs` and
the atlas-data routes are covered, not only the JSON routes. Constant-time
comparison on both halves. `ENVIRONMENT=production` **refuses to boot** without
`CONTROL_PLANE_USER` and `CONTROL_PLANE_PASSWORD` (`config.model_post_init`).
Basic was chosen over a bearer token with a login page because the browser
attaches cached Basic credentials to same-origin `fetch`, so the React UI —
owned by a parallel session that is live in those files — needs no edit.

**2. Two spend ceilings.** `MAX_SESSION_SPEND_USD` stays per process.
`DAILY_SPEND_CAP_USD` is enforced by `CostLedger` against a caller-supplied
`spent_today()` — `UsageLedger.spent_since(00:00 UTC)`, actual cost with the
vendor → modelled → estimate precedence, demo rows excluded. The provider is
read once per UTC day and this process's reservations are added on top, so
completed calls are not double-counted. `core` still imports nothing from
`integrations`; `__main__` does the wiring.

**3. Semrush is reserved before each report, at its upper bound**, and the
unused part of the reservation is released once the real bill is known
(`CostLedger.release`). A refused reservation ends the harvest with a warning.

**4. `HOST` and `PORT` are settings**, so the platform's `PORT` is honoured
without breaking the "no `os.environ` outside `config.py`" rule. CLI flags
override them.

**5. One image, editable install.** A multi-stage `Dockerfile` builds `ui/dist`
with Node and installs the package with `pip install -e ".[ui]"` so `REPO_ROOT`
resolves under `/app`. `railway.json` pins one replica, the health check path
and an on-failure restart policy. `.dockerignore` keeps `.env`, `data/`,
`logs/`, `reports/` and both `node_modules` out of the image.

**6. The health check probes the store.** `/api/health` runs one statement
against the tracker database and answers 503 `degraded` if it fails, so an
unmounted volume fails the platform check instead of passing it.

**7. Bounded job queue.** Fifty waiting jobs; the fifty-first is a 429 with
`Retry-After`. Queued jobs were never trimmed before.

**8. WAL and a 30-second busy timeout on every SQLite connection**, via one
helper (`core/sqlite.py`), replacing five inconsistent `connect` calls two of
which had the 5-second default on the hottest writers.

## Consequences

- Single replica is a hard constraint and is documented as such. Scaling out
  would need a shared database, queue and ledger.
- `--approve-spend` remains in the image `CMD`; without it the UI cannot run
  anything. The credential and the two ceilings are the guards. No
  `--poll-minutes` in the image: a poller fires its first cycle at boot.
- `.env.example` gains a "Railway" block with **absolute** volume paths; the
  relative defaults resolve against the working directory.
- `usage_context(key=None)` clearing semantics (ADR 0017) and the new
  `CostLedger.release` are both load-bearing for the Semrush change.
- Not done here, recorded in KNOWN_GAPS: inbound per-IP rate limiting;
  redaction of query-string keys in the structured logger; soft delete for
  projects; `BudgetedApprovalProvider` comparing against the pipeline's $0.01
  nominal rather than its projected spend.
- Local behaviour is unchanged: no credentials → open on loopback, with a
  warning logged at every start.
