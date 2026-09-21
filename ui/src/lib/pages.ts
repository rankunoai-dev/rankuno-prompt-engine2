/**
 * Exact pages behind a verdict: which client URL earned the citation and
 * which competitor URL took it. Built from what the engine already stores
 * (every citation link with its URL, title and position) so nothing here is
 * inferred beyond the stored links.
 */
import type {
    ActionCard,
    AtlasSnapshot,
    CitationSnapshot,
    Engine,
    Insights,
    PageInventory,
    Project,
} from "@/api/endpoints";

export type PageRole = "client" | "competitor" | "other";

export interface PageHit {
    url: string;
    title: string | null;
    domain: string;
    role: PageRole;
    /** Snapshots (crawl × platform) in which the page was cited. */
    hits: number;
    bestPosition: number;
    engines: Engine[];
}

export interface PageSplit {
    client: PageHit[];
    competitor: PageHit[];
    other: PageHit[];
}

const normalise = (d: string) => d.toLowerCase().replace(/^www\./, "");

export function roleOf(domain: string, project: Project): PageRole {
    const n = normalise(domain);
    const matches = (list: string[]) =>
        list.map(normalise).some((d) => n === d || n.endsWith(`.${d}`));
    if (matches(project.client.domains)) return "client";
    if (matches(project.client.competitor_domains)) return "competitor";
    return "other";
}

/** Aggregate citation links across snapshots into one row per URL, most cited first. */
export function pageHits(
    snapshots: (AtlasSnapshot | CitationSnapshot)[],
    project: Project,
): PageSplit {
    const byUrl = new Map<string, PageHit>();
    for (const sn of snapshots) {
        const seen = new Set<string>();
        for (const c of sn.citation_links) {
            if (seen.has(c.url)) continue;
            seen.add(c.url);
            const hit = byUrl.get(c.url) ?? {
                url: c.url,
                title: c.title ?? null,
                domain: c.domain,
                role: roleOf(c.domain, project),
                hits: 0,
                bestPosition: c.position,
                engines: [],
            };
            hit.hits += 1;
            hit.bestPosition = Math.min(hit.bestPosition, c.position);
            if (!hit.engines.includes(sn.engine)) hit.engines.push(sn.engine);
            if (!hit.title && c.title) hit.title = c.title;
            byUrl.set(c.url, hit);
        }
    }
    const all = [...byUrl.values()].sort(
        (a, b) => b.hits - a.hits || a.bestPosition - b.bestPosition,
    );
    return {
        client: all.filter((h) => h.role === "client"),
        competitor: all.filter((h) => h.role === "competitor"),
        other: all.filter((h) => h.role === "other"),
    };
}

/** The client's and competitors' pages relevant to one action card, from the insight inventory. */
export function pagesForAction(
    insights: Insights | undefined,
    action: ActionCard,
    limit = 3,
): { client: PageInventory[]; competitor: PageInventory[] } {
    if (!insights) return { client: [], competitor: [] };
    const onEngine = (p: PageInventory) => !action.engine || p.engines.includes(action.engine);
    const byRelevance = (a: PageInventory, b: PageInventory) => b.citations - a.citations;
    return {
        client: insights.client_pages.filter(onEngine).sort(byRelevance).slice(0, limit),
        competitor: insights.winning_pages
            .filter((p) => onEngine(p) && !p.is_client)
            .sort(byRelevance)
            .slice(0, limit),
    };
}

/** Top client page and top rival page on one platform, for a health tile. */
export function pagesForEngine(
    insights: Insights | undefined,
    engine: Engine,
    competitors: string[],
): { client: PageInventory | null; rival: PageInventory | null } {
    if (!insights) return { client: null, rival: null };
    const on = (p: PageInventory) => p.engines.includes(engine);
    const comp = new Set(competitors.map(normalise));
    const client =
        insights.client_pages.filter(on).sort((a, b) => b.citations - a.citations)[0] ?? null;
    const rival =
        insights.winning_pages
            .filter((p) => on(p) && !p.is_client && comp.has(normalise(p.domain)))
            .sort((a, b) => b.citations - a.citations)[0] ?? null;
    return { client, rival };
}

/** Shorten a URL to host + path for display; the full URL stays in the href. */
export function shortUrl(url: string, max = 60): string {
    const s = url.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "");
    return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}
