# Cycle 0015 — Railway deployment readiness

**Date**: 2026-09-21
**Operator request**: "tell me is this project ready for deployment on the
railway and its frontend just analyze the whole code and if not lets implement
the deployment process one by one"

## Verdict before the cycle: not ready

Three read-only audits (backend, frontend, public-exposure) found six blockers.
The frontend source itself was clean — relative `/api` paths, no `localhost`,
MSW tree-shaken out of `dist`, SPA fallback already present — so **no `ui/src`
file changed in this cycle**. Everything below is backend and packaging.

| # | Blocker | Closed by |
|---|---|---|
| 1 | Bound `127.0.0.1`; nothing read `$PORT` | `HOST`/`PORT` settings; CLI flags override |
| 2 | No authentication on any route | `BasicAuthMiddleware`, everything but `/api/health` |
| 3 | `--approve-spend` approves any HTTP request | credential + two ceilings make it operator-only |
| 4 | Spend ceiling re-armed on every restart; Semrush bypassed it entirely | `DAILY_SPEND_CAP_USD` read from the ledger; Semrush reserved per report |
| 5 | No build manifest; web stack an optional extra; `ui/dist` gitignored | `Dockerfile` (Node + Python), `railway.json`, `.dockerignore` |
| 6 | `REPO_ROOT` from `__file__` relocated data under `pip install .` | editable install in the image |

## Research findings

- The loopback bind was **the entire security model**: zero `Depends`, zero
  middleware, `/docs` and `/openapi.json` public, and both atlas-data routes
  dump every client's prompts and snapshots with no id required.
- `--approve-spend` wires `CallbackApprovalProvider` around a closure that logs
  and returns `True`; the "operator_ui_approval" log line is that closure, not
  evidence of a human. Without the flag every run is refused — silently, as a
  `FINISHED` job whose `outcome.statuses` says `blocked_pending_approval`.
- `MAX_SESSION_SPEND_USD` was an in-memory float built once per process. The
  usage ledger recorded every call but nothing compared it to a ceiling.
- `_harvest` charged Semrush *after* the loop, on a client constructed fresh
  per run: the vendor billed before the ceiling saw a cent, bounded only by
  `SEMRUSH_MAX_UNITS_PER_RUN` per run.
- `PUT /api/projects/{id}` is the spend amplifier: all four engines, ten
  samples, premium models, `reuse_within_hours: 0`, `generate_prompts: true`.
- Job queue was unbounded and queued jobs were never trimmed.
- `/api/health` never touched the database — it would report `ok` on an
  unmounted volume.
- README's own recommended command included `--poll-minutes 15`; the poller
  fires its first cycle at boot, i.e. on every redeploy.
- Repo already on GitHub with `.env`, `data/`, `logs/`, `reports/`, `ui/dist`
  ignored; 20+ files from cycles 0012–0014 uncommitted, so `origin/main` did
  not match the disk.
- No Railway CLI or Docker on this machine: the Dockerfile is validated by
  Railway's own build; everything else was verified locally.

## What was built

- `core/config.py`: `host`, `port`, `control_plane_user`, `control_plane_password`,
  `daily_spend_cap_usd`; `basic_auth_configured`; production boot refuses
  without credentials; `require()` message names environment variables.
- `control_plane/auth.py` (new): pure-ASGI `BasicAuthMiddleware`,
  `credentials_match` with constant-time comparison, `/api/health` exempt.
- `control_plane/app.py`: middleware wired from settings; `QueueFull` → 429
  with `Retry-After`; health probes the store and answers 503 `degraded`.
- `control_plane/__main__.py`: host/port from settings; `CostLedger` built with
  the daily ceiling and `UsageLedger.spent_since(00:00 UTC)`; warning logged
  on every unauthenticated start.
- `core/rate_limiter.py`: `CostLedger(daily_ceiling_usd=, spent_today=, today=)`,
  baseline read once per UTC day plus this process's reservations;
  `release()` for unused reservations; `remaining_usd` is the tighter ceiling.
- `integrations/usage.py`: `spent_since()` in SQL with the vendor → modelled →
  estimate precedence, demo rows excluded.
- `prompt_tracking/pipeline.py`: Semrush reserved before each report at its
  upper bound; refused reservation ends the harvest with a warning; unused
  reservation released after.
