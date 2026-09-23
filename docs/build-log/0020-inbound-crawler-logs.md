# Cycle 0020 — Inbound AI-crawler logs and the fetch → consulted → cited funnel

**Date**: 2026-09-23
**Operator request**: "Option A (Recommended): Feature 4 — Inbound AI Crawler &
Log Analytics … Build the server log importer (Nginx / Apache / Cloudflare) to
track when OAI-SearchBot, GPTBot, and PerplexityBot crawl customer websites,
establishing the Fetch-to-Citation Funnel." Decisions taken when asked:
manual upload only; verify against bundled published IP ranges and never store
addresses; backend in this session, UI from a brief.

Decision record: ADR 0022. The roadmap called this cycle 0019; the number was
taken by the per-project locale cycle landed in parallel, so it is 0020 here.
Vendor spend: $0 (ten zero-cost GETs for the published IP-range lists, through
`BaseAPIClient`, ledgered at zero).

## Scope

Backend only: the `src/modules/crawler_logs` package (catalogue, URL key,
IP-range verification, streaming parser, aggregation, store, funnel), four
control-plane routes, the `fetched_not_cited` action card through a new
`InsightEngine.extra_cards` hook, three settings, retention purge, the bundled
`ranges.json` and its refresh script, docs and `docs/UI_CRAWLERS_BRIEF.md`
for the UI session. No `ui/` edits.

## Research findings

- No URL canonicaliser existed anywhere in `src/`; citations were compared
  raw. In the live store 306 of 584 ChatGPT citations carry
  `?utm_source=openai`, 444 of 2,225 cited URLs end in `/`, 43 have upper-case
  path characters, `consulted_urls` exists only for ChatGPT, and Gemini keeps
  `resolved=False` redirect URLs. One key had to be applied to both sides of
  the join; case is preserved and a case-folded bucket reports near misses.
- Plain `combined` logs carry no host; behind a CDN `$remote_addr` is the
  proxy. Cloudflare Logpush carries host, path, `VerifiedBotCategory`,
  `SampleInterval` and timestamps in ns, µs, ms, s or RFC 3339.
- Google moved its range lists: `developers.google.com/static/search/apis/
  ipranges/googlebot.json` now 301s to `/static/crawling/ipranges/
  common-crawlers.json`; the old `googlebot.json` name is a 404 under the new
  path. The refresh script now follows a same-host redirect (re-validated per
  hop) and fetches `common-crawlers`, `special-crawlers`,
  `user-triggered-fetchers` and `user-triggered-fetchers-google`.
- `python-multipart` is not installed and the UI convention is "read the file
  in the browser, post JSON text". A 50 MB log inside a JSON string costs three
  to four times its size in memory, so JSON text is capped at 2 MB and real
  files go as a raw streamed body to a spooled temp file. No new dependency.
- `insights.py` (1,446 lines) and `app.py` (418) are past the 400-line
  target; every new line went into new files. `InsightEngine` gained one
  optional `extra_cards` provider and a public `action_id`.
- Per-project writes use `dependencies=owner_only`; both new write routes
  were appended to `_writes` in `test_project_access.py` so the 403 sweep
  covers them.

## What was built

- `crawler_logs/bots.py`: 20-entry catalogue with vendor, purpose
  (`training`/`index`/`live_fetch`) and engine mapping (OAI-SearchBot,
  ChatGPT-User → ChatGPT Search; PerplexityBot, Perplexity-User →
  Perplexity); one compiled alternation with token boundaries.
- `crawler_logs/normalise.py`: `url_key`, `url_key_from_url`, `url_key_ci`,
  `is_asset` (not `.pdf`), `is_sensitive` (hex ≥ 20, UUID, opaque mixed-case
  blob with digits, `@`, > 512 chars).
- `crawler_logs/ranges.py` + `ranges.json` (74 KB; OpenAI 3 lists,
  Perplexity 2, Google 4, Apple 1 with `creationTime`); `verify` per vendor
  union returns `None` for vendors that publish nothing.
- `crawler_logs/parser.py`: streaming, gzip multi-member, incremental UTF-8,
  format sniff; combined with vhost prefix, `X-Forwarded-For` leading (one or
  more proxies) or trailing, IPv6, `\"`/`\xHH` escapes, absolute-URI and
  `-`/HTTP-0.9 request lines, CLF or ISO time in any offset; Cloudflare
  NDJSON/array; caps and ratio guard (`PayloadTooLarge`); `ValueError` for
  unusable bodies naming the missing field and never a line.
- `crawler_logs/ingest.py`: per `(day, bot, url_key)` counters, verified per
  vendor, stealth per `(day, vendor)`, host/method filters, sensitive keys
  dropped, minute rounding for live-fetch bots, 200,000-key cap.
- `crawler_logs/store.py`: four tables, one transaction per import,
  `DuplicateImport` per project on the decompressed hash, overlaps, per-day
  winner (most parsed lines, then newest), purge keeping import rows with
  `purged_at`, `delete_import`, `delete_project_data`.
