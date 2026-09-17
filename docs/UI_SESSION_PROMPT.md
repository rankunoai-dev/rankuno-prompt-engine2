# Prompt for the UI build session (copy everything below the line)

---

You are building the analyst front end for the RankUno Prompt Engine, an
AEO/GEO visibility tracker. Work in `C:\Users\RankUno\Desktop\prompt-engine`
(the backend repo). The UI lives in a new folder `ui/`. Another Claude Code
session owns the Python backend in the same repo; do not edit anything under
`src/`, `tests/`, `scripts/` or `docs/adr/` except where this prompt says so.

## Read first, in this order

1. `docs/UI_BUILD_BRIEF.md` (binding: product idea, data facts, page spec,
   API contract, action-card rules).
2. `docs/ARCHITECTURE.md` and `docs/adr/0009`, `0010`, `0011`, `0012`, `0013`.
3. `src/modules/control_plane/app.py` (every live route) and
   `src/modules/control_plane/schemas.py` (every response shape).
4. `src/modules/control_plane/static/index.html` and `docs/prompt-atlas.html`:
   the two hand-written HTML dashboards you are replacing. Every feature in
   them must exist in the new UI (parity list below).
5. `http://127.0.0.1:8787/openapi.json` for exact schemas. Start the server
   if it is not running:
   `.\.venv\Scripts\python.exe -m src.modules.control_plane --approve-spend`
   (port 8787). **Never click Run or POST `/api/projects/{id}/run` on a real
   project: it spends vendor money.** Use the stored data of the existing
   project and MSW mocks for everything else.

## What this product is for

Analysts track, per client and per prompt, whether AI answer engines (Google
AI Overview, ChatGPT Search, Perplexity, Gemini) link to the client, name it in
the text, at what rank, against which competitors, and how that moves over
consolidated windows of crawls. Measurement exists. The UI's job is to turn it
into action: every first screen shows verdicts, changes and the top actions;
raw tables and charts live one click deeper. The analyst must never have to
"mug up" data to know what to do.

## Stack (fixed, do not substitute)

- React 18, TypeScript strict, Vite, folder `ui/`.
- Ant Design v5 + `@ant-design/icons`; `ConfigProvider` theme tokens, light
  and dark, system preference by default, toggle in the top bar. Accent
  `#1f5eff`, success `#1a8f4d`, warning `#b7791f`, danger `#c0392b`, star
  `#e0a400`.
- TanStack Query v5 for all server state. Query keys per resource. Job
  polling: `refetchInterval` 1000 ms while `state` is `queued|running`, off
  otherwise. Optimistic updates for prompt star/enable/overrides.
- React Router v6, deep links: `/projects`, `/projects/:id/overview`,
  `/projects/:id/battleground`, `/projects/:id/prompts`, `/projects/:id/runs`,
  `/projects/:id/actions`, `/atlas`, `/costs`, drawer state in the query
  string (`?prompt=<prompt_id>&engine=<ENGINE>&crawl=<run_id>`).
- Zustand for UI-only state (selection, view mode, drawer, theme).
- Framer Motion for route/tab transitions, card enter/exit, progress bar
  easing, checklist tick, badge pulse. Honour `prefers-reduced-motion`
  (no motion at all when set). Smooth scrolling (Lenis) only on the Overview
  and Atlas pages, never on pages with AntD tables or virtual lists.
- `@ant-design/plots` for the few charts allowed (see Atlas). No other chart
  library.
- Types generated from OpenAPI with `openapi-typescript` into
  `ui/src/api/schema.d.ts`; a thin typed fetch client; Zod only for form
  input. Never hand-type an API shape.
- Tests: Vitest + React Testing Library with MSW handlers for every endpoint
  (including the planned insight endpoints); Playwright smoke suite against
  the local server using the existing project (read-only flows only).
- Scripts: `npm run dev` (Vite with `/api` and `/reports` proxied to
  `http://127.0.0.1:8787`), `npm run build`, `npm run lint` (eslint +
  prettier), `npm run typecheck`, `npm test`, `npm run e2e`. All must be green
  before you report a step done.

## Layout

Collapsible left rail: Projects, Atlas, Costs. Top bar: project switcher,
"n runs in progress" badge (polls `GET /api/jobs?active=true` every 5 s),
theme toggle, ⌘K command palette (jump to project, prompt, page, action).
Toasts for every mutation result; sticky toast plus browser Notification
(permission asked on first Run click) plus tab-title flash when a job
finishes; the page re-attaches to an active job on reload
(`GET /api/projects/{id}/jobs`, pick the first `queued|running`).

## Pages

