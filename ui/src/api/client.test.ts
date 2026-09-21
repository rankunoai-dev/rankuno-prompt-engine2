import { http, HttpResponse } from "msw";
import { server } from "@/mocks/server";
import { ApiError, http as api } from "./client";
import { endpoints } from "./endpoints";
import { PROJECT_ID, mockState, protectMockProject } from "@/mocks/handlers";
import { registerUnlockHandler, setToken, tokenFor } from "@/lib/projectAuth";

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

    describe("owner credentials (ADR 0019)", () => {
        it("reads a protected project freely and refuses a write with the server code", async () => {
            protectMockProject(PROJECT_ID, "gaurav", "open sesame");
            await expect(endpoints.project(PROJECT_ID)).resolves.toMatchObject({ protected: true });
            const refused = endpoints.updateProject(PROJECT_ID, { notes: "x" });
            await expect(refused).rejects.toMatchObject({ status: 403, code: "project_locked" });
            expect(mockState.projects.find((p) => p.id === PROJECT_ID)?.notes).not.toBe("x");
        });

        it("sends the stored credential on writes", async () => {
            protectMockProject(PROJECT_ID, "gaurav", "open sesame");
            setToken(PROJECT_ID, "gaurav", "open sesame");
            const saved = await endpoints.updateProject(PROJECT_ID, { notes: "mine" });
            expect(saved.notes).toBe("mine");
        });

        it("asks once for the credential when refused, then retries the same write", async () => {
            protectMockProject(PROJECT_ID, "gaurav", "open sesame");
            const asked: string[] = [];
            registerUnlockHandler(async (request) => {
                asked.push(`${request.reason}:${request.owner}`);
                setToken(request.projectId, "gaurav", "open sesame");
                return true;
            });
            const saved = await endpoints.updateProject(PROJECT_ID, { notes: "after" });
            expect(saved.notes).toBe("after");
            expect(asked).toEqual(["locked:gaurav"]);
        });

        it("drops a stale credential and surfaces the refusal when the dialog is cancelled", async () => {
            protectMockProject(PROJECT_ID, "gaurav", "rotated elsewhere");
            setToken(PROJECT_ID, "gaurav", "open sesame");
            const reasons: string[] = [];
            registerUnlockHandler(async (request) => {
                reasons.push(request.reason);
                return false;
            });
            await expect(endpoints.updateProject(PROJECT_ID, { notes: "x" })).rejects.toMatchObject(
                {
                    status: 403,
                    code: "project_credentials_invalid",
                },
            );
            expect(reasons).toEqual(["invalid"]);
            expect(tokenFor(PROJECT_ID)).toBeNull();
        });
    });
});
