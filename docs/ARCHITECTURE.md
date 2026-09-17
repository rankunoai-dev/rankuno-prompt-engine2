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
- Outbound fetches of engine-supplied URLs happen only in `RedirectResolver`,
  under `UrlSafetyPolicy` + pinned transport + robots.

## 4. Storage

SQLite at `TRACKER_DB_PATH` (ADR 0003). Tables: `prompts`, `snapshots`,
`projects`, `project_prompts` (control-plane configuration, JSON payloads),
`answer_samples` (one row per raw engine answer: model, response id, verdict),
`organic_snapshots`, `runs`, `jobs` (scheduler state). Column additions after
first release are applied idempotently by `_migrate()`. `velocity(prompt_id, engine, window_days)` compares
the mean citation rate and best rank of the latest window against the one before
it; `organic_velocity(prompt_id, kind, window_days)` does the same for the best
organic position.

## 5. Quality gate

`scripts/verify.ps1`: ruff format, ruff check, mypy strict, pytest ≥85% coverage.
`scripts/drift_check.py`: modules, connectors and settings must be documented
here or in `README.md` / `.env.example`.