### /projects
Project cards: name, brand, LOB, platforms, interval, next crawl due,
crawls since last consolidation, health verdict (from insights, or "no
consolidation yet"), top action title. Search box. "New project" opens a
Drawer form; edit and delete from the card menu (delete confirms and says
history is kept).

Project form fields (all live today): name, enabled, brand name, LOB,
aliases, client domains, competitor domains, competitor names, seed keywords,
subtopics, landing pages (one per line), platforms (checkboxes), default
interval (presets from `/api/options` plus custom text like `4d`, `10h`,
`6w`), samples per platform (1–10, blank = setting), max engine calls per
run, reuse snapshots newer than N hours, track keyword rank, resolve Gemini
redirects, also run Semrush-generated 10+10 set, per-engine model selection
(`engine_models`, options from `/api/options.models`), **consolidation
window** (`consolidation_runs`, 1–50, default 3, explained inline: "run every
2 days with 3 → positions consolidated every 6 days from 3 crawls"), notes.
Map 422 responses onto fields.

### /projects/:id/overview (default tab)
Layer 1 only. Health strip: one tile per tracked platform with verdict
(Winning / Present / Invisible / Losing to <domain>), cited %, mentioned %,
best rank, delta vs previous consolidation, and "n samples over m crawls"
on hover. "What changed" list (max 5). Top 3 action cards (expand in place;
"Show all actions (n)" goes to /actions). Confidence banner when the latest
data is a single crawl ("low confidence: 1 of 3 crawls"). Data:
`GET /api/projects/{id}/insights` (mock until the backend lands) and
`GET /api/projects/{id}/positions`.

### /projects/:id/battleground
The engine matrix. Rows = prompts grouped by subtopic (collapsible), columns
= platforms. One badge per cell: green *Linked #rank*, amber *Mentioned
only*, grey *Absent*, red *<competitor> wins*, plus a small "due" dot when
the prompt is due on that platform. Hover: the brand sentence and the top 3
cited domains. Toggle above the matrix: **Consolidated (last N crawls)** /
**Point-in-time** with a crawl-date picker from `GET /api/projects/{id}/crawls`.
Click a cell → Inspection Drawer (right, 720 px, deep-linked):
- consolidated numbers (samples, crawls, cited/mentioned rates, best/mean
  rank, rank distribution bar, domain share chips, competitors);
- point-in-time history: one row per crawl with its date and its snapshot;
- the answer text with brand sentences highlighted and citations annotated
  inline (from `GET /api/projects/{id}/samples?prompt_id&engine&run_id`;
  mock until it lands, fall back to `answer_excerpt` from results);
- citation links (title, domain, position, client/competitor colouring),
  consulted-but-not-cited URLs, the engine's search queries, organic rank for
  prompt and keyword.

### /projects/:id/prompts
AntD Table, client-side data (the API returns the full list; paginate at
10/25/50/100 with a persistent page size), sort by volume/subtopic/verdict/
cited %/updated, filters by intent, decision stage, platform, verdict,
due/not due, starred, enabled. Row actions: star, enable/disable, per-prompt
interval (inherit / preset / custom), platform override (inherit or
checkboxes), samples override, Reset overrides, delete (confirm). Inline
edit of prompt text, keyword, subtopic with Save. Import card: file upload
(read client-side) or paste (one prompt per line, optional
`| keyword | subtopic`) → `POST /api/projects/{id}/prompts/import`; add one
prompt form. Bulk bar on selection: run selected, star, set interval, set
platforms, delete.

### /projects/:id/runs
Run controls: Run due now, Run everything now, both disabled while a job is
active for the project. Live progress card while a job is active: percent,
phase, checks done/total (prompt × platform), batches, paid engine calls,
elapsed, current message, outcome and warnings when finished, Hide button.
Queued/recent jobs table (state chip with queue position, requested filters,
progress bar, duration, outcome, Watch). Crawl history table
(`/crawls`: date, pipeline run ids, prompts run, full/partial). Pipeline
runs table (`/runs`: run id, started, prompts, engine calls, failed, spend,
report path). Consolidation panel: latest consolidation summary, "n crawls
since last, next automatic after N", history select, **Consolidate now**
with a custom window input → `POST /api/projects/{id}/consolidate`.
Cost per crawl for this project from `GET /api/costs?project_id=`.

### /projects/:id/actions
Full action checklist from insights: cards grouped by type, filters by
platform, subtopic, status (open/done), outcome (improved/unchanged/
regressed/pending). Each card: title, prescription, impact score, evidence
(quotes with the engine and date, domains, URLs, the engine's queries,
numbers), owner input, checkbox → `PUT /api/projects/{id}/actions/{action_id}`
(mock until it lands). Checked cards animate to the Done group.

### /atlas
Cross-project explorer, parity with `docs/prompt-atlas.html`: LOB/project
picker; engine chips; filters (prompt type, decision stage, intent, citation
status, verdict, domain search, text search); "as of" consolidation or crawl
selector. Views: Overview (prompts cited on ≥ 1 engine with delta, mean
citation and mention rate, web-trigger rate, content gaps, engine scoreboard
with sparkline, citation rate by run trend line, citation rate by decision
stage heatmap, "who gets cited instead" domain bars), Master prompt sheet
(table with group-by, sort, engine cells showing cited/mentioned rates and
rank, landing page or gap pill, verdict pill, row click → the same
Inspection Drawer), Engines (per-engine tiles, rank histogram, domains
cited, prompts ranked by citation rate with sparkline and competitors
ahead), Share of voice (domain × engine share table, click → prompts where
the domain is cited), Content gaps (grouped by subtopic with searches/month
and "already cited" counts, one page-brief per gap), Claim ledger and
Query fan-out map (from insights). Data: `/reports/prompt-atlas-data.json`
(live from SQLite; `?lob=` filter) plus positions and insights per project.
Charts allowed on this page only: trend line, stage heatmap, domain bars,
rank histogram.

### /costs
`GET /api/costs[?days]`: totals (calls, estimated, actual), by source,
vendor table (calls, ok, errors, tokens in/out, searches, units, estimated,
actual, mean per ok call, p50/p95 latency), per-run table, proposed
`COST_*` values with basis and a copy-to-clipboard `.env` block, notes.
Project scope selector reuses the same endpoint with `project_id`.

## Parity checklist (everything the two HTML pages do today must work)

Control plane page: projects sidebar with running dot; tabs Project &
client / Prompts / Results / Runs / Costs; interval picker with presets and
custom; platform checkboxes with inherit; per-engine model select; star,
enable, overrides, reset; import file/paste/add; run due/all/selected;
progress card, sticky toast, notification, title flash, reload re-attach;
results per platform with cited/rate/rank, mention rate and sentences,
citation links, competitors, consulted count, excerpt, due badges; runs and
jobs tables; consolidated vs point-in-time toggle, history, consolidate now;
costs table with recommendations; export JSON. Atlas page: everything listed
under /atlas above, including the drawer's history sparkline, velocity
(30-day windows), cited domains in order, mention snippets, answer excerpt.

## API you can use today (all JSON on 127.0.0.1:8787)

```
GET  /api/health                         {status, active_jobs}
GET  /api/options                        {engines[{value,label}], intervals[{value,label}], models{ENGINE:[{value,label}]}}
GET  /api/projects | POST (201)          Project
GET/PUT/DELETE /api/projects/{id}        (PUT partial; 422 on bad fields; 404 unknown)
GET/POST /api/projects/{id}/prompts      TrackedPrompt[] | 201 TrackedPrompt
POST /api/projects/{id}/prompts/import   {text} → TrackedPrompt[]
PUT/DELETE /api/projects/{id}/prompts/{tid}   (PUT partial; {clear_overrides:true} resets)
GET  /api/projects/{id}/results          PromptResult[] {prompt, effective_interval, effective_engines, snapshots{ENGINE: CitationSnapshot|null}, organic_prompt, organic_keyword, due_on[]}
POST /api/projects/{id}/run              {force?, prompt_ids?, engines?} → 202 RunJob
GET  /api/jobs?active=true | GET /api/jobs/{job_id} | GET /api/projects/{id}/jobs
     RunJob {id, project_id, project_name, request, state, position, created_at, started_at, finished_at,
             progress{phase, message, checks_done, checks_total, batches_done, batches_total, engine_calls, percent}, outcome, error}
GET  /api/projects/{id}/runs             [{run_id, started_at, finished_at, prompts_selected, engine_calls, failed_engine_calls, estimated_cost_usd, report_path}]
GET  /api/projects/{id}/crawls           ProjectRunRecord[] {id, started_at, finished_at, run_ids[], prompts_run, batches, statuses[], full}
POST /api/projects/{id}/consolidate      {window_runs?, note?} → Consolidation (400 when no crawl yet)
GET  /api/projects/{id}/positions[?consolidation_id]
     PositionsView {consolidation|null, positions[ConsolidatedPosition], history[Consolidation], runs_since_last}
GET  /api/costs[?project_id&days]        CostReport {calls, total_estimated_usd, total_actual_usd, by_source, vendors[], runs[], recommendations[], notes[]}
GET  /api/projects/{id}/export           {project, prompts}
GET  /reports/prompt-atlas-data.json[?lob]   {meta, prompts[], snapshots[], runs[]}
GET  /openapi.json
```

## Insight API (live since backend cycle 0011; `/openapi.json` has the exact schemas, which also add `health[].volatility`, `health[].prompts`, `fanout[].client_covered`, `winning_pages[]`, `client_pages[]`, `placement[]`, `freshness[]`, `actions[].metric`, `basis.computed_from`)

```
GET /api/projects/{id}/insights[?consolidation_id]
{
  basis: {consolidation_id|null, crawls, samples, low_confidence: bool},
  health: [{engine, verdict: "winning"|"present"|"invisible"|"losing", losing_to: string|null,
            cited_rate, mention_rate, best_rank|null, delta_cited_rate|null, samples, crawls}],
  changes: [{kind: "flip_up"|"flip_down"|"rank_up"|"rank_down"|"new_competitor"|"lost_platform",
             prompt_id, prompt_text, engine, before, after, text}],
  actions: [{id, type: "convert_mention"|"own_claim"|"read_but_rejected"|"aio_gap"|"earned_placement"|
                      "freshness"|"defend"|"landing_page",
             title, prescription, impact_score, engine|null, subtopic, prompt_ids[],
             evidence: {quotes[{text, engine, run_id, captured_at}], domains[{domain, share}], urls[],
                        queries[], numbers{}},
             status: "open"|"done", outcome: "pending"|"improved"|"unchanged"|"regressed", owner|null, note|null}],
  fanout: [{query, engines[], prompts: n, subtopic}],
  claims: [{sentence, url, domain, engine, prompt_id, is_client, is_competitor}],
  trust_profile: [{engine, domain_class: "vendor"|"aggregator"|"forum"|"publisher"|"docs"|"marketplace"|"other", share}],
  read_but_rejected: [{url, engine, samples, queries[]}]
}
PUT /api/projects/{id}/actions/{action_id}   {status?, owner?, note?} → the action
GET /api/projects/{id}/samples?prompt_id=&engine=&run_id=
    [{prompt_id, engine, model, captured_at, response_id, web_triggered, client_cited, client_rank,
      answer_text, citation_links[], consulted_urls[], search_queries[], citation_claims[{url, sentence, start, end}],
      source_snippets[{url, snippet, date}], mentions[{entity, term, snippet}], mention_detected}]
```

## Deliver in this order; each step has tests and a `docs/build-log/ui-000N-*.md` entry

1. Scaffold: Vite + React + TS strict, AntD theme (light/dark), routes,
   TanStack Query, Zustand, Framer Motion with reduced-motion, OpenAPI type
   generation script, MSW handlers for every endpoint above (live and
   planned) with realistic fixtures copied from the live API of the existing
   project, Vitest/RTL/Playwright configured, all scripts green, `ui/README.md`.
2. App shell: rail, top bar, project switcher, active-jobs badge, theme
   toggle, command palette, toasts, notification helper.
3. Projects list + Drawer form (create/edit/delete) against the real API.
4. Runs page with live progress polling, jobs, crawls, runs, consolidation
   panel with Consolidate now.
5. Prompts page (table, filters, overrides, import, bulk actions).
6. Battleground matrix + Inspection Drawer (consolidated / point-in-time).
7. Overview page (health strip, what changed, top 3 actions, confidence
   banner) on mocked insights; Actions page with checklist behaviour.
8. Atlas page (parity list) and Costs page.
9. Serve the built app from the control plane: add a route in
   `src/modules/control_plane/app.py` that mounts `ui/dist` at `/` when it
   exists and keeps the old page at `/legacy`. This is the one backend file
   you may touch; keep the change to that mount and run the repo gate
   `scripts\verify.ps1` afterwards.

## Rules

- No vendor calls from the browser, ever. No crawl triggered by tests.
- Every number shows its basis (samples, crawls, dates) on hover or in a
  caption. Single-crawl numbers are labelled low confidence.
- First screens show verdicts, changes and actions only; tables and charts
  are one click deeper.
- API shapes come from OpenAPI; mutations invalidate the minimal query keys;
  fetches cancel on route change; lists above 200 rows are virtualised.
- Keyboard navigation for the matrix and tables; Lighthouse accessibility
  ≥ 95; no layout shift on data arrival (skeletons sized to content).
- Errors: show the API `detail` verbatim; map 422 field errors onto forms.
- Follow the repo's SDLC rules in `.agents/rules/` for logs and ADRs; write
  `docs/adr/0015-react-ui.md` recording the stack decision in step 1.
