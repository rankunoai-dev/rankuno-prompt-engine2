# UI cycle 0006 — Exact URLs wherever an insight is stated

**Date**: 2026-09-19
**Operator request**: "citation of exact URLs is necessary … capture the exact
URL of the client and competitor, can be main winning points, and in the UI
wherever the insight is given stating that exact URL would be nailing the
situation" (chat).

## Scope

Surface the exact client page and the exact competitor page beside every
verdict, instead of only the domain: health tiles, action cards, the matrix
hover, the Inspection Drawer and the prompts table.

## Research findings

- Capture already stores everything needed, so no connector change was made:
  each `AnswerSample` keeps every citation link (URL, title, position), the
  client's own cited URLs, consulted-but-not-cited URLs, the sentence each
  URL supports and the source snippet with its date; `CitationSnapshot`
  aggregates the same links per prompt × platform; the insight view exposes
  `winning_pages` and `client_pages` as URL inventories.
- What was missing was only presentation: the UI rolled all of it up to
  domains, so an analyst saw "coupa.com" rather than the page that won.
- `PageInventory.citations` counts across every platform. Rendering it beside
  a single-platform verdict would misread, so the tiles show identity without
  a count, and action cards label the number "citations across platforms".
  The drawer counts crawls for that prompt on that platform, which is exact.

## What was built

- `src/lib/pages.ts` (pure, tested): `roleOf` (client / competitor / other
  from the project's own lists, subdomains included), `pageHits` (citation
  links folded into one row per URL with hit count, best position and the
  platforms), `pagesForAction`, `pagesForEngine`, `shortUrl`.
- `src/components/ExactPages.tsx`: the "Your page / Their page" pair, with a
  compact variant for tiles; the full URL is the link target and the title
  sits in the tooltip.
- Health tiles (Overview), action cards (Overview and Actions), matrix hover
  (`CellView.clientUrl` / `competitorUrl`), Inspection Drawer ("Exact pages
  over every crawl", placed directly under the consolidated position), and a
  "Cited page" column in the prompts table.
- Tests: `pages.test.ts` (4) and a Battleground test asserting the drawer
  names the exact client URL, not just the domain.

## Bugs found and fixed

- Compact tiles clipped both the URL and the count; the pair now wraps.

## Corrections

- None.

## Explicitly not done

- No change to capture: Gemini redirect URLs still resolve only when
  `--resolve-redirects` is on, and unresolved ones stay flagged.
- Client pages are not fetched or verified; the UI reports the URL the
  platform cited.

## Gate output (verbatim)

```
=== typecheck ===  OK
=== lint ===       OK
=== test ===       Test Files 16 passed (16) · Tests 61 passed (61)
=== build ===      ✓ built in 15.29s
```

No file under `src/`, `tests/` or `scripts/` changed; the Python gate was not
rerun.
