# RankUno Prompt Engine

Real-time, API-only prompt intelligence for AEO/GEO work: harvest seed demand
from Semrush, generate and filter prompts, audit who each AI answer engine cites,
and track citation velocity over time.

Engines audited (what is *actually called*, not the consumer product it approximates):

| Engine label | What is called | Web-trigger definition |
| :-- | :-- | :-- |
| `GOOGLE_AI_OVERVIEW` | SerpApi `google` (+ `google_ai_overview` follow-up); sources from `references` and inline `snippet_links` | An AI Overview rendered |
| `CHATGPT_SEARCH` | OpenAI Responses API, `web_search` tool forced via `tool_choice` | A `web_search_call` item present |
| `PERPLEXITY` | Perplexity Agent API `/v1/responses`, `perplexity/sonar`, `web_search` forced | A `search_results` item with sources |
| `GEMINI` | Gemini `generateContent` + `google_search` grounding | `webSearchQueries` non-empty |

Seed demand comes from Semrush Analytics API v3 (`phrase_all`, `phrase_questions`,
`phrase_related`).

Google **organic rank** is tracked too, twice per prompt: for the conversational
prompt text (free, it comes from the same SerpApi call as the AI Overview) and for
the short seed keyword behind it (one extra SerpApi call per distinct keyword per
run, cached across prompts). Device is fixed by `SERP_DEVICE` so history is
comparable, and the market comes from the project's locale (country, language,
city; `SERP_GL`, `SERP_HL` and `SERP_LOCATION` are the fallback). A project's
locale is frozen once it has crawled and a second market is a second project
(ADR 0023); Gemini has no location field in its API and ignores it.

**Executive reports and alerts** (ADR 0024). A project exports a white-label PDF
for its client: cover, headline rates with their 95% intervals, a platform
scorecard, what changed, how the engines describe the brand, the recommended
actions and the exact URLs behind them. The executive summary is written by
`ANTHROPIC_REPORT_MODEL` from the computed numbers and then checked against
them — any sentence containing a figure the engine did not measure is replaced
by the deterministic wording — and with no key the whole narrative is
deterministic. Separately, a crawl that closes a consolidation window can send
a Slack or email alert; by default only a citation-rate drop whose 95%
intervals no longer overlap qualifies, and `ALERTS_MAX_PER_PROJECT_PER_DAY=0`
switches all sending off.

## Quick start (Windows)

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
# fill in .env (see .env.example) — never commit it
.\.venv\Scripts\python.exe -m src.modules.prompt_tracking `
    --lob "Procurement Software" --brand GEP --alias "GEP SMART" `
    --domain gep.com --competitor sap.com --competitor oracle.com `
    --keyword "procurement software" --keyword "source to pay software" `
    --landing-pages .\client-urls.txt --approve-spend
```

Outputs:

- `reports/master-prompts-<lob>-<timestamp>.csv` — the 20-prompt master sheet
  (10 branded + 10 non-branded), one "Cited / Rate / Best Rank" triple per engine,
  cited and competitor domains, mapped URL or `[CONTENT GAP: Need <Subtopic> Page]`,
  plus "Google Organic Rank / Ranking URL" for the prompt and for the keyword.
- `data/prompt_tracker.sqlite` — prompts, per-run citation snapshots, organic rank
  snapshots, run headers. `TimeSeriesDB.velocity()` and `organic_velocity()` give
  window-over-window change in citation rate, citation rank and organic position.
- `logs/audit.jsonl` — structured audit trail of every call, approval and spend.
- `docs/prompt-atlas.html` — static explorer for all of the above. Open it in a
  browser and load the CLI `--json` output, a master CSV, or the SQLite export
  from `scripts/export_dashboard.py` (prompts, snapshots and runs as one JSON file).
  When opened through the control plane (`/docs/prompt-atlas.html`) the data is
  built live from the database on every load, so it is never stale. Each engine
  cell shows both the **citation rate** (client linked as a source) and the
  **mention rate** (brand named in the answer text), with snippets in the drawer.

Useful flags: `--skip-engine-audit` (research only; Semrush still billed),
`--samples N` (max answers per engine per prompt, default 3), `--engine CHATGPT_SEARCH`
(restrict engines, repeatable), `--resolve-redirects` (follow Gemini's redirect
links under the SSRF/robots policy), `--no-keyword-rank` (skip the per-keyword
organic rank call), `--json`.

## Control plane UI (projects, platforms, intervals, prompt-level overrides)

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,ui]"
.\.venv\Scripts\python.exe -m src.modules.control_plane --approve-spend
# open http://127.0.0.1:8787/
```

