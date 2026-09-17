# RankUno Prompt Engine — Analyst UI build brief

Written for: the Claude Code session that will build the React front end, and
the backend session that will add the insight endpoints it depends on. Read
this whole file before writing code. It states what exists, what is decided,
and what the UI must make an analyst *do*, not just see.

Repository: `C:\Users\RankUno\Desktop\prompt-engine` (Python 3.11 backend,
FastAPI control plane at `src/modules/control_plane`). The front end lives in a
new folder `ui/` in this repository and is served by the control plane in
production (`/` → built `index.html`), with Vite dev server proxying `/api` to
`http://127.0.0.1:8787` during development. Binding rules for the repo are in
`.agents/rules/` (SDLC, verify gate, build-log per cycle, ADRs); the UI gets its
own gate: `npm run lint`, `npm run typecheck`, `npm test` (Vitest + Testing
Library), and a Playwright smoke run against the local server.

---

## 1. What the engine already does and measures (the facts)

One **project** = one client line of business: brand, aliases, client domains,
competitor domains and names, seed keywords, landing pages, platforms to track,
run interval, samples per platform, consolidation window, model choices.

One **crawl** of a project sends every enabled prompt, verbatim, to each chosen
platform 2–3 times (adaptive: stops at 2 when both samples agree on
cited/not-cited) and stores, per sample, per snapshot and per run:

| Platform | What is called | What is captured per sample |
| :-- | :-- | :-- |
| ChatGPT Search | OpenAI Responses API, `web_search` forced | answer text, `url_citation` links in order, all URLs the search consulted, model, response id |
| Perplexity | Agent API `/v1/responses`, `perplexity/sonar`, `web_search` forced | answer text, `search_results` (url, title, snippet, date) in order, model, response id, vendor-reported cost |
| Gemini | `generateContent` + Google Search grounding | answer text, grounding chunks (url/domain/title) ordered by first supporting segment, `webSearchQueries`, redirect flags |
| Google AI Overview | SerpApi `google` (+ `google_ai_overview` follow-up) | AI Overview text blocks, inline source links, organic top results, People-Also-Ask questions |

Derived per prompt × platform (snapshot): samples, failed, web-trigger rate,
client cited samples and rate, cited verdict (≥ 50 %), best and mean rank,
cited domains (most frequent first), competitor best ranks, every citation
link (url, title, domain, position), the client's own cited URLs, consulted
URLs, **mention** rate and exact sentence snippets where the brand or a
competitor is named in the text (exact, word-bounded), competitor mentions.

Also per run: Google organic rank for the prompt text (free, same SerpApi
call) and for the seed keyword; landing-page mapping and content-gap verdict
per prompt; run header with spend.

Per project: crawl records; after every N full crawls (analyst-set, default 3)
a **consolidated position set**: per prompt × platform over all N crawls
(cited/mentioned rates and verdicts, best/mean rank, rank distribution, domain
share, competitors, organic best/mean). Every crawl is kept on its own date.

Per API call: a **usage ledger** row (tokens, searches, units, estimated and
vendor/modelled cost, latency, source, run, prompt, engine) and a costing
report proposing `COST_*` parameters from observation.

Numbers seen so far on the live client (GEP, procurement software, 17 Sep):
on the branded prompt "How do I implement GEP procurement software and
integrate it with my ERP?" ChatGPT cites gep.com (2 links), Perplexity cites
15 sources with gep.com at #5–#6 and erpresearch.com / microsoft.com
marketplace / procurementaiagents.com ahead of it, Google AI Overview cites
gep.com #1, Gemini is blocked by the operator's Google billing. Earlier
snapshots (before the connector corrections of cycle 0008) under-report
citations and must not be used for conclusions.

Spend: about $0.03–0.05 per sample; a 15-prompt × 4-platform crawl costs about
$2–3. Session ceiling is $0.50 while testing. **The UI never calls a vendor.**

---

## 2. Data to capture *in the same calls* for free (backend work, cycle 0011)

Everything below is already in the responses we pay for and is currently
discarded or truncated. No extra API call is needed for any of it.

