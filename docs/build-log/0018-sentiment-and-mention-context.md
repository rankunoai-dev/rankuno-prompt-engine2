# Cycle 0018 — Sentiment, brand attributes and mention context

**Date**: 2026-09-23
**Operator request**: "Start Cycle 2 (Sentiment & Mention Context)? identify all
the edge cases and breaking point while architecture, plan and implementation
side by side"

Decision record: ADR 0021, approved by the operator before implementation
(step 3 was a real stop this time; the operator chose "build all 7 steps" with
the defaults: Claude Haiku 4.5 as judge, competitors scored, 400 sentences per
run, on whenever the key is set).

## Research findings

- `InsightEngine` is vendor-free by contract ("Nothing here calls a vendor"),
  so judging had to live in the crawl. It runs as a `judging` phase in
  `ProjectRunner.run()` after every batch and before `_record_crawl`, so the
  consolidation that may follow sees complete judgements.
- `AnswerSample` has no row id on the read path; the four-column natural key
  (`prompt_id, run_id, engine, captured_at`) plus entity and a sentence hash
  is the judgement key. No change to `answer_samples`.
- `mentions.py` strips `[n]` markers before splitting sentences, so a mention
  sentence never matches a `citation_claims` sentence by equality. Both sides
  are normalised (markers removed, whitespace collapsed, case-folded) for the
  claim join; the raw line's nearest marker is the fallback.
- The `Complete<T>` helper on the UI turns every optional API field into a
  required one, so each new field touched every fixture that builds a
  project, a quote or an insights view.

## What was built

Backend
- `integrations/anthropic_judge.py`: `AnthropicJudgeClient.classify()` over
  the Messages API with `output_config.format` (JSON schema), temperature 0,
  no thinking; refusal and truncation are results, not exceptions. Price cards
  for Haiku 4.5, Sonnet 5, Opus 5; `COST_ANTHROPIC_JUDGE_CALL_USD`.
- `prompt_tracking/sentiment.py`: candidate selection (12 client and 6
  competitor sentences per sample, earliest first, run cap 400 with overflow
  stored as unscored), neighbour lookup from `answer_text`, versioned rubric
  (`2026-09-23.1`) that declares the items DATA, batches of 40 per engine
  under a `usage_context` carrying run and engine, strict parsing of every
  field, a cache keyed by sentence hash, entity, model and rubric.
- `time_series_db.py`: table `mention_judgements` (primary key on the natural
  key), `record_judgements` (upsert), `judgements_for`, `cached_judgements`.
- `runner.py`: `judge` injection, `_resolve_judge` (key present, cap > 0),
  `_judge_mentions` (never raises; warnings on the outcome), per-project
  `sentiment` flag on `ProjectBase` / `ProjectUpdate`.
- `insights.py`: `_sentiment` (per engine and entity: counts, negative share
  with Wilson band, top attributes with an example, worst quotes with the
  attributed URL, empty client rows for silent engines), `_mention_context`
  (container, first third, sourced-via domain and class, list block size,
  polarity), the `negative_claim` card (confident negatives, one card per
  engine and topic, quotes with URLs), and `SentimentCoverage`.
- Settings: `ANTHROPIC_JUDGE_MODEL`, `SENTIMENT_MAX_SENTENCES_PER_RUN`,
  `SENTIMENT_BATCH_SIZE`; documented in `.env.example` and the runbook.

Frontend
- `components/SentimentStrip.tsx` on the Overview: one tile per platform with
  negative share and band, attributes with example on hover, worst quote with
  its source; "Not scored" per tile; an info banner when no judge is
  configured.
- `ActionCardView`: `negative_claim` label and help; quotes link to their
  source when the API attached one.
- `InspectionDrawer`: "Mentions in this window" with entity, polarity,
  container, list size, sourced-via domain and class, early-in-answer.
- `ProjectForm`: "Score brand mentions after each crawl" checkbox, so editing
  a project never silently re-enables sentiment.
- Mocks: a keyword stand-in for the judge so `VITE_MOCK` and tests show scored
  mentions; regenerated `openapi.json` and `schema.d.ts`.

Tests
- `tests/integrations/test_anthropic_judge.py` (6): request shape, refusal and
  truncation, malformed JSON, ledger row with modelled cost, HTTP
  classification, missing key.
- `tests/modules/prompt_tracking/test_sentiment.py` (11): selection caps and
  dedupe, neighbours, payload and schema, parsing with bad fields and
  injection-shaped text, batching by engine and size, cache across runs,
  vendor failure and refusal leaving unscored rows, run cap and zero cap,
  idempotent re-run, prompt filter.
- `tests/modules/control_plane/test_runner_judge.py` (4): phase order, crawl
  survives a judge failure, no key and opted-out projects skip, real client
  built from the key.
- `tests/modules/control_plane/test_insights_sentiment.py` (15): profiles
  with band and attributes, older rubric and unscored rows excluded,
  mention context (list membership, attached source, polarity), the card with
  quote and URL, low-confidence negatives not actionable, not-configured
  state, container detection, block sizes.
- UI: Overview test asserts the strip and one tile per platform.

## Bugs found and fixed

- `_parse_reply` typed the usage dict loosely and mypy refused three `.get`
  calls; narrowed with an explicit `dict[str, Any]`.
- The first `mention_judgements` writer lived above the column constant it
  used; reordered.
- A Python heredoc turned `\b` in the mock's regular expressions into a
  backspace character; ESLint's `no-control-regex` caught it. The shell tool
  halves backslashes (the trap ui-0002 recorded); fixed by writing the
  character by code.