Add `--poll-minutes 15` only when you want due work run unattended: the poller
fires its **first cycle at start-up**, so on a server that restarts on every
deploy it would spend within seconds of each boot. Host and port come from the
`HOST` / `PORT` settings (or `--host` / `--port`), so a container can bind
`0.0.0.0` on the platform's injected port. **To put it on the internet, read
[`docs/DEPLOY_RAILWAY.md`](docs/DEPLOY_RAILWAY.md) first** — it covers the HTTP
Basic credential every route now requires when set, the two spend ceilings, the
volume, and why it must stay at one replica.

**Who can change a project.** Everyone who can open the app can *read* every
project. When a project is created you set an **owner name and password** for it
(ticked by default in the form); after that only someone holding that credential
can edit it, change its prompts, run it, consolidate it, tick its action cards or
delete it. Readers see *Read-only · owner X* in the project header and an
*Unlock to edit* button; the first refused change opens the same unlock dialog and
then carries on. The credential is kept for the browser tab only. Projects created
before this existed, or created with the box unticked, show *Open to everyone* and
can be protected from the header. There is no password reset: set the optional
`PROJECT_ADMIN_PASSWORD` (16+ characters) if you want a recovery password that
unlocks any project. Design and limits: ADR 0019.

The control plane is a local web app (FastAPI + one HTML page) where you:

- **Create, edit and delete projects.** A project holds the client inputs (brand,
  aliases, domains, competitors and competitor names, LOB, seed keywords,
  landing pages), the **platforms to track**, the **default run interval**
  (daily, every 2 or 3 days, weekly, every 2 weeks, monthly, or any custom
  value such as `4d` or `10h`), sample counts, call caps and reuse window.
- **Upload or paste prompts** (one per line, optional `| keyword | subtopic`),
  or add them one at a time. Text is sent to the engines exactly as written.
- **Star the most important prompts** and give any prompt its **own interval,
  platforms and sample count**, or reset it to inherit the project's.
- **Run** everything due, everything, or just the selected prompts. Runs are
  queued to a background worker and the page shows a **live progress bar**
  (prompt × platform checks done, batches, paid engine calls, elapsed time,
  current phase), then a **completion toast**, a browser notification if the
  tab is in the background, and a flashing tab title. Refreshing the page
  re-attaches to the running job; the header shows how many runs are in
  progress across projects.
- **See results** per platform: whether the client was cited, at what rank and
  rate, whether it was mentioned and in which sentence, every citation link,
  competitor citations and organic ranks; browse queued/recent jobs and past
  runs.

Scheduling is derived from history: a prompt is due on a platform when its last
sample there is older than its effective interval. `--poll-minutes N` runs due
work for all enabled projects every N minutes in the background; scheduled runs
are approved by `UNATTENDED_SPEND_CAP_USD`, while `--approve-spend` treats every
run started from that server process (including UI clicks) as operator-approved.
API docs are served at `/docs`. `POST /api/projects/{id}/run` returns `202` with
a job; poll `GET /api/jobs/{job_id}` for progress (`checks_done/checks_total`,
`percent`, `phase`, `engine_calls`) and the final `outcome`.

## Captured in the same run: fan-out queries, claims, snippets, and insights

