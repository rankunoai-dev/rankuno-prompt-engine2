# ADR 0022 — Inbound AI-crawler logs: aggregate-only, upload-only, joined to citations by one URL key

**Date**: 2026-09-23
**Status**: Accepted (cycle 0020; the operator chose "Option A: Feature 4 — Inbound AI Crawler & Log Analytics" and, when asked, manual upload only and IP-range verification with no IP storage)
**Builds on**: ADR 0014 (capture everything: `consulted_urls`, `citation_links`), ADR 0016 (prompt identity), ADR 0018 (public deployment posture), ADR 0019 (per-project owner credentials)

## Context

The tracker holds the *outbound* half of a picture: for every answer, the exact
URLs an engine cited and, for ChatGPT, the client URLs it read and rejected. It
had nothing on the *inbound* half — which pages OAI-SearchBot, ChatGPT-User,
PerplexityBot and GPTBot actually fetched from the client's site, when, and
whether the fetch succeeded. Joined, the halves separate three different
problems that today all look like "not cited":

| Observation | Meaning | Owner |
| :-- | :-- | :-- |
| Fetched repeatedly, never cited | the page was found and judged wanting | content |
| Never fetched, never cited | discoverability | SEO / internal linking |
| Fetched, answered 403/429 | the CDN or WAF blocks the crawler | infrastructure |

Facts that constrained the design, from the code audit and a design review:

1. **No URL canonicaliser existed.** Citations are compared raw; 306 of 584
   ChatGPT citations in the live store carry `?utm_source=openai`, 444 of
   2,225 end in `/`, 43 have upper-case path characters. Log paths have none
   of these. Whatever key the join uses must be applied to **both** sides.
2. **Plain `combined` logs carry no host**, and behind a CDN `$remote_addr` is
   the proxy. Cloudflare Logpush carries host, path, `VerifiedBotCategory`,
   `SampleInterval` and timestamps in nanoseconds, microseconds, milliseconds,
   seconds or RFC 3339, depending on the job.
3. **Access logs are personal data.** Every line has an IP address and often a
   token or an e-mail address in a path a human clicked. The app is deployed
   publicly (ADR 0018); whatever is stored is one leaked volume away from a
   client's users.
4. **User agents are free text.** Anyone can claim to be GPTBot. OpenAI,
   Perplexity, Google and Apple publish the IP ranges their crawlers use;
   Anthropic, Common Crawl, ByteDance, Amazon and Meta do not.
5. `insights.py` (1,446 lines) and `app.py` (418) are past the 400-line
   target; `python-multipart` is not installed and the UI convention is "read
   the file in the browser, post JSON text" (`ImportCard.tsx`).

## Decision

### A. Store per-day aggregates and nothing else

`src/modules/crawler_logs/` parses a log in a stream and reduces it to rows of
`(import, day, bot, url_key) → hits, s2xx, s304, s3xx, s4xx, s5xx, blocked,
verified_hits, first_seen, last_seen`. Nothing else is persisted:

- **No IP addresses.** An address is used once, to test membership in a
  vendor's published range, and discarded. The result is a count.
- **No request lines, referers, query strings or user-agent strings.** The
  user agent is reduced to a catalogue name.
- **No sensitive keys.** A path segment of 20+ hex characters, a UUID, an
  opaque mixed-case blob with digits, an `@`, or a path over 512 characters
  marks the line `sensitive_dropped`; it is counted and not keyed.
- **Minute rounding** of `first_seen`/`last_seen` for `live_fetch` bots
  (ChatGPT-User, Perplexity-User…), because such a fetch is one human's
  action at one second.
- **Duplicate lines** are detected with a transient hash set that is dropped
  when the import ends.
- **Retention** is `CRAWLER_LOG_RETENTION_DAYS` (400) keyed on `day`; purge
  runs at start-up and after every import. Import records survive with
  `purged_at` so provenance is never lost.
- The import response carries a `stored` sentence stating exactly what was and
  was not kept. That sentence is the answer to a client's data-processing
  question, and the UI shows it after every upload.

### B. Upload only, owner only, two body shapes, hard caps

- `POST /api/projects/{id}/crawler-logs/import` requires the project
  credential (ADR 0019). There is no anonymous or token-based push endpoint:
  on a public deployment that needs per-source rate limiting first, and no
  customer's server should hold a credential that can write into the tracker.
- JSON `{"text", "note"}` for the browser path, capped at
  `CRAWLER_LOG_MAX_JSON_BYTES` (2 MB) because a log inside a JSON string costs
  three to four times its size in memory. Real files go as a raw body
  (`text/plain`, `application/x-ndjson`, `application/gzip`), streamed to a
  spooled temporary file on the container's ephemeral disk, never the data
  volume, with `CRAWLER_LOG_MAX_BYTES` (50 MB) enforced on **decompressed**
  bytes and a 100× ratio guard. Either overflow is **413**.
- The same decompressed content in the same project is **409** with the
  earlier `import_id`. Overlapping imports are allowed and resolved at query
  time: **per day, the import with the most parsed lines wins, then the
  newest**. Coverage days are known, so "no log for this day" is distinct from
  "zero fetches" on every chart.
- Unusable bodies are **400**: nothing parsed, more than half of at least 20
  lines unparsed, or a required Cloudflare field missing (named). **No line
  content ever appears in an error message or a log record.**

### C. Verify against bundled published ranges; say what verification meant

