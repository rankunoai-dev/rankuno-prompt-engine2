/**
 * Deterministic stand-in for `control_plane/insights.py`, applied to the
 * stored results so mocked screens show what the real endpoint shows for the
 * same data. Rules follow `docs/UI_BUILD_BRIEF.md` §3 where the snapshots carry
 * enough to evaluate them; the rest is left empty rather than invented.
 */
import type {
    ActionCard,
    AnswerSample,
    CitationSnapshot,
    ClaimEntry,
    Engine,
    EngineHealth,
    FreshnessProfile,
    HealthVerdict,
    InsightChange,
    Insights,
    MentionContext,
    PageInventory,
    PlacementProfile,
    PositionsView,
    Project,
    ProjectRunRecord,
    PromptResult,
    RejectedPage,
    SentimentCoverage,
    SentimentProfile,
    TrustShare,
} from "@/api/endpoints";

export type DomainClass =
    "vendor" | "aggregator" | "forum" | "publisher" | "docs" | "marketplace" | "other";

const AGGREGATORS = [
    "g2.com",
    "capterra.com",
    "gartner.com",
    "softwareadvice.com",
    "trustradius.com",
];
const FORUMS = ["reddit.com", "quora.com", "stackexchange.com"];
const MARKETPLACES = ["amazon.com", "microsoft.com", "appexchange.salesforce.com"];
const PUBLISHERS = [
    "forbes.com",
    "techtarget.com",
    "linkedin.com",
    "medium.com",
    "procurementmag.com",
];

export function classifyDomain(domain: string): DomainClass {
    const d = domain.toLowerCase();
    if (AGGREGATORS.some((x) => d.endsWith(x))) return "aggregator";
    if (FORUMS.some((x) => d.endsWith(x))) return "forum";
    if (MARKETPLACES.some((x) => d.endsWith(x))) return "marketplace";
    if (PUBLISHERS.some((x) => d.endsWith(x))) return "publisher";
    if (d.includes("docs.") || d.endsWith("wikipedia.org")) return "docs";
    return "vendor";
}

const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);

const NEGATIVE_WORDS =
    /\b(expensive|costly|lack|lacks|limited|complex|weak|slow|steep|difficult|not ideal|pricey)\b/i;
const POSITIVE_WORDS =
    /\b(recommend|recommended|best|leading|top|strong|robust|excellent|ideal|praised)\b/i;

/** A keyword stand-in for the judge (ADR 0021), so mocked screens show scored mentions. */
export function mockPolarity(sentence: string): "positive" | "neutral" | "negative" {
    if (NEGATIVE_WORDS.test(sentence)) return "negative";
    if (POSITIVE_WORDS.test(sentence)) return "positive";
    return "neutral";
}

function mockAttributes(sentence: string): string[] {
    const out: string[] = [];
    if (/\b(expensive|costly|pricey)\b/i.test(sentence)) out.push("expensive");
    if (/\benterprise\b/i.test(sentence)) out.push("enterprise-grade");
    if (/\b(risk)\b/i.test(sentence)) out.push("supplier risk tools");
    if (/\b(recommend|recommended)\b/i.test(sentence)) out.push("recommended");
    return out.slice(0, 3);
}

/** 95% Wilson band, the same maths as src/core/stats.py, so fixtures look like the server. */
export function wilson(successes: number, trials: number): [number, number] | null {
    if (trials <= 0) return null;
    const z = 1.96;
    const p = successes / trials;
    const d = 1 + (z * z) / trials;
    const centre = (p + (z * z) / (2 * trials)) / d;
    const half = (z / d) * Math.sqrt((p * (1 - p)) / trials + (z * z) / (4 * trials * trials));
    return [Math.max(0, +(centre - half).toFixed(4)), Math.min(1, +(centre + half).toFixed(4))];
}

function verdictFor(
    cited: number,
    mentioned: number,
    bestRank: number | null,
    losingTo: string | null,
): HealthVerdict {
    if (cited >= 0.5 && bestRank !== null && bestRank <= 3) return "winning";
    if (cited > 0 || mentioned > 0) return "present";
    if (losingTo) return "losing";
    return "invisible";
}