Every crawl now keeps everything the paid responses contain, with no extra
call: the full answer text per sample; the engine's own search queries
("query fan-out"); which sentence each cited source supports (claims); the
source passages and dates Perplexity returns; the client pages an engine read
but did not cite; and the SERP's People-Also-Ask, related searches and organic
titles/snippets. From that stored data the control plane computes, per
project: platform health verdicts, what changed since the previous
consolidation, ranked **action cards** with evidence and a prescription (eight
rule types: convert mention to citation, own the claim a competitor owns,
read-but-rejected page, AI Overview gap, earned placement, freshness, defend,
landing page), the fan-out map with client coverage, the claim ledger, the
engine trust profile, winning competitor pages, client pages, brand placement
and source freshness. Cards keep analyst status/owner/note and are scored
improved / unchanged / regressed after the next consolidation.
API: `GET /api/projects/{id}/insights[?consolidation_id]`,
`PUT /api/projects/{id}/actions/{action_id}`,
`GET /api/projects/{id}/samples?prompt_id=&engine=&run_id=&limit=`. See ADR 0014.

## One prompt, end to end

`GET /api/projects/{id}/prompts/{tracked_id}/detail` returns everything known
about a single prompt: each platform's snapshot series and 30-day velocity, both
organic series (prompt and keyword) with their own velocity, the runs that
sampled it, its consolidated position in every window, which rich-capture layers
its samples carry, and whether any landing page targets it.

Three things it is careful about, because the stored data is subtler than it
looks:

- **An empty cell says why.** `EngineStatus` is `has_data`, `asked_failed`,
  `never_asked` or `not_configured`. A failed call writes a snapshot row and no
  sample row, so failure is read from `failed_samples` — 41 prompt × platform
  pairs in the current store are Gemini billing failures, not gaps in coverage.
- **A minority citation is not an absence.** `client_cited` is a ≥50% majority
  verdict, so a prompt cited in one of three samples stores `false` while holding
  a real rank. `cited_samples`, `ok_samples` and `cited_in_minority` expose that.
- **Rates are displayed, not recomputed.** `client_citation_rate` excludes failed
  samples from its denominator and is rounded at write time.

**Editing a prompt's wording keeps its history.** `prompt_id` is minted from
`(lob, prompt_text)` once, at creation, and then frozen; renaming a project's
line of business no longer re-keys its prompts either. See ADR 0016.

**Every insight for one prompt:** `GET /api/projects/{id}/insights?prompt_id=`
computes the whole view — verdicts, changes, action cards, fan-out, claims,
trust profile, pages, placement, freshness — for a single prompt. The scope is
applied *before* the engine's project-wide caps, which is why it is a server
parameter: filtering the unscoped response on the client silently drops rows
for any prompt outside the top-N. `GET /api/costs?project_id=&prompt_id=` does
the same for spend, reporting that prompt's direct engine calls and, separately,
the harvest / keyword-rank / redirect spend shared across its runs. See ADR 0017
and `docs/UI_SCOPE_BRIEF.md` for how the analyst UI uses both.

## Positioning: consolidated over a window of crawls

The run interval says how often a project is crawled. A second, independent
setting, **consolidate positions after every N full crawls** (default 3), says
when the project's positions are recomputed from all N crawls together. Every
crawl keeps its own dated snapshots; every N-th crawl also stores a dated,
project-wide position set (per prompt × platform: cited and mentioned rates
over all samples, best and mean rank, rank distribution, domain share,
competitors, organic best/mean). Example: interval 2 days, N = 3 → a new
position set every 6 days from three crawls of samples. Runs restricted to
selected prompts are stored but do not count towards the window. The Results
tab shows **Consolidated positioning** by default and **Point-in-time** on
demand, with a history of consolidations and a **Consolidate now** button
that accepts a custom window. API: `POST /api/projects/{id}/consolidate`,
`GET /api/projects/{id}/positions[?consolidation_id=]`,
`GET /api/projects/{id}/crawls`. See ADR 0013.