1. **Full answer text per sample** (today only a 600-char excerpt is stored).
   Needed for: where in the answer the brand appears (intro, list item,
   comparison table, "alternatives" section), answer structure (headings,
   lists, tables), and the claim each citation supports.
2. **The engine's own search queries ("query fan-out").** OpenAI:
   `web_search_call.action.query`; Perplexity: `search_results.queries`
   (seen live: `"GEP procurement software ERP integration implementation
   official"`, `"site:gep.com GEP SMART ERP integration"`); Gemini:
   `webSearchQueries`. These are the exact sub-queries the engine ran to
   answer the prompt: the most direct keyword and content targets in GEO.
3. **Citation-to-claim mapping.** OpenAI annotations carry `start_index` /
   `end_index` into the text; Gemini `groundingSupports` map text segments to
   chunk indices; Perplexity `[n]` markers when present. Store the sentence
   each citation supports, so the UI can show "this claim is attributed to
   coupa.com" and prescribe the page that should own it.
4. **Source snippets and dates.** Perplexity `search_results[].snippet`,
   `date`, `last_updated`: the passage that earned the citation and its
   freshness. Store them per citation.
5. **Consulted-but-not-cited pages** (OpenAI `sources`): the engine read the
   client's page and did not cite it. Already captured as URLs; keep it and
   flag client URLs in that list as "read but rejected".
6. **Page-level citation inventory.** Citation links already carry URL and
   title; aggregate per competitor page across prompts (which exact pages win).
7. **SERP extras already in the SerpApi payload:** People-Also-Ask questions,
   related searches, organic titles and snippets for the top 10. Persist them
   per run (only domains and positions are stored today).
8. **Brand position in text** (computed locally): character offset of the
   first mention, whether the mention is inside a list of alternatives, a
   comparison, or a recommendation sentence; sentiment is *not* attempted.
9. **Engine trust profile** (computed locally): which domain classes each
   engine cites for this project's prompts (vendor sites, review aggregators
   such as g2.com/capterra.com, forums such as reddit.com, publishers, docs,
   marketplaces). Derived from citation domains; no call.

Backend contract for these: extend `AnswerSample` with `answer_text`,
`search_queries: list[str]`, `citation_claims: list[{url, sentence, start,
end}]`, `source_snippets: list[{url, snippet, date}]`; extend the organic
snapshot with `paa_questions`, `related_searches`, `organic_results` (top 10
with title and snippet). Persist in the existing tables via the migration
helper. Then add the two endpoints in §5.

---

## 3. The product idea: insight that ends in an action

Every screen answers three questions in this order, and nothing else is shown
until the analyst asks for it:

1. **Where do we stand?** One health verdict per project and per platform:
   *Winning* (cited ≥ 50 % and rank ≤ 3), *Present* (cited or mentioned but
   not leading), *Invisible* (neither), *Losing to X* (competitor cited where
   we are not). Computed from the latest consolidated position set, falling
   back to the latest crawl with a "single crawl, low confidence" label.
2. **What changed?** Deltas between the last two consolidations (or crawls):
   prompts that flipped verdict, platforms that dropped, competitors that
   entered. Shown as a short list, not a chart wall.
3. **What do we do now?** Action cards, each with: the evidence (numbers and
   the exact quote), the prescription, the pages involved, an owner and a
   checkbox. Checked cards are re-evaluated after the next consolidation and
   marked *Improved / No change / Regressed*.

### Action card rules (deterministic, computed on the backend from stored data)

