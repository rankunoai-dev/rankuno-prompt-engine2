/**
 * MSW handlers for every control-plane route, insight routes included.
 *
 * Fixtures are copies of the live API's responses for the existing projects
 * (17 Sep 2026). Mutations change an in-memory copy so tests and the mock
 * browser mode behave like the server; `resetMockState()` restores it.
 * Jobs advance one step per poll so progress UIs can be exercised without
 * spending a cent.
 */
import { HttpResponse, delay, http } from "msw";
import type {
    ActionCard,
    ActionUpdate,
    AnswerSample,
    AtlasDataset,
    Insights,
    Consolidation,
    CostReport,
    Engine,
    Options,
    PositionsView,
    Project,
    ProjectCreate,
    ProjectRunRecord,
    ProjectUpdate,
    PromptResult,
    RunJob,
    RunRequest,
    RunRow,
    TrackedPrompt,
    TrackedPromptCreate,
    TrackedPromptUpdate,
} from "@/api/endpoints";
import { buildInsights, buildSamples } from "./insights";
import optionsFixture from "./fixtures/options.json";
import projectsFixture from "./fixtures/projects.json";
import promptsFixture from "./fixtures/prompts.json";
import promptsP2Fixture from "./fixtures/prompts_p2.json";
import resultsFixture from "./fixtures/results.json";
import resultsP2Fixture from "./fixtures/results_p2.json";
import runsFixture from "./fixtures/runs.json";
import runsP2Fixture from "./fixtures/runs_p2.json";
import costsFixture from "./fixtures/costs.json";
import costsProjectFixture from "./fixtures/costs_project.json";
import atlasFixture from "./fixtures/atlas.json";

export const PROJECT_ID = "e42161487b61";
export const PROJECT_2_ID = "44848df7cc57";

const clone = <T>(x: T): T => JSON.parse(JSON.stringify(x)) as T;
const now = () => new Date().toISOString();
let seq = 0;
const newId = () => `mock${(++seq).toString(16).padStart(8, "0")}`;

interface MockState {
    projects: Project[];
    prompts: Record<string, TrackedPrompt[]>;
    results: Record<string, PromptResult[]>;
    runs: Record<string, RunRow[]>;
    crawls: Record<string, ProjectRunRecord[]>;
    consolidations: Record<string, Consolidation[]>;
    positions: Record<string, PositionsView>;
    jobs: RunJob[];
    actions: Record<string, Record<string, ActionUpdate>>;
}

function fresh(): MockState {
    const projects = clone(projectsFixture as unknown as Project[]);
    const results = clone(resultsFixture as unknown as PromptResult[]);
    const crawl: ProjectRunRecord = {
        id: "crawl00000001",
        project_id: PROJECT_ID,
        started_at: "2026-09-17T06:15:20.000000Z",
        finished_at: "2026-09-17T06:42:59.785358Z",
        run_ids: (runsFixture as unknown as RunRow[]).map((r) => r.run_id),
        prompts_run: results.length,
        batches: 2,
        statuses: ["success", "success"],
        full: true,
    };
    return {
        projects,
        prompts: {
            [PROJECT_ID]: clone(promptsFixture as unknown as TrackedPrompt[]),
            [PROJECT_2_ID]: clone(promptsP2Fixture as unknown as TrackedPrompt[]),
        },
        results: {
            [PROJECT_ID]: results,
            [PROJECT_2_ID]: clone(resultsP2Fixture as unknown as PromptResult[]),
        },
        runs: {
            [PROJECT_ID]: clone(runsFixture as unknown as RunRow[]),
            [PROJECT_2_ID]: clone(runsP2Fixture as unknown as RunRow[]),
        },
        crawls: { [PROJECT_ID]: [crawl], [PROJECT_2_ID]: [] },
        consolidations: { [PROJECT_ID]: [], [PROJECT_2_ID]: [] },
        positions: {
            [PROJECT_ID]: { consolidation: null, positions: [], history: [], runs_since_last: 1 },
            [PROJECT_2_ID]: { consolidation: null, positions: [], history: [], runs_since_last: 0 },
        },
        jobs: [],
        actions: {},
    };
}

export let mockState: MockState = fresh();
export function resetMockState(): void {
    mockState = fresh();
    seq = 0;
}

const notFound = (what: string) =>
    HttpResponse.json({ detail: `Not found: ${what}` }, { status: 404 });

const validation = (loc: string[], msg: string) =>
    HttpResponse.json(
        { detail: [{ loc: ["body", ...loc], msg, type: "value_error" }] },
        { status: 422 },
    );

