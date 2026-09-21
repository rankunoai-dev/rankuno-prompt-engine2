# Architecture — RankUno Prompt Engine

> Status: current, verified state of the code (Step 8 rule: nothing aspirational).
> Decisions with alternatives live in `docs/adr/`. Unimplemented-but-plausible
> items live in `docs/KNOWN_GAPS.md`.

## 1. Dependency direction

```
src/modules/prompt_tracking  ──▶  src/integrations  ──▶  src/core
```

`core` imports nothing from the other two; `integrations` never imports `modules`.

## 2. Layers

### core (domain-agnostic)

| Module | Role |
| :-- | :-- |
| `schemas.py` | `StrictModel` (`extra="forbid"`, `validate_assignment=True`), `RiskClass`, `ApprovalMode`, `ExecutionStatus`, `ToolMetadata`, `ToolResult` |
| `config.py` | `Settings` singleton; the only `os.environ` reader; credentials as `SecretStr` |
| `logger.py` | JSON audit logging to stderr and `AUDIT_LOG_PATH`, trace ids |
| `errors.py` | `RankunoError` hierarchy incl. `UnsafeUrlError`, `UpstreamClientError` |
| `guardrails.py` | Deny-by-default HITL engine; providers: deny, callback, auto (dev only), **budgeted** |
| `rate_limiter.py` | `TokenBucket` (incl. `from_crawl_delay`), `RateLimiterRegistry`, `CostLedger` |
| `retry.py` | tenacity policy; retries `IntegrationError`, never `UpstreamClientError` |
| `circuit_breaker.py` | `CircuitBreaker` (closed → open → half-open) and registry; only transient failures trip it |
| `base_tool.py` | validate → guardrail → rate-limit → charge → execute → validate → audit |
| `registry.py` | explicit tool catalogue |
| `url_safety.py` | `UrlSafetyPolicy.validate()` → `SafeUrl` with resolved IPs (SSRF guard) |
| `robots.py` | `RobotsRules` over stdlib `RobotFileParser`; fail-closed when unavailable |
| `domains.py` | `normalize_domain`, `registrable_domain`, `domain_matches` |

### integrations (all subclass `BaseAPIClient`)

| Module | Vendor call | Returns |
| :-- | :-- | :-- |
| `base_client.py` | — | `BaseAPIClient`: shared per-vendor rate bucket **and circuit breaker**, retry wrapper, audit log; `available` is False while the breaker is open; re-raises `IntegrationError`/`UpstreamClientError` unwrapped |
| `http.py` | shared `httpx` client factory, `PinnedTransport` (host→resolved IP, SNI kept), status→error mapping | — |
| `schemas.py` | — | `Engine`, `Citation`, `EngineAnswer`, `KeywordRecord`, `KeywordSource`, `SerpSnapshot` |
| `semrush.py` | `GET api.semrush.com/?type=phrase_*` | `list[KeywordRecord]`; per-run unit ceiling; in-band `ERROR` bodies handled |
| `openai_search.py` | `POST /v1/responses` with `tools=[{"type":"web_search"}]` | `EngineAnswer`; citations = `url_citation` annotations by `start_index` |
| `perplexity.py` | `POST /v1/responses` (Agent API), model `perplexity/sonar`, `web_search` tool forced, 120 s timeout | `EngineAnswer`; sources from the `search_results` output item, ordered by `[n]` markers when present |
| `gemini_search.py` | `POST models/{model}:generateContent` with `google_search` tool, key in header | `EngineAnswer`; `resolved=False` for unresolved redirect links |
| `serp_api.py` | `GET search.json?engine=google` (+ `google_ai_overview` with `page_token`); locale + `device` fixed | `SerpSnapshot` (AI Overview + `organic_results`) / `EngineAnswer`; `search_and_ask()` returns both from one call |
| `url_resolver.py` | HEAD per redirect hop, pinned transport, robots per host | `ResolvedUrl` |

### modules/prompt_tracking

