# UI brief — prompt scope on every project tab

**For:** the UI session working in `ui/`.
**Backend contract:** shipped in cycle 0013 (ADR 0017). Regenerate types first:
`cd ui && npm run gen:types` — the running server predates the code, so if the
script falls back to the snapshot, dump a fresh one in-process with `create_app`
on a temp SQLite (see the project memory note).
**Do not** touch `src/`, `tests/`, `scripts/` or `docs/adr/`.

## What the operator asked for, in their words

> "i dont want the tabs to be one … i want the present data representation
> whatever the data is right now of all the section is absolutely fine … but i
> want the UI and information in the screen must change on the basis of
> individual prompts also when selected from the dropdown just like the trends
> tab where all the data is for all prompts at once is also there and on the
> basis individual prompts also"

So: **no tab merges, no redesign.** Every layout stays. A selector in the project
header adds a second mode in which every card, table and chart on the five
project tabs re-computes for one prompt. Atlas, Trends and Costs pages are
untouched.

## The control — copy the Trends picker exactly

`src/pages/trends/TrendsPage.tsx:140-154` is the pattern:

- antd `<Select<string>>`, `showSearch`, `optionFilterProp="label"`,
  `style={{ minWidth: 280, maxWidth: 420 }}`.
- First option: `{ value: "all", label: \`All tracked prompts (${prompts.length})\` }`.
- Then one per `usePrompts(project.id)` row: **`value: p.id`** (the stable
  uuid, *not* `prompt_id`), `label: p.prompt_text`.
- Placement: `src/pages/project/ProjectLayout.tsx`, in the header flex row after
  the secondary line (`brand · lob · every interval`), before the `Tabs`.

## State — URL, and only the scope survives tab switches

- Param name is **`?scope=<tracked_id>`**. `?prompt=` is taken: it opens the
  inspection drawer on Battleground (`BattlegroundPage.tsx:79`) and Atlas
  (`AtlasPage.tsx:85`) and is written by `OverviewPage.tsx`, `PromptsPage.tsx`,
  `ActionCardView.tsx` and `CommandPalette.tsx`. Do not overload it.
- `ProjectLayout.tsx:57` currently does `navigate(\`/projects/${id}/${k}\`)`,
  dropping the whole query string. Change it to carry **only** `scope` across
  tabs — not the drawer's `prompt` / `engine` / `crawl`, which are per-tab.
- `TopBar.tsx:27` (project switch) and `ProjectCard.tsx:47,54` drop it, which is
  correct: tracked ids are project-specific.
- `AppShell.tsx:34` animates on `location.pathname` only, so changing `?scope=`
  does not remount the page. Good; leave it.
- Resolve in `ProjectLayout`: `scope = tracked ? { trackedId, promptId: tracked.prompt_id, prompt: tracked } : null`
  from `usePrompts`. Pass it through `Outlet context={{ project, scope }}`;
  every tab reads `useOutletContext<{ project; scope }>()`.
- **Unresolvable id** (deleted prompt, foreign id): render an `Alert`
  "This prompt is no longer tracked — showing all prompts", treat scope as
  `null`, keep the param in the URL so the back button still works. Never 404
  the tab.
- No Zustand entry needed. If you do add one, key it per project like
  `watchedJobs`, and remember the stable-empty-fallback rule
  (`PromptsPage.tsx:52-53`).

## Queries — the id goes in the key

- `useInsights(projectId, consolidationId, promptId)` → `GET …/insights?prompt_id=`.
  Add `promptId` to `qk.insights`. **Never filter the unscoped response
  client-side** — its lists are capped project-wide before serialisation and the
  claims dedup omits the prompt (ADR 0017 has the two proofs).
- `useCosts({ projectId, days, excludeSource, promptId })` → `GET /api/costs?project_id=&prompt_id=`.
  Add `promptId` to the key.
- `useResults`, `usePositions`, `useCrawls`, `usePrompts`: unchanged, filter
  client-side — these are lossless (`PromptResult` and `ConsolidatedPosition`
  carry `prompt_id`; crawls intersect with `PromptDetail.run_ids`).
- `usePromptDetail(projectId, trackedId)` → `GET …/prompts/{tracked_id}/detail`
  (cycle 0012). Needed by Overview and Runs under scope.

## New response fields you will use

**`InsightsView`** — same shape; under `?prompt_id=` every list is that prompt's.
`basis.crawls` and `basis.low_confidence` stay window-level by design;
`basis.samples` is the prompt's own. `health[].prompts`, `fanout[].prompts`,
`winning_pages[].prompts`, `client_pages[].prompts` are always `1` under scope —
do not display them as counts.

**`CostReport`** — `attribution: "all" | "direct_engine_calls"`,
`unattributed_calls: number`, `unattributed_actual_usd: number`. Under scope,
`total_actual_usd` is **direct engine spend only**; the unattributed pair is the
harvest / keyword-rank / redirect spend in the same runs, shared by every prompt.
Show both; never add them together as "this prompt's cost".

**`PromptDetail`** (cycle 0012) — `engines[]` with `status: EngineStatus`
(`has_data | asked_failed | never_asked | not_configured`), `cited_samples`,
`ok_samples`, `failed_samples`, `citation_rate` (stored, pre-rounded),
`cited`, `cited_in_minority`, `models[]`, `history[]`, `velocity`; `run_ids`;
`capture` (`with_answer_text` etc.); `content_gap`; `shared_lob_projects`.

## Per-tab treatment

The rule from the operator: **where a single-prompt equivalent exists, show it;
where none exists, keep the card in place, dimmed, with a short "project-wide"
note.** The layout never jumps.