function project(id: string): Project | undefined {
    return mockState.projects.find((p) => p.id === id);
}

function validateProject(body: Partial<ProjectCreate>): ReturnType<typeof validation> | null {
    if (body.name !== undefined && body.name.trim().length === 0) {
        return validation(["name"], "String should have at least 1 character");
    }
    if (body.client && body.client.brand_name.trim().length === 0) {
        return validation(["client", "brand_name"], "String should have at least 1 character");
    }
    if (body.client && body.client.domains.length === 0) {
        return validation(
            ["client", "domains"],
            "List should have at least 1 item after validation",
        );
    }
    if (body.interval !== undefined && !/^(daily|weekly|monthly|\d+[hdw])$/.test(body.interval)) {
        return validation(["interval"], `Unknown interval: ${body.interval}`);
    }
    return null;
}

/** Advances a queued/running job one notch every time it is read. */
function advance(job: RunJob): RunJob {
    if (job.state === "queued") {
        job.state = "running";
        job.started_at = now();
        job.progress = {
            ...job.progress,
            phase: "audit",
            message: "Auditing prompts",
            checks_total: 8,
            batches_total: 1,
            updated_at: now(),
        };
        return job;
    }
    if (job.state === "running") {
        const pr = job.progress;
        const done = Math.min(pr.checks_total, pr.checks_done + 2);
        pr.checks_done = done;
        pr.engine_calls += 3;
        pr.percent = pr.checks_total ? Math.round((done / pr.checks_total) * 100) : 0;
        pr.message = `Prompt ${done} of ${pr.checks_total}`;
        pr.updated_at = now();
        if (done >= pr.checks_total) {
            job.state = "finished";
            job.finished_at = now();
            pr.phase = "done";
            pr.batches_done = 1;
            pr.message = "Finished";
            job.outcome = {
                project_id: job.project_id,
                started_at: job.started_at ?? now(),
                batches: 1,
                prompts_run: 4,
                run_ids: [newId()],
                statuses: ["success"],
                warnings: [],
                reason: "",
                project_run_id: newId(),
                consolidation_id: null,
            };
        }
    }
    return job;
}

