# Roadmap feasibility: the six features we do not have

**Date**: 2026-09-23
**Status**: Assessment before implementation. Nothing here is built.
**Inputs**: the code as of commit `73e09f9`, `docs/KNOWN_GAPS.md`, the competitive review of 2026-09-22 (`reports/AI prompt tracker competitive gap review.md`) and its six research notes.

Each feature below gets the same treatment: what the market means by it, what we already have to build on, how feasible it is, the cons and risks, what we would still lack after building it, and a recommended first version. Effort is in *cycles*, the repo's unit (one SDLC loop with tests, gate, ADR and build log).

## Corrections to the audit that framed this list

The audit pasted on 2026-09-23 is directionally right about the moat and the gaps, but four of its claims contradict our own research and should not go into sales copy:

- **"No other tool has read-but-rejected."** Peec ships a retrieval-rate versus citation-rate metric that is the same idea. Ours is more granular (exact URLs per sample, a card per page), but it is not unique.
- **"Real API execution" is not a strength.** API answers and logged-in UI answers diverge: Surfer measured 24% brand overlap between the ChatGPT API and the UI, and Petra Labs saw a 32-point swing for one brand on the same day. Every serious competitor is moving to UI-faithful sampling. Our API-only collection is the biggest structural gap in the review, and it is missing from the audit's list.
- **Adaptive sampling is a cost feature, not a moat.** Evertune samples 100 times per prompt. Buyers are asking for more samples with confidence intervals, not fewer.
- **The matrix understates competitors.** Otterly, Peec, Scrunch and Gauge do offer sentiment and, in Peec's case, a relative prompt-volume score. Semrush's prompt index is 213M to 317M depending on page, Ahrefs's 218M to 475M; the audit's 158M is out of date.

None of this changes the build list. It changes how we describe it.

## Shared prerequisite: an LLM client of our own

Features 1, 2 and 6 all need a language model as a judge or a writer. Today the repo has an `ANTHROPIC_API_KEY` setting and nothing that uses it. Coding standard §5 requires every external call to go through a `BaseAPIClient` subclass, and the cost ledger needs a vendor entry and a price card. Build this once, first:

- `src/integrations/anthropic.py`: a `BaseAPIClient` subclass with retries, the circuit breaker and the ledger hook, `RiskClass.read` for judging and `RiskClass.draft` for writing.
- A price card in `pricing.py` and a `COST_ANTHROPIC_*` estimate in settings, so the spend caps cover it.
- Deterministic settings: temperature 0, the model id stored with every judgement, a versioned rubric string stored beside it, so a rubric change is visible in history rather than silently re-labelling it.

Effort: half a cycle. Cost: negligible at Haiku 4.5 prices for judging; see each feature for volume.

## 1. Prompt research and discovery

**What the market means.** "What are people actually asking AI about my category?" Profound and Semrush answer it with licensed clickstream panels and publish prompt volumes. Peec publishes a relative 1 to 5 demand score. Otterly and Ahrefs generate candidates from keyword data.

**What we already have.** Semrush `phrase_questions`, `phrase_all` and `phrase_related` seeds; fixed stage templates; a three-layer rule-based intent filter that documents "an LLM judge for borderline scores" as a gap; Google People Also Ask and related searches captured per prompt; and, uniquely, the engines' own fan-out sub-queries stored on every sample. That last one is real observed demand: it is what the engine chose to search when answering the prompt.

**Feasibility: high. Effort: one cycle after the prerequisite.**

- Candidate sources: Semrush questions and related phrases (already fetched), People Also Ask and related searches (already stored), fan-out sub-queries across the project's existing samples (already stored), and LLM expansion from the seed keywords and the client profile.
- LLM judge replaces the rule gate for borderline candidates: intent, buyer stage, branded or not, and a duplicate check against the tracked set.
- A relative demand score from the Semrush volume we already store, presented as a 1 to 5 band, never as a number of AI prompts.
- New prompts get new frozen ids (ADR 0016), so history is untouched.

**Cons and risks.**

- We cannot produce real prompt volume. No panel is for sale to us, and the review found every vendor's volume figure called "directional" by independent sources. Anything we show must be labelled *modelled* or *observed in fan-out*.
- Auto-generated sets are the category's most common complaint: overlapping and branded prompts padding the count. The judge must dedupe aggressively and keep branded prompts out of headline scores.
- Cost is trivial (a few cents per project per generation) but the judge is non-deterministic across model versions; store the model id.
- English only until locale exists (feature 3).

