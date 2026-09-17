/** The full checklist: every action card, grouped by type, with filters and outcomes. */
import { useEffect, useMemo, useState } from "react";
import { Alert, Card, Collapse, Empty, Select, Skeleton, Space, Typography } from "antd";
import { AnimatePresence } from "framer-motion";
import { useLocation, useOutletContext } from "react-router-dom";
import type { Project } from "@/api/endpoints";
import { useInsights } from "@/api/queries";
import { ACTION_TYPE_LABEL, ActionCardView } from "@/components/ActionCardView";
import { ENGINE_LABEL } from "@/app/theme";

export function ActionsPage() {
    const { project } = useOutletContext<{ project: Project }>();
    const { data: insights, isLoading, error } = useInsights(project.id);
    const location = useLocation();
    const [platform, setPlatform] = useState<string | null>(null);
    const [subtopic, setSubtopic] = useState<string | null>(null);
    const [status, setStatus] = useState<"open" | "done" | null>(null);
    const [outcome, setOutcome] = useState<string | null>(null);

    // Deep link: /actions#<action id> scrolls to that card once loaded.
    useEffect(() => {
        if (!insights || !location.hash) return;
        const el = document.getElementById(decodeURIComponent(location.hash.slice(1)));
        el?.scrollIntoView({ block: "center" });
    }, [insights, location.hash]);

    const actions = useMemo(() => insights?.actions ?? [], [insights]);
    const subtopics = useMemo(() => [...new Set(actions.map((a) => a.subtopic))].sort(), [actions]);
    const shown = actions.filter(
        (a) =>
            (!platform || a.engine === platform) &&
            (!subtopic || a.subtopic === subtopic) &&
            (!status || a.status === status) &&
            (!outcome || a.outcome === outcome),
    );
    const openByType = new Map<string, typeof shown>();
    for (const a of shown.filter((x) => x.status === "open")) {
        openByType.set(a.type, [...(openByType.get(a.type) ?? []), a]);
    }
    const done = shown.filter((a) => a.status === "done");

    if (isLoading) return <Skeleton active paragraph={{ rows: 8 }} />;
    if (error) return <Alert type="error" showIcon message={error.message} />;

    return (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Card size="small">
                <Space wrap>
                    <Select
                        allowClear
                        placeholder="Platform"
                        aria-label="Platform"
                        style={{ width: 200 }}
                        value={platform}
                        onChange={(v) => setPlatform(v ?? null)}
                        options={project.engines.map((e) => ({
                            value: e,
                            label: ENGINE_LABEL[e] ?? e,
                        }))}
                    />
                    <Select
                        allowClear
                        placeholder="Subtopic"
                        aria-label="Subtopic"
                        style={{ width: 200 }}
                        value={subtopic}
                        onChange={(v) => setSubtopic(v ?? null)}
                        options={subtopics.map((s) => ({ value: s, label: s }))}
                    />
                    <Select
                        allowClear
                        placeholder="Status"
                        aria-label="Status"
                        style={{ width: 130 }}
                        value={status}
                        onChange={(v) => setStatus(v ?? null)}
                        options={[
                            { value: "open", label: "Open" },
                            { value: "done", label: "Done" },
                        ]}
                    />
                    <Select
                        allowClear
                        placeholder="Outcome"
                        aria-label="Outcome"
                        style={{ width: 160 }}
                        value={outcome}
                        onChange={(v) => setOutcome(v ?? null)}
                        options={["pending", "improved", "unchanged", "regressed"].map((o) => ({
                            value: o,
                            label: o,
                        }))}
                    />
                    <Typography.Text type="secondary">
                        {shown.length} of {actions.length} cards
                        {insights?.basis.low_confidence ? " · low confidence basis" : ""}
                    </Typography.Text>
                </Space>
            </Card>

            {!shown.length && <Empty description="No action matches these filters." />}

            {[...openByType.entries()].map(([type, list]) => (
                <Collapse
                    key={type}
                    defaultActiveKey={[type]}
                    items={[
                        {
                            key: type,
                            label: (
                                <Typography.Text strong>
                                    {ACTION_TYPE_LABEL[type] ?? type} ({list.length})
                                </Typography.Text>
                            ),
                            children: (
                                <Space direction="vertical" size={10} style={{ width: "100%" }}>
                                    <AnimatePresence initial={false}>
                                        {list.map((a) => (
                                            <ActionCardView
                                                key={a.id}
                                                action={a}
                                                projectId={project.id}
                                                defaultOpen={location.hash === `#${a.id}`}
                                            />
                                        ))}
                                    </AnimatePresence>
                                </Space>
                            ),
                        },
                    ]}
                />
            ))}

            {(status === null || status === "done") && done.length > 0 && (
                <Collapse
                    defaultActiveKey={status === "done" ? ["done"] : []}
                    items={[
                        {
                            key: "done",
                            label: <Typography.Text strong>Done ({done.length})</Typography.Text>,
                            children: (
                                <Space direction="vertical" size={10} style={{ width: "100%" }}>
                                    <AnimatePresence initial={false}>
                                        {done.map((a) => (
                                            <ActionCardView
                                                key={a.id}
                                                action={a}
                                                projectId={project.id}
                                            />
                                        ))}
                                    </AnimatePresence>
                                </Space>
                            ),
                        },
                    ]}
                />
            )}
        </Space>
    );
}
