/**
 * One function per route. Return types are the generated OpenAPI shapes.
 */
import { http, type RequestOptions, type Schemas } from "./client";

/**
 * Pydantic serialises every field of a response model, but fields declared with
 * `default_factory` carry no `default` in the OpenAPI document and are therefore
 * emitted as optional. `Complete` removes that optionality on response shapes
 * only; request bodies keep the generated (partial) types.
 */
export type Complete<T> = T extends (infer U)[]
    ? Complete<U>[]
    : T extends object
      ? { [K in keyof T]-?: Complete<T[K]> }
      : T;

export type Project = Complete<Schemas["Project"]>;
export type ProjectAccess = Complete<Schemas["ProjectAccess"]>;
export type ProjectCredentials = Schemas["ProjectCredentials"];
export type ProjectCreate = Schemas["ProjectCreate"];
export type ProjectUpdate = Schemas["ProjectUpdate"];
export type ClientProfile = Complete<Schemas["ClientProfile"]>;
export type TrackedPrompt = Complete<Schemas["TrackedPrompt"]>;
export type TrackedPromptCreate = Schemas["TrackedPromptCreate"];
/** `clear_overrides` defaults to false on the server, so the body may omit it. */
export type TrackedPromptUpdate = Omit<Schemas["TrackedPromptUpdate"], "clear_overrides"> & {
    clear_overrides?: boolean;
};
export type PromptResult = Complete<Schemas["PromptResult"]>;
export type CitationSnapshot = Complete<Schemas["CitationSnapshot"]>;
export type Citation = Complete<Schemas["Citation"]>;
export type MentionSnippet = Complete<Schemas["MentionSnippet"]>;
export type OrganicRankSnapshot = Complete<Schemas["OrganicRankSnapshot"]>;
export type RunJob = Complete<Schemas["RunJob"]>;
export type RunProgress = Complete<Schemas["RunProgress"]>;
export type RunOutcome = Complete<Schemas["RunOutcome"]>;
export type RunRequest = Schemas["RunRequest"];
export type RunRow = Complete<Schemas["RunRow"]>;
export type ProjectRunRecord = Complete<Schemas["ProjectRunRecord"]>;
export type Consolidation = Complete<Schemas["Consolidation"]>;
export type ConsolidateRequest = Schemas["ConsolidateRequest"];
export type ConsolidatedPosition = Complete<Schemas["ConsolidatedPosition"]>;
export type PositionsView = Complete<Schemas["PositionsView"]>;
export type CostReport = Complete<Schemas["CostReport"]>;
export type VendorCost = Complete<Schemas["VendorCost"]>;
export type RunCost = Complete<Schemas["RunCost"]>;
export type CostRecommendation = Complete<Schemas["CostRecommendation"]>;
export type Engine = Schemas["Engine"];
export type JobState = Schemas["JobState"];

// Insight routes (cycle 0011).
export type Insights = Complete<Schemas["InsightsView"]>;
export type InsightBasis = Complete<Schemas["InsightBasis"]>;
export type EngineHealth = Complete<Schemas["EngineHealth"]>;
export type SentimentProfile = Complete<Schemas["SentimentProfile"]>;
export type SentimentCoverage = Complete<Schemas["SentimentCoverage"]>;
export type MentionContext = Complete<Schemas["MentionContext"]>;
export type InsightChange = Complete<Schemas["InsightChange"]>;
export type ActionCard = Complete<Schemas["ActionCard"]>;
export type ActionEvidence = Complete<Schemas["ActionEvidence"]>;
export type EvidenceQuote = Complete<Schemas["EvidenceQuote"]>;
export type ActionUpdate = Schemas["ActionUpdate"];
export type FanoutQuery = Complete<Schemas["FanoutQuery"]>;
export type ClaimEntry = Complete<Schemas["ClaimEntry"]>;
export type TrustShare = Complete<Schemas["TrustShare"]>;
export type RejectedPage = Complete<Schemas["RejectedPage"]>;
export type PageInventory = Complete<Schemas["PageInventory"]>;
export type PlacementProfile = Complete<Schemas["PlacementProfile"]>;
export type FreshnessProfile = Complete<Schemas["FreshnessProfile"]>;
export type AnswerSample = Complete<Schemas["AnswerSample"]>;
export type CitationClaim = Complete<Schemas["CitationClaim"]>;
export type SourceSnippet = Complete<Schemas["SourceSnippet"]>;
/** `verdict` is a plain string in the schema; these are the values the server emits. */
export type HealthVerdict = "winning" | "present" | "invisible" | "losing";

/** `/api/health` is declared as a free-form object; shape from `control_plane/app.py`. */
export interface Health {
    status: string;
    active_jobs: number;
}

/** `/api/options` is declared as a free-form object; shape from `control_plane/app.py`. */
export interface Options {
    engines: { value: Engine; label: string }[];
    intervals: { value: string; label: string }[];
    models: Partial<Record<Engine, { value: string; label: string }[]>>;
}

/** `/reports/prompt-atlas-data.json`; shape from `prompt_tracking/atlas_export.py`. */
export interface AtlasDataset {
    meta: {
        source: string;
        brand_name: string;
        lob: string;
        aliases: string[];
        domains: string[];
        competitor_domains: string[];
        exported_at: string;
    };
    prompts: AtlasPrompt[];
    snapshots: AtlasSnapshot[];
    runs: AtlasRun[];
}

