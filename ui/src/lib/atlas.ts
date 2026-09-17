/**
 * Pure computations behind the Atlas: the cross-project explorer over the
 * per-run snapshot dataset (`/reports/prompt-atlas-data.json`). Mirrors the
 * definitions of the hand-written `docs/prompt-atlas.html` so the two agree:
 * "latest" means the newest snapshot captured on or before the chosen run's
 * end, and velocity compares the mean rate of two consecutive 30-day windows
 * like `TimeSeriesDB.velocity()`.
 */
import type { AtlasDataset, AtlasPrompt, AtlasRun, AtlasSnapshot, Engine } from "@/api/endpoints";

export const ATLAS_ENGINES: Engine[] = [
    "GOOGLE_AI_OVERVIEW",
    "CHATGPT_SEARCH",
    "PERPLEXITY",
    "GEMINI",
];
export const STAGES = ["AWARENESS", "CONSIDERATION", "DECISION", "POST_PURCHASE"];
export const INTENTS = ["INFORMATIONAL", "COMMERCIAL", "TRANSACTIONAL", "NAVIGATIONAL"];

export interface AtlasFilters {
    engines: Engine[];
    promptType: string | null;
    stage: string | null;
    intent: string | null;
    cited: "yes" | "no" | "gap" | null;
    verdict: string | null;
    domain: string;
    q: string;
}

export const EMPTY_ATLAS_FILTERS: AtlasFilters = {
    engines: [...ATLAS_ENGINES],
    promptType: null,
    stage: null,
    intent: null,
    cited: null,
    verdict: null,
    domain: "",
    q: "",
};

