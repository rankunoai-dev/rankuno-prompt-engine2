/**
 * One project at a glance: verdict, next crawl, consolidation progress and the
 * top action. Detail lives behind the card.
 */
import { Button, Card, Dropdown, Skeleton, Space, Tooltip, Typography } from "antd";
import { DeleteOutlined, EditOutlined, MoreOutlined, StarFilled } from "@ant-design/icons";
import { Link, useNavigate } from "react-router-dom";
import type { Project } from "@/api/endpoints";
import { useInsights, usePositions, useResults } from "@/api/queries";
import { relative } from "@/app/format";
import { EngineTag } from "@/components/EngineTag";
import { VerdictTag } from "@/components/VerdictTag";
import { summariseDue } from "@/lib/due";

interface Props {
    project: Project;
    running: boolean;
    onEdit: (p: Project) => void;
    onDelete: (p: Project) => void;
}

export function ProjectCard({ project, running, onEdit, onDelete }: Props) {
    const navigate = useNavigate();
    const { data: results } = useResults(project.id);
    const { data: positions } = usePositions(project.id);
    const { data: insights, isLoading: insightsLoading } = useInsights(project.id);
    const due = summariseDue(results);
    const runsSince = positions?.runs_since_last ?? 0;
    const window = project.consolidation_runs;
    const topAction = insights?.actions.find((a) => a.status === "open");
    const health = insights?.health ?? [];
    const winning = health.filter((h) => h.verdict === "winning").length;
    const losing = health.filter((h) => h.verdict === "losing");
    const overall = !health.length
        ? null
        : winning === health.length
          ? ("winning" as const)
          : losing.length
            ? ("losing" as const)
            : health.some((h) => h.verdict === "present" || h.verdict === "winning")
              ? ("present" as const)
              : ("invisible" as const);

    return (
        <Card
            hoverable
            onClick={() => navigate(`/projects/${project.id}/overview`)}
            style={{ height: "100%" }}
            styles={{ body: { display: "flex", flexDirection: "column", gap: 10, height: "100%" } }}
            role="link"
            aria-label={`Open ${project.name}`}
            tabIndex={0}
            onKeyDown={(e) => {
                if (e.key === "Enter") navigate(`/projects/${project.id}/overview`);
            }}
        >
            <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                    <Typography.Text strong style={{ fontSize: 16 }}>
                        {running && (
                            <span
                                className="pe-dot"
                                title="Run in progress"
                                style={{ marginRight: 8 }}
                            />
                        )}
                        {project.name}
                    </Typography.Text>
                    <div>
                        <Typography.Text type="secondary">
                            {project.client.brand_name} · {project.client.lob}
                            {project.enabled ? "" : " · paused"}
                        </Typography.Text>
                    </div>
                </div>
                <Dropdown
                    trigger={["click"]}
                    menu={{
                        items: [
                            { key: "edit", icon: <EditOutlined />, label: "Edit" },
                            {
                                key: "delete",
                                icon: <DeleteOutlined />,
                                label: "Delete",
                                danger: true,
                            },
                        ],
                        onClick: ({ key, domEvent }) => {
                            domEvent.stopPropagation();
                            if (key === "edit") onEdit(project);
                            if (key === "delete") onDelete(project);
                        },
                    }}
                >
                    <Button
                        type="text"
                        icon={<MoreOutlined />}
                        aria-label={`Actions for ${project.name}`}
                        onClick={(e) => e.stopPropagation()}
                    />
                </Dropdown>
            </div>

            <div>
                {project.engines.map((e) => (
                    <EngineTag key={e} engine={e} short />
                ))}
            </div>

            <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 16px", fontSize: 13 }}>
                <span>
                    <Typography.Text type="secondary">Every </Typography.Text>
                    {project.interval}
                </span>
                <Tooltip
                    title={
                        results
                            ? `${due.dueNow} prompt × platform checks due now (${due.neverSampled} never sampled)`
                            : "Loading…"
                    }
                >
                    <span>
                        <Typography.Text type="secondary">Next crawl </Typography.Text>
                        {!results
                            ? "…"
                            : due.dueNow
                              ? `due now (${due.promptsDue} prompts)`
                              : due.nextDueAt
                                ? relative(due.nextDueAt.toISOString())
                                : "no prompts"}
                    </span>
                </Tooltip>
                <Tooltip
                    title={`Positions consolidate automatically after every ${window} full crawls`}
                >
                    <span>
                        <Typography.Text type="secondary">Consolidation </Typography.Text>
                        {positions ? `${runsSince} of ${window} crawls` : "…"}
                    </span>
                </Tooltip>
            </div>

            <div style={{ marginTop: "auto", display: "flex", flexDirection: "column", gap: 6 }}>
                {insightsLoading ? (
                    <Skeleton active paragraph={{ rows: 1 }} title={false} />
                ) : overall ? (
                    <Space size={6} wrap>
                        <VerdictTag verdict={overall} losingTo={losing[0]?.losing_to ?? null} />
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {winning}/{health.length} platforms winning
                            {insights?.basis.low_confidence ? " · low confidence" : ""}
                        </Typography.Text>
                    </Space>
                ) : (
                    <Typography.Text type="secondary">No consolidation yet</Typography.Text>
                )}
                {topAction && (
                    <Link
                        to={`/projects/${project.id}/actions#${topAction.id}`}
                        onClick={(e) => e.stopPropagation()}
                        style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 13 }}
                    >
                        <StarFilled className="pe-star-on" style={{ marginTop: 3 }} />
                        <span className="pe-ellipsis">{topAction.title}</span>
                    </Link>
                )}
            </div>
        </Card>
    );
}