export const liveHandlers = [
    http.get("/api/health", () =>
        HttpResponse.json({
            status: "ok",
            active_jobs: mockState.jobs.filter((j) => j.state === "queued" || j.state === "running")
                .length,
        }),
    ),
    http.get("/api/options", () => HttpResponse.json(optionsFixture as Options)),

    http.get("/api/projects", () => HttpResponse.json(mockState.projects)),
    http.post("/api/projects", async ({ request }) => {
        const body = (await request.json()) as ProjectCreate;
        const bad = validateProject(body);
        if (bad) return bad;
        const created = Object.assign(
            {
                engines: ["GOOGLE_AI_OVERVIEW", "CHATGPT_SEARCH", "PERPLEXITY", "GEMINI"],
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
            },
            body,
            { id: newId(), created_at: now(), updated_at: now() },
        ) as Project;
        mockState.projects.push(created);
        mockState.prompts[created.id] = [];
        mockState.results[created.id] = [];
        mockState.runs[created.id] = [];
        mockState.crawls[created.id] = [];
        mockState.positions[created.id] = {
            consolidation: null,
            positions: [],
            history: [],
            runs_since_last: 0,
        };
        return HttpResponse.json(created, { status: 201 });
    }),
    http.get("/api/projects/:id", ({ params }) => {
        const p = project(String(params.id));
        return p ? HttpResponse.json(p) : notFound(String(params.id));
    }),
    http.put("/api/projects/:id", async ({ params, request }) => {
        const p = project(String(params.id));
        if (!p) return notFound(String(params.id));
        const body = (await request.json()) as ProjectUpdate;
        const bad = validateProject(body as Partial<ProjectCreate>);
        if (bad) return bad;
        for (const [k, v] of Object.entries(body)) {
            if (v !== undefined && v !== null) (p as unknown as Record<string, unknown>)[k] = v;
        }
        p.updated_at = now();
        return HttpResponse.json(p);
    }),
    http.delete("/api/projects/:id", ({ params }) => {
        const id = String(params.id);
        if (!project(id)) return notFound(id);
        mockState.projects = mockState.projects.filter((p) => p.id !== id);
        return new HttpResponse(null, { status: 204 });
    }),

    http.get("/api/projects/:id/prompts", ({ params }) => {
        const id = String(params.id);
        if (!project(id)) return notFound(id);
        return HttpResponse.json(mockState.prompts[id] ?? []);
    }),
    http.post("/api/projects/:id/prompts/import", async ({ params, request }) => {
        const id = String(params.id);
        if (!project(id)) return notFound(id);
        const { text } = (await request.json()) as { text: string };
        const added: TrackedPrompt[] = [];
        for (const line of text.split(/\r?\n/)) {
            const [t, kw, sub] = line.split("|").map((s) => s.trim());
            if (!t || t.length < 3) continue;
            added.push(
                makePrompt(id, { prompt_text: t, keyword: kw || null, subtopic: sub || null }),
            );
        }
        if (!added.length) {
            return HttpResponse.json({ detail: "No prompts found in the text." }, { status: 400 });
        }
        mockState.prompts[id] = [...(mockState.prompts[id] ?? []), ...added];
        return HttpResponse.json(added);
    }),
    http.post("/api/projects/:id/prompts", async ({ params, request }) => {
        const id = String(params.id);
        if (!project(id)) return notFound(id);
        const body = (await request.json()) as TrackedPromptCreate;
        if (!body.prompt_text || body.prompt_text.trim().length < 3) {
            return validation(["prompt_text"], "String should have at least 3 characters");
        }
        const created = makePrompt(id, body);
        mockState.prompts[id] = [...(mockState.prompts[id] ?? []), created];
        return HttpResponse.json(created, { status: 201 });
    }),
    http.put("/api/projects/:id/prompts/:tid", async ({ params, request }) => {
        const id = String(params.id);
        const list = mockState.prompts[id];
        const pr = list?.find((x) => x.id === String(params.tid));
        if (!pr) return notFound(String(params.tid));
        const body = (await request.json()) as TrackedPromptUpdate;
        if (body.clear_overrides) {
            pr.interval = null;
            pr.engines = null;
            pr.samples_per_engine = null;
        }
        for (const [k, v] of Object.entries(body)) {
            if (k === "clear_overrides" || v === undefined) continue;
            if (
                v === null &&
                !["keyword", "subtopic", "interval", "engines", "samples_per_engine"].includes(k)
            )
                continue;
            (pr as unknown as Record<string, unknown>)[k] = v;
        }
        pr.updated_at = now();
        return HttpResponse.json(pr);
    }),
    http.delete("/api/projects/:id/prompts/:tid", ({ params }) => {
        const id = String(params.id);
        const before = mockState.prompts[id]?.length ?? 0;
        mockState.prompts[id] = (mockState.prompts[id] ?? []).filter(
            (x) => x.id !== String(params.tid),
        );
        if ((mockState.prompts[id]?.length ?? 0) === before) return notFound(String(params.tid));
        return new HttpResponse(null, { status: 204 });
    }),

    http.get("/api/projects/:id/results", ({ params }) => {
        const id = String(params.id);
        if (!project(id)) return notFound(id);
        return HttpResponse.json(mockState.results[id] ?? []);
    }),
    http.post("/api/projects/:id/run", async ({ params, request }) => {
        const id = String(params.id);
        const p = project(id);
        if (!p) return notFound(id);
        const body = ((await request.json().catch(() => null)) as RunRequest | null) ?? {
            force: false,
            prompt_ids: [],
            engines: null,
        };
        const existing = mockState.jobs.find(
            (j) =>
                j.project_id === id &&
                (j.state === "queued" || j.state === "running") &&
                JSON.stringify(j.request) === JSON.stringify(body),
        );
        if (existing) return HttpResponse.json(existing, { status: 202 });
        const job: RunJob = {
            id: newId(),
            project_id: id,
            project_name: p.name,
            request: {
                force: body.force ?? false,
                prompt_ids: body.prompt_ids ?? [],
                engines: body.engines ?? null,
            },
            state: "queued",
            position: mockState.jobs.filter((j) => j.state === "queued").length,
            created_at: now(),
            started_at: null,
            finished_at: null,
            progress: {
                phase: "queued",
                message: "",
                checks_done: 0,
                checks_total: 0,
                batches_done: 0,
                batches_total: 0,
                engine_calls: 0,
                percent: 0,
                updated_at: now(),
            },
            outcome: null,
            error: null,
        };
        mockState.jobs.unshift(job);
        return HttpResponse.json(job, { status: 202 });
    }),
    http.get("/api/projects/:id/jobs", ({ params }) => {
        const id = String(params.id);
        if (!project(id)) return notFound(id);
        return HttpResponse.json(mockState.jobs.filter((j) => j.project_id === id));
    }),
    http.get("/api/jobs", ({ request }) => {
        const active = new URL(request.url).searchParams.get("active") === "true";
        const list = active
            ? mockState.jobs.filter((j) => j.state === "queued" || j.state === "running")
            : mockState.jobs;
        return HttpResponse.json(list);
    }),
    http.get("/api/jobs/:jobId", ({ params }) => {
        const job = mockState.jobs.find((j) => j.id === String(params.jobId));
        if (!job) return notFound(String(params.jobId));
        return HttpResponse.json(advance(job));
    }),
    http.get("/api/projects/:id/runs", ({ params }) => {
        const id = String(params.id);
        if (!project(id)) return notFound(id);
        return HttpResponse.json(mockState.runs[id] ?? []);
    }),
    http.get("/api/projects/:id/crawls", ({ params }) => {
        const id = String(params.id);
        if (!project(id)) return notFound(id);
        return HttpResponse.json(mockState.crawls[id] ?? []);
    }),
    http.post("/api/projects/:id/consolidate", async ({ params, request }) => {
        const id = String(params.id);
        const p = project(id);
        if (!p) return notFound(id);
        const crawls = mockState.crawls[id] ?? [];
        if (!crawls.length) {
            return HttpResponse.json(
                { detail: "No crawl of this project has been recorded yet." },
                { status: 400 },
            );
        }
        const body =
            ((await request.json().catch(() => null)) as {
                window_runs?: number | null;
                note?: string;
            } | null) ?? {};
        const window = body.window_runs ?? p.consolidation_runs;
        const used = crawls.slice(-window);
        const c: Consolidation = {
            id: newId(),
            project_id: id,
            consolidated_at: now(),
            window_runs: window,
            project_run_ids: used.map((x) => x.id),
            run_ids: used.flatMap((x) => x.run_ids),
            first_run_at: used[0]?.started_at ?? null,
            last_run_at: used[used.length - 1]?.finished_at ?? null,
            prompts: (mockState.results[id] ?? []).length,
            trigger: "manual",
            note: body.note ?? "",
            positions_count: 0,
        };
        const positions = buildPositions(id, c);
        c.positions_count = positions.length;
        const history = [c, ...(mockState.consolidations[id] ?? [])];
        mockState.consolidations[id] = history;
        mockState.positions[id] = { consolidation: c, positions, history, runs_since_last: 0 };
        return HttpResponse.json(c);
    }),
    http.get("/api/projects/:id/positions", ({ params, request }) => {
        const id = String(params.id);
        if (!project(id)) return notFound(id);
        const cid = new URL(request.url).searchParams.get("consolidation_id");
        const view = mockState.positions[id] ?? {
            consolidation: null,
            positions: [],
            history: [],
            runs_since_last: 0,
        };
        if (cid && view.consolidation && view.consolidation.id !== cid) {
            const c = view.history.find((x) => x.id === cid);
            if (!c) return notFound(cid);
            return HttpResponse.json({
                ...view,
                consolidation: c,
                positions: buildPositions(id, c),
            });
        }
        return HttpResponse.json(view);
    }),
    http.get("/api/costs", ({ request }) => {
        const url = new URL(request.url);
        const pid = url.searchParams.get("project_id");
        const exclude = url.searchParams.get("exclude_source");
        const report = clone((pid ? costsProjectFixture : costsFixture) as CostReport);
        if (exclude) delete report.by_source[exclude];
        return HttpResponse.json(report);
    }),
    http.get("/api/projects/:id/export", ({ params }) => {
        const id = String(params.id);
        const p = project(id);
        if (!p) return notFound(id);
        return HttpResponse.json({ project: p, prompts: mockState.prompts[id] ?? [] });
    }),
    http.get("/reports/prompt-atlas-data.json", ({ request }) => {
        const lob = new URL(request.url).searchParams.get("lob");
        const data = atlasFixture as unknown as AtlasDataset;
        if (!lob) return HttpResponse.json(data);
        const prompts = data.prompts.filter((p) => p.lob === lob);
        const ids = new Set(prompts.map((p) => p.prompt_id));
        return HttpResponse.json({
            ...data,
            meta: { ...data.meta, lob },
            prompts,
            snapshots: data.snapshots.filter((s) => ids.has(s.prompt_id)),
            runs: data.runs.filter((r) => r.lob === lob),
        });
    }),
];