```
PipelineInput ─▶ PromptTrackerPipeline (BaseTool, FINANCIAL)
   1 harvest      SemrushClient.phrase_all / phrase_questions   → volumes, questions
                  (skipped when generate_prompts=False: custom prompts only)
   2 generate     PromptGenerator.from_keywords (questions + stage templates)
                  PromptGenerator.from_custom (analyst prompts; gate advisory)
   3 gate         IntentClassifier (L1 reject, L2 score, L3 gate)
   4 select       select_master_set (10 branded + 10 non-branded) + every custom prompt
   5 audit        ThreadPoolExecutor(PIPELINE_MAX_WORKERS) over (prompt × engine):
                    audit.audit_engine — RunPlan.reserve() (call cap + CostLedger) per sample,
                    adaptive early-stop at consensus, breaker-aware, partial outcome on stop
                  snapshot reuse (REUSE_WITHIN_HOURS) bypasses the call entirely
                  Google samples also → build_organic_snapshot(PROMPT)   [no extra call]
                  per distinct seed keyword: SerpApiClient.search → (KEYWORD) [1 call, cached]
   5b analyse     citations.build_snapshot: domains, ranks, full citation links (best
                  position per URL), client URLs, consulted URLs;
                  mentions.detect_mentions: word-bounded brand/alias/competitor matches
                  with the containing sentence (runs on the answer, never on the prompt)
   6 persist      TimeSeriesDB.upsert_prompt / record_snapshot / record_samples /
                  record_organic / record_run; assembly.model_shifts vs previous run
   7 report       assembly.build_record → write_master_sheet (CSV) +
                  write_ui_dataset (JSON, one row per prompt × engine) → TrackerRunSummary
```

Scheduling (`scheduler.py`): `TrackingJob` (client, prompts, engines, interval)
loaded from a jobs file; `Scheduler.run_due()` compares each job's stored
`last_run_at` with its interval and runs what is due through the same
`PromptTrackerPipeline`; `daemon()` polls. State lives in the `jobs` table.

Key contracts: `ClientProfile` (incl. `competitor_names`), `CustomPrompt`,
`PromptCandidate`, `CitationSnapshot` (rates over N samples, best/mean rank,
competitor best ranks, `citation_links`, `client_urls`, `consulted_urls`,
`mention_rate`, `mention_snippets`, `competitor_mentions`, `response_ids`),
`AnswerSample` (one raw answer with links and mentions), `MentionSnippet`,
`OrganicRankSnapshot`
(`RankQueryKind.PROMPT` or `KEYWORD`; best client position across samples,
ranking URL, top-10 domains, competitor positions, device), `MasterPromptRecord`,
`VelocityReport`, `OrganicVelocityReport`, `TrackerRunSummary`. Enums are UPPER
(`SearchIntent`, `DecisionStage`, `PromptType`, `Verdict`, `Engine`, `RankQueryKind`).

Keyword-rank lookups need the real `SerpApiClient`; it is built lazily even when
`GOOGLE_AI_OVERVIEW` is not among the audited engines. An injected stand-in for
that engine (tests) causes the lookup to be skipped, not faked.


### modules/control_plane (UI + API)

```
browser (static/index.html) ──▶ FastAPI app.py ──▶ ProjectStore (projects, project_prompts)
   │ polls /api/jobs/{id}                    ├──▶ JobManager: POST run → RunJob (202), one
   │ every 1 s while active                  │      worker thread, dedupe of identical
   │                                         │      active requests, in-memory history
   │                                         └──▶ ProjectRunner
                                                    planner.due_items: newest snapshot per
                                                      (prompt, platform) older than effective
                                                      interval?  → DueItem[]
                                                    planner.batches: group by (platforms, samples),
                                                      important first
                                                    each batch → PipelineInput(custom_prompts=…)
                                                      → PromptTrackerPipeline(progress=cb)
                                                      PipelineProgress events (per prompt×platform
                                                      check) folded into RunProgress on the job
                                                    results: latest CitationSnapshot per
                                                      prompt × platform + organic ranks + due flags
```

`/docs/prompt-atlas.html` and `/reports/prompt-atlas-data.json` are served by the
same app; the data document is built on each request by
`prompt_tracking/atlas_export.py` (also behind `scripts/export_dashboard.py`).

Contracts: `Project` (client, engines, interval, samples, generation, caps),
`TrackedPrompt` (verbatim text, keyword, subtopic, `important`, `enabled`,
optional `interval`/`engines`/`samples_per_engine` overrides), `RunRequest`
(force, prompt ids, engines), `RunOutcome`, `PromptResult`, `RunJob`
(state queued|running|finished|failed, `RunProgress`, outcome, error). Server:
`python -m src.modules.control_plane [--poll-minutes N] [--approve-spend]`; the
poller queues jobs through the same `JobManager` as the UI.
FastAPI and uvicorn are the `ui` extra; the engine installs without them.

### Capture-everything and insights (cycle 0011)

