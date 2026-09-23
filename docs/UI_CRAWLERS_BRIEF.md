# UI brief — Crawler logs tab (fetch → consulted → cited)

**For:** the UI session working in `ui/`.
**Backend contract:** shipped in cycle 0020 (ADR 0022). Regenerate types
first: `cd ui && npm run gen:types` (dump from `create_app` on a temp SQLite
if the running server predates the code — see the project memory note).
**Do not** touch `src/`, `tests/`, `scripts/` or `docs/adr/`.

## What it is

A sixth project tab, **Crawler logs**, after *Actions* in
`src/pages/project/ProjectLayout.tsx`. The analyst uploads the client's
web-server access log; the tab shows which AI crawlers fetched which pages,
whether the fetch succeeded, and joins that to what the engines cited and
consulted for the project's prompts. Prompt scope (`?scope=`) does **not**
apply to this tab: crawler fetches are not per prompt. Show the scope selector
disabled with the tooltip "Crawler fetches are not per prompt".

## Endpoints

| Call | Returns | Notes |
| :-- | :-- | :-- |
| `GET /api/crawler-logs/bots` | `BotSpecOut[]` | catalogue: `name`, `token`, `vendor`, `purpose` (`training`/`index`/`live_fetch`), `engine`, `verifiable`. Cache for the session. |
| `POST /api/projects/{id}/crawler-logs/import` | `CrawlerImportResult` | **owner write** (attach `X-Project-Authorization` like every other write; 403 opens the unlock dialog). Two body shapes below. |
| `GET /api/projects/{id}/crawler-logs?days=30` | `CrawlerLogView` | `days` 1–400; default 30. Window presets 7 / 30 / 90 / 400. |
| `DELETE /api/projects/{id}/crawler-logs/imports/{import_id}` | 204 | owner write. 404 when already gone. |

Errors: **400** `detail` (unusable log; the message never contains a line and
must be shown verbatim), **409** `{detail, import_id}` (same file already
imported — link to that import in the list), **413** `detail` (too large —
show the "upload as a file" hint below), **403** project locked.

## Import control — copy `src/pages/project/prompts/ImportCard.tsx`

Same card shape, same drag-and-drop / file picker / paste area, same result
panel. Two paths, chosen by size:

1. **≤ 2 MB after pre-filtering → JSON.** Read the file in the browser. If the
   name ends in `.gz`, use `DecompressionStream("gzip")`. Pre-filter lines
   client-side: keep a line when it contains any `token` from
   `/api/crawler-logs/bots` (case-insensitive) — that is what the server keys
   on, so nothing is lost and a 300 MB log usually shrinks to a few hundred
   kilobytes. For Cloudflare NDJSON, filter the same way (the token appears in
   `ClientRequestUserAgent`). Post `{"text": filtered, "note"}`. Show "Kept
   {n} of {m} lines that name a known crawler" before sending.
2. **> 2 MB after filtering → raw upload.** Post the original `File` with
   `Content-Type: text/plain` (or `application/gzip` for `.gz`, unchanged).
   The server streams it; the cap is 50 MB decompressed. Show a progress bar
   from `XMLHttpRequest.upload.onprogress` (fetch has no upload progress).

Note field: `Input` max 500 chars, placeholder "e.g. nginx access.log, 1–30 Sept".

**Result panel** (from `CrawlerImportResult`), in this order:

- Headline: `{hits} crawler fetches from {matched} matched lines across
  {span_from} – {span_to}` (dates as `dd MMM`), then a `Tag` for `format`
  (`combined` → "nginx / Apache", `cloudflare` → "Cloudflare") and a Tag for
  `verification_basis` (`remote_addr` → "verified on remote address", `xff` →
  "verified on X-Forwarded-For", `cloudflare` → "verified by Cloudflare",
  `none` → "not verifiable").
- Counters row: `lines`, `parsed`, `unparsed`, `duplicate_lines`,
  `verified_hits`, `stealth_hits`, `hosts_skipped`, `no_host`,
  `methods_skipped`, `sensitive_dropped`. Hide zeros except `verified_hits`.
- `no_host > 0` → info line "This log carries no host field; requests were
  attributed to the project's domains." `keys_truncated` → warning "More than
  200,000 distinct pages; the smallest were dropped." `sampled` → warning
  "Cloudflare sampled this log; counts are scaled by the sample interval."
- `overlaps.length > 0` → info "Shares days with {n} earlier import(s); per
  day the import with the most lines is used." Link each id to the list.
- Last, in a muted paragraph, the **`stored`** sentence verbatim. This is the
  data-processing answer; never paraphrase it.

## Sections of the view (`CrawlerLogView`)

Empty state (no `imports`): the import card alone with a one-paragraph
explanation of what will appear and the privacy sentence.