### Overview

| Card | All prompts (unchanged) | One prompt |
|---|---|---|
| Low-confidence alert | as is | as is — window is project-level |
| Health tile per engine | `insights.health` | same source, scoped. Replace the tooltip's `N prompts` with the engine's `EngineStatus` from `PromptDetail`; show `cited_samples / ok_samples` under the rate. `asked_failed` → "asked N times, all failed" instead of `0%`; `never_asked` → "not yet sampled"; `not_configured` → "not tracked (history kept)" |
| Delta chip | `delta_cited_rate` | **suppress below 3 samples** — 1,704 snapshots are exactly 0% or 100% at n≤2 |
| What changed | scoped from server | scoped from server; empty copy "Nothing moved for this prompt" |
| Top actions | scoped from server | scoped; `read_but_rejected` and `freshness` cards carry `prompt_ids: []` and are implicitly this prompt's — show them. `impact` pill is relative within the scoped list only |

### Battleground

One row. Group header reads `1 prompt · linked on X of Y platforms`. Drawer
unchanged. The `Consolidated (last N crawls)` / `Point-in-time` toggle still
applies.

### Prompts

Filter the table to the row and highlight it. **Add a "cited in a minority"
state to the Verdict column** from `PromptDetail.engines[].cited_in_minority`:
today `promptView.ts:54-60` derives `linkedOn` from `client_cited`, which is a
≥50% majority verdict, so 482 real snapshots with a rank render as "Absent".
This fix is worth doing in both modes.

### Runs & spend

| Stat / card | All prompts | One prompt |
|---|---|---|
| `Due now` | `summariseDue(results)` | that prompt's `result.due_on` |
| `Full crawls` | crawls with `full` | **"Crawls covering this prompt"** = crawls whose `run_ids ∩ PromptDetail.run_ids ≠ ∅`. Drop empty-string run ids first (legacy samples). Crawls that *reused* the prompt's snapshot wrote no sample — show those as "covered by reuse" using the atlas snapshot `run_id`s, do not omit them |
| `Cost per crawl` | `total_actual_usd / crawlCount` | **"Direct engine spend"** = `total_actual_usd` under scope, with a second line `+ $X shared across the run (harvest, keyword rank, redirects)` from `unattributed_actual_usd`. Hint: "engine samples for this prompt only; shared costs are not divided" |
| Run buttons | Run due now / Run everything | **"Run this prompt now"** → the existing `useRunProject` with `{ force: true, prompt_ids: [prompt_id], engines: null }` (already used by the bulk bar, `PromptsPage.tsx:471-475`) |
| JobProgressCard | as is | as is, dimmed, note "project-wide" |
| ConsolidationPanel | as is | as is, dimmed, note "project-wide — the window is a project setting" |
| Crawl history table | as is | filtered to covering crawls; `Prompts run` and `Batches` columns dimmed (crawl-level) |
| Pipeline runs table | as is | filtered to `PromptDetail.run_ids`; `Engine calls` / `Spend` columns dimmed with tooltip "run totals; this prompt's share is in Direct engine spend above" |

### Actions

Server-scoped. Summary strip reads `N open for this prompt`. Filters unchanged.
Remember the shared state: ticking a scoped card done ticks the same card in the
project view — same id, same prescription. That is intended.

## Degenerate copy — exact replacements

| Where | All prompts | One prompt |
|---|---|---|
| Health tooltip | `… · 20 prompts · volatility 12%` | `… · volatility 12%` (drop the prompt count) |
| Battleground group header | `20 prompts · linked on 31 of 60 sampled cells` | `1 prompt · linked on 2 of 3 platforms` |
| Actions strip | `25 open · 3 done` | `4 open for this prompt · 1 done` |
| Runs `Full crawls` | `Full crawls 12` / `runs that covered every prompt` | `Crawls covering this prompt 9` / `+2 covered by reuse` |
| Runs `Cost per crawl` | `≈ $0.42` | `Direct engine spend $0.11` / `+ $0.03 shared` |
| Any empty scoped list | existing copy | append "for this prompt" |

## Edge cases to test (vitest)

1. `?scope=` survives Overview → Actions → Runs; `?prompt=` does not.
2. Switching project drops `?scope=`.
3. Unknown `?scope=` → banner, all-prompts data, no 404, param retained.
4. Query keys isolate scopes: mock two `/insights` responses, assert the
   scoped one never renders under "all" and vice versa.
5. Health tile for an `asked_failed` engine shows the failure copy, not `0%`.
6. Delta chip absent at 2 samples, present at 3.
7. Runs: a crawl in `PromptDetail.run_ids` appears; one not in it is hidden;
   a reuse-only crawl shows "covered by reuse".
8. Cost: `total_actual_usd` and `unattributed_actual_usd` rendered separately;
   never summed.
9. Prompts: a row with `cited_in_minority` shows the new state, not "Absent".
10. Disabled prompt selected → data shows with the existing `· paused` marker
    (zero disabled prompts exist in the store today, so this path is untested —
    build it defensively).

Per project memory: scope vitest queries by text within a container
(role-with-name over the AntD tree blocks for seconds); Zustand selectors must
not return a fresh `[]`; charts render their table twin in jsdom; the full UI
gate is `npm run lint`, `typecheck`, `test`, `e2e`, `build`.

## What not to do

- Do not merge tabs or move sections. The operator withdrew that explicitly.
- Do not filter the project-wide `/insights` on the client.
- Do not amortise unattributed cost into the prompt's figure.
- Do not put `prompt_id` in the URL; carry `tracked_id`.
- Do not touch Atlas, Trends or Costs pages.
