# ADR 0023 — A project's market is one locale, frozen after the first crawl

**Date**: 2026-09-23
**Status**: Accepted (cycle 0019; the operator asked for "Option B: Feature 3 — Multi-Locale & Geo-Targeting", and `docs/ROADMAP_FEASIBILITY.md` §3 recommended this shape)
**Builds on**: ADR 0012 (prompt identity is frozen), ADR 0010 (consolidation window)

## Context

Answer engines localise. The same prompt asked from Mumbai and from London
returns different sources, different competitors and a different AI Overview,
so a tracker that reports one number has to say which market that number is
for. Until this cycle the tracker had three global settings — `SERP_GL`,
`SERP_HL`, `SERP_LOCATION` — applied to every project and to Google only. The
other three engines ran from wherever the vendor put us.

Two facts constrain the design.

1. **Vendor support is uneven and one engine has none.** Verified against the
   vendors' own documentation on 2026-09-23, not inferred:

   | Engine | Parameter | Granularity |
   |---|---|---|
   | Google AI Overview (SerpApi) | `gl`, `hl`, `location` | Country, language, canonical city string |
   | ChatGPT Search (OpenAI Responses) | `user_location` on the `web_search` tool: `{type: "approximate", country, city, region, timezone}` | Country, free-text city and region, IANA timezone |
   | Perplexity (Agent API `/v1/responses`) | `user_location` on the `web_search` tool: `country`, `region`, `city`, and optional coordinates | Country, region, city — and Perplexity's docs call it a hint, not a guarantee |
   | Gemini (Developer API) | none — the `google_search` tool takes an empty object | Follows the billing account's country |

2. **Every stored row is keyed by `(prompt_id, run_id, engine)`.** Consolidated
   positions, the Atlas, the Trends charts and every action card read that key.
   Adding a locale to the key splits every table, every chart and every history.

## Decision

### A. One locale per project, frozen once the project has crawled

`Project.locale` is a `Locale` value object (`src/core/locale.py`): `country`
(ISO-2, required), `language` (default `en`), and optional `city`, `region`,
`serp_location` and `timezone`. `null` means "use the server defaults", which
is what every project created before this cycle keeps.

The control plane refuses a locale change on a project that has crawls, with
the reason stated in the message: every stored snapshot was captured from the
old market, so changing it mid-history would mix two populations into one
trend line. A second market is a second project. This is the same rule the
tracker already applies to prompt identity (ADR 0012), for the same reason.

### B. The value object translates itself; the connectors do not guess

`Locale.openai_user_location()` and `Locale.perplexity_user_location()` emit
each vendor's own shape, and `serp_location` is stored verbatim because
SerpApi rejects any location outside its own database — a city name is never
derived into a canonical string. The connectors take `locale: Locale | None`
and fall back to `Settings.default_locale()`, so nothing changes for a project
without one. `gemini_search.py` is deliberately untouched.

`Engine.honours_locale` is the single place that records which engines accept
a location, and both the API and the UI read it rather than restating a list.

### C. The UI states the market and the exception

A `LocaleBadge` sits beside the project header, next to the numbers rather
than in settings, and its tooltip says plainly that Gemini has no location
field so its samples follow the billing account whatever the badge says. The
project form has a "Market" section with the same sentence and a note that the
locale is frozen after the first crawl.

## Edge cases and breaking points, with the handling

- *A project created before this cycle.* `locale` is `null`; crawls use
  `SERP_GL` / `SERP_HL` / `SERP_LOCATION` exactly as before. Setting a locale
  on such a project is still a change, so the freeze applies to it too.
- *An invalid SerpApi location string.* SerpApi errors; that is the connector's
  existing error path. The string is the operator's to get right, which is why
  the field is free text beside the city rather than derived from it.
- *Perplexity ignoring the hint.* Its own documentation says the filter steers
  results and does not guarantee them. The badge claims a requested market, not
  a verified one; `docs/KNOWN_GAPS.md` records it.
- *Gemini.* Localising three of four engines and saying so is honest; silently
  labelling a Gemini sample "Mumbai" is not. Hence `honours_locale` and the
  tooltip.
- *Language versus prompts.* `hl=fr` does not translate the prompts. A French
  market needs prompts authored in French; the locale flag alone does nothing.
- *Device.* `SERP_DEVICE` stays a tracker-wide setting. Mobile versus desktop
  is a second axis and would double the crawl, so it is out of this cycle.
- *Cost.* Each extra market is a full extra crawl of every prompt. Ten cities
  is ten times the spend, which is the strongest argument for one market per
  project rather than a fan-out hidden inside one.

## Alternatives rejected

- **Locale in the row key (several markets in one project).** The honest
  multi-locale design, and two to three cycles of work: every table, chart and
  consolidation would need the extra dimension, and every screen a market
  selector. Revisit when a client with several markets is actually booked.
- **Editable locale with a marked history break.** Cheap to build, but it
  leaves a trend line whose early half answers a different question. Rejected
  for the same reason prompt identity is frozen.
- **Global setting only (what we had).** Cannot serve two clients in two
  markets from one deployment.

## Consequences

- A per-project market is visible everywhere the numbers are, and three of the
  four engines honour it.
- "Ten cities" means ten projects and ten times the spend, by design.
- Gemini remains un-localisable until Google adds a field; nothing in the
  product pretends otherwise.
