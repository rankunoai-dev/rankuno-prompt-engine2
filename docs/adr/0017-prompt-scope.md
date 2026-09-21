# ADR 0017 — A prompt scope, applied server-side before aggregation

**Date**: 2026-09-19
**Status**: Accepted
**Amends**: ADR 0016 (the "filter `/insights` on the client" guidance is withdrawn)

## Context

Every project screen shows the whole project. The Trends tab alone offers a
picker — "All tracked prompts (N)" or one prompt — and re-derives its chart for
the selection. The operator wants that on every project tab, keeping each tab's
layout and representation exactly as it is, and asked that the edge cases be
found before anything was built.

The audit found that the obvious implementation is wrong. ADR 0016 told callers
to obtain per-prompt insights by filtering the cached project-wide `/insights`
response on `prompt_id`. That loses data in two ways:

1. **Project-wide caps run before serialisation.** `changes` is cut to 50,
   `fanout` to 200, `claims` to 500, `read_but_rejected` and both page
   inventories to 50 — all inside `InsightEngine.build()`, before the response
   leaves the server. A prompt outside the project's top-N has its rows dropped
   before the client can filter for them. Proven in
   `test_client_side_filtering_would_lose_changes_beyond_the_cap`: 52 prompts
   flip up, the unscoped list holds 50, and the 52nd is present only under scope.
2. **The claims dedup key has no prompt component.** `(sentence, url, engine)`
   collapses a claim shared by two prompts into one entry attributed to whichever
   was iterated first. Filtering by the other prompt finds nothing. Proven in
   `test_client_side_filtering_would_lose_shared_claims`.

Beyond insights: `fanout`, `winning_pages` and `client_pages` collapse their
prompt-id sets to counts; `health`, `read_but_rejected`, `trust_profile`,
`placement` and `freshness` never enter the prompt axis; and `CostReport` has no
prompt dimension at all.

## Decision

**1. The scope is a server-side parameter, applied at the top of
`InsightEngine.build()`.** With `prompt_id`, the tracked-prompt list is reduced
to that prompt and the current and previous consolidation positions are filtered
to it *before* anything is aggregated. Everything downstream — sample loading,
clustering, `_domain_share`, the claims dedup, every cap — then operates on the
scoped set and needs no change. An id the project does not track raises
`KeyError` (→ 404): a stale scope must never fall back to project-wide numbers.

`basis.crawls` and `basis.low_confidence` stay window-level. The consolidation
window is a project property; only `basis.samples` becomes the prompt's own.

**2. Action-card ids are unchanged.** `_action_id` has no prompt component, so a
scoped card and the project card for the same (subtopic × engine) cluster share
analyst state — status, owner, note, baseline. That is the intended semantics:
it is the same prescription. `impact_score` is not comparable across scopes,
because the cluster weight sums the cluster's search volume.

**3. Cost under scope is direct engine spend, reported with its remainder.**
`api_calls.prompt_id` is set only inside a prompt's `usage_context` — the engine
samples. Harvest, keyword rank and redirect resolution belong to the run. The
report therefore carries `attribution: "direct_engine_calls"` plus
`unattributed_calls` / `unattributed_actual_usd` for null-prompt rows in the same
runs. Nothing is amortised: a per-prompt share of a Semrush harvest would be an
arbitrary division.

**4. `usage_context(key=None)` now clears an inherited value.** It previously
ignored `None`, so the keyword-rank call site that reads `prompt_id=None` was a
no-op. Harmless today only because no prompt context enclosed it; the new
semantics make the code do what it says.

**5. Front end: one selector, URL-borne, project tabs only.** Written up for the
UI session in `docs/UI_SCOPE_BRIEF.md`. The URL carries the stable `tracked_id`
under `?scope=` — `?prompt=` is already the inspection-drawer deep link — and
the API is called with the resolved `prompt_id`. Atlas, Trends and Costs pages
are untouched.

## Consequences

- `GET /api/projects/{id}/insights?prompt_id=` and
  `GET /api/costs?project_id=&prompt_id=`. Both 404 on a foreign prompt; costs
  400 without a project.
- `PromptDetail` and `ProjectRunner.prompt_detail` docstrings corrected.
- `api_calls` gains an index on `(prompt_id, ts)`, `IF NOT EXISTS` inside the
  ledger's `_SCHEMA`, so live databases pick it up on open.
- No project guard on `sample_run_ids`: history is keyed by `(lob, text)`, and
  intersecting with `project_runs` would hide the 15 runs already orphaned from
  it — including one project's entire dataset. `shared_lob_projects` remains the
  warning (ADR 0016).
- Under scope some metrics degenerate by construction — `EngineHealth.prompts`,
  `FanoutQuery.prompts` and `PageInventory.prompts` are always 1;
  `freshness` medians are usually `None`; distributions across prompts have no
  meaning. The brief tells the UI which cards to swap for a prompt equivalent and
  which to dim with a "project-wide" note, so the layout never jumps and nothing
  is misread.
