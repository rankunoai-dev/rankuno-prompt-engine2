# Cycle 0023 — OpenRouter route for every language-model platform

**Date**: 2026-09-29
**Operator request**: "i wanted a functionality that all the LLM platform use
openRouter Key … i want the spend credit for all the platform but the openRoute
keys would be used for all the platforms"; then approval of the plan and of a
three-call live probe.

Decision record: ADR 0026.

## Research findings

- The readiness audit of the same day found the previous OpenRouter fallback
  (`b0623fc`) non-functional for sentiment and unsafe for every other vendor;
  see ADR 0026, Context.
- OpenRouter's documentation says native search is used for OpenAI, Google,
  Anthropic and Perplexity models that support it. The probe showed
  `openai/gpt-4o-mini` does not, which is why the ChatGPT default differs by route.
- OpenRouter's model list confirms `anthropic/claude-haiku-4.5`,
  `anthropic/claude-sonnet-5`, `openai/gpt-5-mini` and `google/gemini-3.6-flash`
  all accept a JSON-schema response format; `perplexity/sonar` accepts neither
  reasoning nor response format, so neither is sent to it.

## What was built

- `src/integrations/openrouter.py`: `engine_route`, `openrouter_engine_model`,
  `openrouter_claude_model`, `OpenRouterClient.classify`,
  `OpenRouterEngineClient.ask`, `judge_client`.
- `config.py`: no key substitution in `require()`; blank equals missing;
  `LLM_ROUTE`, `OPENROUTER_CHATGPT_MODEL`.
- `pipeline._engine`: OpenRouter client for a language-model platform when the
  route says so.
- `runner._resolve_judge` and `narrative.compose`: either key.
- `anthropic_judge.py`: the Bearer branch removed; Anthropic key and host only.
- `pricing._normalise`: reads OpenRouter ids, so the narrative's spend figure
  finds the Claude card.
- `/api/options`: priced ChatGPT choices; the Gemini-under-Perplexity choice removed.
- UI `ProjectForm`: edit keeps branding and sampling policy; searchable model pickers.
- Tests: `tests/integrations/test_openrouter.py` (routing, model ids, key
  isolation for five vendors, Perplexity marker claims, OpenAI and Gemini spans
  and native search, billed cost in the ledger, error classes, the judge's JSON
  schema, bad endings, fences, key preference); a pipeline routing test; the
  runner's judge with only the OpenRouter key; the narrative with only the
  OpenRouter key; `tests/test_isolation.py`; `ui/src/pages/projects/ProjectForm.test.ts`.

## Bugs found and fixed

- **Paid calls from the test suite.** `tests/modules/reporting/test_store_and_worker.py`
  and `test_narrative.py` built `Settings()` from the real `.env`. With the
  narrative now accepting the OpenRouter key, the end-to-end report test made a
  real narrative call. The OpenRouter balance moved from $0.034 (the approved
  probe) to $0.121, so about $0.087 was spent by tests without approval. Fixed
  for the whole suite in `tests/conftest.py`; verified by reading the balance
  before and after a full run.
- The same isolation gap explains the Slack rows the audit found in the real
  usage ledger: alert tests wrote to `data/prompt_tracker.sqlite`.
- Twice more, the shell tool halved backslashes in generated code: a regex
  back-reference in `pricing.py` became control characters. Caught by a
  control-character scan across `src/`; fixed by writing the characters by code.

## Corrections

- `b0623fc`'s message said the judge and demo backfill "now work with the $15
  OpenRouter budget". They did not: the ledger held no Anthropic or OpenRouter
  call, and all 4,777 judgements were seeded.
- The feasibility doc's plan pasted into this cycle proposed `gpt-4o-mini`,
  `gemini-2.0-flash` and Claude 3.5 models as OpenRouter defaults. The probe
  refuted the first; the other two were older than the models already configured.

## Explicitly not done

- **The remaining two probe calls** (ChatGPT and Gemini with room to answer)
  were not re-run, so whether `gpt-5-mini` and Gemini return citation
  annotations through OpenRouter is still unmeasured. The parser handles
  both outcomes; the first real crawl will tell.
- No locale on the OpenRouter route.
- The other audit blockers are untouched: spend outside the caps, refused runs
  counted as crawls, Slack 4xx aborting dispatch, project deletion leaving
  data, the demo project being runnable.
- No UI label yet saying which route a platform's samples came from; the model
  id on each sample is the only marker.

## Gate output (verbatim; progress dots and unrelated coverage rows elided)

`scripts\verify.ps1`:

```
=== Format ===
287 files already formatted
PASSED: Format
=== Lint ===
All checks passed!
PASSED: Lint
=== Type check ===
Success: no issues found in 98 source files
PASSED: Type check
=== Tests ===
============================== warnings summary ===============================
=============================== tests coverage ================================
src\integrations\openrouter.py                      202      2     60      6    97%   139, 180->182, 393->388, 404->402, 411, 414->408
TOTAL                                              9705    246   2208    150    96%
45 files skipped due to complete coverage.
Required test coverage of 85.0% reached. Total coverage: 96.46%
1017 passed, 2 warnings in 284.17s (0:04:44)
PASSED: Tests
ALL GATES PASSED.
```

OpenRouter credit used, read from `/api/v1/key` before and after that run (unchanged, so the suite spent nothing):

```
before 0.12076825
exit=0
after 0.12076825
```

`scripts\drift_check.py`: no documentation drift detected. UI: typecheck and lint clean; `vitest` on `src/pages/projects`, `src/api`, `src/lib`: 10 files, 49 tests passed.
