/**
 * TanStack Query hooks: one key family per resource so mutations can
 * invalidate the minimum. Every fetch receives the query's abort signal, so
 * leaving a route cancels its in-flight requests.
 */
import {
    useMutation,
    useQuery,
    useQueryClient,
    type QueryClient,
    type UseQueryOptions,
} from "@tanstack/react-query";
import { endpoints, type ActionUpdate, type Engine, type RunJob } from "./endpoints";

export const qk = {
    health: () => ["health"] as const,
    options: () => ["options"] as const,
    projects: () => ["projects"] as const,
    project: (id: string) => ["projects", id] as const,
    prompts: (id: string) => ["projects", id, "prompts"] as const,
    results: (id: string) => ["projects", id, "results"] as const,
    runs: (id: string) => ["projects", id, "runs"] as const,
    crawls: (id: string) => ["projects", id, "crawls"] as const,
    positions: (id: string, cid: string | null) => ["projects", id, "positions", cid] as const,
    projectJobs: (id: string) => ["projects", id, "jobs"] as const,
    insights: (id: string, cid: string | null) => ["projects", id, "insights", cid] as const,
    samples: (id: string, promptId: string, engine: string, runId: string | null) =>
        ["projects", id, "samples", promptId, engine, runId] as const,
    activeJobs: () => ["jobs", "active"] as const,
    job: (jobId: string) => ["jobs", jobId] as const,
    costs: (projectId: string | undefined, days: number | undefined) =>
        ["costs", projectId ?? "all", days ?? "all"] as const,
    atlas: (lob: string | null) => ["atlas", lob ?? "all"] as const,
};

export const isActiveJob = (job: RunJob | null | undefined): boolean =>
    !!job && (job.state === "queued" || job.state === "running");

export function useOptions() {
    return useQuery({
        queryKey: qk.options(),
        queryFn: ({ signal }) => endpoints.options({ signal }),
        staleTime: Infinity,
    });
}

export function useProjects() {
    return useQuery({
        queryKey: qk.projects(),
        queryFn: ({ signal }) => endpoints.projects({ signal }),
    });
}

export function useProject(id: string | undefined) {
    return useQuery({
        queryKey: qk.project(id ?? ""),
        queryFn: ({ signal }) => endpoints.project(id!, { signal }),
        enabled: !!id,
    });
}

export function usePrompts(id: string | undefined) {
    return useQuery({
        queryKey: qk.prompts(id ?? ""),
        queryFn: ({ signal }) => endpoints.prompts(id!, { signal }),
        enabled: !!id,
    });
}

export function useResults(id: string | undefined) {
    return useQuery({
        queryKey: qk.results(id ?? ""),
        queryFn: ({ signal }) => endpoints.results(id!, { signal }),
        enabled: !!id,
    });
}

export function useRuns(id: string | undefined) {
    return useQuery({
        queryKey: qk.runs(id ?? ""),
        queryFn: ({ signal }) => endpoints.runs(id!, { signal }),
        enabled: !!id,
    });
}

export function useCrawls(id: string | undefined) {
    return useQuery({
        queryKey: qk.crawls(id ?? ""),
        queryFn: ({ signal }) => endpoints.crawls(id!, { signal }),
        enabled: !!id,
    });
}

export function usePositions(id: string | undefined, consolidationId: string | null = null) {
    return useQuery({
        queryKey: qk.positions(id ?? "", consolidationId),
        queryFn: ({ signal }) => endpoints.positions(id!, consolidationId, { signal }),
        enabled: !!id,
    });
}

export function useInsights(id: string | undefined, consolidationId: string | null = null) {
    return useQuery({
        queryKey: qk.insights(id ?? "", consolidationId),
        queryFn: ({ signal }) => endpoints.insights(id!, consolidationId, { signal }),
        enabled: !!id,
    });
}

export function useSamples(
    id: string | undefined,
    params: { prompt_id: string; engine: Engine; run_id?: string | null } | null,
) {
    return useQuery({
        queryKey: qk.samples(
            id ?? "",
            params?.prompt_id ?? "",
            params?.engine ?? "",
            params?.run_id ?? null,
        ),
        queryFn: ({ signal }) => endpoints.samples(id!, params!, { signal }),
        enabled: !!id && !!params,
    });
}

export function useProjectJobs(id: string | undefined) {
    return useQuery({
        queryKey: qk.projectJobs(id ?? ""),
        queryFn: ({ signal }) => endpoints.projectJobs(id!, { signal }),
        enabled: !!id,
    });
}

/** Header badge and rail dots: every active job, polled every 5 s. */
export function useActiveJobs() {
    return useQuery({
        queryKey: qk.activeJobs(),
        queryFn: ({ signal }) => endpoints.jobs(true, { signal }),
        refetchInterval: 5000,
    });
}

/** One job, polled every second while it is queued or running. */
export function useJob(jobId: string | null, options?: Partial<UseQueryOptions<RunJob>>) {
    return useQuery({
        queryKey: qk.job(jobId ?? ""),
        queryFn: ({ signal }) => endpoints.job(jobId!, { signal }),
        enabled: !!jobId,
        refetchInterval: (query) => (isActiveJob(query.state.data) ? 1000 : false),
        ...options,
    });
}

export function useCosts(params: { project_id?: string; days?: number }) {
    return useQuery({
        queryKey: qk.costs(params.project_id, params.days),
        queryFn: ({ signal }) => endpoints.costs(params, { signal }),
    });
}

export function useAtlas(lob: string | null) {
    return useQuery({
        queryKey: qk.atlas(lob),
        queryFn: ({ signal }) => endpoints.atlas(lob, { signal }),
    });
}

/** Everything a finished run can change for one project. */
export function invalidateProjectData(client: QueryClient, projectId: string) {
    const keys = [
        qk.results(projectId),
        qk.runs(projectId),
        qk.crawls(projectId),
        qk.projectJobs(projectId),
        ["projects", projectId, "positions"],
        ["projects", projectId, "insights"],
        ["costs"],
        ["atlas"],
    ];
    return Promise.all(keys.map((queryKey) => client.invalidateQueries({ queryKey })));
}

export function useUpdateAction(projectId: string) {
    const client = useQueryClient();
    return useMutation({
        mutationFn: ({ actionId, body }: { actionId: string; body: ActionUpdate }) =>
            endpoints.updateAction(projectId, actionId, body),
        onSuccess: () =>
            client.invalidateQueries({ queryKey: ["projects", projectId, "insights"] }),
    });
}