| Card | Trigger (per prompt cluster × platform) | Prescription | Evidence shown |
| :-- | :-- | :-- | :-- |
| Convert mention to citation | mention rate ≥ 40 % and citation rate ≤ 10 % | The engine names the brand but links elsewhere: publish or fix the page that should own the claim; make it crawlable and cite-worthy (clear H1, definition sentence, FAQ schema); get listed on the domains this engine links for the claim | the sentences with the mention; the domains cited instead; the claim-to-citation mapping |
| Own the claim competitor owns | a competitor page is cited on ≥ 3 prompts in the cluster while the client is not | Create/upgrade a page matching the competitor page's format for that claim; target the engine's fan-out queries | competitor page URL, title, snippet, dates; fan-out queries; prompts affected |
| Read but rejected | client URL appears in consulted-but-not-cited on ≥ 2 samples | On-page relevance fix on that URL: answer the fan-out query in the first 100 words, add the missing entity/spec, refresh date | the URL, the queries, what was cited instead |
| AI Overview gap | organic rank ≤ 5 for prompt or keyword, AIO present, client absent from AIO sources | Add a concise answer block / FAQ matching the AIO's text blocks; PAA coverage | AIO text blocks, PAA questions, organic position |
| Earned placement | ≥ 30 % of an engine's citations for the cluster go to aggregators/forums (g2, capterra, reddit, wikipedia, marketplaces) | Secure or update the brand's presence on those exact domains | domain share table with the exact URLs cited |
| Freshness | cited competitor sources are newer than the client's cited page by > 12 months (Perplexity dates) | Refresh the client page; update the date and content | dates side by side |
| Defend | citation rate on a platform fell ≥ 20 points between the last two consolidations | Re-check the page's status and the engine's new sources; refresh | before/after, new competitor sources |
| Landing page missing | content-gap verdict from URL mapping | Create the `[CONTENT GAP: Need <subtopic> page]` page; the brief is the fan-out queries + PAA | mapping verdict, subtopic, queries |

Cards are ranked by expected impact = searches/month of the cluster ×
(target rate − current rate) × platform weight, so the top three are the
three biggest wins. Everything else is behind "show all".

### Things no one gives their analysts today (and we can, from the same calls)

- **Query fan-out map**: the engine's own sub-queries per prompt cluster,
  deduplicated, with counts across platforms. This is the keyword research
  the engines did for us.
- **Claim ledger**: which sentences the engines attribute to whom. "Coupa
  owns 'best for mid-market P2P'; we own nothing in this cluster."
- **Engine trust profile**: what kind of sources each engine believes for
  this LOB, so the team knows whether to write pages, earn reviews, or seed
  forums.
- **Read-but-rejected list**: the client pages the engines looked at and
  passed over: the highest-leverage on-page fixes.
- **Consolidation confidence**: every number carries samples and crawls
  behind it; single-crawl numbers are visibly labelled low-confidence.
- **Cost per insight**: the ledger shows what a crawl cost and what it
  produced, so the analyst can choose window and samples with eyes open.

---

## 4. UI specification

### Stack (fixed)

- React 18 + TypeScript + Vite; folder `ui/`.
- Ant Design v5 + `@ant-design/icons`; theme tokens via `ConfigProvider`
  (light and dark; brand accent `#1f5eff`, success `#1a8f4d`, warning
  `#b7791f`, danger `#c0392b`, star `#e0a400`).
- TanStack Query v5 for all server state (keys per resource; `refetchInterval`
  1 s while a job is active; optimistic updates for prompt toggles).
- React Router v6 with deep links: `/projects`, `/projects/:id/:tab`,
  `/projects/:id/prompts/:promptId`, `/atlas`, `/costs`.
- Zustand for UI-only state (drawer open, selected rows, view mode).
- Framer Motion for page/tab transitions, card enter/exit, progress bar
  easing, and the action-checklist tick. Respect `prefers-reduced-motion`.
- `@ant-design/plots` for the few charts allowed (see below). No chart wall.
- Zod schemas generated from the OpenAPI at `http://127.0.0.1:8787/openapi.json`
  (`openapi-typescript` for types); never hand-type API shapes.
- Tests: Vitest + React Testing Library (components and hooks with MSW mocks
  of the API), Playwright smoke (create project → import prompts → run →
  progress → results → action card → check off).

### Layout

Left rail (collapsible): Projects, Atlas, Costs. Top bar: project switcher,
global "runs in progress" badge (polls `/api/jobs?active=true` every 5 s),
theme toggle, command palette (⌘K: jump to prompt, project, page).

### Pages