export const regDomain = (hostOrUrl: string): string => {
    let h = String(hostOrUrl || "")
        .trim()
        .toLowerCase();
    try {
        if (/^https?:\/\//.test(h)) h = new URL(h).hostname;
    } catch {
        /* keep as is */
    }
    return h.replace(/^www\./, "");
};

export const mean = (xs: number[]): number | null =>
    xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
export const median = (xs: number[]): number | null => {
    if (!xs.length) return null;
    const b = [...xs].sort((x, y) => x - y);
    const m = b.length >> 1;
    return b.length % 2 ? b[m]! : (b[m - 1]! + b[m]!) / 2;
};

/** Snapshot history per prompt × engine, oldest first. */
export class AtlasIndex {
    readonly runs: AtlasRun[];
    readonly prompts: AtlasPrompt[];
    private readonly history = new Map<string, AtlasSnapshot[]>();
    readonly promptById = new Map<string, AtlasPrompt>();
    readonly runById = new Map<string, AtlasRun>();

    constructor(readonly data: AtlasDataset) {
        this.runs = [...data.runs].sort((a, b) => a.started_at.localeCompare(b.started_at));
        this.prompts = data.prompts;
        for (const p of data.prompts) this.promptById.set(p.prompt_id, p);
        for (const r of this.runs) this.runById.set(r.run_id, r);
        const snaps = [...data.snapshots].sort((a, b) =>
            a.captured_at.localeCompare(b.captured_at),
        );
        for (const s of snaps) {
            const k = `${s.prompt_id}|${s.engine}`;
            const list = this.history.get(k) ?? [];
            list.push(s);
            this.history.set(k, list);
        }
    }

    historyFor(promptId: string, engine: Engine, asOf?: string | null): AtlasSnapshot[] {
        const list = this.history.get(`${promptId}|${engine}`) ?? [];
        if (!asOf) return list;
        const cutoff = this.runById.get(asOf)?.finished_at ?? "9999";
        return list.filter((s) => s.captured_at <= cutoff);
    }

    latest(promptId: string, engine: Engine, asOf?: string | null): AtlasSnapshot | null {
        const h = this.historyFor(promptId, engine, asOf);
        return h.length ? h[h.length - 1]! : null;
    }

    snapshotsInRun(runId: string): AtlasSnapshot[] {
        return this.data.snapshots.filter((s) => s.run_id === runId);
    }

    velocity(promptId: string, engine: Engine, asOf?: string | null, windowDays = 30) {
        const hs = this.historyFor(promptId, engine, asOf);
        if (!hs.length) return null;
        const newest = new Date(hs[hs.length - 1]!.captured_at).getTime();
        const w = windowDays * 86_400_000;
        const cur = hs.filter((s) => new Date(s.captured_at).getTime() > newest - w);
        const prev = hs.filter((s) => {
            const t = new Date(s.captured_at).getTime();
            return t > newest - 2 * w && t <= newest - w;
        });
        if (!prev.length) return null;
        const best = (arr: AtlasSnapshot[]) => {
            const r = arr.map((s) => s.client_best_rank).filter((x): x is number => x !== null);
            return r.length ? Math.min(...r) : null;
        };
        const cr = mean(cur.map((s) => s.client_citation_rate)) ?? 0;
        const pr = mean(prev.map((s) => s.client_citation_rate)) ?? 0;
        const cb = best(cur);
        const pb = best(prev);
        return {
            window_days: windowDays,
            current_rate: cr,
            previous_rate: pr,
            rate_delta: cr - pr,
            current_best_rank: cb,
            previous_best_rank: pb,
            rank_delta: cb !== null && pb !== null ? pb - cb : null,
        };
    }
}

export function domainRole(
    domain: string,
    meta: AtlasDataset["meta"],
): "client" | "comp" | "other" {
    const d = regDomain(domain);
    if (meta.domains.some((x) => regDomain(x) === d || d.endsWith(`.${regDomain(x)}`)))
        return "client";
    if (meta.competitor_domains.some((x) => regDomain(x) === d)) return "comp";
    return "other";
}

export function filterAtlasPrompts(
    index: AtlasIndex,
    filters: AtlasFilters,
    asOf: string | null,
): AtlasPrompt[] {
    const q = filters.q.trim().toLowerCase();
    const dom = regDomain(filters.domain);
    return index.prompts.filter((p) => {
        if (filters.promptType && p.prompt_type !== filters.promptType) return false;
        if (filters.stage && p.decision_stage !== filters.stage) return false;
        if (filters.intent && p.search_intent !== filters.intent) return false;
        if (filters.verdict && (p.verdict ?? "") !== filters.verdict) return false;
        if (filters.cited) {
            const snaps = filters.engines
                .map((e) => index.latest(p.prompt_id, e, asOf))
                .filter(Boolean) as AtlasSnapshot[];
            const any = snaps.some((s) => s.client_cited);
            if (filters.cited === "yes" && !any) return false;
            if (filters.cited === "no" && any) return false;
            if (filters.cited === "gap" && !p.content_gap) return false;
        }
        if (dom) {
            const hit = filters.engines.some((e) => {
                const s = index.latest(p.prompt_id, e, asOf);
                return s && s.cited_domains.some((x) => regDomain(x) === dom);
            });
            if (!hit) return false;
        }
        if (q) {
            const hay =
                `${p.prompt_text} ${p.subtopic} ${p.core_keyword} ${p.prompt_id}`.toLowerCase();
            if (!hay.includes(q)) return false;
        }
        return true;
    });
}

export interface EngineScore {
    engine: Engine;
    audited: number;
    cited: number;
    meanCited: number | null;
    meanMentioned: number | null;
    medianBestRank: number | null;
    webTrigger: number | null;
    failedSamples: number;
    trend: (number | null)[];
}

export function engineScoreboard(
    index: AtlasIndex,
    prompts: AtlasPrompt[],
    engines: Engine[],
    asOf: string | null,
): EngineScore[] {
    return engines.map((engine) => {
        const snaps = prompts
            .map((p) => index.latest(p.prompt_id, engine, asOf))
            .filter(Boolean) as AtlasSnapshot[];
        const ranks = snaps.map((s) => s.client_best_rank).filter((x): x is number => x !== null);
        return {
            engine,
            audited: snaps.length,
            cited: snaps.filter((s) => s.client_cited).length,
            meanCited: mean(snaps.map((s) => s.client_citation_rate)),
            meanMentioned: mean(snaps.map((s) => s.mention_rate)),
            medianBestRank: median(ranks),
            webTrigger: mean(snaps.map((s) => s.web_trigger_rate)),
            failedSamples: snaps.reduce((a, s) => a + s.failed_samples, 0),
            trend: trendSeries(index, prompts, engine),
        };
    });
}

/** Mean citation rate per run for one engine (null when the run has no snapshot for these prompts). */
export function trendSeries(
    index: AtlasIndex,
    prompts: AtlasPrompt[],
    engine: Engine,
): (number | null)[] {
    const ids = new Set(prompts.map((p) => p.prompt_id));
    return index.runs.map((r) => {
        const vals = index
            .snapshotsInRun(r.run_id)
            .filter((s) => s.engine === engine && ids.has(s.prompt_id))
            .map((s) => s.client_citation_rate);
        return mean(vals);
    });
}

export interface HeatCell {
    row: string;
    engine: Engine;
    value: number | null;
    prompts: number;
}

export function stageHeatmap(
    index: AtlasIndex,
    prompts: AtlasPrompt[],
    engines: Engine[],
    asOf: string | null,
    by: "decision_stage" | "prompt_type" = "decision_stage",
): HeatCell[] {
    const rows = by === "decision_stage" ? STAGES : ["BRANDED", "NON_BRANDED"];
    const out: HeatCell[] = [];
    for (const row of rows) {
        const group = prompts.filter((p) => p[by] === row);
        for (const engine of engines) {
            const vals = group
                .map((p) => index.latest(p.prompt_id, engine, asOf))
                .filter(Boolean)
                .map((s) => s!.client_citation_rate);
            out.push({ row, engine, value: mean(vals), prompts: group.length });
        }
    }
    return out;
}

export interface DomainShare {
    domain: string;
    role: "client" | "comp" | "other";
    count: number;
    share: number;
    perEngine: Record<string, number>;
}

/** Share of prompt × engine answers (as of the run) that cite each domain. */
export function shareOfVoice(
    index: AtlasIndex,
    prompts: AtlasPrompt[],
    engines: Engine[],
    asOf: string | null,
): { domains: DomainShare[]; total: number; perEngineTotal: Record<string, number> } {
    const counts = new Map<string, DomainShare>();
    let total = 0;
    const perEngineTotal: Record<string, number> = {};
    for (const p of prompts) {
        for (const e of engines) {
            const s = index.latest(p.prompt_id, e, asOf);
            if (!s) continue;
            total += 1;
            perEngineTotal[e] = (perEngineTotal[e] ?? 0) + 1;
            for (const d of new Set(s.cited_domains.map(regDomain))) {
                const entry = counts.get(d) ?? {
                    domain: d,
                    role: domainRole(d, index.data.meta),
                    count: 0,
                    share: 0,
                    perEngine: {},
                };
                entry.count += 1;
                entry.perEngine[e] = (entry.perEngine[e] ?? 0) + 1;
                counts.set(d, entry);
            }
        }
    }
    for (const cd of index.data.meta.domains.map(regDomain)) {
        if (!counts.has(cd))
            counts.set(cd, { domain: cd, role: "client", count: 0, share: 0, perEngine: {} });
    }
    const roleOrder = { client: 0, comp: 1, other: 2 };
    const domains = [...counts.values()]
        .map((d) => ({ ...d, share: total ? d.count / total : 0 }))
        .sort((a, b) => roleOrder[a.role] - roleOrder[b.role] || b.count - a.count);
    return { domains, total, perEngineTotal };
}

export interface GapGroup {
    subtopic: string;
    prompts: AtlasPrompt[];
    volume: number;
    alreadyCited: number;
}

export function contentGaps(
    index: AtlasIndex,
    prompts: AtlasPrompt[],
    engines: Engine[],
    asOf: string | null,
): GapGroup[] {
    const groups = new Map<string, AtlasPrompt[]>();
    for (const p of prompts.filter((x) => x.content_gap)) {
        groups.set(p.subtopic, [...(groups.get(p.subtopic) ?? []), p]);
    }
    return [...groups.entries()]
        .map(([subtopic, ps]) => ({
            subtopic,
            prompts: ps,
            volume: ps.reduce((a, p) => a + p.search_volume, 0),
            alreadyCited: ps.filter((p) =>
                engines.some((e) => index.latest(p.prompt_id, e, asOf)?.client_cited),
            ).length,
        }))
        .sort((a, b) => b.volume - a.volume);
}

/** Rank histogram bands for one engine: 0–20, 20–40, … of citation rate. */
export function rateBands(
    index: AtlasIndex,
    prompts: AtlasPrompt[],
    engine: Engine,
    asOf: string | null,
): number[] {
    const bins = [0, 0, 0, 0, 0];
    for (const p of prompts) {
        const s = index.latest(p.prompt_id, engine, asOf);
        if (!s) continue;
        bins[Math.min(4, Math.floor(s.client_citation_rate * 5))]! += 1;
    }
    return bins;
}
