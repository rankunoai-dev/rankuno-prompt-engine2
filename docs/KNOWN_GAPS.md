# Known Gaps

Things that are **not yet implemented** but might look like they are. Move an
entry to "Closed" with the build-log cycle number when it is done; never delete.

## Open

- `src/modules/control_plane/sampling.py` (cycle 0022, ADR 0025) — stability is
  read from the pair's shared `(lob, text)` history, so a second project on the
  same line of business with the same prompt contributes crawls to the first
  project's window and stretch clock; restricting `classify()` to the project's
  own run ids is not done. Boosts are budget-neutral in *expected* calls (a
  stable pair is assumed to early-stop at `MIN_SAMPLES`); actual spend can
  differ when a stable pair disagrees with itself. A stretched pair pools fewer
  samples per consolidation, so its band widens and the `defend` / outcome
  thresholds that compare pooled rates get noisier; band-aware change detection
  (ADR 0020 §4) is still open. Rank movement is not part of the verdict. The
  policy is set through `PUT /api/projects/{id}`; the UI control and the
  sampling tab are specified in `docs/UI_SAMPLING_BRIEF.md` and not yet built.
- `src/modules/crawler_logs/` (cycle 0020, ADR 0022) — logs are uploaded by
  hand; there is no push endpoint for Cloudflare Logpush or a server agent,
  because that needs per-source rate limiting and a credential on the
  customer's server first. Only `combined`, `vhost_combined` (a leading
  `host[:port]`), an optional `X-Forwarded-For` list at either end, and
  Cloudflare NDJSON or arrays are recognised; no custom `log_format` DSL. A
  combined log with no host field is attributed to the project's domains, so a
  server that writes several sites into one file needs the vhost format.
  Overlapping imports are resolved per day (most parsed lines wins, then the
  newest), not per line. Aggregates are stored, not lines, so a catalogue
  change does not re-classify history until the file is re-uploaded. The
  bundled IP ranges (`ranges.json`) go stale between refreshes and the view
  shows their dates; `scripts/refresh_bot_ranges.py` refetches them.
  Fetches by headless browsers that render JavaScript and fetches through
  third-party search APIs carry no crawler token and are invisible. No GA4 or
  referral side of the funnel (roadmap §5).
- `src/modules/prompt_tracking/intent_filter.py` — Layer 3 is a threshold on the
  rule-based score. No LLM judge for borderline prompts (score 0.45–0.75).
- `src/modules/prompt_tracking/prompt_generator.py` — branded prompts come from
  fixed templates, not from an LLM or from real branded keyword data. Subtopics
  default to the seed keyword unless the analyst supplies them.
- `src/modules/prompt_tracking/url_mapper.py` — landing pages must be supplied
  (`--landing-pages` file). No sitemap or crawl ingestion connector exists.
- `src/modules/prompt_tracking/pipeline.py` — parallelism is a thread pool over
  synchronous connectors, not asyncio; throughput is bounded by
  `PIPELINE_MAX_WORKERS` and each vendor's rate bucket.
