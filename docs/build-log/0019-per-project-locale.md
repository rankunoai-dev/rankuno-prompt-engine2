# Cycle 0019 — Per-project locale and geo-targeting

**Date**: 2026-09-23
**Operator request**: "Option B: Feature 3 — Multi-Locale & Geo-Targeting What it
does: Add regional IP/city parameters to API engine connectors for hyper-local
prompt tracking take the context from whatever we are working on right now"

Decision record: ADR 0023. The shape is the one `docs/ROADMAP_FEASIBILITY.md` §3
recommended as v1: one frozen locale per project, a second market is a second
project.

## Research findings

- Vendor support was verified against each vendor's own documentation before any
  code was written, because §3 of the feasibility doc left one engine unverified
  and the operator's standing rule is not to show what the engine cannot do:

  | Engine | Field | Verdict |
  |---|---|---|
  | SerpApi | `gl`, `hl`, `location` | City-level, already partly wired |
  | OpenAI Responses | `user_location` on the `web_search` tool, `{type: "approximate", country, city, region, timezone}` | Supported |
  | Perplexity Agent API `/v1/responses` | `user_location` on the `web_search` tool: `country`, `region`, `city`, optional coordinates | **Supported** — this was the doc's open question. Perplexity's docs call it a hint, not a guarantee |
  | Gemini Developer API | none; the `google_search` tool takes an empty object | **Cannot be done.** Country follows the billing account |

- Every stored row is keyed by `(prompt_id, run_id, engine)`. A second locale
  inside one project would split every table, every chart and every consolidated
  position, which is why the locale sits on the project and is frozen rather than
  becoming part of the key (ADR 0023).
- `ProjectRunner` builds `PipelineInput` in two places (a crawl and a scheduled
  crawl); both had to carry the locale or a scheduled run would silently use the
  server default.
- SerpApi rejects any location outside its own database, so a city name cannot be
  turned into a canonical location string by us. `serp_location` is a separate
  verbatim field.

## What was built

Backend

- `src/core/locale.py`: the `Locale` value object — `country` (ISO-2,
  upper-cased), `language` (lower-cased, default `en`), optional `city`,
  `region`, `serp_location`, `timezone`; `label` for badges;
  `openai_user_location()` and `perplexity_user_location()` emitting each
  vendor's own shape. The module docstring states support engine by engine.
- `Engine.honours_locale` in `src/integrations/schemas.py`: one place that
  records which engines accept a location; false for Gemini only.
- `Settings.default_locale()` builds a locale from `SERP_GL` / `SERP_HL` /
  `SERP_LOCATION`, so a project without one behaves exactly as before.
- `openai_search.py`, `perplexity.py`, `serp_api.py` take `locale: Locale | None`
  and send it; `gemini_search.py` is deliberately untouched.
- `PipelineInput.locale`; the pipeline resolves it once per execution before any
  connector is built and passes it to the three that honour it.
- `ProjectBase.locale` / `ProjectUpdate.locale`, carried into both
  `PipelineInput` sites in `runner.py`.