/** Insight routes, derived deterministically from the stored results (see insights.ts). */
export const plannedHandlers = [
    http.get("/api/projects/:id/insights", async ({ params }) => {
        const id = String(params.id);
        const p = project(id);
        if (!p) return notFound(id);
        await delay(30);
        const insights = buildInsights(
            p,
            mockState.results[id] ?? [],
            mockState.positions[id] ?? null,
            mockState.crawls[id] ?? [],
        );
        const overrides = mockState.actions[id] ?? {};
        insights.actions = insights.actions.map((a) => applyUpdate(a, overrides[a.id]));
        return HttpResponse.json(insights satisfies Insights);
    }),
    http.put("/api/projects/:id/actions/:actionId", async ({ params, request }) => {
        const id = String(params.id);
        const p = project(id);
        if (!p) return notFound(id);
        const body = (await request.json()) as ActionUpdate;
        const insights = buildInsights(
            p,
            mockState.results[id] ?? [],
            mockState.positions[id] ?? null,
            mockState.crawls[id] ?? [],
        );
        const action = insights.actions.find((a) => a.id === String(params.actionId));
        if (!action) return notFound(String(params.actionId));
        const bucket = (mockState.actions[id] ??= {});
        bucket[action.id] = { ...(bucket[action.id] ?? {}), ...body };
        const merged = applyUpdate(action, bucket[action.id]);
        return HttpResponse.json(merged);
    }),
    http.get("/api/projects/:id/samples", ({ params, request }) => {
        const id = String(params.id);
        const p = project(id);
        if (!p) return notFound(id);
        const q = new URL(request.url).searchParams;
        const promptId = q.get("prompt_id") ?? "";
        const engine = (q.get("engine") ?? "") as Engine;
        const samples: AnswerSample[] = buildSamples(
            p,
            mockState.results[id] ?? [],
            promptId,
            engine,
        );
        return HttpResponse.json(samples);
    }),
];