1. **Coverage strip.** `covered_days` of `days` ("Logs cover 21 of the last
   30 days"), `since`–`until`, and the `ranges` snapshot as a muted line:
   "Crawler address lists: OpenAI {date}, Perplexity {date}, Google {date},
   Apple {date}" from `ranges.vendors`, with `ranges.fetched_at` in the tooltip.

2. **By bot** (`by_bot`, sorted by `hits` desc). Table: bot (with vendor
   muted), purpose Tag (`training` grey "Training", `index` blue "Search
   index", `live_fetch` green "Live fetch"), engine Tag via `ENGINE_LABEL` when
   set, `hits`, verified column: `verified_hits === null` → "unverifiable"
   muted; else `verified_hits / hits` with a warning colour when below 90 %
   ("{n} fetches came from outside the vendor's published addresses"),
   `blocked` (red when > 0), `pages`, `last_seen` relative. Row help on
   Googlebot: "Shown for reference; Googlebot never produces an action card."

3. **Daily chart** (`daily`). Stacked bars per `by_bot` key in `daily[].by_bot`
   using the chart components in `src/components/charts`. A day with
   `covered: false` is drawn as a hatched empty column, **not** zero. Legend
   groups by purpose colour.

4. **Stealth** (`stealth`, vendor → hits). A small callout only when
   non-empty: "{n} requests came from {vendor}'s published crawler addresses
   without naming a crawler." Tooltip: what stealth means (ADR 0022 §C).

5. **Funnel table** (`pages`). One row per `url_key`. Columns: page (the
   `url_key` without host when it is the project's primary domain; full key
   otherwise; click opens `https://{url_key}` in a new tab), fetches (total,
   with the per-bot breakdown from `fetches[]` in a popover: bot, purpose,
   `hits`, `ok`, `blocked`, `redirected`, `verified`, `last_seen`),
   consulted (`consulted === null` → "n/a" muted with tooltip "Only ChatGPT
   reports the pages it read"; else the number), cited (sum of `cited` values,
   with per-engine Tags on hover), match Tag (`exact` green "Cited",
   `near` amber "Cited as a variant" with `near_urls` in the tooltip, `none`
   → no tag), last fetch, last cited. Row states: `is_asset` → muted row with
   the tag "asset"; `redirected_only` → amber tag "redirects — check the
   target"; any `fetches[].blocked > 0` → red tag "blocked". Default sort:
   `match === "none"` first, then `ok_fetches` desc. Filters: bot, purpose,
   match, hide assets (default on).

6. **Fetched, never cited** (`fetched_not_cited`). The card list above the
   table, one card per row: title `{bot} fetched {url_key} {fetches}× in
   {days} days`, subtitle `{engine} never cited it`, `consulted` when not
   null ("read in {n} answers" / "never read"), `queries` as chips. These are
   the same items that appear on the Actions tab as `fetched_not_cited`; link
   each card to `/projects/{id}/actions?type=fetched_not_cited`.

7. **Imports** (`imports`, newest first). Table: `imported_at`, `format`,
   `span_from`–`span_to`, `lines` / `parsed` / `matched`, `verification_basis`,
   `note`, `overlaps` (count with ids in the tooltip), `purged_at` (muted
   "aggregates purged" when set), delete button (owner write; confirm; then
   refetch the view). `sampled` → a small "sampled" tag.

## Actions tab and drawer

- `ACTION_TYPE_LABEL.fetched_not_cited = "Fetched but never cited"`;
  `ACTION_TYPE_HELP.fetched_not_cited = "A search or live-fetch crawler read
  this page repeatedly and its platform never cited it. The crawl is not the
  problem; the page is."` in `src/components/ActionCardView.tsx`.
- The card's `evidence.urls[0]` is the page; `evidence.numbers` carries
  `fetches`, `verified_fetches` (absent when unverifiable), `blocked`,
  `consulted` (absent when n/a), `days`. `subtopic` is always `"Crawl"` and
  `prompt_ids` is empty — the Actions page must not hide cards with no
  prompts, and the prompt-scope filter must keep these cards visible in
  project scope and hide them in single-prompt scope.
- Inspection drawer (`battleground/InspectionDrawer.tsx`): no change this
  cycle. A per-prompt fetch view is not possible; fetches are per page.

## Copy and states

- Loading: skeletons per section. Error: `Alert` with `detail`.
- Owner-locked project: the import card and delete buttons render, and a 403
  opens the unlock dialog exactly as prompts import does.
- Numbers use `Intl.NumberFormat`; dates use the project's existing helpers.
- Never show an IP address anywhere: the API does not return one, and the
  pre-filter must not display sample lines from the file.

## Tests to add (vitest)

- Pre-filter keeps a line per token, case-insensitive, and reports kept/total.
- Size routing: a 1 KB filtered log posts JSON; a 3 MB one posts raw with the
  original content type.
- Result panel renders every non-zero counter, the `stored` sentence
  verbatim, and the 409 path links to the earlier import.
- Coverage strip and daily chart distinguish `covered: false` from zero.
- Funnel table sorts `none` first and hides assets by default.
- `fetched_not_cited` label/help present; card with empty `prompt_ids`
  renders on the Actions page.
