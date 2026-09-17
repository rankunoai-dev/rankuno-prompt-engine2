import { http, HttpResponse } from "msw";
import { server } from "@/mocks/server";
import { ApiError, http as api } from "./client";
import { endpoints } from "./endpoints";

describe("api client", () => {
    it("returns typed JSON for a live route", async () => {
        const projects = await endpoints.projects();
        expect(projects.length).toBeGreaterThan(0);
        expect(projects[0]?.client.brand_name).toBe("GEP");
    });

    it("maps 422 validation errors onto fields", async () => {
        await expect(
            endpoints.createProject({
                name: "",
                engines: ["CHATGPT_SEARCH"],
                engine_models: {},
                interval: "daily",
                enabled: true,
                samples_per_engine: null,
                generate_prompts: false,
                track_keyword_rank: true,
                resolve_redirects: false,
                max_engine_calls: null,
                reuse_within_hours: null,
                consolidation_runs: 3,
                notes: "",
                client: {
                    brand_name: "",
                    lob: "x",
                    domains: ["a.com"],
                    seed_keywords: ["k"],
                    aliases: [],
                    competitor_domains: [],
                    competitor_names: [],
                    subtopics: [],
                    landing_pages: [],
                },
            }),
        ).rejects.toMatchObject({ status: 422, fieldErrors: { name: expect.any(String) } });
    });

    it("surfaces the server detail verbatim", async () => {
        server.use(
            http.get("/api/projects/nope", () =>
                HttpResponse.json({ detail: "Not found: nope" }, { status: 404 }),
            ),
        );
        const err = await api.get("/api/projects/nope").catch((e: unknown) => e);
        expect(err).toBeInstanceOf(ApiError);
        expect((err as ApiError).message).toBe("Not found: nope");
    });

    it("drops empty query parameters", async () => {
        let url = "";
        server.use(
            http.get("/api/costs", ({ request }) => {
                url = request.url;
                return HttpResponse.json({ calls: 0 });
            }),
        );
        await api.get("/api/costs", { query: { project_id: undefined, days: 7 } });
        expect(url.endsWith("/api/costs?days=7")).toBe(true);
    });
});