function snapshotsFor(results: PromptResult[], engine: Engine): CitationSnapshot[] {
    return results
        .map((r) => r.snapshots[engine])
        .filter((s): s is CitationSnapshot => !!s && s.samples - s.failed_samples > 0);
}

type CardInput = Omit<ActionCard, "status" | "outcome" | "owner" | "note" | "metric"> &
    Partial<Pick<ActionCard, "metric">>;

function card(partial: CardInput): ActionCard {
    return {
        metric: "cited_rate",
        status: "open",
        outcome: "pending",
        owner: null,
        note: null,
        ...partial,
    };
}

const domainShares = (domains: string[], step: number, limit: number) =>
    domains.slice(0, limit).map((domain, i) => ({ domain, share: round(1 - i * step) }));

/**
 * Crawler cards come from the crawler-log module, not from the samples, so the
 * mock adds them here. They are the only cards with no prompt at all: a fetch
 * belongs to a page (ADR 0022), which is what makes them worth a fixture.
 */
function crawlerCards(project: Project): ActionCard[] {
    const page = `${project.client.domains[0] ?? "example.com"}/blog/ai-in-procurement-guide`;
    return [
        card({
            id: "crawl:fetched_not_cited:1",
            type: "fetched_not_cited",
            title: `OAI-SearchBot fetched ${page} 222x in 60 days`,
            prescription:
                "The crawler reads this page and ChatGPT Search cites something else. Answer the query in the first 100 words and make the claim quotable.",
            impact_score: 0.62,
            engine: "CHATGPT_SEARCH",
            subtopic: "Crawl",
            prompt_ids: [],
            evidence: {
                quotes: [],
                domains: [],
                urls: [`https://${page}`],
                queries: ["ai in procurement", "procurement automation guide"],
                numbers: {
                    fetches: 222,
                    verified_fetches: 222,
                    blocked: 0,
                    consulted: 65,
                    days: 60,
                },
            },
        }),
    ];
}

