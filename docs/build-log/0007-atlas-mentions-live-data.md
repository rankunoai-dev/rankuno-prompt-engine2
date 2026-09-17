# Cycle 0007 — Mention rate beside citation rate in Prompt Atlas; live atlas data

**Date**: 2026-09-17
**Operator request**: "right now you are showing only citation %; I want you to
show both the mentions and citations percentage" (chat, with a screenshot of
the Prompt Atlas master sheet).

## Scope

Every engine cell in the Prompt Atlas master sheet shows the citation rate
(client linked as a source) and the mention rate (brand named in the answer
text). The same pair appears in the overview tiles, the engine scoreboard and
the engine view. The data the page reads must be current. Acceptance: both
rates visible per cell on the live page; gate green.

## Research findings

- The store and the exporter already carried `mention_rate` and
  `mention_snippets` (cycle 0004); the page used them only in the drawer. The
  master sheet, tiles and engine tables read `client_citation_rate` alone.
- The page was reading `reports/prompt-atlas-data.json`, a file written by
  `scripts/export_dashboard.py` at 06:25 that morning; two later runs were
  missing. A static file needs a manual re-export after every run, which the
  operator will not do; the control plane can build the document from SQLite
  on request in well under a second at this data size (224 snapshots).
- Export logic lived under `scripts/`, which `src` must not import; it moved
  to `src/modules/prompt_tracking/atlas_export.py` and the script became a
  thin wrapper with the same CLI.

## What was built

- `prompt_tracking/atlas_export.py` (new): `export_atlas(db_path, domains,
  competitors, lob)`; decodes all JSON columns (now also `citation_links`,
  `client_urls`), coerces `mention_rate`, optional LOB filter.
- `scripts/export_dashboard.py`: wrapper over the module; new `--lob`.
- `control_plane/app.py`: `/reports/prompt-atlas-data.json` and
  `/docs/prompt-atlas-data.json` build the document live; client and
  competitor domains are the union across projects; `?lob=` filter; 404 when
  no database exists yet.
- `TimeSeriesDB.path` property.
- `docs/prompt-atlas.html`: `engineCell` renders two rows, "Cited x%" (with
  best rank) and "Mentioned y%" (with snippet count and the snippets as a
  tooltip); `fixSnap` defaults mention fields for CSV/CLI/demo sources; mean
  mention rate tiles on the overview and engine views; "Cited / Mentioned" in
  the engine scoreboard; engine ranked table column renamed.
- Tests: `test_atlas_export.py` (decode, mentions, LOB filter, missing DB,
  script wrapper), app route test.
- Docs: README, ARCHITECTURE, KNOWN_GAPS (closed entry; new narrower gap for
  links/organic in Atlas), build-log index.

## Bugs found and fixed

- Test helper `_create(client, client=…)` clashed with the fixture name;
  rewritten as a direct POST.
- Test used `MentionSnippet(sample=…)`; the contract is `(entity, term,
  snippet)`.
- Ruff D205 on the new module docstring; reworded.

## Corrections

- Cycle 0005 and KNOWN_GAPS said the Atlas page "does not read the new
  tables". It read the export file, which already had mentions; the gap was in
  the page's cells and in freshness. Both closed.

## Explicitly not done

- Atlas still does not show per-link citations, consulted URLs or organic
  ranks (the control-plane Results tab does).
- The page's sort on an engine column sorts by citation rate only.
- No cache on the live export; every page load re-reads SQLite.

## Local verification

Server restarted. `GET /reports/prompt-atlas-data.json` returned 43 prompts,
224 snapshots, 15 runs with `exported_at` of the request time, client domain
`gep.com`, five competitor domains from the projects, and 92 snapshots with a
non-zero mention rate and snippets. `GET /docs/prompt-atlas.html` contains the
new "Mentioned" cell. `scripts/export_dashboard.py --domain gep.com` rewrote the
static file with the same counts.

## Step 5 audit answers (delta from cycle 0006)

1–2. No new vendors or rate keys.
3. No spend path touched.
4. The export is read-only (`mode=ro`) and idempotent.
5. Missing database → 404 JSON, not a stack trace.
6. Exported data contains prompt text, domains and answer excerpts already
   held by the store; no new PII.
7. Query parameter `lob` is a plain string used only for equality filtering.
8. Unchanged; loopback default.

## Gate output (verbatim)

```
=== Format ===  PASSED
=== Lint ===    PASSED
=== Type check ===
Success: no issues found in 52 source files
PASSED: Type check
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
src\modules\control_plane\__main__.py                70      2     10      1    96%   72-73, 120->128
src\modules\control_plane\app.py                    117      4      0      0    97%   79, 89, 100-101
src\modules\control_plane\planner.py                 57      1     26      1    98%   73
src\modules\control_plane\store.py                  117      3     16      0    98%   81-83
src\modules\prompt_tracking\__main__.py             157      4     46      2    97%   157, 159, 298-299
src\modules\prompt_tracking\pipeline.py             255      3     70      2    98%   268, 429-430
src\modules\prompt_tracking\prompt_generator.py      73      1     24      1    98%   119
src\modules\prompt_tracking\scheduler.py            132      0     20      1    99%   266->275
---------------------------------------------------------------------------------------------
TOTAL                                              3818     43    794     18    99%
35 files skipped due to complete coverage.
Required test coverage of 85.0% reached. Total coverage: 98.50%
616 passed, 2 warnings in 133.39s (0:02:13)
PASSED: Tests
ALL GATES PASSED.
```

Drift audit: `scripts/drift_check.py` → "No documentation drift detected."