```
connectors ──▶ EngineAnswer{answer_text, search_queries, citation_claims, source_snippets}
   OpenAI: action.query/queries, url_citation indices → sentence_at()
   Perplexity: search_results.queries, results[].snippet/date, [n] markers → sentences_with_marker()
   Gemini: webSearchQueries, groundingSupports segments → chunk URIs
   SerpApi: AIO block text per inline link, related_searches, organic title/snippet, PAA
assembly.answer_samples ──▶ answer_samples(+answer_text, search_queries, citation_claims, source_snippets)
organic.build_organic_snapshot ──▶ organic_snapshots(+organic_results, paa_questions, related_searches)
control_plane/insights.InsightEngine.build(project, prompts)
   ← PositionStore.positions()/compute(), TimeSeriesDB.samples_for(), prompt_records(), organic_history()
   → InsightsView{basis, health, changes, actions, fanout, claims, trust_profile,
                  read_but_rejected, winning_pages, client_pages, placement, freshness}
control_plane/actions.ActionStateStore ── action_states (status, owner, note, baseline)
routes: GET /api/projects/{id}/insights · PUT /api/projects/{id}/actions/{id} · GET /api/projects/{id}/samples
```

### Per-prompt detail (cycle 0012)

`prompt_id` is minted from `(lob, prompt_text)` at creation and then **frozen**:
editing the text or renaming the LOB keeps it, so the series behind a prompt
survives (ADR 0016). The UI routes on `TrackedPrompt.id`; `prompt_id` is only the
history join key.

```
ProjectRunner.prompt_detail(project_id, tracked_id)
   ← TimeSeriesDB.history()/velocity()      per platform, newest first
   ← TimeSeriesDB.organic_history()/organic_velocity()   PROMPT and KEYWORD
   ← TimeSeriesDB.sample_run_ids()          the prompt's own runs, not the project's crawls
   ← TimeSeriesDB.capture_coverage()        which rich layers exist, counted in SQL
   ← PositionStore.positions_for_prompt()   one join across every consolidation
   → PromptDetail{result, engines[PromptEngineDetail], organic_*, run_ids,
                  positions, content_gap, capture, shared_lob_projects}

EngineStatus: has_data | asked_failed | never_asked | not_configured
   a failed call writes a snapshot row and no sample row, so failure is read
   from snapshots.failed_samples — never from a missing sample
PromptEngineDetail carries cited_samples/ok_samples and cited_in_minority because
   CitationSnapshot.client_cited is a >=50% majority verdict, not "cited at all"
insights are NOT assembled here — see the prompt scope below

routes: GET /api/projects/{id}/prompts/{tracked_id} · …/detail
        GET /api/projects/{id}/samples?prompt_id=&engine=&run_id=&limit=  (project-scoped)
```

### Prompt scope (cycle 0013)

```
GET /api/projects/{id}/insights?prompt_id=
   InsightEngine.build(prompt_id=)  — scope applied at the TOP, before aggregation:
      prompts   → the one TrackedPrompt (KeyError → 404 if not tracked)
      positions → _scoped(view.positions), previous likewise
      then _load_samples, clusters, _domain_share, claims dedup and every
      [:50]/[:200]/[:500] cap operate on the scoped set unchanged
   basis.crawls / low_confidence stay window-level; basis.samples is the prompt's
   action-card ids unchanged → scoped and project cards share analyst state

GET /api/costs?project_id=&prompt_id=
   UsageLedger.calls(prompt_id=)  — engine samples only (set in audit.py's usage_context)
   CostReport.attribution = "direct_engine_calls"
   CostReport.unattributed_calls / _actual_usd = null-prompt rows in the same runs
   (harvest, keyword rank, redirects) — reported beside, never amortised

why server-side: client-side filtering of the unscoped view loses rows to the
   project-wide caps and to the claims dedup key (sentence, url, engine), which
   has no prompt component. Both proven in test_insights.py.

UI: docs/UI_SCOPE_BRIEF.md — selector in the project header, ?scope=<tracked_id>,
   project tabs only; Atlas/Trends/Costs untouched.
```

### Consolidation window (positioning)

```
ProjectRunner.run() ─▶ PositionStore.record_project_run()   (one row per crawl, full or partial)
   full crawls since last consolidation >= project.consolidation_runs
      └─▶ PositionStore.consolidate(window=N): snapshots + answer_samples + organic
            by run id ─▶ aggregate_position() per prompt x platform ─▶ consolidations/positions
   analyst: POST /api/projects/{id}/consolidate {window_runs?, note}
   read:    GET /api/projects/{id}/positions[?consolidation_id]  → Results tab (consolidated view)
```

### Usage ledger and costing

```
BaseAPIClient.call() ──▶ UsageLedger.record(ApiCall)  (ok | error | refused, latency, estimate)
   connector.note_usage() ─▶ UsageLedger.update(...)  (model, tokens, searches, units,
                                                        vendor_cost_usd | modelled_cost_usd)
usage_context(source, run_id, prompt_id, engine)  — set by pipeline / audit / jobs / CLI /
   live_check; copied into pool threads by the pipeline
costing.build_cost_report() ──▶ per vendor / run / source + COST_* recommendations
   surfaces: CLI `costs`, GET /api/costs, control-plane Costs tab
```