- `src/modules/prompt_tracking/scheduler.py` — intervals are elapsed-time
  based (`daily` = 24h after the last run), not calendar-anchored ("every day at
  06:00"). Anchor the first run with Task Scheduler / cron if wall-clock timing
  matters. `monthly` is 30 days.
- `src/modules/prompt_tracking/mentions.py` — mention detection is exact,
  word-bounded matching on brand, aliases and competitor names. No fuzzy or
  possessive handling ("GEP's" matches; "G.E.P." does not) and no sentiment.
  Competitor terms derived from domain labels can miss brands whose domain
  differs from their name (e.g. `ariba.com` vs "SAP Ariba"); set
  `competitor_names` for those.
- `src/modules/prompt_tracking/report.py` — the JSON dataset is written per run
  and not merged across runs; the Prompt Atlas page reads the SQLite export
  instead (live via the control plane, or `scripts/export_dashboard.py`).
- `docs/prompt-atlas.html` — shows citation and mention rates, snippets and
  cited domains, but not the per-link citation list, consulted URLs or organic
  ranks that the export now carries; the control-plane Results tab does.
- `src/modules/prompt_tracking/sentiment.py` — sentiment is scored by an LLM judge
  (cycle 0018, ADR 0021) on the sentences that name the client or a competitor;
  English-first, no translation, no narrative trend over time, no re-scoring of
  history when the rubric version changes (older rows count as unscored), and
  nothing acts on it automatically. Without `ANTHROPIC_API_KEY` the step is
  skipped and the Overview says so. The Atlas share-of-voice view does not yet
  split mentions by context; the Inspection Drawer and the API do.
- `src/modules/control_plane/` — per-project owner credentials (cycle 0016, ADR
  0019) separate readers from the one person who may change a project, but this
  is access control between colleagues behind a shared login, **not tenant
  isolation**: every reader sees every project's data, anyone who can reach the
  app may create a project, projects created without a credential (and every
  project older than cycle 0016) stay open until claimed, there is no password
  reset (only the optional `PROJECT_ADMIN_PASSWORD`), and the `/legacy` page
  cannot unlock a protected project. HTTP Basic auth (cycle 0015)
  covers every route but `/api/health` once `CONTROL_PLANE_USER`/`_PASSWORD`
  are set, and production refuses to boot without them; **without them the
  app is open**, so local runs still rely on the loopback bind. No per-IP
  inbound rate limiting — only the bounded job queue (50 waiting → 429).
  Single replica by design: job state is in memory, the store is one SQLite
  volume, the spend ceilings are per process.
- `src/core/logger.py` — no redaction filter. SerpApi and Semrush send their
  keys as query-string parameters; the designed error path logs only a body
  excerpt, but a URL reaching a log line would carry the key. Scrub
  `api_key=`/`key=` before any log sink that leaves the box.
- `src/core/guardrails.py` — `BudgetedApprovalProvider` compares its cap with
  `ToolMetadata.estimated_cost_usd`, which for the pipeline is a nominal $0.01,
  not the projected spend `describe_invocation` computes. Any non-zero
  `UNATTENDED_SPEND_CAP_USD` therefore approves runs of any size. Not on the
  deployed path (`--approve-spend` is used), but real.
- `src/modules/control_plane/store.py` — `DELETE` routes hard-delete with no
  confirmation or soft-delete column; history is orphaned (prompt ids carry
  no project component). Auth closes the anonymous case, not the operator one.
- `src/modules/control_plane/jobs.py` — job state (queue, progress, outcome)
  is in memory; a server restart forgets queued jobs and their progress. The
  `runs` table remains the durable record. No cancellation: a queued or running
  job cannot be stopped from the UI (the call cap and budget ceiling still
  bound it). Progress is polled (1 s) rather than pushed. The waiting queue is
  bounded at 50 (cycle 0015); the session ceiling `MAX_SESSION_SPEND_USD` still
  resets on restart, but `DAILY_SPEND_CAP_USD` is read back from the ledger
  and does not.
- `src/modules/prompt_tracking/pipeline.py` — progress is reported per finished
  prompt × platform check, not per engine call, so a single slow check shows no
  movement until it completes.
- `src/modules/control_plane/insights.py` — action rules are thresholds
  (mention ≥ 40 % and cited ≤ 10 %, competitor share ≥ 30 %, third-party source
  share ≥ 30 %, drop ≥ 20 points, freshness gap > 365 days); no learning from
  outcomes yet. Domain classes are a hand-kept list (`_AGGREGATORS`, `_FORUMS`,
  …) — a review site not on it is classed `publisher` and does not count toward
  earned placement. Samples recorded before cycle 0011 have empty
  fan-out/claims/snippets, so cards over them are sparser.
- `src/integrations/openai_search.py` — claim spans are computed on the joined
  text before the final strip; with leading whitespace the offsets shift by
  that amount (sentence text is exact regardless).
- `src/modules/control_plane/positioning.py` — consolidated positions live in
  the control plane only; Prompt Atlas still shows per-run snapshots. The
  window counts full crawls, not calendar days; a crawl that ran nothing
  (nothing due) is not recorded and does not count.
- `src/modules/control_plane/planner.py` — intervals are elapsed-time per
  (prompt, platform); no calendar anchoring ("every Monday 06:00").
- `src/modules/control_plane/store.py` — `prompt_id` is minted from
  `(lob, prompt_text)` and then frozen (ADR 0016), so it has no project
  component. Two projects on the same line of business still produce the same
  id for the same prompt text and **share one history**; `runner.results` reads
  snapshots globally by `prompt_id`, so each would show the other's samples.
  `PromptDetail.shared_lob_projects` names the other projects so the UI can warn,
  but nothing prevents the merge. Deleting a prompt also keeps its rows, so
  re-adding the same text in the same LOB resurrects the old series.
- `src/modules/prompt_tracking/schemas.py` — `CitationSnapshot.client_cited` is a
  **majority verdict** (`client_citation_rate >= 0.5`), not "cited at all". 482
  snapshots in the current store hold a real `client_best_rank` while storing
  `client_cited = 0`. `PromptEngineDetail` exposes `cited_samples` / `ok_samples`
  and a `cited_in_minority` flag; the React UI's `promptView.ts` still derives
  `linkedOn` from `client_cited` alone and renders those as absent.
- `src/modules/control_plane/runner.py` — `prompt_detail` issues one `history`
  call per platform plus a `velocity` call that re-reads the same series. Fine
  for one prompt on demand; do not call it in a loop over a project.
- `src/modules/control_plane/insights.py` — under `prompt_id` scope, action
  cards keep their project-wide ids (`_action_id` has no prompt component), so a
  scoped card and the project card for the same (subtopic × engine) cluster
  **share analyst state**. Intended — same prescription — but `impact_score` is
  not comparable across scopes. `freshness` cards almost never fire for one
  prompt (both medians are usually `None`). `EngineHealth.prompts`,
  `FanoutQuery.prompts` and `PageInventory.prompts` are always 1 under scope.
- `src/modules/prompt_tracking/costing.py` — per-prompt spend is **direct engine
  calls only**. Harvest (Semrush), keyword rank and redirect resolution are
  recorded with no `prompt_id` and reported as `unattributed_*` beside the
  figure, never divided. A prompt's true share of run cost is therefore unknown.
  `/api/costs` also narrows by LOB, not project (`app.py`), so two projects on
  one LOB share a cost figure.
- `src/modules/prompt_tracking/audit.py` — adaptive early-stop uses agreement
  on *client cited or not*; it does not test agreement on rank position.
- `src/modules/prompt_tracking/time_series_db.py` — single-tenant SQLite. No
  migrations framework; schema changes require a manual migration.
- `src/integrations/url_resolver.py` — `PinnedTransport` pins to the first
  resolved address only; no retry across the other addresses.
- `src/integrations/gemini_search.py` — redirect resolution is opt-in
  (`--resolve-redirects`). Without it Gemini citations whose payload lacks
  `web.domain` are attributed by `title` heuristics and flagged `resolved=False`.
- `src/integrations/semrush.py` — unit costs per row (40/10/40) are declared
  constants from the vendor price list and are not verified against the live
  `countapiunits` balance endpoint.
- `src/core/config.py` — per-call `COST_*` estimates are still operator-set
  constants at reservation time. The usage ledger (cycle 0009) records the
  vendor-reported or modelled cost per call and proposes new values; the
  operator copies them into `.env`. Live charging of actual cost is not done.
- `src/integrations/pricing.py` — list prices are hand-maintained and dated;
  unknown models borrow their family card. OpenAI and Gemini do not report
  cost per response, so their "actual" is modelled until the operator checks
  the vendor invoice against the ledger totals.
- `src/integrations/usage.py` — ledger rows tagged `backfill` for calls made
  before the ledger existed carry estimates (or saved vendor costs) and notes;
  one Perplexity probe that timed out client-side has unknown billing.
- `src/integrations/gemini_search.py` — the operator's Google AI Studio project
  reports `RESOURCE_EXHAUSTED` (prepaid credits depleted, 2026-09-17); Gemini
  samples fail until billing is topped up. The failure text is now in the log.
- `src/modules/control_plane/app.py` — the per-project model list (added by a
  parallel session) still offers retired Perplexity names and a Gemini model
  under the Perplexity engine; retired names are mapped to `perplexity/sonar`,
  but a third-party model there would not measure Perplexity.
- Snapshots recorded on 2026-09-17 before cycle 0008 under-report citations for
  ChatGPT Search, Perplexity and Google AI Overview (ADR 0011). History is
  append-only; they are not rewritten.
- `.agents/rules/coding_standards.md` §9 names `AsyncTokenBucket.from_crawl_delay()`;
  the codebase is synchronous and provides `TokenBucket.from_crawl_delay()`
  instead. Same guarantee, sync API.
- `src/core/base_tool.py` — `run()` returns `ToolResult[BaseModel]`, so
  `model_dump_json()` on a result serialises `data` as `{}` unless callers pass
  `serialize_as_any=True` (the CLI does). Returning `ToolResult[OutputT]` would
  fix every consumer.
- `src/core/retry.py` — `retry_policy()` reads `get_settings()` for the default
  attempt count rather than injected `Settings`; `BaseAPIClient.call()` now
  passes `max_attempts` explicitly, but other callers of `with_retries()` still
  see global settings.
- No live integration test suite (`@pytest.mark.integration`) yet; every engine
  payload shape in tests is a fixture modelled on vendor docs, not a recording.
  A manual live check script exists in the operator's scratch area only; cycle
  0008 showed fixtures alone missed four real faults.

- `src/core/locale.py` — one market per project (ADR 0023). Several locales
  inside one project are not possible: every stored row is keyed by
  `(prompt_id, run_id, engine)`, so a second market is a second project, with a
  second crawl's spend.
- `src/integrations/gemini_search.py` — Gemini cannot be localised. The
  Developer API's `google_search` tool takes an empty object and has no
  location field, so a Gemini sample follows the billing account's country
  whatever the project's locale says. `Engine.honours_locale` is false for it
  and the UI badge says so.
- `src/integrations/perplexity.py` — `user_location` is documented by
  Perplexity as a hint that steers results, not a guarantee. A Perplexity
  sample is labelled with the requested market, never a verified one.
- `src/core/locale.py` — `serp_location` is stored verbatim because SerpApi
  rejects names outside its own database. Nothing derives a canonical location
  string from `city`, and an unknown string fails in the connector, not in
  validation.
- Device remains tracker-wide (`SERP_DEVICE`); mobile versus desktop is not
  part of the project's market and would double every crawl.
- A locale sets `hl`, not the prompts. A French market needs prompts authored
  in French; `src/modules/prompt_tracking/prompt_generator.py` writes English.

- `src/modules/alerting/triggers.py` — the `spend` and `crawl_failed` rules are
  defined and selectable but nothing raises them yet; they need the usage
  ledger and the run outcome wired into the alerting phase. A project that
  ticks them today hears nothing.
- `src/modules/reporting/` — reports are generated on request only. There is no
  schedule, so "send the client a PDF on the 1st" is still a human action.
- `src/modules/reporting/facts.py` — `show_spend` prints the project's whole
  tracked vendor spend, not the spend of the crawls in the reported window: the
  usage ledger is not keyed by consolidation.
- `src/modules/reporting/pdf.py` — the document carries one window and the
  previous window's totals, so there is no trend chart across consolidations,
  and the report is English only whatever the project's locale says.
- `src/modules/alerting/triggers.py` — the citation-drop rule deliberately
  misses real drops in small windows: it needs ten answered samples a side and
  non-overlapping 95% intervals. Quiet by design (ADR 0024).
- `src/integrations/email_send.py` — SMTP only, one message per call, no
  bounce handling and no unsubscribe: alerts and reports go to addresses an
  operator typed, not to a list.
- `ui/public/mockServiceWorker.js` is absent, so `VITE_MOCK=1` in a browser
  cannot register MSW; the node-side test server is unaffected.
- No `conftest.py` socket guard: "every external call is mocked" is a
  convention. Cycle 0021 found one test that really posted to Slack.

## Closed

- (cycle 0014) `earned_placement` cards showed shares above 100% (338% on the
  demo project) because per-domain answer shares were summed, and the same sum
  inflated the impact score so those cards outranked everything — the share is
  now the fraction of cited sources that are third-party, and the impact is
  scaled by the gap to target like every other card.
- (cycle 0003) No circuit breaker — `src/core/circuit_breaker.py`, wired into
  `BaseAPIClient.call()`.
- (cycle 0003) Runs sequential, no scheduler — thread pool in `pipeline.py`,
  `scheduler.py` + `schedule` CLI subcommands.
- (cycle 0011) Responses were discarded beyond citations and a 300-char excerpt;
  nothing turned measurement into action — capture-everything connectors,
  `insights.py`, action cards with outcomes (ADR 0014).
- (cycle 0009) No per-call volume or cost record; estimates could not be
  corrected from evidence — `api_calls` ledger, `costing.py`, CLI `costs`,
  `/api/costs`, Costs tab (ADR 0012).
- (cycle 0008) Structured log fields (`extra=`) were silently dropped by the
  logger adapter — `_MergingAdapter` in `core/logger.py`.
- (cycle 0008) ChatGPT/Perplexity samples answered without searching; Google
  AI Overview inline sources ignored — ADR 0011.
- (cycle 0007) Prompt Atlas showed only the citation rate and read a stale
  file — `atlas_export.py` served live by the control plane; mention rate
  beside citation rate in every engine cell, tile and engine table.
- (cycle 0006) Control-plane runs blocked the HTTP request with no progress —
  `jobs.py` background worker, `PipelineProgress`/`RunProgress`, progress bar
  and completion notifications in the UI (ADR 0010).
