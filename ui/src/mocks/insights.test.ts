import { buildInsights, classifyDomain } from "./insights";
import projects from "./fixtures/projects.json";
import results from "./fixtures/results.json";
import type { Project, PromptResult } from "@/api/endpoints";

describe("mock insights", () => {
    const project = (projects as unknown as Project[])[0]!;
    const insights = buildInsights(project, results as unknown as PromptResult[], null, []);

    it("produces one health row per tracked platform with a basis", () => {
        expect(insights.health.map((h) => h.engine)).toEqual(project.engines);
        for (const h of insights.health) {
            expect(h.samples).toBeGreaterThanOrEqual(0);
            expect(["winning", "present", "invisible", "losing"]).toContain(h.verdict);
        }
        expect(insights.basis.low_confidence).toBe(true);
    });

    it("raises convert-mention actions where the brand is named but not linked", () => {
        const convert = insights.actions.filter((a) => a.type === "convert_mention");
        expect(convert.length).toBeGreaterThan(0);
        expect(convert[0]?.evidence.quotes.length).toBeGreaterThan(0);
        expect(insights.actions).toEqual(
            [...insights.actions].sort((a, b) => b.impact_score - a.impact_score),
        );
    });

    it("classifies domains into trust classes", () => {
        expect(classifyDomain("g2.com")).toBe("aggregator");
        expect(classifyDomain("reddit.com")).toBe("forum");
        expect(classifyDomain("gep.com")).toBe("vendor");
    });
});