**Still lacking after v1.** True volumes; persona variants; non-English prompts; the "Prompt Research" style topic browser across industries we do not track.

**Recommended v1.** A "Suggested prompts" panel on the Prompts page: fan-out queries seen across the project's samples ranked by how many samples ran them, plus judged Semrush and PAA candidates, each with its source badge and a one-click add. This uses only data we already pay for, and the fan-out source is something no competitor shows.

## 2. Sentiment and brand-attribute scoring

**What the market means.** Positive, neutral or negative framing of the brand in the answer, and the attributes the engine associates with it. Semrush's is binary (favourable or general); Profound scores claims; AthenaHQ's is criticised as shallow. Seventy percent of marketers do not track it yet, so the bar is low.

**What we already have.** Every brand and competitor sentence is stored as a `MentionSnippet` with the term that matched; every claim is stored with its sentence and source; the full answer text is stored. Nothing scores them. `KNOWN_GAPS.md` says so.

**Feasibility: high. Effort: one cycle after the prerequisite.**

- Judge each stored mention sentence with its surrounding two sentences: polarity on a three-point scale, plus up to three attributes ("expensive", "enterprise-grade", "hard to implement").
- Aggregate per prompt, engine and consolidation window like every other metric, so it gets the same repeat-sampling treatment and volatility figure.
- Volume: roughly 200 tokens per sentence; a 20-prompt, four-engine, three-sample crawl produces at most a few hundred sentences. Well under one cent per crawl at Haiku prices.

**Cons and risks.**

- Sentiment on a listicle answer is mostly neutral; the useful signal is the attribute list and the *negative* outliers (a hallucinated limitation, a security claim). Design the UI around outliers and attributes, not an average score.
- A competitor's sentence and the client's often sit in the same paragraph; the judge must be told which entity it is scoring, and we must store which entity it scored.
- Rubric drift: changing the prompt to the judge changes history. Version the rubric and re-score only forward.
- It is table stakes. It closes a gap; it does not differentiate.

**Still lacking after v1.** Narrative drivers over time (which attributes are gaining); a Perception report across competitors; anything in languages other than English.

**Recommended v1.** Polarity and attributes per mention, a "How engines describe you" strip on Overview with the negative outliers surfaced as evidence quotes, a `sentiment` metric on action cards, and a new card type for a negative claim with its source URL. The last item turns sentiment into an action, which is where we are stronger than the tools that only chart it.

## 3. Hyper-local and multi-locale execution

**What the market means.** Answers for a city or a country and language, not one US-English desktop view. Semrush's 68,500 locations are for its modelled database; live prompt tracking is 40 countries. Profound runs 150 regions. This matters for local-intent prompts and for multi-market brands; it matters little for a generic "best procurement software" question.

**What we already have.** Three global settings (`SERP_GL`, `SERP_HL`, `SERP_LOCATION`) applied to every project. SerpApi accepts a city-level location string, so Google AI Overview can go hyper-local today with a parameter change.

**Engine by engine.**

- Google AI Overview via SerpApi: city and language supported. Cheap.
- ChatGPT Search: the Responses API `web_search` tool accepts a `user_location` (country, city, region, timezone). Supported, one field to add.
- Perplexity Agent API: location controls exist on the older chat endpoint; support on `/v1/responses` needs verifying before we promise it.
- Gemini grounding: no location control in the API. Country follows the billing account. Cannot be done.

**Feasibility: medium. Effort: one cycle for a per-project locale; two to three cycles for several locales inside one project.**

The difference is structural. Every table, the consolidated positions, the Atlas and the Trends charts are keyed by prompt and engine. A second locale inside a project means adding locale to every key, splitting every history and every chart. A per-project locale is a settings change plus three connector fields.

**Cons and risks.**

- Cost scales linearly: each extra locale is a full extra crawl. Ten cities is ten times the spend.
- Two of four engines cannot be localised (Gemini) or are unverified (Perplexity), so a "local" run is really an AI Overview and ChatGPT run.
- Changing a project's locale mid-history mixes two populations in one trend. Locale must be frozen at project creation like the prompt identity, or become part of the identity.
- Non-English prompts need the prompts themselves translated or authored in that language; the locale flag alone does nothing for a French market.

