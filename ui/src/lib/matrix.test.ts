import { buildMatrix, cellFromPosition, cellFromSnapshot, highlightMentions } from "./matrix";
import { wilson } from "@/mocks/insights";
import projects from "@/mocks/fixtures/projects.json";
import results from "@/mocks/fixtures/results.json";
import type { Engine, Project, PromptResult } from "@/api/endpoints";

const project = (projects as unknown as Project[])[0]!;
const rows = results as unknown as PromptResult[];

describe("matrix cells", () => {
    it("classifies linked, mentioned and absent cells from snapshots", () => {
        const kinds = new Set<string>();
        for (const r of rows) {
            for (const e of project.engines)
                kinds.add(cellFromSnapshot(r.snapshots[e], project).kind);
        }
        expect(kinds.has("linked")).toBe(true);
        expect(kinds.has("mentioned")).toBe(true);
        expect(kinds.has("absent")).toBe(true);
    });

    it("marks a competitor win when the client is absent and a competitor is cited", () => {
        const base = rows[0]!.snapshots.GOOGLE_AI_OVERVIEW!;
        const cell = cellFromSnapshot(
            {
                ...base,
                client_cited: false,
                client_citation_rate: 0,
                mention_detected: false,
                mention_rate: 0,
                cited_domains: ["coupa.com", "gartner.com"],
                competitor_citations: { "coupa.com": 1 },
            },
            project,
        );
        expect(cell.kind).toBe("competitor");
        expect(cell.competitor).toBe("coupa.com");
    });

    it("groups rows by subtopic with linked/sampled counts", () => {
        const groups = buildMatrix(rows, project.engines as Engine[], (r, e) =>
            cellFromSnapshot(r.snapshots[e], project),
        );
        expect(groups.length).toBeGreaterThan(0);
        const total = groups.reduce((a, g) => a + g.rows.length, 0);
        expect(total).toBe(rows.length);
        expect(groups.every((g) => g.linked <= g.sampled)).toBe(true);
    });

    it("carries the 95% band on consolidated cells and leaves it off point-in-time ones", () => {
        const [low, high] = wilson(6, 9)!;
        const pos = {
            prompt_id: "p",
            engine: "PERPLEXITY" as Engine,
            runs: 3,
            first_run_at: "2026-09-17T06:00:00Z",
            last_run_at: "2026-09-19T06:00:00Z",
            samples: 9,
            failed_samples: 0,
            cited_samples: 6,
            citation_rate: 0.6667,
            citation_rate_low: low,
            citation_rate_high: high,
            cited: true,
            mention_samples: 9,
            mention_rate: 1,
            mention_rate_low: null,
            mention_rate_high: null,
            mentioned: true,
            best_rank: 2,
            mean_rank: 2.5,
            rank_distribution: { "2": 6, "not cited": 3 },
            cited_domain_share: { "gep.com": 0.67 },
            competitor_citations: {},
            organic_prompt_best: null,
            organic_prompt_mean: null,
            organic_keyword_best: null,
            organic_keyword_mean: null,
        };
        const cell = cellFromPosition(pos, project);
        expect(cell.citedLow).toBe(low);
        expect(cell.citedHigh).toBe(high);
        expect(low).toBeLessThan(0.6667);
        expect(high).toBeGreaterThan(0.6667);
        const snap = cellFromSnapshot(
            (results as unknown as PromptResult[])[0]!.snapshots.PERPLEXITY,
            project,
        );
        expect(snap.citedLow).toBeNull();
    });

    it("splits answer text around brand sentences", () => {
        const parts = highlightMentions("Intro. GEP SMART is a leading suite. Outro.", [
            "GEP SMART is a leading suite.",
        ]);
        expect(parts.map((p) => p.hit)).toEqual([false, true, false]);
        expect(parts[1]!.text).toBe("GEP SMART is a leading suite.");
    });
});
