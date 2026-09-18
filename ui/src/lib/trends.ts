/**
 * Series for the Trends page: one point per pipeline run (from the atlas
 * snapshots) or per consolidation (from stored position sets), per platform,
 * for the citation rate and the mention rate separately. Pure and tested.
 */
import type { Engine, PositionsView } from "@/api/endpoints";
import type { TrendPoint } from "@/components/charts/Charts";
import { fmtDateTime } from "@/app/format";
import { mean, type AtlasIndex } from "./atlas";

export type TrendMetric = "citation" | "mention";

export interface TrendSeries {
    citation: TrendPoint[];
    mention: TrendPoint[];
    /** How many prompt × platform samples each x position rests on. */
    basis: Record<string, number>;
}

/** One point per pipeline run: the mean rate over the chosen prompts on each platform. */
export function perRunSeries(
    index: AtlasIndex,
    promptIds: Set<string> | null,
    engines: Engine[],
): TrendSeries {
    const out: TrendSeries = { citation: [], mention: [], basis: {} };
    for (const run of index.runs) {
        const snaps = index
            .snapshotsInRun(run.run_id)
            .filter(
                (s) =>
                    (!promptIds || promptIds.has(s.prompt_id)) && s.samples - s.failed_samples > 0,
            );
        if (!snaps.length) continue;
        const label = `${fmtDateTime(run.started_at)} · ${run.run_id.slice(0, 6)}`;
        out.basis[label] = snaps.length;
        for (const e of engines) {
            const mine = snaps.filter((s) => s.engine === e);
            out.citation.push({
                run: run.run_id,
                date: label,
                engine: e,
                value: mean(mine.map((s) => s.client_citation_rate)),
            });
            out.mention.push({
                run: run.run_id,
                date: label,
                engine: e,
                value: mean(mine.map((s) => s.mention_rate)),
            });
        }
    }
    return out;
}

/** One point per consolidation: the mean position rate over the chosen prompts per platform. */
export function consolidatedSeries(
    views: PositionsView[],
    promptIds: Set<string> | null,
    engines: Engine[],
): TrendSeries {
    const out: TrendSeries = { citation: [], mention: [], basis: {} };
    const sorted = [...views]
        .filter((v) => v.consolidation)
        .sort((a, b) =>
            a.consolidation!.consolidated_at.localeCompare(b.consolidation!.consolidated_at),
        );
    for (const v of sorted) {
        const c = v.consolidation!;
        const positions = v.positions.filter(
            (p) => (!promptIds || promptIds.has(p.prompt_id)) && p.samples - p.failed_samples > 0,
        );
        if (!positions.length) continue;
        const label = `${fmtDateTime(c.consolidated_at)} · ${c.window_runs} crawls`;
        out.basis[label] = positions.reduce((a, p) => a + p.samples, 0);
        for (const e of engines) {
            const mine = positions.filter((p) => p.engine === e);
            out.citation.push({
                run: c.id,
                date: label,
                engine: e,
                value: mean(mine.map((p) => p.citation_rate)),
            });
            out.mention.push({
                run: c.id,
                date: label,
                engine: e,
                value: mean(mine.map((p) => p.mention_rate)),
            });
        }
    }
    return out;
}