**Still lacking after v1.** Multi-locale in one project; Gemini localisation; mobile versus desktop; logged-in personalisation, which is the larger fidelity problem the review ranked first.

**Recommended v1.** Locale, language and location string as frozen project fields, passed to SerpApi and ChatGPT, shown on every screen as a badge. A second market is a second project. Revisit multi-locale keys only if a client with many markets is actually on the pipeline.

## 4. Inbound AI crawler and bot analytics

**What the market means.** Which pages GPTBot, OAI-SearchBot, ChatGPT-User, PerplexityBot, ClaudeBot, Google-Extended and Meta-ExternalAgent fetch from the client's site, how often, and whether the hits are for training, indexing or live retrieval. Profound ingests CDN logs; Ahrefs offers a free Cloudflare worker; Botify parses server logs.

**What we already have.** Nothing on the ingestion side. What we do have that nobody else has in the same place is the *outbound* side: exact cited URLs, consulted-but-not-cited URLs and fan-out queries per sample. The join between "the bot fetched this page on Tuesday" and "the answer cited this page on Wednesday" is the insight, and we hold half of it already.

**Feasibility: medium to hard. Effort: two cycles for a v1 that aggregates uploaded or pushed logs; more for streaming.**

- Ingestion options, easiest first: manual upload of an access log, an authenticated HTTP endpoint that Cloudflare Logpush or a cron on the client's server posts to, S3 pulls.
- Parsing: user-agent classification plus IP-range verification (OpenAI, Perplexity and Google publish their ranges) so a spoofed user agent does not count.
- Storage: aggregate to one row per day, bot, URL. Never keep raw lines.

**Cons and risks.**

- We do not host the client's site. Every path needs the client's engineering team to do something, which is the real adoption barrier.
- Volume and platform: raw logs are gigabytes; the app is a single SQLite container on one Railway volume. Aggregation on the way in is mandatory, and even then a busy site produces a lot of rows.
- Privacy: access logs contain IP addresses. That is personal data. We need a retention rule and to discard IPs after classification. There is no retention or purge mechanism in the app today.
- Google AI Overview is invisible here: it uses ordinary Googlebot crawling, so the highest-value engine for many clients shows nothing.
- A public ingestion endpoint on a deployment with no per-IP rate limiting is a new attack surface.

**Still lacking after v1.** Real-time data; JavaScript-rendered fetch detection; the human referral side (feature 5); anything for clients on Adobe or Akamai unless they can export logs.

**Recommended v1.** A Cloudflare Logpush endpoint with a shared secret and a size cap, plus a manual log upload for everyone else, aggregated per day, bot and URL, joined on the Battleground drawer and the action cards: "fetched 14 times by OAI-SearchBot this week, cited 0 times" is a card we can generate and nobody else can. Do this after feature 5, which is cheaper and answers the question executives ask first.

## 5. AI referral traffic and conversion attribution (GA4)

**What the market means.** Sessions and conversions that arrive from chatgpt.com, perplexity.ai, gemini.google.com and copilot, per landing page, tied back to visibility. GA4 has had an "AI Assistant" default channel since June 2026. Profound adds a pixel; Similarweb shows competitors' AI traffic from its panel.

**What we already have.** Exact cited URLs per engine and prompt, which is the join key. No analytics integration of any kind.

**Feasibility: medium. Effort: one to two cycles.**

- Access: the client adds a service-account email as a Viewer on their GA4 property. No OAuth consent screen, no Google app verification. This is the path that avoids months of review.
- Pull: the GA4 Data API, daily, sessions and key events by session source and landing page, filtered to the AI assistant sources. Quotas are generous at this volume.
- Join: landing page to cited URL, so a Battleground cell or an action card can say "this page: cited by ChatGPT 3 times this window, 42 sessions from chatgpt.com, 2 conversions".

**Cons and risks.**

- Undercounting is built in: 35 to 70 percent of AI referrals arrive as Direct because the assistants strip referrers. We must say so on every number.
- Conversions depend on the client having configured key events. Many have not.
- Only GA4. Adobe Analytics clients get nothing; a Similarweb-style competitor view is impossible without a panel.
- Credential handling: a service-account key is a secret we store per project. It needs the same care as the owner credential and belongs in the risk register.
- A pixel of our own, the Profound approach, means shipping JavaScript onto client sites. Out of scope; it is a product in itself.