export function buildInsights(
    project: Project,
    results: PromptResult[],
    positions: PositionsView | null,
    crawls: ProjectRunRecord[],
): Insights {
    const consolidation = positions?.consolidation ?? null;
    const crawlCount = consolidation
        ? consolidation.run_ids.length || 1
        : Math.min(1, crawls.length || 1);
    const health: EngineHealth[] = project.engines.map((engine) => {
        const snaps = snapshotsFor(results, engine);
        const cited = mean(snaps.map((s) => s.client_citation_rate));
        const mentioned = mean(snaps.map((s) => s.mention_rate));
        const ranks = snaps.map((s) => s.client_best_rank).filter((x): x is number => x !== null);
        const competitorWins = snaps
            .filter((s) => !s.client_cited)
            .flatMap((s) => Object.keys(s.competitor_citations));
        const losingTo = competitorWins.length ? mostCommon(competitorWins) : null;
        const okAll = snaps.reduce((a, s) => a + s.samples - s.failed_samples, 0);
        const split = snaps.filter((s) => s.client_citation_rate > 0 && s.client_citation_rate < 1);
        return {
            engine,
            verdict: verdictFor(
                cited,
                mentioned,
                ranks.length ? Math.min(...ranks) : null,
                losingTo,
            ),
            losing_to: losingTo,
            cited_rate: round(cited),
            mention_rate: round(mentioned),
            cited_rate_low: wilson(Math.round(cited * okAll), okAll)?.[0] ?? null,
            cited_rate_high: wilson(Math.round(cited * okAll), okAll)?.[1] ?? null,
            mention_rate_low: wilson(Math.round(mentioned * okAll), okAll)?.[0] ?? null,
            mention_rate_high: wilson(Math.round(mentioned * okAll), okAll)?.[1] ?? null,
            best_rank: ranks.length ? Math.min(...ranks) : null,
            delta_cited_rate: consolidation ? 0 : null,
            samples: snaps.reduce((a, s) => a + s.samples, 0),
            crawls: crawlCount,
            volatility: snaps.length ? round(split.length / snaps.length) : 0,
            prompts: snaps.length,
        };
    });

    const changes: InsightChange[] = [];
    const actions: ActionCard[] = [];
    const claims: ClaimEntry[] = [];
    const readRejected: RejectedPage[] = [];
    const pages = new Map<string, PageInventory>();
    const clientDomains = project.client.domains.map(normalise);
    const competitorDomains = project.client.competitor_domains.map(normalise);

    for (const r of results) {
        for (const [engineKey, sn] of Object.entries(r.snapshots)) {
            const engine = engineKey as Engine;
            if (!sn) continue;
            const subtopic = r.prompt.subtopic ?? r.prompt.keyword ?? "General";
            if (sn.mention_rate >= 0.4 && sn.client_citation_rate <= 0.1) {
                actions.push(
                    card({
                        id: `convert:${r.prompt.prompt_id}:${engine}`,
                        type: "convert_mention",
                        title: `${label(engine)} names ${project.client.brand_name} but links elsewhere`,
                        prescription:
                            "Publish or fix the page that should own this claim: clear H1, a definition sentence in the first 100 words, FAQ schema, and get listed on the domains this engine links for the claim.",
                        impact_score: round(0.6 + sn.mention_rate * 0.4),
                        engine,
                        subtopic,
                        prompt_ids: [r.prompt.prompt_id],
                        evidence: {
                            quotes: sn.mention_snippets.map((m) => ({
                                text: m.snippet,
                                engine,
                                run_id: null,
                                captured_at: sn.captured_at,
                                entity: m.entity,
                                url: null,
                            })),
                            domains: domainShares(sn.cited_domains, 0.15, 5),
                            urls: sn.citation_links.slice(0, 5).map((c) => c.url),
                            queries: [],
                            numbers: {
                                mention_rate: sn.mention_rate,
                                citation_rate: sn.client_citation_rate,
                                samples: sn.samples,
                            },
                        },
                    }),
                );
            }
            for (const c of sn.citation_links) {
                const dom = normalise(c.domain);
                const isClient = clientDomains.some((d) => dom.endsWith(d));
                const entry = pages.get(c.url) ?? {
                    url: c.url,
                    domain: c.domain,
                    title: c.title ?? null,
                    citations: 0,
                    engines: [],
                    prompts: 0,
                    snippet: null,
                    date: null,
                    is_client: isClient,
                };
                entry.citations += 1;
                entry.prompts += 1;
                if (!entry.engines.includes(engine)) entry.engines.push(engine);
                pages.set(c.url, entry);
                if (claims.length < 60 && sn.answer_excerpt) {
                    claims.push({
                        sentence: firstSentence(sn.answer_excerpt),
                        url: c.url,
                        domain: c.domain,
                        engine,
                        prompt_id: r.prompt.prompt_id,
                        is_client: isClient,
                        is_competitor: competitorDomains.some((d) => dom.endsWith(d)),
                    });
                }
            }
            const rejected = sn.consulted_urls.filter((u) =>
                clientDomains.some((d) => hostOf(u).endsWith(d)),
            );
            for (const url of rejected) {
                readRejected.push({ url, engine, samples: 1, queries: [] });
                actions.push(
                    card({
                        id: `rejected:${r.prompt.prompt_id}:${engine}:${hash(url)}`,
                        type: "read_but_rejected",
                        title: `${label(engine)} read ${hostOf(url)} and did not cite it`,
                        prescription:
                            "On-page relevance fix on that URL: answer the query in the first 100 words, add the missing entity or spec, refresh the date.",
                        impact_score: 0.55,
                        engine,
                        subtopic,
                        prompt_ids: [r.prompt.prompt_id],
                        evidence: {
                            quotes: [],
                            domains: domainShares(sn.cited_domains, 0.2, 3),
                            urls: [url],
                            queries: [],
                            numbers: { consulted: sn.consulted_urls.length },
                        },
                    }),
                );
            }
            if (
                engine === "GOOGLE_AI_OVERVIEW" &&
                sn.web_trigger_rate > 0 &&
                !sn.client_cited &&
                r.organic_prompt?.client_position &&
                r.organic_prompt.client_position <= 5
            ) {
                actions.push(
                    card({
                        id: `aio:${r.prompt.prompt_id}`,
                        type: "aio_gap",
                        title: `Ranked #${r.organic_prompt.client_position} organically, absent from the AI Overview`,
                        prescription:
                            "Add a concise answer block or FAQ matching the AI Overview text blocks; cover the People-also-ask questions.",
                        impact_score: 0.7,
                        engine,
                        subtopic,
                        prompt_ids: [r.prompt.prompt_id],
                        evidence: {
                            quotes: sn.answer_excerpt
                                ? [
                                      {
                                          text: sn.answer_excerpt,
                                          engine,
                                          run_id: null,
                                          captured_at: sn.captured_at,
                                          entity: null,
                                          url: null,
                                      },
                                  ]
                                : [],
                            domains: domainShares(sn.cited_domains, 0.15, 5),
                            urls: sn.citation_links.slice(0, 5).map((c) => c.url),
                            queries: [],
                            numbers: { organic_position: r.organic_prompt.client_position },
                        },
                    }),
                );
            }
        }
    }

    const competitorPages = new Map<
        string,
        { engine: Engine; prompts: Set<string>; urls: Set<string> }
    >();
    for (const r of results) {
        for (const [engineKey, sn] of Object.entries(r.snapshots)) {
            if (!sn || sn.client_cited) continue;
            for (const c of sn.citation_links) {
                const dom = normalise(c.domain);
                if (!competitorDomains.some((d) => dom.endsWith(d))) continue;
                const key = `${dom}|${engineKey}`;
                const entry = competitorPages.get(key) ?? {
                    engine: engineKey as Engine,
                    prompts: new Set<string>(),
                    urls: new Set<string>(),
                };
                entry.prompts.add(r.prompt.prompt_id);
                entry.urls.add(c.url);
                competitorPages.set(key, entry);
            }
        }
    }
    for (const [key, entry] of competitorPages) {
        if (entry.prompts.size < 3) continue;
        const [dom] = key.split("|");
        actions.push(
            card({
                id: `own:${key}`,
                type: "own_claim",
                title: `${dom} is cited on ${entry.prompts.size} prompts where ${project.client.brand_name} is not`,
                prescription:
                    "Create or upgrade a page matching the competitor page format for that claim; target the engine fan-out queries.",
                impact_score: round(0.5 + Math.min(entry.prompts.size, 10) * 0.04),
                engine: entry.engine,
                subtopic: "Competitor pages",
                prompt_ids: [...entry.prompts],
                evidence: {
                    quotes: [],
                    domains: [{ domain: dom ?? "", share: 1 }],
                    urls: [...entry.urls].slice(0, 5),
                    queries: [],
                    numbers: { prompts: entry.prompts.size },
                },
            }),
        );
    }

    for (const engine of project.engines) {
        const snaps = snapshotsFor(results, engine);
        const links = snaps.flatMap((s) => s.citation_links);
        if (links.length < 5) continue;
        const earned = links.filter((c) =>
            ["aggregator", "forum", "marketplace"].includes(classifyDomain(c.domain)),
        );
        const share = earned.length / links.length;
        if (share >= 0.3) {
            const byDomain = new Map<string, number>();
            earned.forEach((c) => byDomain.set(c.domain, (byDomain.get(c.domain) ?? 0) + 1));
            actions.push(
                card({
                    id: `earned:${engine}`,
                    type: "earned_placement",
                    title: `${Math.round(share * 100)}% of ${label(engine)} citations go to review sites and forums`,
                    prescription: "Secure or update the brand presence on exactly these domains.",
                    impact_score: round(0.4 + share * 0.5),
                    engine,
                    subtopic: "Third-party presence",
                    prompt_ids: [],
                    evidence: {
                        quotes: [],
                        domains: [...byDomain.entries()]
                            .sort((a, b) => b[1] - a[1])
                            .map(([domain, n]) => ({ domain, share: round(n / earned.length) })),
                        urls: [...new Set(earned.map((c) => c.url))].slice(0, 8),
                        queries: [],
                        numbers: { citations: links.length, earned: earned.length },
                    },
                }),
            );
        }
    }

    actions.sort((a, b) => b.impact_score - a.impact_score);

    const trust: TrustShare[] = [];
    for (const engine of project.engines) {
        const links = snapshotsFor(results, engine).flatMap((s) => s.citation_links);
        if (!links.length) continue;
        const counts = new Map<DomainClass, number>();
        links.forEach((c) => {
            const k = classifyDomain(c.domain);
            counts.set(k, (counts.get(k) ?? 0) + 1);
        });
        for (const [domain_class, n] of counts) {
            trust.push({ engine, domain_class, share: round(n / links.length), citations: n });
        }
    }

    const placement: PlacementProfile[] = project.engines.map((engine) => {
        const withMention = snapshotsFor(results, engine).filter((s) => s.mention_detected);
        return {
            engine,
            samples_with_mention: withMention.length,
            first_third: withMention.length,
            in_list: withMention.filter((s) =>
                s.mention_snippets.some((m) => /^\s*[-*\d]/.test(m.snippet)),
            ).length,
            in_table: 0,
            recommendation_sentence: withMention.filter((s) =>
                s.mention_snippets.some((m) => /recommend|best|leading|top/i.test(m.snippet)),
            ).length,
        };
    });
    const freshness: FreshnessProfile[] = project.engines.map((engine) => ({
        engine,
        dated_sources: 0,
        median_age_days: null,
        client_median_age_days: null,
        competitor_median_age_days: null,
    }));

    const inventory = [...pages.values()].sort((a, b) => b.citations - a.citations);

    // Sentiment and mention context from the snapshots' mention sentences.
    const sentiment: SentimentProfile[] = [];
    const mentionContext: MentionContext[] = [];
    let judgedTotal = 0;
    for (const engine of project.engines) {
        const byEntity = new Map<
            string,
            { sentence: string; polarity: string; captured: string; prompt: string }[]
        >();
        for (const r of results) {
            const sn = r.snapshots[engine];
            if (!sn) continue;
            for (const m of sn.mention_snippets) {
                const polarity = mockPolarity(m.snippet);
                const list = byEntity.get(m.entity) ?? [];
                list.push({
                    sentence: m.snippet,
                    polarity,
                    captured: sn.captured_at,
                    prompt: r.prompt.prompt_id,
                });
                byEntity.set(m.entity, list);
                mentionContext.push({
                    prompt_id: r.prompt.prompt_id,
                    engine,
                    entity: m.entity,
                    sentence: m.snippet,
                    container: /^\s*(?:[-*•]|\d+[.)])\s/.test(m.snippet) ? "list" : "prose",
                    first_third: true,
                    sourced_via_domain: sn.cited_domains[0] ?? null,
                    sourced_via_class: sn.cited_domains[0]
                        ? classifyDomain(sn.cited_domains[0])
                        : null,
                    listed_with: 0,
                    polarity,
                    captured_at: sn.captured_at,
                });
            }
        }
        if (!byEntity.has("client")) byEntity.set("client", []);
        for (const [entity, rows] of byEntity) {
            const counts = { positive: 0, neutral: 0, negative: 0 };
            const attrs = new Map<string, { count: number; example: string }>();
            for (const row of rows) {
                counts[row.polarity as keyof typeof counts] += 1;
                for (const a of mockAttributes(row.sentence)) {
                    const cur = attrs.get(a) ?? { count: 0, example: row.sentence };
                    cur.count += 1;
                    attrs.set(a, cur);
                }
            }
            const scored = rows.length;
            judgedTotal += scored;
            const band = wilson(counts.negative, scored);
            sentiment.push({
                engine,
                entity,
                judged: scored,
                positive: counts.positive,
                neutral: counts.neutral,
                negative: counts.negative,
                not_about_brand: 0,
                unscored: 0,
                negative_share: scored ? round(counts.negative / scored) : 0,
                negative_share_low: band?.[0] ?? null,
                negative_share_high: band?.[1] ?? null,
                attributes: [...attrs.entries()]
                    .sort((a, b) => b[1].count - a[1].count)
                    .slice(0, 5)
                    .map(([attribute, v]) => ({ attribute, count: v.count, example: v.example })),
                worst: rows
                    .filter((r) => r.polarity === "negative")
                    .slice(0, 3)
                    .map((r) => ({
                        text: r.sentence,
                        engine,
                        run_id: null,
                        captured_at: r.captured,
                        entity,
                        url: null,
                    })),
                model: scored ? "claude-haiku-4-5" : null,
                rubric_version: scored ? "2026-09-23.1" : null,
            });
        }
    }
    const sentimentCoverage: SentimentCoverage = {
        configured: true,
        judged: judgedTotal,
        unscored: 0,
        model: "claude-haiku-4-5",
        rubric_version: "2026-09-23.1",
    };
    const totalSamples = results.reduce(
        (a, r) => a + Object.values(r.snapshots).reduce((b, s) => b + (s?.samples ?? 0), 0),
        0,
    );
    return {
        generated_at: new Date().toISOString(),
        basis: {
            consolidation_id: consolidation?.id ?? null,
            computed_from: consolidation
                ? "consolidation"
                : results.length
                  ? "latest_crawl"
                  : "none",
            crawls: crawlCount,
            samples: totalSamples,
            low_confidence: !consolidation || crawlCount < project.consolidation_runs,
        },
        health,
        changes,
        actions: [...actions, ...crawlerCards(project)],
        fanout: [],
        claims,
        trust_profile: trust,
        read_but_rejected: readRejected,
        winning_pages: inventory.filter((p) => !p.is_client).slice(0, 20),
        client_pages: inventory.filter((p) => p.is_client).slice(0, 20),
        placement,
        freshness,
        sentiment,
        sentiment_coverage: sentimentCoverage,
        mention_context: mentionContext,
    };
}

