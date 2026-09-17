# Cycle 0001 — Prompt tracker foundation

**Date**: 2026-09-16
**Operator approval**: "yes approved" (chat, 2026-09-16) against the investigation
findings and recommended decisions presented in the same session.

## Scope

Stand up `Desktop\prompt-engine` as a self-contained repository implementing the
Prompt Tracker blueprint v2: governed core, five connectors, redirect resolver,
and the 7-step `prompt_tracking` module with CLI, SQLite time-series and CSV
master sheet. Acceptance: `scripts\verify.ps1` exits zero at ≥ 85% coverage;
docs drift audit clean; ADRs for every consequential decision.

## Research findings (Step 2)

- `project-standards/src/core` already implemented the governed core the rules
  demand; `url_safety`, `robots` and a crawl-delay bucket named in the rules did
  not exist anywhere. Copied core verbatim (ADR 0001), added the missing pieces.
- Phase 1 "200 OK" validation scripts (`.gemini/antigravity/brain/7ed17b82-…/scratch/`)
  proved key validity only: OpenAI called without search, Gemini without
  grounding, Perplexity via a model router with `web_triggered` hard-coded.
  The saved Semrush capability report shows `ERROR 122` on every endpoint,
  contradicting the walkthrough. Endpoint choices redone in ADR 0004.
- The same script embeds five live API keys as plain-text fallbacks. Not copied.
  **Rotation required.**
- Vendor docs confirmed: SerpApi AI Overview often needs a second call with a
  `page_token` that expires within a minute; Gemini grounding URIs are Google
  redirects; OpenAI web search lives on the Responses API; Semrush bills per row
  with a 10,000-row default.

## What was built

- `src/core`: + `url_safety.py`, `robots.py`, `domains.py`,
  `BudgetedApprovalProvider`, `UnsafeUrlError`, `UpstreamClientError`,
  `TokenBucket.from_crawl_delay`, tracker settings (`SERPAPI_KEY` alias accepted).
- `src/integrations`: `http.py` (pinned transport, status→error map), rewritten
  `semrush.py`, new `openai_search.py`, `perplexity.py`, `gemini_search.py`,
  `serp_api.py`, `url_resolver.py`, shared `schemas.py` (`EngineAnswer`).
- `src/modules/prompt_tracking`: `schemas`, `intent_filter`, `prompt_generator`,
  `selector`, `citations`, `url_mapper`, `time_series_db`, `report`, `pipeline`
  (`BaseTool`, FINANCIAL), `__main__` CLI.
- Tests mirror `src/` package-for-package; all network via `httpx.MockTransport`
  or stubs. Docs: README, ARCHITECTURE, KNOWN_GAPS, ADR 0001–0004, blueprint v2.
  The project-standards blueprint now points at v2.

## Bugs found and fixed

- `base_client.call()` ignored injected `Settings` for retry count (used global
  `get_settings()`); now passes `max_attempts` explicitly. Found by a test that
  would otherwise have slept through three real backoffs.
- `semrush._keyword_report` treated `display_limit=0` as unset via `or`, turning
  an explicit zero into 50 billed rows; now `None`-checked and `0` raises.
- CLI `--json` printed `"data": {}` because `BaseTool.run()` returns
  `ToolResult[BaseModel]`; fixed with `serialize_as_any=True`. Root cause left
  open in `KNOWN_GAPS.md`.
- `RedirectResolver.resolve()` let `IntegrationError` from a hop escape despite
  documenting best-effort semantics; now returns a `resolved=False` result.
- Copied `base_tool.py` had a mypy-unreachable branch under the newer mypy;
  reworked the isinstance check. Copied `drift_check.py` failed ruff; rewritten
  and extended to check connectors and settings.
- Spec bug: the blueprint's `CitationSnapshot` recorded one boolean per engine
  per day. Replaced with N-sample rates (`SAMPLES_PER_ENGINE`, default 3).

## Corrections

- Phase 1 blueprint §2 "100% Live Verified" is withdrawn as a capability claim;
  it verified credentials. Recorded in blueprint v2 §2 and ADR 0004.
- The Phase 1 walkthrough's Semrush "[AVAILABLE]" results are contradicted by the
  persisted report file in the same folder.
- The rules' `AsyncTokenBucket.from_crawl_delay()` does not exist; the codebase is
  synchronous and provides `TokenBucket.from_crawl_delay()`.

## Explicitly not done

See `docs/KNOWN_GAPS.md`. Highlights: no LLM judge in the intent gate; branded
prompts are template-generated; no sitemap ingestion (landing pages supplied by
file); sequential execution, no scheduler; SQLite single-tenant, no migrations;
no circuit breaker; no live `@pytest.mark.integration` suite — every vendor
payload in tests is a fixture modelled on documentation, not a recording.
**No live API call has been made from this repository yet.**

## Step 5 audit answers

1. Hosts: api.semrush.com, api.openai.com, api.perplexity.ai,
   generativelanguage.googleapis.com, serpapi.com, plus arbitrary redirect hops
   (HEAD only, resolver). Default run: 20 × 4 × 3 = 240 engine calls + ≤ 20 AIO
   follow-ups + Semrush 2 calls/seed.
2. Rate keys: `semrush.api`, `openai.responses`, `perplexity.chat`,
   `gemini.generate_content`, `serpapi.search`, `web.redirect_resolver` (30 rpm)
   plus per-host robots buckets. No sharing between vendors.
3. Worst case per invocation with defaults ≈ $6 engine spend; bounded by
   `MAX_SESSION_SPEND_USD`; every call charged to `CostLedger` first. A
   10,000-item batch is impossible in one process without raising the ceiling.
4. Idempotency: reads only; `prompt_id` upsert makes re-runs append history.
5. Circuit breaker: none (gap). Retries only on transient statuses.
6. PII: none read or stored. Audit log excludes secrets (`SecretStr`).
7. All vendor payloads narrowed into `StrictModel`s inside connectors; prompt text
   is analyst-authored, not fetched.
8. Credentials via `get_settings()` only; `.env` gitignored; CI secret-scan job kept.

## Gate output (verbatim)

```
=== Format ===
93 files already formatted
PASSED: Format
=== Lint ===
All checks passed!
PASSED: Lint
=== Type check ===
Success: no issues found in 36 source files
PASSED: Type check
=== Tests ===
Name                               Stmts   Miss Branch BrPart  Cover   Missing
------------------------------------------------------------------------------
src\core\base_tool.py                 82      6     16      2    92%   68, 108, 193-194, 226-228
src\core\guardrails.py                86      2     20      2    96%   197, 213
src\core\logger.py                    59      1     10      1    97%   107
src\core\rate_limiter.py             113      4     26      2    96%   77-78, 133->138, 237-238
src\core\retry.py                     20      2      0      0    90%   47-48
src\core\url_safety.py                71      1     18      0    99%   64
src\integrations\url_resolver.py      97      2     18      0    98%   105-106
------------------------------------------------------------------------------
TOTAL                               2078     18    434      7    99%
29 files skipped due to complete coverage.
Required test coverage of 85.0% reached. Total coverage: 99.00%
416 passed in 8.80s
PASSED: Tests
ALL GATES PASSED.
```

Drift audit: `scripts/drift_check.py` → "No documentation drift detected."
