import { Menu, Typography } from "antd";
import {
    CompassOutlined,
    DashboardOutlined,
    DollarOutlined,
    FolderOpenOutlined,
    LineChartOutlined,
} from "@ant-design/icons";
import { Link, useLocation } from "react-router-dom";
import { useActiveJobs, useProjects } from "@/api/queries";
import { useUiStore } from "@/store/ui";

export function Rail({ collapsed }: { collapsed: boolean }) {
    const location = useLocation();
    const { data: projects } = useProjects();
    const { data: active } = useActiveJobs();
    const lastProjectId = useUiStore((s) => s.lastProjectId);
    const activeId = lastProjectId ?? projects?.[0]?.id;
    const running = active?.length ?? 0;
    const path = location.pathname;

    const selected = path.startsWith("/atlas")
        ? "atlas"
        : path.startsWith("/trends")
          ? "trends"
          : path.startsWith("/costs")
            ? "costs"
            : path === "/projects" || path === "/projects/"
              ? "projects"
              : "dashboard";

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
            <div
                style={{
                    height: 52,
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    padding: collapsed ? "0 0 0 20px" : "0 16px",
                    borderBottom: "1px solid var(--ant-color-border-secondary)",
                }}
            >
                <span
                    aria-hidden
                    style={{
                        width: 24,
                        height: 24,
                        borderRadius: 6,
                        background: "var(--ant-color-primary)",
                        color: "#fff",
                        display: "grid",
                        placeItems: "center",
                        fontSize: 12,
                        fontWeight: 700,
                        flex: "none",
                    }}
                >
                    PE
                </span>
                {!collapsed && (
                    <Typography.Text strong style={{ whiteSpace: "nowrap" }}>
                        Prompt Engine
                    </Typography.Text>
                )}
            </div>
            <Menu
                mode="inline"
                selectedKeys={[selected]}
                style={{ borderInlineEnd: 0, paddingTop: 8 }}
                items={[
                    {
                        key: "dashboard",
                        icon: <DashboardOutlined />,
                        label: (
                            <Link to={activeId ? `/projects/${activeId}/overview` : "/projects"}>
                                Dashboard
                            </Link>
                        ),
                    },
                    {
                        key: "projects",
                        icon: <FolderOpenOutlined />,
                        label: (
                            <Link to="/projects">
                                Projects
                                {running > 0 && (
                                    <span
                                        className="pe-dot"
                                        title={`${running} run${running > 1 ? "s" : ""} in progress`}
                                        style={{ marginLeft: 8 }}
                                    />
                                )}
                            </Link>
                        ),
                    },
                    {
                        key: "atlas",
                        icon: <CompassOutlined />,
                        label: <Link to="/atlas">Atlas</Link>,
                    },
                    {
                        key: "trends",
                        icon: <LineChartOutlined />,
                        label: <Link to="/trends">Trends</Link>,
                    },
                    {
                        key: "costs",
                        icon: <DollarOutlined />,
                        label: <Link to="/costs">Costs</Link>,
                    },
                ]}
            />
        </div>
    );
}
