import atlas from "@/mocks/fixtures/atlas.json";
import type { AtlasDataset, PositionsView } from "@/api/endpoints";
import { AtlasIndex, ATLAS_ENGINES } from "./atlas";
import { consolidatedSeries, perRunSeries } from "./trends";

const index = new AtlasIndex(atlas as unknown as AtlasDataset);

describe("trend series", () => {
    it("emits one point per run and platform for both metrics", () => {
        const s = perRunSeries(index, null, ATLAS_ENGINES);
        const runsWithData = Object.keys(s.basis).length;
        expect(runsWithData).toBeGreaterThan(0);
        expect(s.citation.length).toBe(runsWithData * ATLAS_ENGINES.length);
        expect(s.mention.length).toBe(s.citation.length);
        for (const p of s.citation) if (p.value !== null) expect(p.value).toBeLessThanOrEqual(1);
    });

    it("restricts per-run points to the chosen prompts", () => {
        const one = new Set([index.prompts[0]!.prompt_id]);
        const s = perRunSeries(index, one, ["CHATGPT_SEARCH"]);
        expect(Object.values(s.basis).every((n) => n <= 4)).toBe(true);
    });

    it("orders consolidations by date and averages positions per platform", () => {
        const mk = (id: string, at: string, rate: number): PositionsView => ({
            consolidation: {
                id,
                project_id: "p",
                consolidated_at: at,
                window_runs: 3,
                project_run_ids: [],
                run_ids: [],
                first_run_at: null,
                last_run_at: null,
                prompts: 1,
                trigger: "auto",
                note: "",
                positions_count: 2,
            },
            positions: [
                {
                    prompt_id: "a",
                    engine: "PERPLEXITY",
                    runs: 3,
                    first_run_at: at,
                    last_run_at: at,
                    samples: 6,
                    failed_samples: 0,
                    cited_samples: 3,
                    citation_rate: rate,
                    cited: true,
                    mention_samples: 6,
                    mention_rate: 1,
                    mentioned: true,
                    best_rank: 2,
                    mean_rank: 2,
                    rank_distribution: {},
                    cited_domain_share: {},
                    competitor_citations: {},
                    organic_prompt_best: null,
                    organic_prompt_mean: null,
                    organic_keyword_best: null,
                    organic_keyword_mean: null,
                },
                {
                    prompt_id: "b",
                    engine: "PERPLEXITY",
                    runs: 3,
                    first_run_at: at,
                    last_run_at: at,
                    samples: 6,
                    failed_samples: 0,
                    cited_samples: 0,
                    citation_rate: 0,
                    cited: false,
                    mention_samples: 0,
                    mention_rate: 0,
                    mentioned: false,
                    best_rank: null,
                    mean_rank: null,
                    rank_distribution: {},
                    cited_domain_share: {},
                    competitor_citations: {},
                    organic_prompt_best: null,
                    organic_prompt_mean: null,
                    organic_keyword_best: null,
                    organic_keyword_mean: null,
                },
            ],
            history: [],
            runs_since_last: 0,
        });
        const s = consolidatedSeries(
            [mk("c2", "2026-09-20T00:00:00Z", 1), mk("c1", "2026-09-10T00:00:00Z", 0.5)],
            null,
            ["PERPLEXITY"],
        );
        expect(s.citation.map((p) => p.run)).toEqual(["c1", "c2"]);
        expect(s.citation[0]!.value).toBeCloseTo(0.25);
        expect(s.citation[1]!.value).toBeCloseTo(0.5);
        expect(s.mention[0]!.value).toBeCloseTo(0.5);
        expect(
            consolidatedSeries([mk("c1", "2026-09-10T00:00:00Z", 1)], new Set(["a"]), [
                "PERPLEXITY",
            ]).citation[0]!.value,
        ).toBe(1);
    });
});
