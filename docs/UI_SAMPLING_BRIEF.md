# UI brief — prompt stability and the sampling policy

**For:** the UI session working in `ui/`.
**Backend contract:** shipped in cycle 0022 (ADR 0025). Regenerate types first:
`cd ui && npm run gen:types`.
**Do not** touch `src/`, `tests/`, `scripts/` or `docs/adr/`.

## What it is

Every prompt × platform pair now has a stability verdict, and a project has a
`sampling_policy` (`fixed` | `save` | `reallocate`) that decides whether the
planner acts on it. The UI has three jobs: let the analyst set the policy, show
them the dry run of the next crawl before and after they do, and show each
pair's verdict where they already look at that pair.

## Endpoints and fields

| Where | What |
| :-- | :-- |
| `Project.sampling_policy` | `"fixed"` default. Set with `PUT /api/projects/{id}` (owner write). |
| `GET /api/projects/{id}/sampling` | `SamplingView`: `policy`, `simulated`, `window_crawls`, `min_crawls`, `stretch_max`, `volatile_boost`, `computed_at`, `summary: SamplingSummary`, `decisions: SamplingDecision[]`, `warnings: string[]`. |
| `GET /api/projects/{id}/sampling?policy=save` | Same, computed as if the project had that policy; `simulated: true`. |
| `SamplingDecision` | `tracked_id`, `prompt_id`, `prompt_text`, `engine`, `stability: StabilityReport`, `base_samples`, `samples`, `multiplier`, `stretched`, `boosted`, `due`, `skipped`, `expected_calls`, `baseline_calls`, `next_due_at`, `note`. |
| `StabilityReport` | `state` (`unknown`/`stable`/`volatile`/`failing`), `score` (0..1 or null), `crawls`, `ok_samples`, `cited_samples`, `rate`, `rate_low`, `rate_high`, `flips`, `streak`, `newest_at`, `reason` (the sentence to show). |
| `SamplingSummary` | `policy`, `pairs`, `due_pairs`, `stretched_pairs`, `skipped_pairs`, `boosted_pairs`, `calls_planned`, `calls_baseline`, `calls_saved`, `calls_boosted`, `carry_calls`, `est_cost_planned_usd`, `est_cost_baseline_usd`, `next_due_at`. |
| `RunOutcome.sampling`, `ProjectRunRecord.sampling` | The summary for that crawl; `null` for crawls before this cycle. |
| `PromptEngineDetail.stability` | The pair's report for the drawer. |
| `RunOutcome.reason` | Idle crawls now read `nothing due; 3 stable pair(s) stretched, next due 2026-09-26`. Keep matching on the `nothing due` prefix. |

## 1. Project form: the policy control

In `src/pages/projects/ProjectForm.tsx`, a `Segmented` with three options under
the interval and samples fields:

- **Fixed** — "Every prompt on every platform, at its interval."
- **Save** — "Settled prompts are sampled every 2 to 3 intervals. Cheaper; their bands widen."
- **Reallocate** — "Save, and give the extra samples to prompts that are still a coin flip. Same budget, tighter bands where it matters."

Below the control, when the project exists, a live line from
`GET /sampling?policy=<selected>`: "Next crawl: {calls_planned} calls
(~{est_cost_planned_usd}) versus {calls_baseline} (~{est_cost_baseline_usd})
fixed · {skipped_pairs} pairs skipped · {boosted_pairs} boosted". Debounce the
fetch; it is a dry run and cheap, but do not fire it per keystroke. Zod:
`sampling_policy: z.enum(["fixed", "save", "reallocate"])`.

## 2. Runs & spend tab: a Sampling section

Above the crawls table on `RunsPage.tsx`:

- **Header line**: the policy as a Tag (grey Fixed, blue Save, green
  Reallocate), then "{stretched_pairs} stretched · {boosted_pairs} boosted ·
  next crawl {calls_planned} calls vs {calls_baseline} fixed". When
  `carry_calls > 0`: "{carry_calls} saved calls carried forward".
- **Warnings** from `warnings[]` as `Alert type="info"` (the fixed-policy
  hint and the reuse-window warning are the two that exist).
- **Decisions table**, one row per pair, grouped by prompt (expandable rows,
  prompt text as the group header). Columns: platform (`ENGINE_LABEL`),
  stability Tag (`stable` green, `volatile` amber, `failing` red, `unknown`
  grey) with `stability.reason` in the tooltip, band as "likely
  {rate_low}–{rate_high}%" or "—", crawls, streak, "next crawl" cell: `due`
  → "{samples} samples" with a small "+{samples − base_samples}" chip when
  `boosted`; `skipped` → "skipped, due {next_due_at}" muted; otherwise "not
  due until {next_due_at}". `note` in the row tooltip. Filters: state, due
  only, changed by policy (stretched or boosted). Default sort: volatile
  first, then stable, then unknown.
- **Simulate**: a `Segmented` above the table, defaulting to the project's
  policy, that refetches with `?policy=` and shows a "simulated" ribbon when
  it differs. This is how the analyst previews Save or Reallocate before
  saving it on the project form; put a "Apply as project policy" button next
  to it that opens the project form with that value selected.
- **Crawls table**: a new column "Sampling" from `record.sampling`:
  "{calls_planned}/{calls_baseline} calls · {skipped_pairs} skipped ·
  {boosted_pairs} boosted"; "—" when `null`. The job card
  (`runs/JobProgressCard.tsx`) shows the same from `outcome.sampling` and
  renders the full `reason` string on idle crawls, which now explains itself.

## 3. Inspection drawer

In `battleground/InspectionDrawer.tsx`, under the consolidated numbers of the
selected platform, one line from `PromptEngineDetail.stability`: the state Tag
and `reason`. When `state === "stable"` and the project's policy is not fixed,
append "· sampled every {multiplier} intervals" using the matching decision
from `GET /sampling` (match on `tracked_id` + `engine`); do not compute the
multiplier in the browser.

## 4. Prompts table

An optional column "Stability" showing the worst state across the prompt's
platforms (volatile > failing > unknown > stable) as a Tag, from
`GET /sampling` decisions. Off by default; toggled in the table's column
picker like the other optional columns.

## Copy rules

- Never show `score` as a number; it orders rows. Show the sentence.
- Band as whole percentages, en dash between, exactly as the backend's
  `reason` does.
- "skipped" means "not sampled this crawl because it is settled"; never
  "missing" or "failed".
- `failing` is a platform problem, not a prompt problem: tooltip "Every sample
  failed; check the platform's credentials or quota."

## Tests to add (vitest)

- Policy segmented control round-trips through the form and the PUT body.
- Runs section renders the header line, groups decisions by prompt, and
  switches to the simulated view with the ribbon.
- Crawl rows render "—" for `sampling: null` and the summary otherwise.
- Drawer shows the stability sentence for the selected platform.
- Idle job card renders the extended `nothing due; …` reason verbatim.