`ranges.json` ships in the package with the ten vendor lists (OpenAI 3,
Perplexity 2, Google 4, Apple 1), each with its `creationTime` and source URL,
and is refreshed by `scripts/refresh_bot_ranges.py` through a `BaseAPIClient`
subclass under `UrlSafetyPolicy` (zero cost). `verified_hits` is per vendor
union and is `NULL` for vendors that publish nothing, so an unverifiable bot is
shown as "unverifiable", never as "spoofed".

Which address was tested is stated in `verification_basis`: `remote_addr`,
`xff` (leftmost `X-Forwarded-For` entry, leading or trailing form), or
`cloudflare` (`VerifiedBotCategory` from the edge). A hit from a vendor's
range whose user agent names no crawler is counted as **stealth** per vendor
and day, because it is the one signal that a vendor fetches without
identifying itself.

### D. One URL key on both sides of the join

`normalise.url_key(host, path)`: host lower-cased with `www.` and port
removed; path percent-decoded, NFC-normalised and re-quoted canonically,
**case preserved**, `index.html|htm|php` and `default.aspx` stripped, trailing
slash stripped with the root kept as `/`, scheme, query and fragment dropped.
`url_key_from_url()` applies the same to a cited or consulted URL. The key is
idempotent. Case is kept because two pages can differ only by case; a
case-folded `url_key_ci` bucket reports those as `match: near` with the cited
variants, so an analyst sees "cited as /Blog/Case-Study, fetched as
/blog/case-study" rather than a false "never cited".

The funnel (`funnel.py`) runs in Python because the normalisation cannot live
in SQL. Samples are pulled for the project's prompts and engines within the
window (`TimeSeriesDB.samples_since`); citations with `resolved=False`
(Gemini redirect URLs) are unattributable and skipped; `consulted` is `None`
for engines that report no consulted URLs (everything but ChatGPT) so the UI
prints "n/a" and never "0".

### E. The `fetched_not_cited` card is narrow on purpose

A card is emitted only for a bot with an engine mapping (OAI-SearchBot and
ChatGPT-User → ChatGPT Search; PerplexityBot and Perplexity-User →
Perplexity) whose purpose is `index` or `live_fetch`, with **≥ 3 successful
fetches** of a non-asset page whose key no sample of that engine cited, and
only when that engine has **≥ 3 samples in the window** (a denominator check:
one failed run must not accuse every page). Googlebot is catalogued and shown
in the by-bot table because clients ask, but it never drives a card: it feeds
Google Search and the AI Overview is measured elsewhere. Training crawlers
(GPTBot, ClaudeBot, CCBot…) never drive a card either; a training fetch has
no answer to be cited in.

The card enters `InsightEngine.build()` through a new `extra_cards` hook and
receives analyst state like every other card; nothing was added to
`_actions_for`. Its `id` is `action_id("fetched_not_cited", engine, "Crawl",
url_key)`, stable across rebuilds.

### F. Catalogue

`bots.py` names every crawler with its vendor, purpose and engine, matched by
one compiled alternation with token boundaries, most specific first. The traps
the tests pin: `Google-Extended` and `Applebot-Extended` are robots tokens,
not user agents; `facebookexternalhit` is not `meta-externalagent`;
`DuckDuckBot` is not `DuckAssistBot`; `AppleWebKit` is not `Applebot`;
`Amazon CloudFront` is not `Amazonbot`; `AdsBot-Google`,
`Google-InspectionTool` and `Storebot-Google` are not `Googlebot`.

## Alternatives considered

- **Store raw lines for later re-parsing.** Rejected: a public deployment
  holding a client's access log is a liability the feature does not need; the
  aggregates answer every question the funnel asks. Re-parsing after a
  catalogue change means re-uploading, which is a documented gap.
- **Push ingestion (Logpush destination, agent).** Deferred: needs per-source
  rate limiting and a write credential on the customer's infrastructure.
- **Trust `X-Forwarded-For` as the verified address.** Partly rejected: the
  leftmost entry is used but the basis is reported, because a client whose
  log records it can spoof it as easily as a user agent.
- **Line-level de-duplication across imports.** Rejected for this cycle;
  per-day winner is enough when a customer uploads rotated daily files, and
  the response lists the overlapping imports so the analyst sees it.
- **Adding the key to SQL.** Rejected: the canonicaliser is Unicode-aware and
  the join is small (hundreds of pages, thousands of samples).

## Consequences

- New module `src/modules/crawler_logs` (bots, normalise, ranges, parser,
  ingest, store, funnel, schemas); may import `core`, `integrations.schemas`
  and `prompt_tracking`, never `control_plane`.
- Four routes in `control_plane/crawler_routes.py`; the card mapper in
  `control_plane/crawler_cards.py`; `InsightEngine(extra_cards=…)`;
  `TimeSeriesDB.samples_since`; `delete_project` also clears crawler data.
- Four tables in the tracker database: `crawler_imports`,
  `crawler_import_days`, `crawler_hits`, `crawler_stealth`.
- Three settings: `CRAWLER_LOG_MAX_BYTES`, `CRAWLER_LOG_MAX_JSON_BYTES`,
  `CRAWLER_LOG_RETENTION_DAYS`.
- Combined logs without a host are attributed to the project's domains; a
  server that hosts several sites in one log needs the `vhost_combined`
  format or a Cloudflare export. Documented in `KNOWN_GAPS.md`.
- The UI tab is specified in `docs/UI_CRAWLERS_BRIEF.md` and built by the UI
  session.
