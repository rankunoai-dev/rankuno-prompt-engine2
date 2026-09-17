/**
 * The prompts table joins three sources by `prompt_id`: the tracked prompt
 * (configuration), the latest results (cited/mentioned/due) and the atlas
 * dataset (search volume, intent, stage from the research pass). Pure so it
 * can be unit-tested without React.
 */
import type {
    AtlasDataset,
    AtlasPrompt,
    Engine,
    PromptResult,
    TrackedPrompt,
} from "@/api/endpoints";

export type PromptVerdict = "linked" | "mentioned" | "absent" | "unsampled";

export interface PromptRowView {
    prompt: TrackedPrompt;
    result: PromptResult | null;
    atlas: AtlasPrompt | null;
    verdict: PromptVerdict;
    /** Mean client citation rate across sampled platforms, null when unsampled. */
    citedRate: number | null;
    mentionRate: number | null;
    dueOn: Engine[];
    sampledOn: Engine[];
    linkedOn: Engine[];
    searchVolume: number | null;
    intent: string | null;
    stage: string | null;
}

export function buildPromptRows(
    prompts: TrackedPrompt[] | undefined,
    results: PromptResult[] | undefined,
    atlas: AtlasDataset | undefined,
): PromptRowView[] {
    const byResult = new Map((results ?? []).map((r) => [r.prompt.prompt_id, r]));
    const byAtlas = new Map((atlas?.prompts ?? []).map((p) => [p.prompt_id, p]));
    return (prompts ?? []).map((prompt) => {
        const result = byResult.get(prompt.prompt_id) ?? null;
        const a = byAtlas.get(prompt.prompt_id) ?? null;
        const snaps = result
            ? Object.entries(result.snapshots).filter(
                  ([, s]) => s && s.samples - s.failed_samples > 0,
              )
            : [];
        const sampledOn = snaps.map(([e]) => e as Engine);
        const linkedOn = snaps.filter(([, s]) => s!.client_cited).map(([e]) => e as Engine);
        const cited = snaps.map(([, s]) => s!.client_citation_rate);
        const mentioned = snaps.map(([, s]) => s!.mention_rate);
        const mean = (xs: number[]) =>
            xs.length ? xs.reduce((x, y) => x + y, 0) / xs.length : null;
        const verdict: PromptVerdict = !snaps.length
            ? "unsampled"
            : linkedOn.length
              ? "linked"
              : snaps.some(([, s]) => s!.mention_detected)
                ? "mentioned"
                : "absent";
        return {
            prompt,
            result,
            atlas: a,
            verdict,
            citedRate: mean(cited),
            mentionRate: mean(mentioned),
            dueOn: result?.due_on ?? [],
            sampledOn,
            linkedOn,
            searchVolume: a?.search_volume ?? null,
            intent: a?.search_intent ?? null,
            stage: a?.decision_stage ?? null,
        };
    });
}

export interface PromptFilters {
    q: string;
    intent: string | null;
    stage: string | null;
    platform: Engine | null;
    verdict: PromptVerdict | null;
    due: "due" | "not_due" | null;
    starred: boolean;
    enabled: "on" | "off" | null;
}

export const EMPTY_FILTERS: PromptFilters = {
    q: "",
    intent: null,
    stage: null,
    platform: null,
    verdict: null,
    due: null,
    starred: false,
    enabled: null,
};

export function filterPromptRows(rows: PromptRowView[], f: PromptFilters): PromptRowView[] {
    const q = f.q.trim().toLowerCase();
    return rows.filter((r) => {
        if (q) {
            const hay = [r.prompt.prompt_text, r.prompt.keyword ?? "", r.prompt.subtopic ?? ""]
                .join(" ")
                .toLowerCase();
            if (!hay.includes(q)) return false;
        }
        if (f.intent && r.intent !== f.intent) return false;
        if (f.stage && r.stage !== f.stage) return false;
        if (f.platform) {
            const engines = r.prompt.engines ?? r.result?.effective_engines ?? [];
            if (!engines.includes(f.platform)) return false;
        }
        if (f.verdict && r.verdict !== f.verdict) return false;
        if (f.due === "due" && !r.dueOn.length) return false;
        if (f.due === "not_due" && r.dueOn.length) return false;
        if (f.starred && !r.prompt.important) return false;
        if (f.enabled === "on" && !r.prompt.enabled) return false;
        if (f.enabled === "off" && r.prompt.enabled) return false;
        return true;
    });
}

/** Parses the import text: one prompt per line, optional `| keyword | subtopic`. */
export function parseImportText(
    text: string,
): { prompt_text: string; keyword: string | null; subtopic: string | null }[] {
    return text
        .split(/\r?\n/)
        .map((line) => line.split("|").map((s) => s.trim()))
        .filter((parts) => (parts[0] ?? "").length >= 3)
        .map((parts) => ({
            prompt_text: parts[0]!,
            keyword: parts[1] || null,
            subtopic: parts[2] || null,
        }));
}
