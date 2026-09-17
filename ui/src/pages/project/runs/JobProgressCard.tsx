/**
 * Live progress of one job: checks (prompt × platform) are the unit, paid
 * calls a counter, elapsed ticks every second while active.
 */
import { useEffect, useState } from "react";
import { Alert, Button, Card, Collapse, Space, Tag, Typography } from "antd";
import { motion } from "framer-motion";
import type { RunJob } from "@/api/endpoints";
import { isActiveJob } from "@/api/queries";
import { fmtDuration } from "@/app/format";
import { useUiStore } from "@/store/ui";

function useNow(active: boolean): number {
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        if (!active) return;
        const t = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(t);
    }, [active]);
    return now;
}

export function JobProgressCard({ job }: { job: RunJob }) {
    const active = isActiveJob(job);
    const hideJob = useUiStore((s) => s.hideJob);
    const now = useNow(active);
    const pr = job.progress;
    const known = pr.checks_total > 0;
    const started = job.started_at ? new Date(job.started_at).getTime() : null;
    const end = job.finished_at ? new Date(job.finished_at).getTime() : now;
    const headline =
        job.state === "queued"
            ? `Queued${job.position ? ` · ${job.position} ahead` : " · starting next"}`
            : job.state === "running"
              ? `Running · ${pr.phase.replace(/_/g, " ")}`
              : job.state === "finished"
                ? "Run finished"
                : "Run failed";
    const color =
        job.state === "failed"
            ? "var(--ant-color-error)"
            : job.state === "finished"
              ? "var(--ant-color-success)"
              : "var(--ant-color-primary)";
    const out = job.outcome;
    const bad = job.state === "failed" || (out?.statuses ?? []).some((s) => s !== "success");

    return (
        <Card
            data-testid="job-progress"
            style={{ borderLeft: `4px solid ${color}` }}
            styles={{ body: { padding: "14px 18px" } }}
        >
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                {active && <span className="pe-dot" />}
                <Typography.Text strong>{headline}</Typography.Text>
                {known && (
                    <Typography.Text type="secondary" className="pe-num">
                        {Math.round(pr.percent)}%
                    </Typography.Text>
                )}
                <div style={{ flex: 1 }} />
                {!active && (
                    <Button size="small" onClick={() => hideJob(job.id)}>
                        Hide
                    </Button>
                )}
            </div>
            <div
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={known ? Math.round(pr.percent) : undefined}
                aria-label="Run progress"
                style={{
                    height: 10,
                    borderRadius: 999,
                    background: "var(--ant-color-fill-secondary)",
                    overflow: "hidden",
                    margin: "10px 0",
                }}
            >
                <motion.div
                    initial={false}
                    animate={{ width: `${active && !known ? 30 : pr.percent}%` }}
                    transition={{ type: "spring", stiffness: 120, damping: 20 }}
                    style={{ height: "100%", background: color, borderRadius: 999 }}
                />
            </div>
            <Space size={[16, 4]} wrap style={{ fontSize: 12 }}>
                <span>
                    Checks{" "}
                    <b className="pe-num">
                        {pr.checks_done}/{pr.checks_total || "?"}
                    </b>{" "}
                    <span className="pe-muted">(prompt × platform)</span>
                </span>
                <span>
                    Batches{" "}
                    <b className="pe-num">
                        {pr.batches_done}/{pr.batches_total || "?"}
                    </b>
                </span>
                <span>
                    Paid engine calls <b className="pe-num">{pr.engine_calls}</b>
                </span>
                {started && (
                    <span>
                        Elapsed <b className="pe-num">{fmtDuration(end - started)}</b>
                    </span>
                )}
            </Space>
            {pr.message && (
                <Typography.Paragraph type="secondary" style={{ margin: "6px 0 0", fontSize: 12 }}>
                    {pr.message}
                </Typography.Paragraph>
            )}
            {job.error && (
                <Alert type="error" showIcon message={job.error} style={{ marginTop: 8 }} />
            )}
            {out && (
                <div style={{ marginTop: 8, fontSize: 12 }}>
                    <Tag color={bad ? "warning" : "success"}>
                        {out.batches
                            ? `Ran ${out.batches} batch(es), ${out.prompts_run} prompt(s): ${out.statuses.join(", ")}`
                            : `Nothing to do (${out.reason || "nothing due"})`}
                    </Tag>
                    {out.consolidation_id && <Tag color="processing">Consolidated</Tag>}
                    {out.warnings.length > 0 && (
                        <Collapse
                            ghost
                            size="small"
                            items={[
                                {
                                    key: "w",
                                    label: `${out.warnings.length} warning(s)`,
                                    children: (
                                        <ul style={{ margin: 0, paddingLeft: 18 }}>
                                            {out.warnings.map((w) => (
                                                <li key={w}>{w}</li>
                                            ))}
                                        </ul>
                                    ),
                                },
                            ]}
                        />
                    )}
                </div>
            )}
        </Card>
    );
}