- `control_plane/jobs.py`: `max_queued=50`, `QueueFull`.
- `core/sqlite.py` (new): one `connect()` — WAL, 30 s busy timeout, foreign
  keys — used by all five stores. `TimeSeriesDB.ping()`.
- `Dockerfile` (multi-stage), `.dockerignore`, `railway.json`.
- `.env.example`: new settings and a Railway block with absolute volume paths.
- Tests: `test_deploy_readiness.py` (11) — credential shape, every route but
  health guarded, open without credentials, production refuses, `HOST`/`PORT`
  from env with the no-`os.environ` rule re-asserted over `src/`, the restart
  case for the daily cap, midnight rollover and `release`, inert without a
  provider, `spent_since` precedence and demo exclusion, queue → 429, health
  → 503 on a vanished store. `test_parser_defaults` inverted.
- Docs: `docs/DEPLOY_RAILWAY.md` (runbook), ADR 0018, KNOWN_GAPS (auth line
  corrected; four new gaps), README (deploy pointer; poller warning; spend
  section), ARCHITECTURE (serving section; storage pragmas).

## Bugs found and fixed

- Semrush spend escaped the ceiling (above). Pre-charging at the upper bound
  over-reserved by 480 units in the pipeline tests; `CostLedger.release()`
  hands the difference back once the real bill is known, so spend figures are
  exact and the tests hold unchanged.
- Two of five SQLite stores had the 5-second default busy timeout — the two
  hottest writers.

## Local verification

- `cd ui && npm run build` → `✓ built in 14.45s` (this is the Dockerfile's
  first stage; `tsc` strict passed on the UI session's current tree).
- Server started the way Railway will, on a **scratch database** with the repo
  `.env` neutralised: `HOST=0.0.0.0 PORT=8790 ENVIRONMENT=production` plus a
  credential. Bound `0.0.0.0:8790`. Probes: `/api/health` 200 with no
  credential; `/api/projects`, `/`, `/openapi.json`, `/assets/*` → 401 with
  `WWW-Authenticate: Basic realm="Prompt Engine"`; wrong password → 401; right
  credential → 200 and the React `index.html` at `/` and on a deep link.
- Same start without the credential → `ConfigurationError:
  CONTROL_PLANE_USER and CONTROL_PLANE_PASSWORD are required in production.`,
  nothing listening.
- No vendor call, no live run, no touch of `data/prompt_tracker.sqlite`.

## Explicitly not done

- No `ui/src` edits. No Railway dashboard actions (no CLI here; the runbook is
  the handoff). No Docker build locally (not installed; Railway validates it).
- No per-IP inbound rate limiting, no log redaction of query-string keys, no
  soft delete, no fix for `BudgetedApprovalProvider`'s nominal-cost comparison
  — all recorded in KNOWN_GAPS.
- Multi-replica support: out of scope by design.

## Step 5 audit answers (delta from cycle 0014)

1–3. No new vendors or endpoints that spend; the cycle *reduces* spend paths
(Semrush pre-charge, daily cap). 4. WAL is a per-file persistent setting
applied on open; no schema change. 5. Auth and the health probe are read-side;
a refused credential never reaches a handler. 6. Credentials are `SecretStr`;
the middleware receives the plain value once at construction and never logs
it (`auth_rejected` logs path and whether a header was presented, nothing
else). 7. New settings are typed and bounded (`port` 1–65535). 8. The
production boot check is the new hard gate; `.dockerignore` keeps `.env` and
the database out of the image.

## Gate output (verbatim, abridged to changed modules; 100%-covered files omitted by `skip_covered`)

```
=== Format ===  PASSED
=== Lint ===    All checks passed!  PASSED
=== Type check === Success: no issues found in 61 source files  PASSED
=== Tests ===
src\core\rate_limiter.py                    147      6     38      3    95%   81-82, 137->142, 224, 310-312
src\integrations\usage.py                   153      3     24      0    98%   171-173
src\modules\control_plane\app.py            191      6     12      2    96%   130-131, 137->140, 142, 153-154, 382
src\modules\prompt_tracking\pipeline.py     283      7     78      4    97%   277, 416-417, 464-465, 493, 495
TOTAL                                      5548     93   1244     57    98%
Required test coverage of 85.0% reached. Total coverage: 97.75%
722 passed, 2 warnings in 127.49s (0:02:07)
ALL GATES PASSED.
```

Drift audit: `scripts/drift_check.py` → "No documentation drift detected."

