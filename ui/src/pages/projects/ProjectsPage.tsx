import { useMemo, useState } from "react";
import { Alert, App, Button, Col, Empty, Input, Row, Skeleton, Space, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { AnimatePresence, motion } from "framer-motion";
import type { Project } from "@/api/endpoints";
import { useActiveJobs, useProjects } from "@/api/queries";
import { useDeleteProject } from "@/api/mutations";
import { ProjectCard } from "./ProjectCard";
import { ProjectForm } from "./ProjectForm";

export function ProjectsPage() {
    const { modal, message } = App.useApp();
    const { data: projects, isLoading, error } = useProjects();
    const { data: active } = useActiveJobs();
    const remove = useDeleteProject();
    const [q, setQ] = useState("");
    const [editing, setEditing] = useState<Project | null>(null);
    const [formOpen, setFormOpen] = useState(false);

    const shown = useMemo(() => {
        const needle = q.trim().toLowerCase();
        return (projects ?? []).filter(
            (p) =>
                !needle ||
                [p.name, p.client.brand_name, p.client.lob].some((s) =>
                    s.toLowerCase().includes(needle),
                ),
        );
    }, [projects, q]);

    const confirmDelete = (p: Project) => {
        modal.confirm({
            title: `Delete "${p.name}"?`,
            content:
                "The project and its prompt configuration are removed. Tracking history is kept and stays visible in the Atlas.",
            okText: "Delete",
            okButtonProps: { danger: true },
            onOk: async () => {
                try {
                    await remove.mutateAsync(p.id);
                    message.success("Project deleted. History is kept.");
                } catch (err) {
                    message.error(
                        err instanceof Error ? err.message : "Could not delete the project.",
                    );
                }
            },
        });
    };

    return (
        <div>
            <div
                style={{
                    display: "flex",
                    flexWrap: "wrap",
                    gap: 12,
                    alignItems: "center",
                    marginBottom: 16,
                }}
            >
                <Typography.Title level={3} style={{ margin: 0 }}>
                    Projects
                </Typography.Title>
                <div style={{ flex: 1 }} />
                <Input.Search
                    allowClear
                    placeholder="Search projects"
                    aria-label="Search projects"
                    value={q}
                    onChange={(e) => setQ(e.target.value)}
                    style={{ width: 260 }}
                />
                <Button
                    type="primary"
                    icon={<PlusOutlined />}
                    onClick={() => {
                        setEditing(null);
                        setFormOpen(true);
                    }}
                >
                    New project
                </Button>
            </div>

            {error && (
                <Alert type="error" showIcon message={error.message} style={{ marginBottom: 16 }} />
            )}
            {isLoading && (
                <Row gutter={[16, 16]}>
                    {[0, 1, 2].map((i) => (
                        <Col key={i} xs={24} md={12} xl={8}>
                            <Skeleton active paragraph={{ rows: 4 }} />
                        </Col>
                    ))}
                </Row>
            )}
            {!isLoading && !shown.length && (
                <Empty
                    description={
                        projects?.length
                            ? "No project matches the search."
                            : "No projects yet. Create the first one."
                    }
                >
                    {!projects?.length && (
                        <Button type="primary" onClick={() => setFormOpen(true)}>
                            New project
                        </Button>
                    )}
                </Empty>
            )}
            <Row gutter={[16, 16]}>
                <AnimatePresence initial={false}>
                    {shown.map((p) => (
                        <Col key={p.id} xs={24} md={12} xl={8} style={{ display: "flex" }}>
                            <motion.div
                                layout
                                initial={{ opacity: 0, scale: 0.98 }}
                                animate={{ opacity: 1, scale: 1 }}
                                exit={{ opacity: 0, scale: 0.98 }}
                                transition={{ duration: 0.16 }}
                                style={{ width: "100%" }}
                            >
                                <ProjectCard
                                    project={p}
                                    running={!!active?.some((j) => j.project_id === p.id)}
                                    onEdit={(proj) => {
                                        setEditing(proj);
                                        setFormOpen(true);
                                    }}
                                    onDelete={confirmDelete}
                                />
                            </motion.div>
                        </Col>
                    ))}
                </AnimatePresence>
            </Row>
            <Space />
            <ProjectForm open={formOpen} project={editing} onClose={() => setFormOpen(false)} />
        </div>
    );
}
