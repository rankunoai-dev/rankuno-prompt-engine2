# ADR 0012 — Per-call usage ledger and observed costing parameters

**Status**: Accepted (2026-09-17). Operator request: "whenever you are calling
any API, its volume and number of calls to multiple platforms, I want you to
track the costing, hence in the end we would have a better idea and can create
very better and best costing parameters."

## Context

Spend control (ADR 0002, 0006) reserves a configured estimate per call
(`COST_*` settings) against a session ceiling. The estimates were guesses. The
`runs` table records one estimated total per run; nothing recorded per call
volume, tokens, searches, or what the vendor actually charged, so the estimates
could never be corrected from evidence, and calls made outside a run (live
checks, probes) were invisible.

## Decisions

1. **One row per outbound request, written in `BaseAPIClient.call()`.** Every
   connector already routes through it, so ok, error and breaker-refused calls
   are all recorded with vendor, operation, latency and the configured estimate.
   The connector then enriches the row (`note_usage`) with the model, tokens,
   search invocations, units, and cost. Recording never raises into a paid call.
2. **Three cost columns, one precedence.** `estimated_cost_usd` (the setting),
   `vendor_cost_usd` (what the API reported; Perplexity does), and
   `modelled_cost_usd` (list prices × tokens + per-search fee; OpenAI and
   Gemini; plan price for SerpApi; unit price for Semrush). "Actual" means
   vendor-reported, else modelled, else estimate, and the report says which.
3. **Context tags, not parameters.** `usage_context(source, run_id, prompt_id,
   engine)` is a `contextvars` value set by the pipeline (run), the audit
   worker (prompt, engine), the job manager (`control_plane`), the CLI (`cli`)
   and the live check (`live_check`). The pipeline copies the context into
   pool threads explicitly. Connectors stay unaware of the pipeline.
4. **Same SQLite file as the tracker (`api_calls`).** One store for the control
   plane, the CLI and the report; no second database to back up.
5. **Recommendations are observed mean × 1.15.** `costing.py` aggregates per
   vendor, run and source, and proposes each `COST_*` value from successful
   calls only, stating whether the basis is vendor-reported, modelled or the
   estimate itself. The margin keeps the ledger reservation a slight
   over-estimate, which is the safe direction for a spend ceiling.
6. **List prices live in `pricing.py`, clearly labelled as such.** They are
   the bootstrap, not the goal; with vendor-reported costs the report ignores
   them. Unknown models borrow their family's card and are flagged.
7. **History before the ledger is backfilled once**, tagged
   `source="backfill"` with a note, from the session transcript and saved
   payloads, so this session's spend is visible in the same report.

## Surfaces

- CLI: `python -m src.modules.prompt_tracking costs [--days N] [--run-id …] [--json]`.
- API: `GET /api/costs?project_id=&days=`.
- UI: Costs tab per project (or everything), with proposed parameters.

## Alternatives considered

- **Reading vendor billing dashboards**: not per prompt, not per run, and not
  available for every vendor by API.
- **Charging vendor-reported cost to the ledger live**: attractive, but the
  reservation must happen before the call; the observed mean feeding the
  setting achieves the same correction one run later. Left as a gap.

## Consequences

- `BaseAPIClient.call()` gains an `estimated_cost_usd` keyword; connectors
  pass their setting. A connector that forgets still gets a row with a zero
  estimate, which the report exposes.
- Tests that build connectors record into the hermetic tracker DB under
  `tmp_path`; nothing is written to the real ledger from tests.
