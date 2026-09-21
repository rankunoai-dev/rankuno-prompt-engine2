# UI cycle 0005 — Demonstration project seeded through the engine's own stores

**Date**: 2026-09-18
**Operator request**: "create a dummy project and embed 20 prompts and embed
the 100 percent data … tracking of this dummy project of 2 months, per run two
days leap and 3 run as one consolidated, same GEP as the client … don't show
the things which this engine cannot do" (chat). Explicit constraint: no real
API calls.

## Scope

`scripts/seed_demo_project.py` (+ `seed_demo_content.py`): a project
"GEP demo - full capability" (brand GEP, LOB "GEP Procurement (demo)", five
competitors, aliases, six landing pages, all four platforms, interval `2d`,
three samples per platform, consolidation window 3) with 20 prompts across
seven subtopics, 30 crawls at a two-day interval ending yesterday, three
samples per prompt × platform (two when the first two agree, like the
adaptive sampler), consolidations computed by the engine after every third
crawl, and analyst action states with baselines. Everything is written through
`TimeSeriesDB`, `ProjectStore`, `PositionStore`, `ActionStateStore` and
`UsageLedger`; no connector is imported and nothing leaves the machine.

## Research findings

- Every capability the engine records was traced to a store call so nothing
  is shown that the engine cannot produce: per-sample full answer text, citation
  links with positions, claims mapped to sentences, source snippets with
  dates, the engine's fan-out queries, consulted-but-not-cited URLs, brand and
  competitor mention snippets; per-snapshot rates and ranks; organic top-10
  with titles, snippets, People-also-ask and related searches for prompt and
  keyword; run headers; usage-ledger rows with tokens, latency, estimated and
  vendor-reported or modelled cost; crawl records; consolidations; action
  states with baselines.
- The scenario was tuned against the insight rules (`insights.py`
  thresholds) until all eight card types fire on the stored data and the
  verdicts span the range: Google AI Overview *losing to coupa.com*, ChatGPT
  *present*, Perplexity and Gemini *winning*; outcomes *improved*,
  *regressed* and *unchanged* score from stored baselines.
- Two-of-nine sampling noise defeated a rule on the first dry run; the demo
  uses a fixed seed and wider margins so the story is deterministic.
- Verified through a fresh control-plane instance on port 8788 (no spend
  approval): 30 crawls, 10 consolidations, 80 positions, 575 samples behind
  the latest verdicts, 28 action cards, 21 fan-out queries, 500 claims,
  5,684 ledger rows; screenshots of every page reviewed.

## What was built

- `scripts/seed_demo_project.py`, `scripts/seed_demo_content.py`
  (`--reset` removes a previous demo and every row it seeded; `--db` targets
  another file, which is how the dry runs were done on a scratch copy).
- Trends: per-run x labels are the date alone, with the time only when
  several runs share a day, and the run id only when a label would repeat.
  Overview: "what changed" rows lead with the prompt text.
- Backup of the database before seeding was kept in the session scratchpad.

## Bugs found (backend, reported, not fixed here)

- `earned_placement` titles read "338% of ChatGPT Search's sources…":
  `insights.py` sums per-domain shares (each the share of samples citing that
  domain), which exceeds 100% when a sample cites several review sites. The
  card still triggers correctly; only the percentage in the copy is wrong.

## Corrections

- None.

## Explicitly not done

- No vendor call was made; nothing in the demo is a real answer. The project
  notes state this.
- The operator's running server (8787) predates the insight routes and was
  not restarted; it serves the demo project's prompts, results, runs, crawls
  and positions, but `/insights` returns 404 there until restart.

## Gate output (verbatim)

```
ruff check scripts/        All checks passed!
ruff format                (both files formatted)
vitest (trends, overview)  10 passed
tsc / eslint               clean
```

The wider Python gate was not rerun: `src/` and `tests/` are unchanged by
this cycle.
