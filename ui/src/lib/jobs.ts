/**
 * Which job a project page should watch: the one the analyst chose, else the
 * first queued/running job the server reports (so a reload re-attaches).
 */
import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { invalidateProjectData, useJob, useProjectJobs, isActiveJob } from "@/api/queries";
import type { RunJob } from "@/api/endpoints";
import { useUiStore } from "@/store/ui";

export function useWatchedJob(projectId: string | undefined): {
    job: RunJob | undefined;
    activeJob: RunJob | undefined;
    watch: (jobId: string) => void;
} {
    const { data: jobs } = useProjectJobs(projectId);
    const watched = useUiStore((s) => (projectId ? s.watchedJobs[projectId] : undefined));
    const setWatched = useUiStore((s) => s.setWatchedJob);
    const activeJob = jobs?.find(isActiveJob);
    const candidate = watched ?? activeJob?.id ?? null;
    const { data: job } = useJob(candidate, {
        initialData: candidate ? jobs?.find((j) => j.id === candidate) : undefined,
    });
    useEffect(() => {
        if (projectId && !watched && activeJob) setWatched(projectId, activeJob.id);
    }, [projectId, watched, activeJob, setWatched]);
    // The moment the watched job leaves the active state, refresh what it changed
    // instead of waiting for the 5 s active-jobs poll.
    const client = useQueryClient();
    const wasActive = useRef(false);
    useEffect(() => {
        const active = isActiveJob(job);
        if (wasActive.current && !active && projectId)
            void invalidateProjectData(client, projectId);
        wasActive.current = active;
    }, [job, client, projectId]);
    return {
        job,
        activeJob,
        watch: (jobId) => {
            if (projectId) setWatched(projectId, jobId);
        },
    };
}

export function describeRequest(job: RunJob): string {
    const parts = [
        job.request.force ? "forced" : "due only",
        job.request.prompt_ids?.length ? `${job.request.prompt_ids.length} prompt(s)` : null,
        job.request.engines?.length ? job.request.engines.join(", ") : null,
    ];
    return parts.filter(Boolean).join(" · ");
}
