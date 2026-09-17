import atlas from "@/mocks/fixtures/atlas.json";
import type { AtlasDataset } from "@/api/endpoints";
import {
    ATLAS_ENGINES,
    AtlasIndex,
    EMPTY_ATLAS_FILTERS,
    contentGaps,
    engineScoreboard,
    filterAtlasPrompts,
    shareOfVoice,
    stageHeatmap,
    trendSeries,
} from "./atlas";

const index = new AtlasIndex(atlas as unknown as AtlasDataset);
const latestRun = index.runs[index.runs.length - 1]!.run_id;

describe("atlas index", () => {
    it("orders history and honours the as-of run", () => {
        const p = index.prompts[0]!;
        const all = index.historyFor(p.prompt_id, "CHATGPT_SEARCH");
        const first = index.runs[0]!.run_id;
        const early = index.historyFor(p.prompt_id, "CHATGPT_SEARCH", first);
        expect(early.length).toBeLessThanOrEqual(all.length);
        for (let i = 1; i < all.length; i++) {
            expect(all[i]!.captured_at >= all[i - 1]!.captured_at).toBe(true);
        }
    });

    it("scores engines and produces a trend point per run", () => {
        const board = engineScoreboard(index, index.prompts, ATLAS_ENGINES, latestRun);
        expect(board.length).toBe(4);
        expect(board[0]!.trend.length).toBe(index.runs.length);
        expect(trendSeries(index, index.prompts, "PERPLEXITY").length).toBe(index.runs.length);
        for (const b of board) expect(b.cited).toBeLessThanOrEqual(b.audited);
    });

    it("builds the stage heatmap and share of voice with the client first", () => {
        const heat = stageHeatmap(index, index.prompts, ATLAS_ENGINES, latestRun);
        expect(heat.length).toBe(4 * 4);
        const sov = shareOfVoice(index, index.prompts, ATLAS_ENGINES, latestRun);
        expect(sov.domains[0]!.role).toBe("client");
        expect(sov.domains[0]!.domain).toBe("gep.com");
        expect(sov.total).toBeGreaterThan(0);
    });

    it("filters by citation status and domain", () => {
        const cited = filterAtlasPrompts(
            index,
            { ...EMPTY_ATLAS_FILTERS, cited: "yes" },
            latestRun,
        );
        const not = filterAtlasPrompts(index, { ...EMPTY_ATLAS_FILTERS, cited: "no" }, latestRun);
        expect(cited.length + not.length).toBe(index.prompts.length);
        const gep = filterAtlasPrompts(
            index,
            { ...EMPTY_ATLAS_FILTERS, domain: "gep.com" },
            latestRun,
        );
        expect(gep.length).toBe(cited.length);
    });

    it("groups content gaps by subtopic with demand", () => {
        const gaps = contentGaps(index, index.prompts, ATLAS_ENGINES, latestRun);
        for (const g of gaps) {
            expect(g.prompts.every((p) => p.content_gap)).toBe(true);
            expect(g.alreadyCited).toBeLessThanOrEqual(g.prompts.length);
        }
    });
});
