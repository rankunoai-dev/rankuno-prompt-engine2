/**
 * Mutation hooks. Each invalidates the smallest key family its change can
 * affect; prompt toggles are optimistic so a star or switch responds at once.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
    endpoints,
    type ConsolidateRequest,
    type Project,
    type ProjectCreate,
    type ProjectCredentials,
    type ProjectUpdate,
    type RunRequest,
    type TrackedPrompt,
    type TrackedPromptCreate,
    type TrackedPromptUpdate,
} from "./endpoints";
import { qk } from "./queries";
import { setToken } from "@/lib/projectAuth";

export function useCreateProject() {
    const client = useQueryClient();
    return useMutation({
        mutationFn: (body: ProjectCreate) => endpoints.createProject(body),
        onSuccess: (created, body) => {
            // Whoever sets the owner credential is its first holder: no unlock prompt
            // right after creating the project.
            if (body.credentials) {
                setToken(created.id, body.credentials.owner, body.credentials.password);
            }
            client.setQueryData(qk.project(created.id), created);
            return client.invalidateQueries({ queryKey: qk.projects() });
        },
    });
}

/** Set (claim an open project) or rotate the owner credential; the new one is kept. */
export function useSetProjectCredentials(projectId: string) {
    const client = useQueryClient();
    return useMutation({
        mutationFn: (body: ProjectCredentials) => endpoints.setProjectCredentials(projectId, body),
        onSuccess: (_access, body) => {
            setToken(projectId, body.owner, body.password);
            return Promise.all([
                client.invalidateQueries({ queryKey: qk.project(projectId) }),
                client.invalidateQueries({ queryKey: qk.projects() }),
            ]);
        },
    });
}

export function useUpdateProject() {
    const client = useQueryClient();
    return useMutation({
        mutationFn: ({ id, body }: { id: string; body: ProjectUpdate }) =>
            endpoints.updateProject(id, body),
        onSuccess: (updated) => {
            client.setQueryData(qk.project(updated.id), updated);
            client.setQueryData<Project[]>(qk.projects(), (list) =>
                list?.map((p) => (p.id === updated.id ? updated : p)),
            );
            return Promise.all([
                client.invalidateQueries({ queryKey: qk.projects() }),
                client.invalidateQueries({ queryKey: qk.results(updated.id) }),
            ]);
        },
    });
}

export function useDeleteProject() {
    const client = useQueryClient();
    return useMutation({
        mutationFn: (id: string) => endpoints.deleteProject(id),
        onSuccess: (_, id) => {
            client.removeQueries({ queryKey: qk.project(id) });
            return client.invalidateQueries({ queryKey: qk.projects() });
        },
    });
}

export function useAddPrompt(projectId: string) {
    const client = useQueryClient();
    return useMutation({
        mutationFn: (body: TrackedPromptCreate) => endpoints.addPrompt(projectId, body),
        onSuccess: () =>
            Promise.all([
                client.invalidateQueries({ queryKey: qk.prompts(projectId) }),
                client.invalidateQueries({ queryKey: qk.results(projectId) }),
            ]),
    });
}

export function useImportPrompts(projectId: string) {
    const client = useQueryClient();
    return useMutation({
        mutationFn: (text: string) => endpoints.importPrompts(projectId, text),
        onSuccess: () =>
            Promise.all([
                client.invalidateQueries({ queryKey: qk.prompts(projectId) }),
                client.invalidateQueries({ queryKey: qk.results(projectId) }),
            ]),
    });
}

/** Optimistic: the list is patched before the server answers and rolled back on error. */
export function useUpdatePrompt(projectId: string) {
    const client = useQueryClient();
    return useMutation({
        mutationFn: ({ tid, body }: { tid: string; body: TrackedPromptUpdate }) =>
            endpoints.updatePrompt(projectId, tid, body),
        onMutate: async ({ tid, body }) => {
            await client.cancelQueries({ queryKey: qk.prompts(projectId) });
            const before = client.getQueryData<TrackedPrompt[]>(qk.prompts(projectId));
            client.setQueryData<TrackedPrompt[]>(qk.prompts(projectId), (list) =>
                list?.map((p) => {
                    if (p.id !== tid) return p;
                    const patch: Partial<TrackedPrompt> = {};
                    for (const [k, v] of Object.entries(body)) {
                        if (k !== "clear_overrides" && v !== undefined) {
                            (patch as Record<string, unknown>)[k] = v;
                        }
                    }
                    const reset = body.clear_overrides
                        ? { interval: null, engines: null, samples_per_engine: null }
                        : {};
                    return { ...p, ...reset, ...patch };
                }),
            );
            return { before };
        },
        onError: (_err, _vars, ctx) => {
            if (ctx?.before) client.setQueryData(qk.prompts(projectId), ctx.before);
        },
        onSettled: () =>
            Promise.all([
                client.invalidateQueries({ queryKey: qk.prompts(projectId) }),
                client.invalidateQueries({ queryKey: qk.results(projectId) }),
            ]),
    });
}

export function useDeletePrompt(projectId: string) {
    const client = useQueryClient();
    return useMutation({
        mutationFn: (tid: string) => endpoints.deletePrompt(projectId, tid),
        onSuccess: () =>
            Promise.all([
                client.invalidateQueries({ queryKey: qk.prompts(projectId) }),
                client.invalidateQueries({ queryKey: qk.results(projectId) }),
            ]),
    });
}

export function useRunProject(projectId: string) {
    const client = useQueryClient();
    return useMutation({
        mutationFn: (body: RunRequest) => endpoints.run(projectId, body),
        onSuccess: (job) => {
            client.setQueryData(qk.job(job.id), job);
            return Promise.all([
                client.invalidateQueries({ queryKey: qk.projectJobs(projectId) }),
                client.invalidateQueries({ queryKey: qk.activeJobs() }),
            ]);
        },
    });
}

export function useConsolidate(projectId: string) {
    const client = useQueryClient();
    return useMutation({
        mutationFn: (body: ConsolidateRequest) => endpoints.consolidate(projectId, body),
        onSuccess: () =>
            Promise.all([
                client.invalidateQueries({ queryKey: ["projects", projectId, "positions"] }),
                client.invalidateQueries({ queryKey: ["projects", projectId, "insights"] }),
            ]),
    });
}