Every consolidated citation rate and mention rate carries a **95% confidence band**
(Wilson interval over the samples behind it, ADR 0020). The UI shows it as
"cited 67%, likely 35–88%": nine samples give a wide band, and that band is the
honest answer to "did we move?" until more samples narrow it.

**How engines describe you (sentiment and mention context).** With
`ANTHROPIC_API_KEY` set, every crawl ends with a judging step: each sentence that
names the client or a competitor is scored by Claude Haiku (temperature 0, fixed
rubric, JSON schema) for polarity and up to three attributes, per target entity,
and stored with the model id and rubric version. Identical sentences are scored
once. The Overview shows the negative share per platform with its band, the
attributes engines attach to the brand, and the worst sentence with its source; a
`negative_claim` action card carries the quote and the URL behind it. Separately,
and without any vendor, every mention is placed in context: list item, table row or
prose; early in the answer or not; sourced via which domain and what kind of site;
and how many other items sit in the same list. See ADR 0021 for the edge cases
(injection, entity ambiguity, rubric drift, caps) and what is deliberately left out.

## Cost tracking (usage ledger)

Every outbound vendor request is recorded in the `api_calls` table of the
tracker database: vendor, operation, model, source (`cli`, `control_plane`,
`live_check`, `pipeline`, `backfill`), run id, prompt id, engine, status,
latency, tokens in/out/cached, web-search invocations, Semrush units, the
configured estimate (`COST_*`), the vendor-reported cost when the API returns
one (Perplexity does) and a list-price model otherwise (`src/integrations/pricing.py`).

```powershell
.\.venv\Scripts\python.exe -m src.modules.prompt_tracking costs            # all time
.\.venv\Scripts\python.exe -m src.modules.prompt_tracking costs --days 7   # window
.\.venv\Scripts\python.exe -m src.modules.prompt_tracking costs --json     # machine-readable
```

The report shows volume and spend per vendor, per run and per source, and
proposes a value for each `COST_*` setting from the observed mean per
successful call (× 1.15 safety margin), stating whether that mean is
vendor-reported, modelled or only the estimate. The control plane shows the
same under the **Costs** tab (`GET /api/costs?project_id=&days=`). Copy the
suggested values into `.env` to make the spend ceiling reservation track real
prices. See ADR 0012.

## Links, mentions and the UI dataset

Prompts are sent to every engine **exactly as written**. The client profile
(brand, aliases, domains, competitors) is never injected into a prompt; it is
applied only after the answer comes back, to read the result. For every prompt
and engine the run keeps:

- **Citation links** — every cited URL with its position, title and domain
  (OpenAI `url_citation` annotations, Perplexity `citations`, Gemini grounding
  sources, Google AI Overview references), plus the client's own cited URLs and
  the URLs the engine consulted but did not cite.
- **Mentions** — whether the answer *names* the client (brand or any alias, exact
  and word-bounded), the mention rate across samples, and the sentence(s) where
  it happens; competitor mentions are counted the same way (`--competitor-name`
  or, by default, the first label of each competitor domain).
- **Organic ranks**, competitor citations, model string and response ids.

Each run writes two files next to each other: the CSV master sheet and a
UI-ready JSON dataset (`reports/master-prompts-<lob>-<stamp>.json`) with one
flat row per prompt × engine — `prompt_text`, `client`, `engine`,
`web_triggered`, `client_cited`, `client_rank`, `mention_detected`,
`mention_snippet`, `citation_links[]`, `consulted_urls[]`,
`competitor_citations`, `competitor_mentions`, organic ranks and more. The same
fields are stored per raw answer in the `answer_samples` table.

## Feed your own prompts

```powershell
.\.venv\Scripts\python.exe -m src.modules.prompt_tracking run `
    --lob "Procurement Software" --brand GEP --domain gep.com --keyword "procurement software" `
    --prompt "What is the best procurement software for enterprise manufacturers?" `
    --prompts-file .\docs\examples\prompts.example.txt --approve-spend
