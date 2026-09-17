/** Derived scheduling facts from a project's results (due work is never stored). */
import type { PromptResult } from "@/api/endpoints";
import { intervalToMs } from "@/app/format";

export interface DueSummary {
    /** Prompt × platform checks due right now. */
    dueNow: number;
    /** Prompts with at least one platform due. */
    promptsDue: number;
    /** Earliest upcoming due time among checks that are not yet due, or null. */
    nextDueAt: Date | null;
    /** Checks that have never been sampled. */
    neverSampled: number;
}

export function summariseDue(results: PromptResult[] | undefined): DueSummary {
    const out: DueSummary = { dueNow: 0, promptsDue: 0, nextDueAt: null, neverSampled: 0 };
    if (!results) return out;
    for (const r of results) {
        if (!r.prompt.enabled) continue;
        let promptDue = false;
        const interval = intervalToMs(r.effective_interval);
        for (const engine of r.effective_engines) {
            const sn = r.snapshots[engine];
            if (r.due_on.includes(engine)) {
                out.dueNow += 1;
                promptDue = true;
                if (!sn) out.neverSampled += 1;
                continue;
            }
            if (sn && interval) {
                const at = new Date(new Date(sn.captured_at).getTime() + interval);
                if (!out.nextDueAt || at < out.nextDueAt) out.nextDueAt = at;
            }
        }
        if (promptDue) out.promptsDue += 1;
    }
    return out;
}
