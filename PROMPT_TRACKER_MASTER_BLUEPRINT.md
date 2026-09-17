# RANKUNO PROMPT INTELLIGENCE & REAL-TIME AUTONOMOUS PROMPT TRACKER
## Master Technical Specification & Architecture Blueprint — v2 (2026-09-16)

> v2 supersedes the Phase 1 blueprint. Section 2 records what the Phase 1
> validation actually proved and what changed as a result. Implementation lives
> in this repository; see `docs/ARCHITECTURE.md` for the as-built view and
> `docs/KNOWN_GAPS.md` for what is not yet implemented.

---

## 1. Vision & Purpose

RankUno is an AI Visibility & Prompt Intelligence platform for AEO/GEO work.
Compared with legacy SEO suites and early GEO tools it is:

1. **100% programmatic** — no manual CSV exports from vendor UIs.
2. **Live** — every audit queries the engines' live search paths, not stored panels.
3. **Intent-aware** — listicles, job and investor queries are purged before selection.
4. **Longitudinal** — every prompt is sampled repeatedly and stored, so
   month-over-month velocity compares distributions, not single snapshots.

---

## 2. Phase 1 Validation — corrected record

The Phase 1 matrix reported five "200 OK" results. The scripts behind it proved
key validity, not the capability claimed:

| Provider | Phase 1 call | What it proved | v2 decision |
| :-- | :-- | :-- | :-- |
| Semrush v3 | `phrase_questions` | Key format accepted (the saved capability report shows `ERROR 122` for every endpoint) | Keep v3 `phrase_all` / `phrase_questions` / `phrase_related`, explicit `display_limit`, per-run unit ceiling |
| Perplexity | Agent API `/v1/responses`, model `google/gemini-3.6-flash` | Router works; answer was Gemini's; `web_triggered` was hard-coded | Agent API with `perplexity/sonar` (Perplexity's own model) and the `web_search` tool forced; `/chat/completions` was retired by the vendor (ADR 0011) |
| OpenAI | `/v1/chat/completions`, `gpt-4o-mini` | Key works; **no web search occurred** | Responses API + `web_search` tool; citations from `url_citation` annotations |
| Gemini | `generateContent` without tools | Key works; **no grounding occurred** | `google_search` tool; key in header; redirect links flagged and optionally resolved |
| SerpApi | `engine=google` | SERP returned; async AI Overview path unhandled | Chain `google_ai_overview` with `page_token`; fixed `gl/hl/location` |

Security note: the Phase 1 script embedded live keys as fallbacks in plain text.
Those keys must be rotated. None were copied into this repository.

---

## 3. Architecture (as built)

```
                        PromptTrackerPipeline  (BaseTool, RiskClass.FINANCIAL)
                                     │
   ┌──────────────┬──────────────────┼──────────────────┬──────────────────┐
   ▼              ▼                  ▼                  ▼                  ▼
 Semrush      OpenAI Responses   Perplexity         Gemini            SerpApi
 phrase_*     + web_search       sonar-pro          + google_search   google (+AIO)
   │              └──────────────────┴──────────────────┴──────────────────┘
   │                                   EngineAnswer (unified contract)
   ▼                                              │
 KeywordRecord ─▶ PromptGenerator ─▶ IntentClassifier ─▶ select_master_set (10+10)
                                                  │
                                       build_snapshot (N samples → rates, ranks)
                                                  │
                                    TimeSeriesDB (SQLite) ─▶ velocity()
                                                  │
                             UrlMapper ─▶ MasterPromptRecord ─▶ CSV master sheet
```

Layering: `modules → integrations → core`, inward only. Every outbound request
goes through a `BaseAPIClient` subclass (rate limit, retry, audit log). Every
fetch of an engine-supplied URL goes through `UrlSafetyPolicy` (SSRF guard),
a pinned transport, and robots.txt.

Repository: `C:\Users\RankUno\Desktop\prompt-engine`.

---

## 4. Intent & Entity Filter (3 layers, rule-based)