export const handlers = [...liveHandlers, ...plannedHandlers];

/** Status may be null in an update body; the card keeps its current value then. */
function applyUpdate(action: ActionCard, update: ActionUpdate | undefined): ActionCard {
    if (!update) return action;
    return {
        ...action,
        status: update.status ?? action.status,
        owner: update.owner === undefined ? action.owner : update.owner,
        note: update.note === undefined ? action.note : update.note,
    };
}

function makePrompt(
    projectId: string,
    body: Partial<TrackedPromptCreate> & { prompt_text: string },
): TrackedPrompt {
    return Object.assign(
        {
            keyword: null,
            subtopic: null,
            important: false,
            enabled: true,
            interval: null,
            engines: null,
            samples_per_engine: null,
        },
        body,
        {
            id: newId(),
            project_id: projectId,
            prompt_id: newId().padEnd(16, "0").slice(0, 16),
            created_at: now(),
            updated_at: now(),
        },
    ) as TrackedPrompt;
}

/** Consolidated positions computed from the latest snapshots (one crawl in the mock). */
function buildPositions(projectId: string, c: Consolidation): PositionsView["positions"] {
    const out: PositionsView["positions"] = [];
    for (const r of mockState.results[projectId] ?? []) {
        for (const [engine, sn] of Object.entries(r.snapshots)) {
            if (!sn) continue;
            const ok = sn.samples - sn.failed_samples;
            const dist: Record<string, number> = {};
            if (sn.client_best_rank) dist[String(sn.client_best_rank)] = sn.client_cited_samples;
            if (ok - sn.client_cited_samples > 0) dist["not cited"] = ok - sn.client_cited_samples;
            const share: Record<string, number> = {};
            sn.cited_domains.forEach((d, i) => {
                share[d] = Math.max(0.1, 1 - i * 0.15);
            });
            out.push({
                prompt_id: r.prompt.prompt_id,
                engine: engine as Engine,
                runs: c.run_ids.length || 1,
                first_run_at: c.first_run_at ?? sn.captured_at,
                last_run_at: c.last_run_at ?? sn.captured_at,
                samples: sn.samples,
                failed_samples: sn.failed_samples,
                cited_samples: sn.client_cited_samples,
                citation_rate: sn.client_citation_rate,
                cited: sn.client_cited,
                mention_samples: Math.round(sn.mention_rate * ok),
                mention_rate: sn.mention_rate,
                mentioned: sn.mention_rate >= 0.5,
                best_rank: sn.client_best_rank,
                mean_rank: sn.client_mean_rank,
                rank_distribution: dist,
                cited_domain_share: share,
                competitor_citations: sn.competitor_citations,
                organic_prompt_best: r.organic_prompt?.client_position ?? null,
                organic_prompt_mean: r.organic_prompt?.client_position ?? null,
                organic_keyword_best: r.organic_keyword?.client_position ?? null,
                organic_keyword_mean: r.organic_keyword?.client_position ?? null,
            });
        }
    }
    return out;
}
