# Known Gaps

Things that are **not yet implemented** but might look like they are. Move an
entry to "Closed" with the build-log cycle number when it is done; never delete.

## Open

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