- The freeze: `PUT /api/projects/{id}` refuses a locale change once the project
  has crawls, with the reason in the message ("every stored snapshot was captured
  from the old market ... create a second project for another market").

Frontend

- `components/LocaleBadge.tsx`: the market beside the project header, with a
  tooltip listing the fields and stating that Gemini ignores them.
- `ProjectForm`: a "Market" section (country, language, city, region, Google
  location string) with the same two sentences — what Gemini does, and that the
  locale is frozen after the first crawl.
- Regenerated `openapi.json` and `schema.d.ts` from an in-process app (not the
  running server, as in cycles 0016 and 0018).

Tests

- `tests/integrations/test_openai_search.py`, `test_perplexity.py`,
  `test_serp_api.py`: the pinned request bodies now assert the `user_location`
  shape per vendor, a project locale overriding the settings, and the fallback
  when `serp_location` is not set.
- `tests/integrations/test_gemini_search.py`: asserts no location ever appears in
  a Gemini body and that `Engine.GEMINI.honours_locale` is false.
- `tests/modules/control_plane/test_app.py`: the locale reaches the pipeline and
  a change is refused after a crawl.
- UI: `ProjectsPage.test.tsx` asserts the form sends a locale and defaults the
  language.

## Bugs found and fixed

- `ProjectLock.test.tsx` failed on `Unable to find a label with the text of:
  /Mark done:/`. Not this cycle's change: commit `b6f6bb8` ("action groups start
  closed on the Actions page"), from the session running in parallel, leaves
  every group collapsed, so the checkbox that test clicks is not rendered. The
  test is about the credential, not the layout, so it now opens the first group
  before reaching for a card.
- The whole UI suite failed 12 cases on `Test timed out in 30000ms` with no
  assertion errors — the heaviest jsdom renders (command palette 22 s, theme
  switch 15 s) run close to the limit alone and past it under three workers.
  Cycle 0018 worked around the same thing with a CLI override
  (`--testTimeout=120000`); `testTimeout` in `vite.config.ts` is now 60 s and
  `asyncUtilTimeout` 20 s, so the durable config matches the machine.
- An AntD optional label renders as "Country (optional)", so
  `getByLabelText("Country")` found nothing; the test helper takes a `RegExp`
  now.

## Corrections

- `docs/ROADMAP_FEASIBILITY.md` §3 said "Perplexity Agent API: location controls
  exist on the older chat endpoint; support on `/v1/responses` needs verifying
  before we promise it" and, in the risks, "two of four engines cannot be
  localised (Gemini) or are unverified (Perplexity)". Both are corrected in place
  with the verification date: three of four engines take a location, and only
  Gemini cannot.
- This cycle's ADR was written as 0022 and renumbered to **0023**: the session
  running in parallel had already claimed 0022 for crawler-log analytics in
  `src/modules/crawler_logs/__init__.py` and `.env.example`.

## Explicitly not done

- Several locales inside one project. A second market is a second project, and
  its spend is a second full crawl.
- Gemini localisation: impossible on the Developer API, so the badge says so
  rather than implying a market it cannot honour.
- Device (mobile versus desktop) stays tracker-wide (`SERP_DEVICE`).
- No locale on the Atlas, Trends or Costs screens beyond the project header
  badge, and no comparison of two markets side by side.
- Prompts are not translated: `hl=fr` does not make a French prompt set.
- Nothing backfills existing projects; their `locale` is null and they keep using
  the server defaults.
- No live vendor call was made to confirm a localised answer differs; the payload
  shapes are asserted against fixtures modelled on the vendor docs (the standing
  gap in `docs/KNOWN_GAPS.md`), and the operator's instruction not to spend was
  kept.

## Gate output (verbatim)

Python — `ruff format --check`, `ruff check`, `mypy --strict src`:

```
220 files already formatted
All checks passed!
Success: no issues found in 67 source files
```

`pytest -q`:

```
789 collected; 787 passed, 2 failed (pytest prints the file list on
`--collect-only`; the counts are summed from it).

=========================== short test summary info ===========================
FAILED tests/modules/control_plane/test_project_access.py::test_every_write_is_refused_without_the_credential_and_changes_nothing
FAILED tests/modules/control_plane/test_project_access.py::test_the_holder_can_do_every_write

E   AssertionError: ('POST', '/api/projects/9f10e76d38bb/crawler-logs/import', '{"detail":"Method Not Allowed"}')
    assert 405 == 403
```

Both failures are the parallel session's work in progress, not this cycle's:
they added `POST /api/projects/{id}/crawler-logs/import` and its delete to the
write list in `test_project_access.py` before the routes exist, so the API
answers 405. `grep -c crawler src/modules/control_plane/app.py` returns 0, that
file is not in this commit, and the same suite ran clean an hour earlier, before
their edit landed in the tree.

`scripts/drift_check.py`:

```
--- Drift Audit Results ---
Architecture drift detected:
  - Module 'src/modules/crawler_logs' is undocumented.
```

That module is the parallel session's uncommitted work (`src/modules/crawler_logs/`
is untracked), not this cycle's, and nothing of it is in this commit. The check is
clean for every file this cycle touched.

UI (`npm run typecheck` and `npm run lint` clean after `prettier --write` on the
setup file; `npm test`):

```
 Test Files  19 passed (19)
      Tests  75 passed (75)
   Duration  379.67s (transform 3.70s, setup 10.39s, collect 113.83s, tests 924.40s, environment 13.25s, prepare 2.28s)
```

`npm run build`:

```
dist/assets/index-b-8N31BC.js  1,478.24 kB │ gzip: 437.10 kB
✓ built in 32.48s
```
