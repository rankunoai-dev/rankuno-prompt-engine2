/**
 * Battleground cell semantics. One badge per prompt × platform:
 * linked (client cited, with rank) · mentioned only · competitor wins
 * (client absent, a competitor cited) · absent · unsampled. Pure and shared
 * by the consolidated and point-in-time views.
 */
import type {
    AtlasSnapshot,
    CitationSnapshot,
    ConsolidatedPosition,
    Engine,
    PromptResult,
    Project,
} from "@/api/endpoints";

export type CellKind = "linked" | "mentioned" | "competitor" | "absent" | "unsampled";

export interface CellView {
    kind: CellKind;
    rank: number | null;
    competitor: string | null;
    citedRate: number | null;
    mentionRate: number | null;
    /** 95% band on citedRate; null on point-in-time cells and old consolidations. */
    citedLow: number | null;
    citedHigh: number | null;
    samples: number;
    crawls: number;
    /** A sentence where the brand is named, for the hover. */
    snippet: string | null;
    topDomains: string[];
    /** Exact page that earned the citation, and the rival page that took it. */
    clientUrl: string | null;
    competitorUrl: string | null;
    due: boolean;
    capturedAt: string | null;
}

const normalise = (d: string) => d.toLowerCase().replace(/^www\./, "");

function competitorAmong(domains: string[], project: Project): string | null {
    const comps = project.client.competitor_domains.map(normalise);
    for (const d of domains) {
        const n = normalise(d);
        const hit = comps.find((c) => n === c || n.endsWith(`.${c}`));
        if (hit) return hit;
    }
    return null;
}

export function cellFromSnapshot(
    sn: CitationSnapshot | AtlasSnapshot | null | undefined,
    project: Project,
    due = false,
): CellView {
    if (!sn || sn.samples - sn.failed_samples <= 0) {
        return {
            kind: "unsampled",
            rank: null,
            competitor: null,
            citedRate: null,
            mentionRate: null,
            citedLow: null,
            citedHigh: null,
            samples: sn?.samples ?? 0,
            crawls: sn ? 1 : 0,
            snippet: null,
            topDomains: [],
            clientUrl: null,
            competitorUrl: null,
            due,
            capturedAt: sn?.captured_at ?? null,
        };
    }
    const competitor = sn.client_cited
        ? null
        : (competitorAmong(Object.keys(sn.competitor_citations), project) ??
          competitorAmong(sn.cited_domains, project));
    const mentioned = "mention_detected" in sn ? sn.mention_detected : sn.mention_rate > 0;
    const kind: CellKind = sn.client_cited
        ? "linked"
        : mentioned
          ? "mentioned"
          : competitor
            ? "competitor"
            : "absent";
    return {
        kind,
        rank: sn.client_best_rank,
        competitor,
        citedRate: sn.client_citation_rate,
        mentionRate: sn.mention_rate,
        citedLow: null,
        citedHigh: null,
        samples: sn.samples,
        crawls: 1,
        snippet: sn.mention_snippets[0]?.snippet ?? null,
        topDomains: sn.cited_domains.slice(0, 3),
        clientUrl:
            sn.client_urls[0] ??
            sn.citation_links.find(
                (c) =>
                    competitorAmong([c.domain], project) === null &&
                    project.client.domains.some((d) => normalise(c.domain).endsWith(normalise(d))),
            )?.url ??
            null,
        competitorUrl: competitor
            ? (sn.citation_links.find((c) => normalise(c.domain) === competitor)?.url ?? null)
            : null,
        due,
        capturedAt: sn.captured_at,
    };
}

export function cellFromPosition(
    pos: ConsolidatedPosition | undefined,
    project: Project,
    due = false,
): CellView {
    if (!pos || pos.samples - pos.failed_samples <= 0) {
        return {
            kind: "unsampled",
            rank: null,
            competitor: null,
            citedRate: null,
            mentionRate: null,
            citedLow: null,
            citedHigh: null,
            samples: pos?.samples ?? 0,
            crawls: pos?.runs ?? 0,
            snippet: null,
            topDomains: [],
            clientUrl: null,
            competitorUrl: null,
            due,
            capturedAt: pos?.last_run_at ?? null,
        };
    }
    const domains = Object.entries(pos.cited_domain_share)
        .sort((a, b) => b[1] - a[1])
        .map(([d]) => d);
    const competitor = pos.cited
        ? null
        : (competitorAmong(Object.keys(pos.competitor_citations), project) ??
          competitorAmong(domains, project));
    const kind: CellKind = pos.cited
        ? "linked"
        : pos.mentioned
          ? "mentioned"
          : competitor
            ? "competitor"
            : "absent";
    return {
        kind,
        rank: pos.best_rank,
        competitor,
        citedRate: pos.citation_rate,
        mentionRate: pos.mention_rate,
        citedLow: pos.citation_rate_low ?? null,
        citedHigh: pos.citation_rate_high ?? null,
        samples: pos.samples,
        crawls: pos.runs,
        snippet: null,
        topDomains: domains.slice(0, 3),
        clientUrl: null,
        competitorUrl: null,
        due,
        capturedAt: pos.last_run_at,
    };
}

export interface MatrixRow {
    result: PromptResult;
    subtopic: string;
    cells: Record<string, CellView>;
}

export interface MatrixGroup {
    subtopic: string;
    rows: MatrixRow[];
    /** Linked cells over sampled cells in the group. */
    linked: number;
    sampled: number;
}

export function buildMatrix(
    results: PromptResult[] | undefined,
    engines: Engine[],
    cellFor: (r: PromptResult, engine: Engine) => CellView,
): MatrixGroup[] {
    const groups = new Map<string, MatrixRow[]>();
    for (const r of results ?? []) {
        const subtopic = r.prompt.subtopic ?? r.prompt.keyword ?? "Ungrouped";
        const cells: Record<string, CellView> = {};
        for (const e of engines) cells[e] = cellFor(r, e);
        const list = groups.get(subtopic) ?? [];
        list.push({ result: r, subtopic, cells });
        groups.set(subtopic, list);
    }
    return [...groups.entries()]
        .map(([subtopic, rows]) => {
            const all = rows.flatMap((row) => Object.values(row.cells));
            return {
                subtopic,
                rows,
                linked: all.filter((c) => c.kind === "linked").length,
                sampled: all.filter((c) => c.kind !== "unsampled").length,
            };
        })
        .sort((a, b) => a.subtopic.localeCompare(b.subtopic));
}

/** Highlights brand sentences in an answer: returns text segments with a flag. */
export function highlightMentions(
    text: string,
    snippets: string[],
): { text: string; hit: boolean }[] {
    if (!text) return [];
    const needles = snippets.map((s) => s.trim()).filter((s) => s.length > 8);
    if (!needles.length) return [{ text, hit: false }];
    const out: { text: string; hit: boolean }[] = [];
    let rest = text;
    while (rest.length) {
        let best: { idx: number; len: number } | null = null;
        for (const n of needles) {
            const idx = rest.indexOf(n);
            if (idx >= 0 && (!best || idx < best.idx)) best = { idx, len: n.length };
        }
        if (!best) {
            out.push({ text: rest, hit: false });
            break;
        }
        if (best.idx > 0) out.push({ text: rest.slice(0, best.idx), hit: false });
        out.push({ text: rest.slice(best.idx, best.idx + best.len), hit: true });
        rest = rest.slice(best.idx + best.len);
    }
    return out;
}