```

Custom prompts are always tracked (the intent gate is advisory for them). With
custom prompts supplied, Semrush harvesting and the generated 10+10 set are
skipped unless you add `--also-generate`. A prompts file has one prompt per
line with optional `| keyword | subtopic` fields; see
`docs/examples/prompts.example.txt`.

## Schedule it (daily, weekly, monthly, or any interval)

Describe jobs in a JSON file (`docs/examples/jobs.example.json`): client, custom
prompts, engines, samples, and an `interval` of `hourly`, `daily`, `weekly`,
`monthly`, or `15min`, `6h`, `3d`, `2w`, `1mo`.

```powershell
# run whatever is due (call this hourly from Task Scheduler / cron)
.\.venv\Scripts\python.exe -m src.modules.prompt_tracking schedule run-due --jobs jobs.json
# or keep a process polling
.\.venv\Scripts\python.exe -m src.modules.prompt_tracking schedule daemon --jobs jobs.json --poll-minutes 15
# last/next run per job
.\.venv\Scripts\python.exe -m src.modules.prompt_tracking schedule status --jobs jobs.json
# print (never run) the Task Scheduler registration command
.\.venv\Scripts\python.exe -m src.modules.prompt_tracking schedule install-task --jobs jobs.json
```

Scheduled runs need `UNATTENDED_SPEND_CAP_USD > 0` in `.env` so they are
pre-approved by budget. Job state (last run, status, next due) lives in the
SQLite store, so `run-due` is safe to invoke repeatedly.

## Minimising API calls without losing reliability

- **Adaptive sampling** (`ADAPTIVE_SAMPLING`, `MIN_SAMPLES`): take at least two
  samples per engine and stop once they agree on whether the client was cited;
  spend the full count only on genuinely unstable prompts.
- **Snapshot reuse** (`REUSE_WITHIN_HOURS` / `--reuse-hours`): a prompt/engine
  measured recently is copied, not re-bought. A crashed run re-runs for free.
- **Per-run call cap** (`MAX_ENGINE_CALLS_PER_RUN` / `--max-calls`): a hard stop
  independent of the dollar ceiling.
- **Circuit breaker** (`CIRCUIT_FAILURE_THRESHOLD`, `CIRCUIT_COOLDOWN_S`): a
  vendor that keeps failing is skipped at no cost until its cooldown passes,
  instead of retrying across every prompt.
- **Keyword rank cache**: one SerpApi call per distinct seed keyword per run.
- **Parallel engine calls** (`PIPELINE_MAX_WORKERS` / `--workers`): a bounded
  thread pool; 240 calls finish in minutes rather than half an hour.
- **Model-shift detection**: every answer is stored with its model string and
  response id; the run reports `MODEL SHIFT: GEMINI: a -> b` when a vendor's
  model changed since the previous run, so a citation drop can be attributed.

## Inbound crawler logs: the fetch → consulted → cited funnel

The tracker records what the engines cited and, for ChatGPT, what they read
and rejected. Upload the client's web-server access log and it also records
what the AI crawlers **fetched**: OAI-SearchBot, ChatGPT-User, PerplexityBot,
Perplexity-User, GPTBot, ClaudeBot, Googlebot, Applebot and the rest of the
catalogue at `GET /api/crawler-logs/bots`.

```
POST /api/projects/{id}/crawler-logs/import        # owner credential required
  Content-Type: application/json   {"text": "<log>", "note": "Sept nginx"}   ≤ 2 MB
  Content-Type: text/plain | application/x-ndjson | application/gzip   ≤ 50 MB decompressed
GET  /api/projects/{id}/crawler-logs?days=30        # by bot, per day, per page, imports
DELETE /api/projects/{id}/crawler-logs/imports/{import_id}
```

Formats: nginx and Apache `combined` (with or without a leading virtual host or
an `X-Forwarded-For` list), and Cloudflare Logpush NDJSON or arrays. Times in
any offset become UTC days. Per page the view joins the fetches to the
citations and consulted URLs of the project's prompts on one canonical URL key
(no scheme, `www.`, port, query, fragment, trailing slash or index file), so
`https://www.gep.com/software/gep-smart/?utm_source=openai` and a log line for
`/software/gep-smart` are the same page. The join yields three buckets per
page: fetched and cited, fetched and never cited, fetched but blocked or
redirected. When a search or live-fetch crawler fetched a page three or more
times and its engine never cited it, the Actions tab gets a `fetched_not_cited`
card with the prescription.