- **Layer 1 — reject**: company listicles (`top 10 companies/vendors/providers`),
  jobs/salaries/careers/hiring/internships, stocks/shares/tickers, courses,
  certifications, degrees, news. Score 0.1.
- **Layer 2 — score**: software/solution vocabulary (software, platform, system,
  tool, solution, features, pricing, cost, integration, automation, ERP, WMS,
  TMS, SaaS, cloud, AI …) → 0.6 + 0.15 per extra signal; question framing +0.1.
- **Layer 3 — gate**: keep at score ≥ 0.6.

Also classified: `SearchIntent` (INFORMATIONAL / COMMERCIAL / TRANSACTIONAL /
NAVIGATIONAL), `DecisionStage` (AWARENESS / CONSIDERATION / DECISION /
POST_PURCHASE) and `PromptType` (BRANDED if the brand or any alias matches on a
word boundary).

Gap: no LLM judge for borderline scores (see `docs/KNOWN_GAPS.md`).

---

## 5. Data contracts (Pydantic v2, `StrictModel`)

```python
class Citation(StrictModel):
    url: str
    domain: str
    title: str | None
    position: int  # 1-indexed
    resolved: bool = True  # False = still a vendor redirect


class EngineAnswer(StrictModel):
    engine: Engine
    model: str
    prompt: str
    answer_text: str
    web_triggered: bool
    citations: list[Citation]
    consulted_urls: list[str]
    captured_at: datetime
    latency_ms: float
    response_id: str | None


class CitationSnapshot(StrictModel):  # N samples aggregated
    engine: Engine
    model: str
    captured_at: datetime
    samples: int
    failed_samples: int
    web_trigger_rate: float
    client_cited_samples: int
    client_citation_rate: float
    client_cited: bool  # cited in >= half the samples
    client_best_rank: int | None
    client_mean_rank: float | None
    cited_domains: list[str]  # union, most frequent first
    competitor_citations: dict[str, int]  # best rank per competitor
    answer_excerpt: str


class MasterPromptRecord(StrictModel):
    prompt_id: str
    lob: str
    subtopic: str
    core_keyword: str
    search_volume: int
    prompt_text: str
    search_intent: SearchIntent
    decision_stage: DecisionStage
    prompt_type: PromptType
    web_triggers: bool
    citation_history: list[CitationSnapshot]
    mapped_url: str | None
    content_gap: bool
    verdict: Verdict
    verdict_reason: str
    created_at: datetime
```

`prompt_id = sha256(lob | prompt_text)[:16]`, so re-runs append history.

---

## 6. The 7-step methodology (implemented)

1. **Harvest** — Semrush `phrase_all` (seed volume) + `phrase_questions` per seed.
2. **Generate** — Semrush questions (non-branded) + stage templates (branded and
   non-branded fallbacks), de-duplicated.
3. **Gate** — 3-layer intent filter.
4. **Select** — exactly 10 branded + 10 non-branded, round-robin across the four
   stages by (intent score, volume); shortfalls are reported, never padded.
5. **Audit** — each prompt × each engine × `SAMPLES_PER_ENGINE` (default 3).
   Web-trigger status is engine-specific and recorded as a rate. The Google
   call also yields the prompt's **organic rank** (no extra cost); one extra
   SerpApi call per distinct seed keyword yields the **keyword's organic rank**
   (ADR 0005). Device fixed by `SERP_DEVICE`.
6. **Persist** — prompts, citation snapshots, organic rank snapshots and run
   header to SQLite.
7. **Map & report** — landing-page mapping by slug-token overlap or
   `[CONTENT GAP: Need <Subtopic> Page]`; verdict; CSV master sheet.

---

## 7. Governance & cost

- Tool is FINANCIAL; deny-by-default. Runs need `--approve-spend` (human) or
  `UNATTENDED_SPEND_CAP_USD > 0` (budgeted, for schedulers).
- Every engine call is charged to the `CostLedger` before it is made;
  `MAX_SESSION_SPEND_USD` is the hard stop. Default per-call estimates
  (`COST_*` settings): OpenAI 0.03, Perplexity 0.02, Gemini 0.04, SerpApi 0.01 USD.
