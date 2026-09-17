# Cycle 0005 — Control plane UI: projects, platforms, intervals, prompt overrides

**Date**: 2026-09-17
**Operator request**: "a separate UI where I can configure, edit, delete, create
a project … upload all the input data … choose which platforms to include …
configure at what intervals to run (daily, one-day leap, two leap, weekly,
monthly or whatever) … select the most important prompt and at prompt level set
its interval and platform selection" (chat).

## Scope

New module `src/modules/control_plane`: FastAPI JSON API + single-page UI;
SQLite-backed projects and tracked prompts; prompt-level `important`,
`interval`, `engines`, `samples_per_engine` overrides; due-work planner; runner
that batches due prompts through the unchanged tracker pipeline; server with
optional background poller. Acceptance: CRUD works from the browser, prompt
overrides change what runs, gate green.

## Research findings

- Storing per-prompt schedule state would duplicate what snapshot history
  already records; due-ness is derivable from the newest snapshot per
  (prompt, platform) and stays consistent with the data by construction.
- Grouping due prompts by (platform set, samples) lets each group be one
  `PipelineInput` with `custom_prompts`, so all cycle 0002–0004 behaviour
  (organic ranks, adaptive sampling, cap, reuse, breakers, links, mentions) is
  inherited without change.
- Large heredoc writes through the Bash tool failed to parse in this shell;
  files were written with the dedicated Write tool instead.

## What was built

- `schemas.py`: `Project*`, `TrackedPrompt*`, `DueItem`, `WorkBatch`,
  `RunRequest`, `RunOutcome`, `PromptResult`, `INTERVAL_PRESETS` (daily, 2d,
  3d, weekly, 2w, monthly; any `parse_interval` value accepted).
- `store.py`: `ProjectStore` (tables `projects`, `project_prompts`, JSON
  payloads; import from prompts-file text; LOB change rewrites prompt ids).
- `planner.py`: `due_items` (per platform, honours overrides, enabled flags,
  force, id and platform filters) and `batches` (important first).
- `runner.py`: `ProjectRunner.run/run_due_all/results`, optional Semrush
  generation at the project interval (state in `jobs` table).
- `app.py`: REST endpoints under `/api`, UI at `/`, OpenAPI at `/docs`.
- `static/index.html`: projects sidebar; tabs for project & client, prompts
  (upload/paste/add, star, enable, per-prompt interval/platforms/samples,
  reset, run selected), results (per platform: cited/rate/rank, mention rate
  and snippets, citation links, competitors, organic ranks, due badges), runs.
- `__main__.py`: `python -m src.modules.control_plane [--host] [--port]
  [--poll-minutes N] [--approve-spend]`.
- `TimeSeriesDB.runs_for(lob)` for the runs table. `pyproject` `ui` extra.
- Docs: README, ARCHITECTURE, KNOWN_GAPS, ADR 0009, blueprint §7c.

## Bugs found and fixed

- `ProjectStore.update_*` built the merged model with `model_copy(update=…)`
  from a dumped dict, producing a pydantic serializer warning (client stored as
  dict). Rebuilt via `model_validate({**current, **changes})`.
- A route added to `app.py` by a parallel session exceeded the line limit and
  failed the gate; formatted. That session also added `--openai-model` /
  `--perplexity-model` flags to the tracker CLI and changed `perplexity.py`
  (now 84% covered) — not part of this cycle.

## Corrections

- README previously implied the only UI was the read-only Prompt Atlas page;
  the control plane is now the configuration surface, Atlas remains an
  explorer (also served by the control plane at `/docs/prompt-atlas.html`).

## Explicitly not done

- No authentication; single user on 127.0.0.1.
- Runs execute synchronously in the request; no queue or progress stream.
- Intervals remain elapsed-time, not calendar-anchored.
- The Prompt Atlas page still does not read links/mentions/organic tables.
- No live vendor call has been made from this repository; the smoke test
  below used the API with no engine spend (deny/approve paths only).

## Local verification

Server started with `--approve-spend --port 8787`; `curl` smoke test: health
ok, UI 200 (28 KB), project created (`2d`, two platforms), two prompts
imported from text, one starred with `daily` / Perplexity-only / 5 samples,
results show it due only on Perplexity, OpenAPI at `/docs` 200.

## Step 5 audit answers (delta from cycle 0004)

1–2. No new vendors or rate keys; the UI only calls the existing pipeline.
3. Same spend controls; a UI "Run" is approved only when the server was started
   with `--approve-spend` or a budget cap is set; otherwise
   `blocked_pending_approval` is shown.
4. Project/prompt writes are idempotent upserts by id; duplicate prompt text
   returns the existing row.
5. Unchanged. 6. Configuration holds client brand data only; no PII.
7. Every request body is a `StrictModel`/pydantic model; unknown fields → 422.
8. Unchanged; server binds to loopback by default.

## Gate output (verbatim)

```
=== Format ===  PASSED
=== Lint ===    PASSED
=== Type check === Success: no issues found in 50 source files  PASSED
=== Tests ===
Name                                              Stmts   Miss Branch BrPart  Cover   Missing
---------------------------------------------------------------------------------------------
src\core\base_tool.py                                82      6     16      2    92%   68, 108, 193-194, 226-228
src\core\circuit_breaker.py                          86      0     20      1    99%   115->120
src\core\guardrails.py                               86      2     20      2    96%   197, 213
src\core\logger.py                                   59      1     10      1    97%   107
src\core\rate_limiter.py                            113      4     26      2    96%   77-78, 133->138, 237-238
src\core\retry.py                                    20      2      0      0    90%   47-48
src\core\url_safety.py                               71      1     18      0    99%   64
src\integrations\perplexity.py                       75      7     32      2    84%   67->69, 93-99
src\integrations\url_resolver.py                     97      2     18      0    98%   105-106
src\modules\control_plane\__main__.py                64      2     10      1    96%   60-61, 108->116
src\modules\control_plane\app.py                     96      3      0      0    97%   66, 76, 81
src\modules\control_plane\planner.py                 57      1     26      1    98%   73
src\modules\control_plane\store.py                  117      3     16      0    98%   81-83
src\modules\prompt_tracking\__main__.py             157      4     46      2    97%   157, 159, 298-299
src\modules\prompt_tracking\pipeline.py             234      3     68      2    98%   232, 383-384
src\modules\prompt_tracking\prompt_generator.py      73      1     24      1    98%   119
src\modules\prompt_tracking\scheduler.py            132      0     20      1    99%   266->275
---------------------------------------------------------------------------------------------
TOTAL                                              3516     42    756     18    98%
33 files skipped due to complete coverage.
Required test coverage of 85.0% reached. Total coverage: 98.41%
585 passed, 2 warnings in 117.73s (0:01:57)
PASSED: Tests
ALL GATES PASSED.
```

Drift audit: `scripts/drift_check.py` → "No documentation drift detected."
