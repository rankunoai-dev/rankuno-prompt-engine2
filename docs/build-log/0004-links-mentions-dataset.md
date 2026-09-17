# Cycle 0004 — Citation links, brand mentions, UI dataset, verbatim prompts

**Date**: 2026-09-16
**Operator request**: "for all my prompts I should get the links of the citation
in all the platforms … and also the mentions in each platform and if possible
the snippet of the response where my client name was mentioned … an input
field of the client … but the engine run should not be polluted" (chat).

## Scope

Persist and report every citation URL per prompt per engine; detect brand and
competitor mentions in answer text with the containing sentence; emit a
UI-ready per-run dataset; guarantee prompts reach engines byte-for-byte as
supplied. Acceptance: links and mentions round-trip through SQLite, appear in
CSV and JSON, custom prompts are unmodified, gate green.

## Research findings (corrections to a pasted claim)

The operator pasted a summary asserting these were already true. They were not:

- Links were extracted in memory (`EngineAnswer.citations`, `consulted_urls`)
  but only *domains* were stored in `snapshots` / `answer_samples` and shown in
  the CSV.
- No mention detection existed anywhere.
- `PromptGenerator.from_custom` capitalised the first letter and appended "?"
  to analyst prompts before they were sent — a modification of the query.

What was already true: the client profile was never injected into prompts; it
was used only after answers returned.

## What was built

- `mentions.py`: `split_sentences`, `detect_mentions` (word-bounded, longest
  alias first, one snippet per sentence per entity, ≤600 chars).
- `ClientProfile.competitor_names` + `competitor_terms()` (fallback: ≥3-char
  first label of each competitor domain). CLI `--competitor-name`.
- `citations.py`: `merge_links` (best position per URL); snapshot now carries
  `citation_links`, `client_urls`, `consulted_urls`, `mention_detected`,
  `mention_rate`, `mention_snippets`, `competitor_mentions`.
- `AnswerSample` carries `citation_links`, `consulted_urls`, `mention_detected`,
  `mentions`; `assembly.answer_samples` fills them.
- Store: six new `snapshots` columns and four new `answer_samples` columns,
  applied to existing databases by `_migrate()`.
- `report.py`: CSV engine block widened to five columns (adds Client URLs and
  Mention Snippet); `write_ui_dataset` / `dataset_rows` emit one JSON row per
  prompt × engine. Pipeline writes `.json` next to `.csv`; summary gains
  `dataset_path`.
- `from_custom` sends prompts verbatim.

## Bugs found and fixed

- Custom prompts were normalised before sending (see above). Fixed and pinned
  by `test_custom_only_skips_semrush_and_tracks_every_prompt`.
- `mention_detected` was not stored and rehydrated as False; now derived from
  `mention_rate > 0` on read (no redundant column). Caught by the round-trip
  test.

## Corrections

- Cycle 0001–0003 documentation implied links were "captured"; they were
  captured in memory only. README/ARCHITECTURE now state what is stored.
- The CSV engine block is five columns per engine, not three (cycle 0001).

## Explicitly not done

- No fuzzy/possessive/abbreviation matching and no sentiment on mentions
  (KNOWN_GAPS). Exact matching is deterministic and auditable.
- The dataset JSON is per run, not merged; the other session's dashboard does
  not read it yet.
- Still no live API call from this repository.

## Step 5 audit answers (delta from cycle 0003)

1–5. Unchanged: no new hosts, calls, spend or breaker changes.
6. Stored text grows: full URLs, consulted URLs and mention sentences (≤600
   chars each, ≤10 per snapshot). Vendor answers to product prompts; no PII by
   construction.
7. All new payload fields are typed `StrictModel`s; JSON columns are decoded
   only in `_row_to_*`.
8. Unchanged.

## Gate output (verbatim)

```
=== Format ===
PASSED: Format
=== Lint ===
All checks passed!
PASSED: Lint
=== Type check ===
Success: no issues found in 43 source files
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
src\integrations\url_resolver.py                     97      2     18      0    98%   105-106
src\modules\prompt_tracking\pipeline.py             234      3     68      2    98%   232, 383-384
src\modules\prompt_tracking\prompt_generator.py      73      1     24      1    98%   119
src\modules\prompt_tracking\scheduler.py            132      0     20      1    99%   266->275
---------------------------------------------------------------------------------------------
TOTAL                                              2936     22    668     12    99%
32 files skipped due to complete coverage.
Required test coverage of 85.0% reached. Total coverage: 99.06%
549 passed in 103.67s (0:01:43)
PASSED: Tests
ALL GATES PASSED.
```

Drift audit: `scripts/drift_check.py` → "No documentation drift detected."