/** Raw samples reconstructed from a snapshot; the real route returns the stored answers. */
export function buildSamples(
    project: Project,
    results: PromptResult[],
    promptId: string,
    engine: Engine,
): AnswerSample[] {
    const r = results.find((x) => x.prompt.prompt_id === promptId);
    const sn = r?.snapshots[engine];
    if (!r || !sn) return [];
    void project;
    const ok = Math.max(1, sn.samples - sn.failed_samples);
    const sentence = firstSentence(sn.answer_excerpt);
    return Array.from({ length: ok }, (_, i) => ({
        prompt_id: promptId,
        run_id: "",
        engine,
        model: sn.model,
        captured_at: sn.captured_at,
        response_id: sn.response_ids[i] ?? null,
        web_triggered: sn.web_trigger_rate > 0,
        client_cited: i < sn.client_cited_samples,
        client_rank: i < sn.client_cited_samples ? sn.client_best_rank : null,
        cited_domains: sn.cited_domains,
        citation_links: sn.citation_links,
        consulted_urls: sn.consulted_urls,
        mention_detected: sn.mention_detected,
        mentions: sn.mention_snippets,
        answer_excerpt: sn.answer_excerpt,
        answer_text: sn.answer_excerpt,
        search_queries: [],
        citation_claims: sn.citation_links.slice(0, 3).map((c) => ({
            url: c.url,
            sentence,
            start: 0,
            end: Math.min(sn.answer_excerpt.length, sentence.length),
        })),
        source_snippets: [],
    }));
}

function label(engine: Engine): string {
    return (
        {
            GOOGLE_AI_OVERVIEW: "Google AI Overview",
            CHATGPT_SEARCH: "ChatGPT Search",
            PERPLEXITY: "Perplexity",
            GEMINI: "Gemini",
        }[engine] ?? engine
    );
}
function normalise(d: string): string {
    return (
        d
            .toLowerCase()
            .replace(/^https?:\/\//, "")
            .replace(/^www\./, "")
            .split("/")[0] ?? d
    );
}
function hostOf(url: string): string {
    try {
        return new URL(url).hostname.replace(/^www\./, "");
    } catch {
        return url;
    }
}
function firstSentence(text: string): string {
    const m = text.replace(/\s+/g, " ").match(/^.*?[.!?](\s|$)/);
    return (m ? m[0] : text).trim().slice(0, 240);
}
function mostCommon(xs: string[]): string {
    const c = new Map<string, number>();
    xs.forEach((x) => c.set(x, (c.get(x) ?? 0) + 1));
    return [...c.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? xs[0] ?? "";
}
function round(x: number): number {
    return Math.round(x * 1000) / 1000;
}
function hash(s: string): string {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return (h >>> 0).toString(16);
}
