# ADR 0004 — Which endpoint represents each engine

**Status**: Accepted (2026-09-16)

## Context

The Phase 1 validation matrix reported "200 OK" for five endpoints. Reading the
scripts that produced it (`api_validation_suite.py`) showed the calls proved key
validity only:

- OpenAI was called on `/v1/chat/completions` with plain `gpt-4o-mini` — no web
  search, so no citations are possible.
- Gemini was called without the `google_search` tool — no grounding metadata.
- Perplexity was called on the Agent API with `google/gemini-3.6-flash` — a
  routed Gemini answer, and `web_triggered: True` was hard-coded in the script.
- SerpApi's asynchronous AI Overview (`page_token`) path was not handled.
- The persisted Semrush capability report shows `ERROR 122` on every endpoint,
  contradicting the walkthrough's "AVAILABLE" claims.

## Decision

| Engine | Endpoint | Why |
| :-- | :-- | :-- |
| ChatGPT Search | OpenAI **Responses API** + `web_search` tool, model from `OPENAI_SEARCH_MODEL` | Only supported path to live search with `url_citation` annotations; `*-search-preview` chat models can be retired on two weeks' notice |
| Perplexity | `/chat/completions` with `sonar-pro` | Perplexity's own model — measures Perplexity, not a routed third party |
| Gemini | `generateContent` + `tools=[{"google_search":{}}]`, key in `x-goog-api-key` header | Grounding is opt-in; header keeps the key out of URLs/logs |
| Google AI Overview | SerpApi `google`, then `google_ai_overview` with `page_token` when required | Overviews are often loaded asynchronously; the token expires within a minute |
| Semrush | v3 `phrase_all` / `phrase_questions` / `phrase_related` with explicit `display_limit` | Bills per row; default is 10,000 rows |

Engine labels name what is called (`CHATGPT_SEARCH`, not "ChatGPT").

## Alternatives considered

- Keep the Agent API for Perplexity: rejected, attribution would be wrong.
- Use `gpt-4o-search-preview` on chat completions: rejected, deprecation risk.

## Consequences

- All five connectors are new code; none of the Phase 1 scripts were reused.
- Each connector's `web_triggered` definition is documented in its module
  docstring and in `README.md`.
- The hard-coded live keys found in the Phase 1 script must be rotated. They
  were not copied anywhere in this repository.
