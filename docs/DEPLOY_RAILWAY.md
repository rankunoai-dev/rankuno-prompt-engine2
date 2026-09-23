# Deploying to Railway

One Railway service runs the FastAPI control plane and serves the built React UI
at the same origin. The image is built from the repo's `Dockerfile`; there is no
separate front-end deploy.

Read the **Posture** section once before the first deploy. It explains what a
public URL changes and what protects you.

## Posture: what a public URL means here

Until this cycle the app bound to `127.0.0.1`, and that was its entire access
control. On Railway it gets a public HTTPS domain, so:

- **Every route except `/api/health` requires HTTP Basic auth** —
  `CONTROL_PLANE_USER` / `CONTROL_PLANE_PASSWORD`. The browser prompts once and
  attaches the credential to every same-origin request, so the React UI works
  unchanged. In `ENVIRONMENT=production` the server **refuses to boot** without
  both values set.
- The image starts with `--approve-spend`, because that is what lets the UI's
  Run button work at all. On a public host that is safe only because of the
  credential above plus two spend ceilings: `MAX_SESSION_SPEND_USD` (per
  process) and `DAILY_SPEND_CAP_USD` (actual spend since 00:00 UTC, read back
  from the usage ledger on the volume, so **a restart does not re-arm it**).
- **The site login is shared; project credentials are not.** Everyone with the
  login above can *read* every project. Changing, running or deleting a project
  needs that project's **owner credential**, set in the UI when the project is
  created (ADR 0019). Projects that already exist on the volume have none and
  stay open until someone clicks *Protect* in the project header. Optionally set
  `PROJECT_ADMIN_PASSWORD` (16+ characters) as a recovery password for a lost
  owner password; leave it unset to disable the override.
- **One replica only.** Job state is in memory, the SQLite store is on one
  volume, and the spend ceilings are per process. `railway.json` pins
  `numReplicas: 1`; do not raise it.
- The image deliberately has no `--poll-minutes`. A poller fires its first
  cycle at boot — on every redeploy — and would spend before you looked.
  Scheduled runs are a later, explicit decision.

## Prerequisites

- The GitHub repo `rankunoai-dev/rankuno-prompt-engine2` with `main` pushed
  (Railway builds from the branch; `ui/dist` is gitignored and is built inside
  the image, so nothing needs committing besides source).
- A Railway account with a project.
- Vendor keys to hand: OpenAI, Perplexity, Gemini, SerpApi, Semrush. **Rotate
  any key that ever appeared in a script, chat log or screenshot before pasting
  it here** — the Phase-1 exposure in the project memory is still outstanding.

## Steps

### 1. Create the service from GitHub

Railway → *New Project* → *Deploy from GitHub repo* → pick the repo, branch
`main`. Railway detects `railway.json` and the `Dockerfile` automatically. The
first build will start immediately and **fail the health check** — that is
expected until the variables and volume below exist. Let it fail; do not wait.

### 2. Add a Volume

Service → *Settings* → *Volumes* → *Add Volume*. Mount path: **`/data`**.

Everything durable lives here: the SQLite store (all five tables sets), the
usage ledger inside it, run reports and the audit log. Without it, every
redeploy starts from an empty database.

### 3. Set the variables

Service → *Variables* → *Raw Editor*, paste and fill in:

```
ENVIRONMENT=production
HOST=0.0.0.0
CONTROL_PLANE_USER=<your username>
CONTROL_PLANE_PASSWORD=<a long random string — 32+ characters>
# optional recovery password for lost project owner passwords (16+ characters)
PROJECT_ADMIN_PASSWORD=
# optional: scores brand mentions after every crawl (ADR 0021). A few cents per
# crawl at Haiku prices; counted against the two spend caps like any vendor.
ANTHROPIC_API_KEY=

MAX_SESSION_SPEND_USD=5.0
DAILY_SPEND_CAP_USD=5.0
UNATTENDED_SPEND_CAP_USD=0

TRACKER_DB_PATH=/data/prompt_tracker.sqlite
REPORTS_DIR=/data/reports
AUDIT_LOG_PATH=/data/logs/audit.jsonl

OPENAI_API_KEY=
PERPLEXITY_API_KEY=
GEMINI_API_KEY=
SERP_API_KEY=
SEMRUSH_API_KEY=

LOG_LEVEL=INFO
LOG_FORMAT=json
```

Do **not** set `PORT` — Railway injects it and the app reads it. Pick the two
spend caps deliberately; they are the only thing between an authenticated
session and your vendor invoices. `.env.example` documents every other setting
and its default.

### 4. Generate a domain

Service → *Settings* → *Networking* → *Generate Domain*. Railway terminates
TLS; the app sees plain HTTP internally, which is fine.

### 5. Redeploy and watch the build

*Deployments* → *Redeploy*. In the build log you should see, in order:

1. the Node stage: `npm ci` then `vite build` ending in `✓ built in …s`
2. the Python stage: `pip install -e ".[ui]"` succeeding
3. the health check passing against `/api/health`

If step 1 fails on a TypeScript error, the UI tree on `main` does not type-check;
fix it there (`cd ui && npm run build` reproduces it locally). If step 3 fails
with `CONTROL_PLANE_USER and CONTROL_PLANE_PASSWORD are required`, step 3 above
was incomplete.

### 6. Verify

- `https://<domain>/api/health` → `{"status":"ok","active_jobs":0}` with **no**
  login prompt. If it returns 503 `degraded`, the volume is not mounted at
  `/data` or the path variables are wrong.
- `https://<domain>/` → the browser asks for the username and password once,
  then the Projects page loads.
- `https://<domain>/api/projects` in a private window → 401. If you ever see
  JSON here without a prompt, stop and check the variables.

### 7. Optional: seed the demo project

The volume starts empty. To load the demonstration project without touching a
vendor:

```
railway run python scripts/seed_demo_project.py
```

(from a checkout with the Railway CLI linked to the service). It writes through
the engine's own stores. The Costs page has a *Hide demo data* switch because
the seed also writes ledger rows tagged `source=demo`; the daily cap already
ignores those.

## Day-to-day

- **Deploys**: push to `main`. CI runs the same gate as `scripts/verify.ps1`;
  Railway builds independently of CI, so a red CI does not block a deploy —
  check CI before pushing.
- **What a restart resets**: the in-memory job queue and progress (the `runs`
  table keeps the durable record) and the *session* ceiling. **What it no
  longer resets**: the daily ceiling, which is recomputed from the ledger.
- **Rotating the password**: change the variable; Railway redeploys; browsers
  prompt again.
- **Spend**: the Costs page (scope *Everything*, period *Last 24 h*, *Hide demo
  data* on) is the number the daily cap is enforcing.
- **Backups**: the volume is the only copy of the store. Railway volumes can be
  snapshotted from the dashboard; do it before any migration cycle.

## Known limits on this platform

- Single replica, by design (above). Horizontal scaling would need a shared
  database, a shared job queue and a shared ledger.
- No inbound rate limiting beyond the bounded job queue (50 waiting → 429).
  Basic auth is the control; keep the password long.
- Run reports are written to `/data/reports` and referenced by path from the
  `runs` table; they survive restarts only because of the volume.
- The SerpApi and Semrush connectors send their keys as query-string
  parameters. Structured logs never include request URLs on the designed
  error path, but no redaction filter exists yet (see KNOWN_GAPS).
