/**
 * Watches the active-jobs poll and, when a job leaves the active set, fetches
 * its final state and announces it: sticky toast, browser Notification when
 * the tab is hidden, flashing tab title. Then invalidates the project's data.
 */
import { useEffect, useRef } from "react";
import { App } from "antd";
import { useQueryClient } from "@tanstack/react-query";
import { endpoints, type RunJob } from "@/api/endpoints";
import { invalidateProjectData, qk, useActiveJobs } from "@/api/queries";
import { announceJobDone } from "./notify";

export function summariseJob(job: RunJob): { title: string; body: string; failed: boolean } {
    const out = job.outcome;
    if (job.state === "failed" || !out) {
        return {
            title: "Run failed",
            body: `Run failed: ${job.error ?? "unknown error"}`,
            failed: true,
        };
    }
    const failed = out.statuses.some((s) => s !== "success");
    const body = out.batches
        ? `Run finished for ${job.project_name}: ${out.prompts_run} prompt(s), ${out.statuses.join(", ")}${
              out.warnings.length ? ` · ${out.warnings.length} warning(s)` : ""
          }`
        : `Run finished for ${job.project_name}: nothing to do (${out.reason || "nothing due"}).`;
    return { title: "Run finished", body, failed };
}

export function JobAnnouncer() {
    const { notification } = App.useApp();
    const client = useQueryClient();
    const { data: active } = useActiveJobs();
    const previous = useRef<Set<string> | null>(null);

    useEffect(() => {
        if (!active) return;
        const current = new Set(active.map((j) => j.id));
        const before = previous.current;
        previous.current = current;
        if (!before) return;
        const finished = [...before].filter((id) => !current.has(id));
        for (const id of finished) {
            endpoints
                .job(id)
                .then((job) => {
                    client.setQueryData(qk.job(id), job);
                    const s = summariseJob(job);
                    notification[s.failed ? "error" : "success"]({
                        key: `job-${id}`,
                        message: s.title,
                        description: s.body,
                        duration: 0,
                    });
                    announceJobDone(s.title, s.body);
                    void invalidateProjectData(client, job.project_id);
                })
                .catch(() => undefined);
        }
    }, [active, client, notification]);

    return null;
}