**/projects** — cards with health verdict, next crawl due, crawls since last
consolidation, top action title. Create/edit in a Drawer form (AntD Form,
validated against the API's 422s).

**/projects/:id/overview** (default) — Layer 1 only:
- Health strip: one tile per platform: verdict, cited %, mentioned %, best
  rank, trend arrow vs previous consolidation, samples/crawls behind it.
- "What changed" list (max 5 lines).
- Top 3 action cards, each expandable in place; "Show all actions (n)".
- Confidence banner if the latest data is a single crawl.

**/projects/:id/battleground** — Layer 2: the engine matrix. Rows = prompts
(grouped by cluster/subtopic, collapsible), columns = platforms. Each cell is
one badge: green *Linked* (with rank), amber *Mentioned only*, grey *Absent*,
red *Competitor wins* (with the competitor's domain). Hover shows the sentence
and the top three cited domains. Click opens the Inspection Drawer (Layer 3):
consolidated numbers, every crawl on its own date (point-in-time), the raw
answer text with the brand sentences highlighted and citations annotated,
the citation links, consulted URLs, fan-out queries, organic rank. Toggle
"Consolidated (last N crawls) / Point-in-time (crawl date picker)".

**/projects/:id/prompts** — the master prompt table (AntD Table, server-side
pagination 10/25/50/100, sort by volume, cluster, verdict, cited %, filters
by intent, stage, platform, verdict), star, on/off, per-prompt interval and
platform override, import via file/paste, bulk actions on selected rows (run
selected, star, set interval).

**/projects/:id/runs** — Run controls (due / all / selected), live progress
card (percent, checks done/total, batches, paid calls, elapsed, phase,
current prompt), queued/recent jobs, crawl history, consolidation history
with "Consolidate now" and custom window, and the project's cost per crawl.

**/projects/:id/actions** — the full action checklist: cards grouped by
type, filter by platform/cluster/status, owner, checked/unchecked, outcome
after the next consolidation.

**/atlas** — the cross-project explorer: share of voice per engine, content
gaps, claim ledger, fan-out map, engine trust profile. Charts allowed here
only: domain share bars per engine, citation-rate trend per consolidation,
rank distribution histogram. Everything clickable to the drawer.

**/costs** — usage ledger: totals, per vendor, per run, per source, latency
percentiles, proposed `COST_*` values with a copy-to-clipboard `.env` block.

### Interaction rules

- Never show a raw table on a landing view; tables live one click deeper.
- Every number shows its basis on hover: samples, crawls, dates.
- Every action card links to the exact evidence (drawer opens at that sample).
- Empty states explain the next step ("No consolidation yet: 2 of 3 crawls
  done, next due Thursday").
- Errors from the API (`detail`) are shown verbatim in a toast; 422 field
  errors map onto form fields.
- All lists virtualised above 200 rows; all fetches cancellable on route
  change; all mutations invalidate the minimal query keys.

---

## 5. API contract

Existing (control plane, all JSON, loopback):

```
GET  /api/health                         {status, active_jobs}
GET  /api/options                        {engines[], intervals[], models{}}
GET/POST /api/projects                   Project[] | create → 201 Project
GET/PUT/DELETE /api/projects/{id}
GET/POST /api/projects/{id}/prompts      TrackedPrompt[] | add → 201
POST /api/projects/{id}/prompts/import   {text} → TrackedPrompt[]
PUT/DELETE /api/projects/{id}/prompts/{tid}   (PUT body: partial; clear_overrides)
GET  /api/projects/{id}/results          PromptResult[] (latest snapshot per platform, due flags)
POST /api/projects/{id}/run              {force?, prompt_ids?, engines?} → 202 RunJob
GET  /api/jobs?active=true | /api/jobs/{job_id} | /api/projects/{id}/jobs
GET  /api/projects/{id}/runs             pipeline run headers
GET  /api/projects/{id}/crawls           ProjectRunRecord[]
POST /api/projects/{id}/consolidate      {window_runs?, note?} → Consolidation
GET  /api/projects/{id}/positions[?consolidation_id]  PositionsView
GET  /api/costs[?project_id&days]        CostReport
GET  /api/projects/{id}/export
GET  /reports/prompt-atlas-data.json[?lob]   atlas dataset (live from SQLite)
GET  /openapi.json                       full schemas (generate types from this)
```

Live since backend cycle 0011 (shapes below are the contract; the exact
schemas are in `/openapi.json`). Extra fields beyond the first draft:
`health[].volatility`, `health[].prompts`, `fanout[].client_covered`,
`winning_pages[]`, `client_pages[]`, `placement[]`, `freshness[]`,
`actions[].metric`, `basis.computed_from`.

```
GET /api/projects/{id}/insights[?consolidation_id]
  {
    health: [{engine, verdict: "winning|present|invisible|losing", cited_rate,
              mention_rate, best_rank, delta_cited_rate, samples, crawls}],
    changes: [{kind, prompt_id, engine, before, after, text}],
    actions: [{id, type, title, prescription, impact_score, engine, cluster,
               prompt_ids, evidence: {quotes[], domains[], urls[], queries[],
               numbers{}}, status: "open|done", outcome: "improved|unchanged|
               regressed|pending", owner?}],
    fanout: [{query, engines[], prompts: n}],
    claims: [{sentence, url, domain, engine, prompt_id}],
    trust_profile: [{engine, domain_class, share}],
    read_but_rejected: [{url, samples, engine, queries[]}]
  }
PUT /api/projects/{id}/actions/{action_id}   {status, owner?, note?}
GET /api/projects/{id}/samples?prompt_id&engine&run_id   raw AnswerSample[] (full text)
```

---

## 6. Prompt to start the UI session (copy from here)

> You are building the analyst front end for the RankUno Prompt Engine in
> `C:\Users\RankUno\Desktop\prompt-engine\ui`. Read `docs/UI_BUILD_BRIEF.md`
> in full first; it is binding. Then read `docs/ARCHITECTURE.md`,
> `docs/adr/0009` to `0013`, and `src/modules/control_plane/app.py` for the
> live routes, and fetch `http://127.0.0.1:8787/openapi.json` for the exact
> schemas (start the server with `.\.venv\Scripts\python.exe -m
> src.modules.control_plane --approve-spend` if it is not running; never run a
> project crawl yourself, it spends money: use the existing project's stored
> data and MSW mocks).
>
> Deliver, in this order, each as its own commit-sized step with tests:
> 1. Vite + React 18 + TypeScript scaffold in `ui/`, AntD v5 theme
>    (light/dark), React Router v6 routes from the brief, TanStack Query
>    client, Zustand store, Framer Motion transitions with reduced-motion
>    support, generated API types from OpenAPI, MSW handlers for every
>    endpoint including the planned insight endpoints, Vitest + RTL + Playwright
>    configured, `npm run lint|typecheck|test|e2e` green.
> 2. Projects list and project Drawer form (create/edit/delete) wired to the
>    real API with 422 field mapping.
> 3. Project Runs page: run controls, live progress card polling `/api/jobs/{id}`
>    every second, jobs and crawls lists, consolidation history and
>    "Consolidate now" with custom window.
> 4. Prompts page: server-side paginated AntD table with sort/filter, star,
>    on/off, overrides, import via file/paste, bulk actions.
> 5. Battleground page with the badge matrix and the Inspection Drawer
>    (consolidated vs point-in-time toggle, highlighted answer text, citations,
>    consulted URLs, fan-out queries, organic rank).
> 6. Overview page: health strip, "what changed", top 3 action cards from the
>    insights endpoint (mocked until the backend lands), checklist behaviour.
> 7. Actions page, Atlas page (three charts only), Costs page with the `.env`
>    copy block.
> 8. Serve the built app from the control plane (`ui/dist` → `/`), keep the
>    old `static/index.html` reachable at `/legacy` until parity is confirmed.
>
> Rules: no vendor calls from the browser; every number shows its basis;
> nothing lands on a first screen except verdicts, changes and actions; all
> API shapes come from OpenAPI, never hand-typed; `prefers-reduced-motion`
> honoured; keyboard navigation for the matrix and tables; Lighthouse
> accessibility ≥ 95. Write a short `ui/README.md` and add a build-log entry
> under `docs/build-log/` for each step, as the repo rules require.
