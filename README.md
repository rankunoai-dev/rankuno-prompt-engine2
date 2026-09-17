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
run, cached across prompts). Locale and device are fixed by `SERP_GL`, `SERP_HL`,
`SERP_LOCATION` and `SERP_DEVICE` so history is comparable.

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
.\.venv\Scripts\python.exe -m src.modules.control_plane --approve-spend --poll-minutes 15
# open http://127.0.0.1:8787/
```

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
`GET /api/projects/{id}/samples?prompt_id=&engine=&run_id=`. See ADR 0014.

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

## Spend and approval

The pipeline is `RiskClass.FINANCIAL`. It runs only when one of these holds:

1. `--approve-spend` — the operator approves this run on the command line.
2. `UNATTENDED_SPEND_CAP_USD > 0` — scheduled runs pre-approved by budget
   (`BudgetedApprovalProvider`).

Either way every engine call is charged to the `CostLedger` *before* it is made,
and `MAX_SESSION_SPEND_USD` is the hard ceiling. Hitting it stops the audit
cleanly; everything collected so far is already in the database.

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
                   __main__ (server)
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