What is stored is per-day counts per crawler and page. Never IP addresses,
request lines, referers, query strings or user agents; paths that look like
tokens or e-mail addresses are dropped; timestamps of user-triggered fetches
are rounded to the minute; rows older than `CRAWLER_LOG_RETENTION_DAYS` are
purged. An IP address is used once, to check the crawler against the vendor's
published ranges bundled in `src/modules/crawler_logs/ranges.json`, and the
result is a count of `verified_hits` (refresh with
`scripts\refresh_bot_ranges.py`). The import response says all of this in one
sentence, for the client who asks. Design: ADR 0022.

## Demonstration data

`.\.venv\Scripts\python.exe scripts\seed_demo_project.py --reset` seeds a
project "GEP demo - full capability": 20 prompts, 30 crawls at a two-day
interval over two months, consolidations every three crawls, full samples,
organic ranks, ledger rows and action states, all invented and written through
the engine's own stores. No vendor is called. The project notes say it is a
demonstration.

## Spend and approval

The pipeline is `RiskClass.FINANCIAL`. It runs only when one of these holds:

1. `--approve-spend` — the operator approves this run on the command line.
2. `UNATTENDED_SPEND_CAP_USD > 0` — scheduled runs pre-approved by budget
   (`BudgetedApprovalProvider`).

Either way every engine call — and, since cycle 0015, every Semrush report —
is charged to the `CostLedger` *before* it is made. Two ceilings apply:
`MAX_SESSION_SPEND_USD` for this process, and `DAILY_SPEND_CAP_USD` for actual
spend since 00:00 UTC, read back from the usage ledger so that restarting the
server does not re-arm the budget. Hitting either stops the audit cleanly;
everything collected so far is already in the database.

## Configuration

All settings are environment variables read once through `get_settings()`.
`.env.example` lists every key with its default. Credentials are `SecretStr`
and never appear in logs. `SERP_API_KEY` and `SERPAPI_KEY` are both accepted.

## Repository layout

```
src/core/          governed pipeline, schemas, config, logging, guardrails,
                   rate limiting, retry, url_safety (SSRF), robots, domains
src/integrations/  BaseAPIClient + connectors: semrush, openai_search, perplexity,
                   gemini_search, serp_api, url_resolver; shared http helpers
src/modules/prompt_tracking/
                   schemas, inputs, intent_filter, prompt_generator, selector,
                   citations, mentions, organic, audit, assembly, url_mapper,
                   time_series_db, report, pipeline, scheduler, __main__ (CLI)
src/modules/control_plane/
                   schemas, store, planner, runner, app (FastAPI), static/index.html,
                   crawler_routes, crawler_cards, __main__ (server)
src/modules/crawler_logs/
                   bots (catalogue), normalise (url_key), ranges (+ ranges.json),
                   parser, ingest, store, funnel, schemas
tests/             mirrors src/ package-for-package; all network mocked
docs/              ARCHITECTURE.md, KNOWN_GAPS.md, adr/, build-log/, standards/
```

Dependency direction is `modules -> integrations -> core`, never outward.

## Development

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify.ps1        # full gate
powershell -ExecutionPolicy Bypass -File .\scripts\verify.ps1 -Fix   # autofix then gate
.\.venv\Scripts\python.exe scripts\drift_check.py                     # docs drift audit
```

The gate is ruff format, ruff check, mypy `--strict`, pytest with an 85% coverage
floor. No task is complete until it exits zero. See `.agents/rules/` for the
binding coding standards and the 8-step SDLC protocol, and
`PROMPT_TRACKER_MASTER_BLUEPRINT.md` for the specification this implements.