- Default run: 20 × 4 × 3 = 240 engine calls ≈ $6.00, plus one SerpApi call per
  seed keyword for keyword rank, plus Semrush units. Set `MAX_SESSION_SPEND_USD`
  accordingly.

---

## 7a. Scale & reliability (cycle 0003, ADR 0006–0007)

- Adaptive sampling (min 2, stop at consensus), per-run call cap, snapshot
  reuse window, per-vendor circuit breaker, bounded thread pool, raw answer
  samples with model/response id and model-shift warnings.
- Analyst prompts via `--prompt` / `--prompts-file` / job file; Semrush
  generation optional.
- Interval scheduling: jobs file + `schedule run-due|daemon|status|install-task`;
  intervals `hourly|daily|weekly|monthly|<n>min|h|d|w|mo`.

## 7b. Links, mentions & unpolluted prompts (cycle 0004, ADR 0008)

- Prompts go to engines verbatim; the client profile is applied only to the
  returned answer.
- Every citation URL (position, title, domain), the client's cited URLs and
  consulted URLs are stored per sample and per snapshot.
- Mention detection: exact word-bounded brand/alias/competitor matches with the
  containing sentence as snippet; `mention_rate` across samples.
- Per-run UI dataset (JSON, one row per prompt × engine) next to the CSV.

## 7c. Control plane UI (cycle 0005, ADR 0009)

Local FastAPI app + single page: projects (client inputs, platforms, default
interval), prompt upload/paste/add, starred prompts with per-prompt interval,
platforms and samples, run now / run due / run selected, results per platform
with links and mention snippets, run history. Due work derived from snapshot
history per (prompt, platform).

## 7d. Background runs & progress (cycle 0006, ADR 0010)

Runs are queued to one worker thread (`JobManager`); `POST …/run` answers 202
with a `RunJob`. The pipeline emits `PipelineProgress` per prompt × platform
check; the runner folds batches into `RunProgress`. The page polls the job,
draws the bar, and announces completion (toast, browser notification, tab
title). Identical active requests are deduplicated; the poller queues through
the same worker.

## 7e. Usage ledger & costing (cycle 0009, ADR 0012)

Every vendor request is recorded (`api_calls`): source, run, prompt, engine,
model, status, latency, tokens, searches, units, estimated / vendor-reported /
modelled cost. `costs` CLI, `GET /api/costs` and the Costs tab aggregate per
vendor, run and source and propose `COST_*` values from observed means.

## 7f. Consolidation window (cycle 0010, ADR 0013)

Per project, `consolidation_runs` (default 3) is independent of the run
interval. Each crawl is stored on its own date; after every N-th full crawl
(or on demand) positions per prompt × platform are recomputed from all N
crawls and stored as a dated set (`consolidations`, `positions`). Results tab:
consolidated view (default) and point-in-time view.

## 7g. Capture everything & insights (cycle 0011, ADR 0014)

No extra calls: connectors keep full answer text, the engine's own search
queries, claim-to-source sentences, source snippets/dates, consulted-but-not-
cited pages, PAA/related searches. `InsightEngine` computes health verdicts,
changes, eight kinds of action cards with evidence and outcomes, fan-out map,
claim ledger, trust profile, winning/client pages, placement, freshness.

## 8. Execution

```powershell
.\.venv\Scripts\python.exe -m src.modules.prompt_tracking --lob "<LOB>" --brand <Brand> `
    --domain <client.com> --keyword "<seed>" [--competitor sap.com] [--alias "..."] `
    [--landing-pages urls.txt] [--samples 3] [--engine CHATGPT_SEARCH] `
    [--resolve-redirects] [--skip-engine-audit] --approve-spend
```

Quality gate: `scripts\verify.ps1` (ruff, mypy strict, pytest ≥ 85%).
Build history: `docs/build-log/`. Decisions: `docs/adr/0001`–`0005`.