- `crawler_logs/funnel.py`: `build_view` joining winners to
  `TimeSeriesDB.samples_since` (new; `prompt_id IN json_each(?)`), per-page
  match exact/near/none, blocked and redirected buckets, `FetchedNotCited`
  rows with the ≥ 3 fetches and ≥ 3 samples floors; never Googlebot or
  training bots.
- `control_plane/crawler_routes.py`: import (owner-only; JSON ≤ 2 MB or raw
  streamed ≤ 50 MB decompressed), view (`days` 1–400), catalogue, delete;
  409 and 413 handlers. `control_plane/crawler_cards.py`: the card. `app.py`
  registers the router before the SPA fallback, purges at start-up and clears
  crawler data on project delete. `runner.py` owns the `CrawlerLogStore` and
  passes the `extra_cards` lambda.
- Settings `CRAWLER_LOG_MAX_BYTES`, `CRAWLER_LOG_MAX_JSON_BYTES`,
  `CRAWLER_LOG_RETENTION_DAYS` in `config.py` and `.env.example`.
- `scripts/refresh_bot_ranges.py`: `BotRangeClient(BaseAPIClient)` under
  `UrlSafetyPolicy` and `PinnedTransport`, redirect following capped at three
  same-host hops, writes the bundled snapshot.

## Bugs found and fixed (during the cycle, before the gate)

- `is_sensitive` flagged every hyphenated slug of 20+ characters
  (`/blog/how-to-implement-gep`). Split into hex, UUID and "opaque" (digits
  and upper case required) patterns; slugs with a year stay pages.
- The leading `X-Forwarded-For` form only matched a single proxy; two proxies
  before `$remote_addr` failed the whole line. The group now takes
  `ip, ip, … ip ` with an optional last element.
- A `"-"` request line was counted as a skipped method; it is now `no_url`
  (method must be alphabetic to count as a method).
- `FunnelPage.consulted` and the card's `consulted` were `None` for a page
  ChatGPT never read, indistinguishable from "engine reports nothing"; now
  `0` when the engine reports consulted URLs and `None` only when it does not.
- Google's range URLs (see findings).
- Test fixtures: identical log lines dedupe (per design), so fixtures vary the
  second; Cloudflare epoch fixtures were a year off.

## Corrections

- The plan named the cycle 0019 and put the schemas in
  `control_plane/schemas.py`; they live in `crawler_logs/schemas.py` because
  the funnel is a module contract that `control_plane` imports, not the
  reverse.
- The plan's Google list was `googlebot.json`, `special-crawlers.json`,
  `user-triggered-fetchers.json`; Google's current set is the four files
  above.

## Explicitly not done

- No push endpoint (Logpush destination, agent); no custom log-format DSL; no
  per-line overlap de-duplication (per-day winner only); no JS-rendered fetch
  detection; no GA4 side; no `ui/` changes (brief written). Combined logs
  without a host assume the project's domains. All in `KNOWN_GAPS.md`.

## Verification

End to end on a scratch SQLite (never the live database): project `gep.com`,
three ChatGPT samples citing `https://www.gep.com/software/gep-smart?utm_source=openai`
and consulting `https://gep.com/blog/x/`, a gzip nginx log with three fetches
of `/software/gep-smart/`, four in-range and one spoofed fetch of `/blog/x`,
one duplicate line, one 403, six Googlebot fetches, one `/reset/<token>`:

```
import: format=combined lines=17 parsed=16 duplicate_lines=1 matched=16 hits=15
        verified_hits=14 sensitive_dropped=1 no_host=16 verification_basis=remote_addr
funnel pages: ['gep.com/blog/x', 'gep.com/software/gep-smart']   (gep-smart: match=exact, cited {CHATGPT_SEARCH: 3})
by_bot: [('OAI-SearchBot', 9, 8), ('Googlebot', 6, 6)]
card: OAI-SearchBot fetched /blog/x 5x in 30 days; CHATGPT_SEARCH never cited it | impact 2.0
card numbers: {'fetches': 5.0, 'blocked': 1.0, 'days': 30.0, 'cited_rate': 0.0, 'verified_fetches': 5.0, 'consulted': 3.0}
second upload → 409 with the first import_id; delete → 204; view empty
no address, no user-agent string and no raw line in the database dump
E2E OK
```

## Gate output

```
=== Format ===       PASSED
=== Lint ===         All checks passed!   PASSED
=== Type check ===   Success: no issues found in 78 source files   PASSED
=== Tests ===
src\modules\control_plane\crawler_cards.py           34      0      8      3    93%
src\modules\control_plane\crawler_routes.py          94      1     14      1    98%
src\modules\crawler_logs\funnel.py                  136      3     52      5    96%
src\modules\crawler_logs\ingest.py                  109      5     38      2    93%
src\modules\crawler_logs\parser.py                  268     30     98     14    88%
src\modules\crawler_logs\ranges.py                   61      6     20      2    90%
src\modules\crawler_logs\store.py                   117      3     28      2    95%
TOTAL                                              7449    156   1714     94    97%
Required test coverage of 85.0% reached. Total coverage: 97.10%
870 passed, 2 warnings in 156.32s (0:02:36)
PASSED: Tests
ALL GATES PASSED.

--- Drift Audit Results ---
No documentation drift detected.
```
