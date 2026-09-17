# ADR 0011 — Engine connectors corrected against live behaviour

**Status**: Accepted (2026-09-17). Amends ADR 0004. Operator report: a branded
prompt ("How do I implement GEP procurement software and integrate it with my
ERP?") showed "not cited" on every engine while ChatGPT and Perplexity visibly
cite gep.com for it; also, spend on tests must stay under $0.50.

## What was actually wrong (one live call per engine, $0.10 total)

| Engine | Stored result | Cause found | Fix |
| :-- | :-- | :-- | :-- |
| ChatGPT Search | model `gpt-4o-mini-2024-07-18`, web trigger 0%, 0 citations | `tools: [web_search]` was offered but `tool_choice` was `auto`; the model answered from memory. | Force the tool: `tool_choice: {"type": "web_search"}`. Live: 2 citations, both gep.com. |
| Perplexity | model `google/gemini-3.6-flash`, 0 citations | Perplexity retired `/chat/completions` ("Sonar is now the Agent API", HTTP 403). A parallel session moved to `/v1/responses` but hard-swapped the model to Gemini and sent no tools; the Agent API returns no sources unless `web_search` is requested, and its payload shape (`output[].type == "search_results"`) was never parsed. | `/v1/responses`, model `perplexity/sonar` (Perplexity's own), `tools: [web_search]`, `tool_choice` forced, 120 s timeout (the API takes 45–60 s). Sources are the `search_results` item, ordered by `[n]` markers when present. Legacy names `sonar`, `sonar-pro` map to `perplexity/sonar`. Live: 10–15 sources, gep.com first. |
| Google AI Overview | present but 0 references; earlier samples "failed" | Google now embeds sources as `snippet_links` inside `text_blocks` and omits the `references` list the parser read. Earlier failures were `serpapi_reported_error`, whose text was lost (see logger). | Parse inline links in reading order after any explicit references. Live: gep.com #1. |
| Gemini | "unavailable", samples failed | HTTP 429 `RESOURCE_EXHAUSTED`: "Your prepayment credits are depleted" on the operator's Google AI Studio project. | Not a code defect. Top up billing; the connector is unchanged. |
| All | error text missing from `logs/audit.jsonl` | `get_logger()` returned `logging.LoggerAdapter(logger, {})`; on Python < 3.13 the adapter replaces the caller's `extra=` with its own dict, so every structured field was dropped since the first cycle. | Adapter subclass that merges extras. Regression test added. |

## Decisions

1. **Search is forced, never optional.** For ChatGPT Search and Perplexity the
   web tool is mandated through `tool_choice`. A sample where the model skipped
   the tool measures the model's memory, not the search product, and would
   read as "not cited" for the wrong reason.
2. **Perplexity means `perplexity/sonar`.** Third-party models behind
   Perplexity's router are still not used for the `PERPLEXITY` engine, as in
   ADR 0004. Retired Sonar chat names are accepted and mapped so old `.env`
   files and the control plane's model list keep working.
3. **Prompts stay verbatim.** Perplexity's docs suggest appending a citation-
   format instruction to get `[n]` markers; the tracker does not, because that
   would change the query. Without markers, sources are taken in the API's
   order; with markers, in first-marker order.
4. **A live check is part of the definition of done for a connector.** Unit
   tests with fixtures modelled on docs did not catch any of the four faults;
   one $0.03 call per engine did. `docs/build-log` entries for connector
   changes must include the live check output.
5. **Test spend ceiling.** `MAX_SESSION_SPEND_USD=0.5` in `.env`, and every
   diagnostic script sets its own `CostLedger(ceiling_usd=0.5)`. Raise the
   `.env` value deliberately before a production run.

## Consequences

- `PERPLEXITY_MODEL` default is now `perplexity/sonar`; the README and
  ARCHITECTURE engine tables are updated.
- Perplexity's Agent API reports the real cost in `usage.cost.total_cost`
  (about $0.01 per answer with search). The ledger still uses the configured
  estimate; wiring actual cost is a known gap.
- Historical snapshots taken with the faulty connectors (17 Sep, before this
  change) under-report citations for ChatGPT, Perplexity and Google AI
  Overview. They are kept (history is append-only) and are identifiable by
  model `google/gemini-3.6-flash` for Perplexity and by run ids before
  the first run after this change.