export interface AtlasPrompt {
    prompt_id: string;
    lob: string;
    brand_name: string;
    prompt_text: string;
    prompt_type: string;
    core_keyword: string;
    subtopic: string;
    search_volume: number;
    search_intent: string;
    decision_stage: string;
    mapped_url: string | null;
    last_seen: string;
    content_gap: boolean;
    created_at: string;
    verdict?: string | null;
    verdict_reason?: string | null;
}

export interface AtlasSnapshot {
    prompt_id: string;
    run_id: string;
    engine: Engine;
    model: string;
    captured_at: string;
    samples: number;
    failed_samples: number;
    web_trigger_rate: number;
    client_cited_samples: number;
    client_citation_rate: number;
    client_cited: boolean;
    client_best_rank: number | null;
    client_mean_rank: number | null;
    cited_domains: string[];
    competitor_citations: Record<string, number>;
    answer_excerpt: string;
    mention_rate: number;
    mention_snippets: MentionSnippet[];
    competitor_mentions: Record<string, number>;
    citation_links: Citation[];
    client_urls: string[];
}

export interface AtlasRun {
    run_id: string;
    lob: string;
    brand_name: string;
    started_at: string;
    finished_at: string;
    prompts_selected: number;
    engine_calls: number;
    failed_engine_calls: number;
    estimated_cost_usd: number;
    semrush_units: number;
    report_path: string | null;
    warnings: string[];
}

const p = (id: string) => `/api/projects/${encodeURIComponent(id)}`;

export const endpoints = {
    health: (o?: RequestOptions) => http.get<Health>("/api/health", o),
    options: (o?: RequestOptions) => http.get<Options>("/api/options", o),

    projects: (o?: RequestOptions) => http.get<Project[]>("/api/projects", o),
    project: (id: string, o?: RequestOptions) => http.get<Project>(p(id), o),
    createProject: (body: ProjectCreate) => http.post<Project>("/api/projects", body),
    updateProject: (id: string, body: ProjectUpdate) => http.put<Project>(p(id), body),
    deleteProject: (id: string) => http.del(p(id)),
    /** What the presented credential may do; `headers` probes one before it is stored. */
    projectAccess: (id: string, o?: RequestOptions) =>
        http.get<ProjectAccess>(`${p(id)}/access`, o),
    setProjectCredentials: (id: string, body: ProjectCredentials) =>
        http.put<ProjectAccess>(`${p(id)}/credentials`, body),

    prompts: (id: string, o?: RequestOptions) => http.get<TrackedPrompt[]>(`${p(id)}/prompts`, o),
    addPrompt: (id: string, body: TrackedPromptCreate) =>
        http.post<TrackedPrompt>(`${p(id)}/prompts`, body),
    importPrompts: (id: string, text: string) =>
        http.post<TrackedPrompt[]>(`${p(id)}/prompts/import`, { text }),
    updatePrompt: (id: string, tid: string, body: TrackedPromptUpdate) =>
        http.put<TrackedPrompt>(`${p(id)}/prompts/${encodeURIComponent(tid)}`, body),
    deletePrompt: (id: string, tid: string) =>
        http.del(`${p(id)}/prompts/${encodeURIComponent(tid)}`),

    results: (id: string, o?: RequestOptions) => http.get<PromptResult[]>(`${p(id)}/results`, o),
    run: (id: string, body: RunRequest) => http.post<RunJob>(`${p(id)}/run`, body),
    projectJobs: (id: string, o?: RequestOptions) => http.get<RunJob[]>(`${p(id)}/jobs`, o),
    jobs: (active: boolean, o?: RequestOptions) =>
        http.get<RunJob[]>("/api/jobs", { ...o, query: { active } }),
    job: (jobId: string, o?: RequestOptions) =>
        http.get<RunJob>(`/api/jobs/${encodeURIComponent(jobId)}`, o),
    runs: (id: string, o?: RequestOptions) => http.get<RunRow[]>(`${p(id)}/runs`, o),
    crawls: (id: string, o?: RequestOptions) => http.get<ProjectRunRecord[]>(`${p(id)}/crawls`, o),
    consolidate: (id: string, body: ConsolidateRequest) =>
        http.post<Consolidation>(`${p(id)}/consolidate`, body),
    positions: (id: string, consolidationId?: string | null, o?: RequestOptions) =>
        http.get<PositionsView>(`${p(id)}/positions`, {
            ...o,
            query: { consolidation_id: consolidationId ?? undefined },
        }),
    costs: (
        params: { project_id?: string; days?: number; exclude_source?: string },
        o?: RequestOptions,
    ) => http.get<CostReport>("/api/costs", { ...o, query: params }),
    exportProject: (id: string, o?: RequestOptions) =>
        http.get<{ project: Project; prompts: TrackedPrompt[] }>(`${p(id)}/export`, o),
    atlas: (lob?: string | null, o?: RequestOptions) =>
        http.get<AtlasDataset>("/reports/prompt-atlas-data.json", {
            ...o,
            query: { lob: lob ?? undefined },
        }),

    // Insight routes (cycle 0011).
    insights: (id: string, consolidationId?: string | null, o?: RequestOptions) =>
        http.get<Insights>(`${p(id)}/insights`, {
            ...o,
            query: { consolidation_id: consolidationId ?? undefined },
        }),
    updateAction: (id: string, actionId: string, body: ActionUpdate) =>
        http.put<ActionCard>(`${p(id)}/actions/${encodeURIComponent(actionId)}`, body),
    samples: (
        id: string,
        params: { prompt_id: string; engine: Engine; run_id?: string | null },
        o?: RequestOptions,
    ) => http.get<AnswerSample[]>(`${p(id)}/samples`, { ...o, query: params }),
};