`pricing.py` holds list prices per model (tokens) and per-search fees, used
only when the vendor does not report cost. Table: `api_calls` in the tracker
SQLite file.

## 3. Governance in the pipeline

- Tool is `RiskClass.FINANCIAL` with a nominal declared cost; actual per-call
  costs (settings `COST_*`) are charged to the shared `CostLedger` before each
  engine call. `BudgetExceededError` ends the audit early with a warning; records
  so far are persisted. `describe_invocation()` shows the projected total.
- Approval paths: `--approve-spend` (CLI callback) or `BudgetedApprovalProvider`
  when `UNATTENDED_SPEND_CAP_USD > 0`. Default is deny.
- Two spend ceilings (cycle 0015): `MAX_SESSION_SPEND_USD` on this process's
  reservations, and `DAILY_SPEND_CAP_USD` on actual spend since 00:00 UTC read
  back from the usage ledger (`CostLedger(spent_today=UsageLedger.spent_since)`),
  so a restart does not re-arm the budget. Semrush reports are reserved at their
  upper bound before the call and the unused part released after
  (`CostLedger.release`).
- Outbound fetches of engine-supplied URLs happen only in `RedirectResolver`,
  under `UrlSafetyPolicy` + pinned transport + robots.

### Serving it (ADR 0018)

`python -m src.modules.control_plane` binds `HOST:PORT` from settings (loopback
by default; a container sets `HOST=0.0.0.0` and the platform injects `PORT`).
With `CONTROL_PLANE_USER`/`_PASSWORD` set, `BasicAuthMiddleware`
(`control_plane/auth.py`) guards every request except `/api/health`; production
refuses to boot without them. `/api/health` probes the store and answers 503 if
it cannot. `JobManager` bounds the waiting queue (50 → 429). One `Dockerfile`
builds `ui/dist` with Node and installs the package *editable* so `REPO_ROOT`
stays under `/app`; `railway.json` pins one replica. Runbook:
`docs/DEPLOY_RAILWAY.md`.

### Who may change a project (ADR 0019)

The site login answers "may this browser reach the app"; on a shared deployment
everyone holds it. **Per-project owner credentials** answer "may this person change
this project": everyone reads every project, and only the holder of a project's
credential may edit it, change its prompts, run it, consolidate it, update its
action cards or delete it.

- Set at creation (`ProjectCreate.credentials`), or later from the project header.
  `control_plane/credentials.py` stores a salted scrypt digest in its own table,
  `project_credentials`; `Project.protected` and `Project.owner` are derived by a
  join on read and never stored in the payload.
- `control_plane/project_access.py`: `ProjectAccessGuard.require_write` is a
  dependency on every mutating `/api/projects/{id}` route. The credential travels
  as `X-Project-Authorization: Basic <owner:password>` (the `Authorization` header
  belongs to the site login). A refusal is **403** with `code` `project_locked` or
  `project_credentials_invalid`, never 401, which would make the browser drop the
  site login. Eight wrong guesses per project in five minutes answer 429.
- `GET /api/projects/{id}/access` reports what the presented credential may do;
  `PUT /api/projects/{id}/credentials` claims an open project or rotates the
  credential of a protected one.
- Projects without a credential stay open (stores older than this cycle have none).
  `PROJECT_ADMIN_PASSWORD` (optional, 16+ characters) is a recovery password that
  unlocks any project.
- UI: `ui/src/lib/projectAuth.ts` keeps the credential in `sessionStorage` per
  project; `api/client.ts` attaches it to writes, and on a 403 opens the unlock
  dialog (`app/ProjectUnlock.tsx`) and retries the write once.

## 4. Storage

SQLite at `TRACKER_DB_PATH` (ADR 0003). Tables: `prompts`, `snapshots`,
`projects`, `project_prompts` (control-plane configuration, JSON payloads),
`answer_samples` (one row per raw engine answer: model, response id, verdict),
`organic_snapshots`, `runs`, `jobs` (scheduler state). Column additions after
first release are applied idempotently by `_migrate()`. Every connection goes
through `core/sqlite.connect()` — WAL journal, 30 s busy timeout, foreign keys —
so the polling UI and the worker do not block each other. `velocity(prompt_id, engine, window_days)` compares
the mean citation rate and best rank of the latest window against the one before
it; `organic_velocity(prompt_id, kind, window_days)` does the same for the best
organic position.

## 5. Quality gate

`scripts/verify.ps1`: ruff format, ruff check, mypy strict, pytest ≥85% coverage.
`scripts/drift_check.py`: modules, connectors and settings must be documented
here or in `README.md` / `.env.example`.
