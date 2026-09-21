# ADR 0016 — Prompt identity is frozen at creation; one endpoint serves a prompt's whole story

**Date**: 2026-09-19
**Status**: Accepted
**Supersedes**: the identity half of ADR 0008 (`prompt_id` as a pure content hash)

## Context

The analyst UI is moving to a prompt-centric workspace: one interface for every
prompt in a project, where selecting a prompt re-orients the whole view. Two
things blocked that, both found by auditing the live store rather than the code.

**`prompt_id` was a content hash that moved.** `prompt_id_for(lob, prompt_text)`
is `sha256(f"{lob}|{text}")[:16]`, and every time-series table joins on it. The
control plane re-derived it whenever the text changed (`store.update_prompt`) and
for every prompt at once whenever a project's LOB changed (`store.update_project`).
So an analyst fixing a typo moved the row to a new key and abandoned every
snapshot, sample, organic rank and consolidated position behind it. The prompt
then read as never-sampled and was re-queued at cost. The store already carries
24 orphaned prompt ids whose history has no owner — that is the footprint of
edits already made.

Two further consequences of the same design: the hash has no project component,
so two projects sharing a line of business produce identical ids and merge their
histories (projects `44848df7cc57` and `907c0b196ddc` already share
`"Procurement Software"`); and deleting a prompt keeps its rows, so re-adding the
same text resurrects the old series.

**A prompt's story was unreachable.** Exactly one per-prompt read endpoint existed
(`/samples`), hard-capped at 200 and scanning a table with no `prompt_id` index.
Meanwhile `db.history`, `db.velocity`, `db.organic_history`, `db.organic_velocity`
and `db.sample_run_ids` were written and unit-tested but reachable from no route.
Assembling a prompt view meant fetching whole-project payloads and discarding
95% of them, or the whole-database atlas JSON.

## Decision

**1. `prompt_id` is minted once and never re-derived.** `prompt_id_for` still
produces it at creation, where the content hash usefully dedupes. After that it
is an opaque surrogate: `update_prompt` preserves it across a text change and
`update_project` no longer touches prompts on a LOB rename. The UI routes on
`TrackedPrompt.id` (a uuid that was already stable) and treats `prompt_id` purely
as the history join key.

**2. One assembly endpoint,** `GET /api/projects/{id}/prompts/{tracked_id}/detail`,
returning `PromptDetail`: per-engine snapshot series with velocity, both organic
series with velocity, the run list, consolidated positions across every window,
capture coverage and content-gap state.

**3. Insights stay out of it.** They are served by `/insights?prompt_id=` instead
(ADR 0017). *This ADR originally said the caller should filter the cached
project-wide response by `prompt_id`; that is withdrawn — every list in
`InsightsView` is capped project-wide before serialisation and the claims dedup
key omits the prompt, so client-side filtering silently loses rows.*

**4. Empty cells declare their cause.** `EngineStatus` distinguishes `has_data`,
`asked_failed`, `never_asked` and `not_configured`. 41 prompt × engine pairs in
the store are pure Gemini billing failures; rendering those the same as "not
tracked" misreports the product's coverage.

**5. Stored rates are displayed, never recomputed.** `client_citation_rate` uses
a denominator that excludes failed samples and is rounded at write time; 71
snapshots disagree with a naive recomputation. `PromptEngineDetail` also carries
`cited_samples` / `ok_samples` and a `cited_in_minority` flag, because
`client_cited` is a ≥50% majority verdict: 482 snapshots hold a real rank while
storing `client_cited = 0`, and the UI renders those as absent today.

## Consequences

- Editing a prompt's wording keeps its history. The stored text and the hash
  diverge from that moment, which is intended and why the hash is no longer
  treated as derivable.
- A LOB rename is now safe. It was the most destructive button in the product.
- Two projects on one LOB still share history for identical prompt text. Not
  fixed here; `PromptDetail.shared_lob_projects` names the other projects so the
  UI can warn. Recorded in KNOWN_GAPS.
- The 24 pre-existing orphans stay orphaned. No lineage table: the freeze
  prevents new ones, and back-linking the old ones would need a migration over
  three FK-bearing tables for data no project references.
- `answer_samples` gains an index on `(prompt_id, captured_at)` and `positions`
  one on `(prompt_id, engine)`. Both are `CREATE INDEX IF NOT EXISTS` inside the
  existing `_SCHEMA`, which is re-run on every open, so live databases pick them
  up without a migration step.
- `AnswerSample` gains `run_id`, which the query already selected and the mapper
  dropped. It defaults to `""` so nothing that constructs a sample breaks.
- `/samples` is now scoped to the project and takes a `limit` (clamped to 1000).
  It previously served any prompt's samples under any project id.
