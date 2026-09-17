import { buildPromptRows, filterPromptRows, parseImportText, EMPTY_FILTERS } from "./promptView";
import prompts from "@/mocks/fixtures/prompts.json";
import results from "@/mocks/fixtures/results.json";
import atlas from "@/mocks/fixtures/atlas.json";
import type { AtlasDataset, PromptResult, TrackedPrompt } from "@/api/endpoints";

const rows = buildPromptRows(
    prompts as unknown as TrackedPrompt[],
    results as unknown as PromptResult[],
    atlas as unknown as AtlasDataset,
);

describe("prompt rows", () => {
    it("joins results and atlas facts by prompt id", () => {
        expect(rows.length).toBe((prompts as unknown[]).length);
        const linked = rows.filter((r) => r.verdict === "linked");
        expect(linked.length).toBeGreaterThan(0);
        expect(linked[0]!.linkedOn.length).toBeGreaterThan(0);
        expect(rows.some((r) => r.searchVolume !== null)).toBe(true);
    });

    it("filters by verdict, due state and search text", () => {
        const linked = filterPromptRows(rows, { ...EMPTY_FILTERS, verdict: "linked" });
        expect(linked.every((r) => r.verdict === "linked")).toBe(true);
        const due = filterPromptRows(rows, { ...EMPTY_FILTERS, due: "not_due" });
        expect(due.every((r) => r.dueOn.length === 0)).toBe(true);
        const q = filterPromptRows(rows, { ...EMPTY_FILTERS, q: "supplier" });
        expect(q.length).toBeGreaterThan(0);
        expect(q.every((r) => /supplier/i.test(r.prompt.prompt_text))).toBe(true);
    });

    it("parses import text with optional keyword and subtopic", () => {
        expect(parseImportText("What is S2P? | s2p | Basics\nno\nAnother prompt here")).toEqual([
            { prompt_text: "What is S2P?", keyword: "s2p", subtopic: "Basics" },
            { prompt_text: "Another prompt here", keyword: null, subtopic: null },
        ]);
    });
});
