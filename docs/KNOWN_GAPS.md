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
- `src/modules/control_plane/` — single-user, no authentication; bind to
  127.0.0.1 only.
- `src/modules/control_plane/jobs.py` — job state (queue, progress, outcome)
  is in memory; a server restart forgets queued jobs and their progress. The
  `runs` table remains the durable record. No cancellation: a queued or running
  job cannot be stopped from the UI (the call cap and budget ceiling still
  bound it). Progress is polled (1 s) rather than pushed.
- `src/modules/prompt_tracking/pipeline.py` — progress is reported per finished
  prompt × platform check, not per engine call, so a single slow check shows no
  movement until it completes.
- `src/modules/control_plane/insights.py` — action rules are thresholds
  (mention ≥ 40 % and cited ≤ 10 %, competitor share ≥ 30 %, drop ≥ 20 points,
  freshness gap > 365 days); no learning from outcomes yet. Domain classes are a
  hand-kept list (`_AGGREGATORS`, `_FORUMS`, …). Samples recorded before cycle
  0011 have empty fan-out/claims/snippets, so cards over them are sparser.
- `src/integrations/openai_search.py` — claim spans are computed on the joined
  text before the final strip; with leading whitespace the offsets shift by
  that amount (sentence text is exact regardless).
- `src/modules/control_plane/positioning.py` — consolidated positions live in
  the control plane only; Prompt Atlas still shows per-run snapshots. The
  window counts full crawls, not calendar days; a crawl that ran nothing
  (nothing due) is not recorded and does not count.
- `src/modules/control_plane/planner.py` — intervals are elapsed-time per
  (prompt, platform); no calendar anchoring ("every Monday 06:00").
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
