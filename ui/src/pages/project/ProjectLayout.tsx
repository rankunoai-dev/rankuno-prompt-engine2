import { Outlet, useParams } from "react-router-dom";
import { Alert, Skeleton, Tabs, Typography } from "antd";
import { useNavigate, useMatch } from "react-router-dom";
import { useProject } from "@/api/queries";
import { useEffect } from "react";
import { useUiStore } from "@/store/ui";
import { ProjectLockBadge } from "@/app/ProjectUnlock";

const TABS = [
    { key: "overview", label: "Overview" },
    { key: "battleground", label: "Battleground" },
    { key: "prompts", label: "Prompts" },
    { key: "runs", label: "Runs & spend" },
    { key: "actions", label: "Actions" },
];

export function ProjectLayout() {
    const { id } = useParams<{ id: string }>();
    const navigate = useNavigate();
    const match = useMatch("/projects/:id/:tab");
    const tab = match?.params.tab ?? "overview";
    const { data: project, isLoading, error } = useProject(id);
    const setLast = useUiStore((s) => s.setLastProjectId);
    useEffect(() => {
        if (project) setLast(project.id);
    }, [project, setLast]);

    if (isLoading) return <Skeleton active paragraph={{ rows: 6 }} />;
    if (error || !project) {
        return (
            <Alert
                type="error"
                showIcon
                message={error instanceof Error ? error.message : "Project not found"}
            />
        );
    }
    return (
        <div>
            <div
                style={{
                    display: "flex",
                    flexWrap: "wrap",
                    alignItems: "center",
                    gap: "4px 12px",
                }}
            >
                <Typography.Title level={3} style={{ margin: 0 }}>
                    {project.name}
                </Typography.Title>
                <Typography.Text type="secondary">
                    {project.client.brand_name} · {project.client.lob} · every {project.interval}
                    {project.enabled ? "" : " · paused"}
                </Typography.Text>
                <ProjectLockBadge project={project} />
            </div>
            <Tabs
                activeKey={tab}
                onChange={(k) => navigate(`/projects/${project.id}/${k}`)}
                items={TABS}
                style={{ marginTop: 8 }}
            />
            <Outlet context={{ project }} />
        </div>
    );
}
