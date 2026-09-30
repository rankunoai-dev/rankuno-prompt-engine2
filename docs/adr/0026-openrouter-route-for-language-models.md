# ADR 0026 — OpenRouter as a route for every language-model platform

**Date**: 2026-09-29
**Status**: Accepted (operator approved the plan and the three-call probe on 2026-09-29)
**Supersedes**: the OpenRouter fallback added in commit `b0623fc`

## Context

The operator wants one key and one credit balance for every language-model
platform: ChatGPT, Perplexity, Gemini, and the Claude models behind sentiment
and the report narrative. Gemini's direct Google account has had depleted
credits since 2026-09-17, and there is no Anthropic key at all.

Commit `b0623fc` tried to do this by making `Settings.require()` return the
OpenRouter key for *any* unset credential. The readiness audit of 2026-09-29
found that it did not work and was unsafe:

- The Anthropic judge still posted to `api.anthropic.com`, only with a Bearer
  header; the unit test checked the header, not the destination.
- Sentiment and the report narrative checked for `ANTHROPIC_API_KEY` first and
  skipped, so the fallback was never reached.
- Every connector calls `require()`, so a missing OpenAI, Perplexity, Gemini,
  SerpApi or Semrush key would have sent the OpenRouter key to that vendor
  (SerpApi takes it as a query-string parameter).

## The probe (2026-09-29, $0.034 of OpenRouter credit)

One question, "What is the best procurement software for supplier risk
management in 2026?", asked through OpenRouter's chat-completions endpoint:

| Platform | Result |
|---|---|
| ChatGPT, `openai/gpt-4o-mini` | HTTP 404: "does not support native web search". Not billed. |
| ChatGPT, `openai/gpt-5-mini` | Ran 2 native web searches; $0.025; the 700-token cap went to reasoning, answer empty |
| Perplexity, `perplexity/sonar` | Full answer, 19 `url_citation` annotations with URL and title, zero-width spans, `[n]` markers in the text; $0.006 |
| Gemini, `google/gemini-3.6-flash` | Answer cut off by the same cap; no search recorded |

OpenRouter returns the billed cost in `usage.cost` on every reply.

## Decision

1. **No credential substitution.** `require()` returns the named key or fails.
   A blank value counts as missing.
2. **A route per platform, chosen by `LLM_ROUTE`.** `auto` (default) uses a
   platform's own key when set, else OpenRouter; `openrouter` sends every
   language-model platform through OpenRouter; `direct` uses vendor keys only.
   Google AI Overview (SerpApi) and Semrush are not language models and are
   always direct.
3. **`src/integrations/openrouter.py`**: one `BaseAPIClient` with its own
   vendor name, rate limiter and breaker. `OpenRouterEngineClient.ask()` asks
   with the provider's own search (`plugins: [{id: web, engine: native}]` for
   OpenAI and Google; Perplexity always searches), low reasoning effort and a
   4,000-token cap so reasoning models still answer. `OpenRouterClient.classify()`
   serves the judge and the narrative with a JSON-schema response format.
4. **Models on the OpenRouter route.** Bare vendor ids gain their provider
   prefix. OpenAI models without native search there (`gpt-4o-mini`, `gpt-4o`)
   run `OPENROUTER_CHATGPT_MODEL`, default `openai/gpt-5-mini`. Claude ids map
   to OpenRouter's names (`claude-haiku-4-5` becomes `anthropic/claude-haiku-4.5`).
5. **Real cost.** The ledger row carries OpenRouter's billed cost as the
   vendor-reported cost, so spend reports show money, not estimates.
6. **The judge.** Sentiment and the narrative use the Anthropic key when set,
   otherwise the OpenRouter key; `LLM_ROUTE=openrouter` forces OpenRouter.

## Consequences

- **Some captured fields are empty on the OpenRouter route.** OpenRouter
  normalises replies to text plus citations. Consulted-but-not-cited pages,
  fan-out queries and source dates do not come through, so read-but-rejected
  (ChatGPT) and freshness (Perplexity) cards stay quiet for platforms on this
  route. Sentence-to-source claims are rebuilt from spans or `[n]` markers.
- **ChatGPT on this route is a different model** from direct `gpt-4o-mini`.
  Samples record the model id (`openai/gpt-5-mini`), so the history shows the
  switch; comparisons across it are not like for like.
- **Locale is not sent** on this route; OpenRouter has no location field.
- **One breaker for three platforms.** Five consecutive transient failures on
  any OpenRouter model pause all of them for the cool-down. Refusals and bad
  requests (4xx) do not count.
- **Cost per ChatGPT answer roughly doubles** on this route in the probe
  ($0.025 against about $0.012 direct), because search results arrive as input
  tokens to a reasoning model.

## Also fixed in this cycle

- **Tests could spend money.** Two report tests built `Settings()` from the
  real `.env`. Once the narrative accepted the OpenRouter key, they made paid
  calls: about $0.087 across two local runs, before this was caught. The root
  `conftest.py` now blanks every paid credential and points storage at a
  temporary directory for every test, and refuses real HTTP at the transport.
  `tests/test_isolation.py` proves both.
- **The project form reset hidden settings on every edit.** It now sends the
  stored branding and sampling policy back unchanged.
- **Model choices** in `/api/options`: unpriced `gpt-4.5-preview` and `o3-mini`
  replaced by `gpt-5-mini` and `gpt-5`; the Perplexity entry that pointed at a
  Gemini model (recording Gemini answers as Perplexity) removed.