**Still lacking after v1.** Direct-traffic recovery; competitor traffic; non-GA4 stacks; revenue attribution beyond GA4's own key events.

**Recommended v1.** Service-account connection per project, a daily pull into one table, an "AI traffic" column on the prompts table and drawer, and a Trends series. Cheap, and it is the number a client's executive asks for before any of the others.

## 6. One-click content fix generation

**What the market means.** Turn a recommendation into a draft: an FAQ block, a definition paragraph, JSON-LD schema, or a rewrite of a page section, ready to paste or pushed into the CMS. Profound's Aim agent and Sitecore's Scrunch do this; reviewers call much of the output "the same SEO advice".

**What we already have.** Eight deterministic action cards with evidence: the exact fan-out queries the engine ran, the exact winning competitor URLs, the claims attributed to them, and the client page that was read but rejected. That evidence is a far better brief for a writer than any competitor's card. What we lack: the client page is never fetched or verified (`KNOWN_GAPS.md`), and there is no writer.

**Feasibility: medium for drafts; large for CMS push. Effort: two cycles for drafts.**

- Fetch the client page and the winning page through the existing `UrlSafetyPolicy`, pinned transport and robots checks, which already exist for redirect resolution.
- Give the model the card, the fan-out queries, the winning page's structure and the client page, and ask for a bounded artefact: an FAQ block that answers the listed queries, a definition sentence, JSON-LD for the page type.
- Under the repo's risk classes this is `draft`: it executes and the output is flagged for human review. A CMS push is `write` and needs mandatory approval.

**Cons and risks.**

- Quality and hallucination: the model will invent product facts. Every draft must be grounded in the client's own page and profile, with the sources listed, and shown as a draft with a diff, never applied.
- Copying: drafting "in the format of the winning page" must never reproduce the competitor's text. Structure only.
- Brand voice and legal review are outside the tool; agencies will still edit everything.
- Cost: a few cents per draft with a mid-tier model, but drafts for every card on every crawl would add up. Generate on demand only.
- CMS push (WordPress, HubSpot, Webflow) is three separate integrations with stored credentials and a write risk class. Do not start there.
- The reviewer complaint about generic advice applies to us too unless the draft is visibly built from the evidence on the card.

**Still lacking after v1.** CMS publishing; on-page audits of technical readiness (schema validity, crawlability) beyond what we fetch; proof that a draft moved a citation, although the card's outcome scoring after the next consolidation gives us a way to show that which competitors lack.

**Recommended v1.** A "Draft the fix" button on cards of type convert_mention, landing_page and read_but_rejected, producing a Markdown block and JSON-LD in a drawer with its sources, copy button, and a note back on the card. No push. Tie the card's outcome score to it so a draft that was applied can be shown to have improved, unchanged or regressed.

## Recommended order

| Order | Feature | Why here | Effort |
|---|---|---|---|
| 0 | LLM client, price card, versioned rubric | Three features depend on it | 0.5 cycle |
| 1 | Sentiment and attributes | Only stored data, closes the most-cited gap, feeds a new card | 1 cycle |
| 2 | Prompt discovery | Only stored data plus a judge, and the fan-out source is a differentiator | 1 cycle |
| 3 | Per-project locale | Small change, but freeze it before more history accumulates | 1 cycle |
| 4 | GA4 referral join | The executive's first question, cheap, service-account access | 1 to 2 cycles |
| 5 | Content drafts | Needs the LLM client and page fetching; high perceived value | 2 cycles |
| 6 | Bot log analytics | Needs client cooperation, storage and privacy work | 2+ cycles |

Two things outrank all six and are absent from the audit's list: **UI-faithful sampling** (the review's top gap, and a disclosed second collection surface rather than a replacement) and **confidence intervals on every rate**, which is a half-cycle change to the consolidation maths and the thing credible practitioners now ask for first. Both should be scheduled around the list above, not after it.

## Decisions needed before cycle 1

- Which LLM and at what cost ceiling for judging and drafting. The Anthropic key exists; a per-day cap for it is a settings addition.
- Whether locale is frozen at project creation (recommended) or editable with a history break.
- Whether sentiment is scored for competitors too (recommended: yes, it is the same call and makes share of voice comparable).
- Whether any of this changes the single-tenant decision. Features 4 and 5 store client credentials and client traffic data; if agency use is the goal, tenancy moves ahead of them.
