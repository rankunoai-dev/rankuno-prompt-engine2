import { App, Button, Card, Popconfirm, Space, Table, Tag, Tooltip, Typography } from "antd";
import { PlayCircleOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { useOutletContext } from "react-router-dom";
import type { Project, ProjectRunRecord, RunJob, RunRow } from "@/api/endpoints";
import {
    isActiveJob,
    useCosts,
    useCrawls,
    useProjectJobs,
    useResults,
    useRuns,
} from "@/api/queries";
import { useRunProject } from "@/api/mutations";
import { fmtDateTime, fmtDuration, money } from "@/app/format";
import { askNotifyPermission } from "@/app/notify";
import { summariseDue } from "@/lib/due";
import { describeRequest, useWatchedJob } from "@/lib/jobs";
import { useUiStore } from "@/store/ui";
import { JobProgressCard } from "./runs/JobProgressCard";
import { ConsolidationPanel } from "./runs/ConsolidationPanel";

const STATE_COLOR: Record<string, string> = {
    queued: "default",
    running: "processing",
    finished: "success",
    failed: "error",
};

export function RunsPage() {
    const { project } = useOutletContext<{ project: Project }>();
    const { message } = App.useApp();
    const { data: results } = useResults(project.id);
    const { data: jobs } = useProjectJobs(project.id);
    const { data: crawls } = useCrawls(project.id);
    const { data: runs } = useRuns(project.id);
    const { data: costs } = useCosts({ project_id: project.id });
    const { job, activeJob, watch } = useWatchedJob(project.id);
    const hidden = useUiStore((s) => s.hiddenJobs);
    const run = useRunProject(project.id);
    const due = summariseDue(results);
    const busy = !!activeJob || run.isPending;

    const start = async (force: boolean) => {
        askNotifyPermission();
        try {
            const created = await run.mutateAsync({ force, prompt_ids: [], engines: null });
            watch(created.id);
            message.success(
                created.state === "queued" && created.position
                    ? `Queued behind ${created.position} other run(s).`
                    : "Run started. Progress is shown above.",
            );
        } catch (err) {
            message.error(err instanceof Error ? err.message : "Could not start the run.");
        }
    };

    const crawlCount = crawls?.filter((c) => c.full).length ?? 0;
    const perCrawl = costs && crawlCount ? costs.total_actual_usd / crawlCount : null;

    return (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Card
                title="Run"
                extra={
                    <Space>
                        <Popconfirm
                            title="Run what is due now?"
                            description={`${due.dueNow} prompt × platform checks are due. Every engine call is charged to the ledger.`}
                            okText="Run due"
                            onConfirm={() => start(false)}
                            disabled={busy}
                        >
                            <Button icon={<PlayCircleOutlined />} disabled={busy}>
                                Run due now
                            </Button>
                        </Popconfirm>
                        <Popconfirm
                            title="Run every enabled prompt on every platform?"
                            description="This ignores intervals and samples everything again. Spend is bounded by the session ceiling."
                            okText="Run everything"
                            okButtonProps={{ danger: true }}
                            onConfirm={() => start(true)}
                            disabled={busy}
                        >
                            <Button icon={<ThunderboltOutlined />} disabled={busy}>
                                Run everything now
                            </Button>
                        </Popconfirm>
                    </Space>
                }
            >
                <Space size={[24, 8]} wrap>
                    <Stat
                        label="Due now"
                        value={`${due.dueNow} checks`}
                        hint={`${due.promptsDue} prompts · ${due.neverSampled} never sampled`}
                    />
                    <Stat
                        label="Full crawls"
                        value={String(crawlCount)}
                        hint="runs that covered every prompt"
                    />
                    <Stat
                        label="Cost per crawl"
                        value={perCrawl === null ? "—" : `≈ ${money(perCrawl)}`}
                        hint={
                            costs
                                ? `${costs.calls} vendor calls, actual ${money(costs.total_actual_usd)} across this project`
                                : "no ledger rows yet"
                        }
                    />
                    {activeJob && <Tag color="processing">{activeJob.state}</Tag>}
                </Space>
            </Card>

            {job && !hidden.includes(job.id) && <JobProgressCard job={job} />}

            <ConsolidationPanel project={project} />

            <Card title="Queued and recent runs (this server session)">
                <Table<RunJob>
                    size="small"
                    rowKey="id"
                    pagination={false}
                    dataSource={jobs ?? []}
                    locale={{ emptyText: "No runs queued in this server session." }}
                    columns={[
                        {
                            title: "State",
                            dataIndex: "state",
                            render: (_, j) => (
                                <Tag color={STATE_COLOR[j.state]}>
                                    {j.state === "queued" && j.position
                                        ? `queued · ${j.position} ahead`
                                        : j.state}
                                </Tag>
                            ),
                        },
                        {
                            title: "Requested",
                            render: (_, j) => (
                                <>
                                    {fmtDateTime(j.created_at)}
                                    <div className="pe-muted" style={{ fontSize: 12 }}>
                                        {describeRequest(j)}
                                    </div>
                                </>
                            ),
                        },
                        {
                            title: "Progress",
                            width: 220,
                            render: (_, j) => (
                                <>
                                    <div
                                        style={{
                                            height: 6,
                                            background: "var(--ant-color-fill-secondary)",
                                            borderRadius: 999,
                                            overflow: "hidden",
                                        }}
                                    >
                                        <div
                                            style={{
                                                width: `${j.progress.percent}%`,
                                                height: "100%",
                                                background: "var(--ant-color-primary)",
                                            }}
                                        />
                                    </div>
                                    <span className="pe-muted pe-num" style={{ fontSize: 12 }}>
                                        {j.progress.checks_done}/{j.progress.checks_total || "?"}{" "}
                                        checks · {j.progress.engine_calls} calls
                                    </span>
                                </>
                            ),
                        },
                        {
                            title: "Duration",
                            render: (_, j) =>
                                j.started_at
                                    ? fmtDuration(
                                          (j.finished_at
                                              ? new Date(j.finished_at).getTime()
                                              : Date.now()) - new Date(j.started_at).getTime(),
                                      )
                                    : "—",
                        },
                        {
                            title: "Outcome",
                            render: (_, j) =>
                                j.error ? (
                                    <Typography.Text type="danger">{j.error}</Typography.Text>
                                ) : j.outcome ? (
                                    j.outcome.batches ? (
                                        `${j.outcome.statuses.join(", ")}${j.outcome.warnings.length ? ` · ${j.outcome.warnings.length} warning(s)` : ""}`
                                    ) : (
                                        j.outcome.reason || "nothing due"
                                    )
                                ) : (
                                    j.progress.message
                                ),
                        },
                        {
                            title: "",
                            render: (_, j) =>
                                isActiveJob(j) && j.id !== job?.id ? (
                                    <Button size="small" onClick={() => watch(j.id)}>
                                        Watch
                                    </Button>
                                ) : null,
                        },
                    ]}
                />
            </Card>

            <Card title="Crawl history">
                <Table<ProjectRunRecord>
                    size="small"
                    rowKey="id"
                    pagination={false}
                    dataSource={crawls ?? []}
                    locale={{ emptyText: "No crawl recorded yet." }}
                    columns={[
                        { title: "Started", render: (_, c) => fmtDateTime(c.started_at) },
                        { title: "Finished", render: (_, c) => fmtDateTime(c.finished_at) },
                        { title: "Prompts run", dataIndex: "prompts_run", align: "right" },
                        { title: "Batches", dataIndex: "batches", align: "right" },
                        {
                            title: "Coverage",
                            render: (_, c) => (
                                <Tooltip
                                    title={
                                        c.full
                                            ? "Counts towards the consolidation window"
                                            : "Restricted to selected prompts; does not count towards the window"
                                    }
                                >
                                    <Tag color={c.full ? "success" : "default"}>
                                        {c.full ? "full" : "partial"}
                                    </Tag>
                                </Tooltip>
                            ),
                        },
                        {
                            title: "Pipeline runs",
                            render: (_, c) => (
                                <span className="pe-mono">
                                    {c.run_ids.map((r) => r.slice(0, 8)).join(", ")}
                                </span>
                            ),
                        },
                        { title: "Statuses", render: (_, c) => c.statuses.join(", ") },
                    ]}
                />
            </Card>

            <Card title="Pipeline runs">
                <Table<RunRow>
                    size="small"
                    rowKey="run_id"
                    pagination={{ pageSize: 10, hideOnSinglePage: true }}
                    dataSource={runs ?? []}
                    locale={{
                        emptyText: "No completed pipeline run for this line of business yet.",
                    }}
                    columns={[
                        {
                            title: "Run",
                            dataIndex: "run_id",
                            render: (v: string) => <span className="pe-mono">{v}</span>,
                        },
                        { title: "Started", render: (_, r) => fmtDateTime(r.started_at) },
                        { title: "Prompts", dataIndex: "prompts_selected", align: "right" },
                        { title: "Engine calls", dataIndex: "engine_calls", align: "right" },
                        {
                            title: "Failed",
                            dataIndex: "failed_engine_calls",
                            align: "right",
                            render: (v: number) =>
                                v ? <Typography.Text type="danger">{v}</Typography.Text> : 0,
                        },
                        {
                            title: "Spend",
                            align: "right",
                            render: (_, r) => money(r.estimated_cost_usd),
                        },
                        {
                            title: "Report",
                            render: (_, r) => (
                                <span className="pe-mono" title={r.report_path ?? ""}>
                                    {r.report_path ? r.report_path.split(/[\\/]/).pop() : "—"}
                                </span>
                            ),
                        },
                    ]}
                />
            </Card>
        </Space>
    );
}

function Stat({ label, value, hint }: { label: string; value: string; hint: string }) {
    return (
        <Tooltip title={hint}>
            <span>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {label}{" "}
                </Typography.Text>
                <Typography.Text strong className="pe-num">
                    {value}
                </Typography.Text>
            </span>
        </Tooltip>
    );
}