- The runner test asserted `outcome.warnings == []` while the fake pipeline
  always adds a warning of its own; the assertion now checks for sentiment
  warnings only.
- The insights test used a 7-character crawl id; `ProjectRunRecord` requires 8.
- The command-palette test's internal 8 s wait failed twice on this laptop
  (as in cycle 0016) while the whole UI suite ran three to five times slower
  than on 2026-09-17; the wait is now 30 s, with the reason in the test.

## Corrections

- ADR 0021 said judging would run "after `record_samples` for every prompt
  and before consolidation". It runs after all batches of the run, which is
  the same thing for a single-batch crawl and later for a multi-batch one;
  the ADR wording is loose, the code is right.
- The feasibility doc estimated one cycle after the prerequisite. Backend and
  UI together were closer to two.

## Explicitly not done

- Atlas share-of-voice split by mention context (the API carries it; the
  Atlas view does not render it yet).
- No trend of sentiment or attributes over consolidations; no Perception
  report across competitors.
- No translation; non-English sentences are judged as they are.
- No backfill of samples stored before this cycle; they show as not scored
  until their prompts are crawled again.
- Nothing acts on sentiment automatically; the `negative_claim` card is the
  only consumer, and it needs confidence of at least 0.6.
- `insights.py` is now over 1,400 lines, well past the 400-line target. It
  was over 1,000 before this cycle; splitting it into per-section modules is
  its own change.
- The live judge was not exercised against Anthropic in this cycle (no key in
  the dev environment); the transport is covered by mocked tests only.

## Gate output (verbatim; progress dots and unrelated coverage rows elided)

`scripts\verify.ps1`:

```
=== Format ===
218 files already formatted
PASSED: Format
=== Lint ===
All checks passed!
PASSED: Lint
=== Type check ===
Success: no issues found in 66 source files
PASSED: Type check
=== Tests ===
============================== warnings summary ===============================
=============================== tests coverage ================================
src\integrations\anthropic_judge.py                  70      0      4      1    99%   112->114
src\modules\control_plane\insights.py               535     17    230     16    95%   254-255, 494-495, 548, 561, 572-573, 597, 676, 751->749, 790, 819, 846, 975, 986->988, 1016, 1038->1031, 1272, 1437
src\modules\control_plane\runner.py                 266     18     66      3    92%   315, 335, 350-353, 545-557
src\modules\prompt_tracking\sentiment.py            193      4     58      5    96%   181, 247, 251->253, 294->298, 296->295, 309-310
src\modules\prompt_tracking\time_series_db.py       202      2     36      3    98%   399, 459, 473->470
TOTAL                                              6334    104   1418     65    98%
36 files skipped due to complete coverage.
Required test coverage of 85.0% reached. Total coverage: 97.72%
784 passed, 2 warnings in 325.47s (0:05:25)
PASSED: Tests
ALL GATES PASSED.
```

`scripts\drift_check.py`:

```
--- Drift Audit Results ---
No documentation drift detected.
```

UI (`npm run typecheck`, `npm run lint` clean; `npx vitest run --testTimeout=120000`, first full run, before the palette wait was raised):

```
✓ src/pages/project/BattlegroundPage.test.tsx (5 tests) 88975ms
 ✓ src/pages/projects/ProjectsPage.test.tsx (5 tests) 106696ms
 ✓ src/pages/project/PromptsPage.test.tsx (5 tests) 158309ms
 ✓ src/pages/atlas/AtlasPage.test.tsx (3 tests) 121319ms
 ✓ src/pages/project/RunsPage.test.tsx (4 tests) 109704ms
 ✓ src/pages/project/OverviewPage.test.tsx (4 tests) 64145ms
 ❯ src/app/AppShell.test.tsx (4 tests | 1 failed) 59611ms
 ✓ src/pages/project/ProjectLock.test.tsx (2 tests) 54866ms
 ✓ src/api/client.test.ts (8 tests) 140ms
 ✓ src/lib/trends.test.ts (3 tests) 28ms
 ✓ src/lib/atlas.test.ts (5 tests) 24ms
 ✓ src/lib/matrix.test.ts (5 tests) 29ms
 ✓ src/lib/pages.test.ts (4 tests) 29ms
 ✓ src/lib/projectAuth.test.ts (4 tests) 28ms
 ✓ src/mocks/insights.test.ts (3 tests) 23ms
 ✓ src/pages/trends/TrendsPage.test.tsx (3 tests) 90098ms
 ✓ src/lib/promptView.test.ts (3 tests) 19ms
 ✓ src/pages/costs/CostsPage.test.tsx (2 tests) 40323ms
 ✓ src/store/ui.test.ts (2 tests) 18ms
⎯⎯⎯⎯⎯⎯⎯ Failed Tests 1 ⎯⎯⎯⎯⎯⎯⎯
 FAIL  src/app/AppShell.test.tsx > app shell > opens the command palette with Ctrl+K and jumps to a project
TestingLibraryElementError: Unable to find role="heading" and name "GEP procurement (demo)"
 ❯ waitForWrapper node_modules/@testing-library/dom/dist/wait-for.js:163:27
 ❯ src/app/AppShell.test.tsx:55:15
 Test Files  1 failed | 18 passed (19)
      Tests  1 failed | 73 passed (74)
```

`npx vitest run src/app/AppShell.test.tsx` after raising the wait:

```
 Test Files  1 passed (1)
      Tests  4 passed (4)
```
