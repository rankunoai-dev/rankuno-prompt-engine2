/** The full checklist: every action card, grouped by type, with filters and outcomes. */
import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Collapse, Empty, Select, Skeleton, Tag, Tooltip } from "antd";
import { AnimatePresence } from "framer-motion";
import { useLocation, useOutletContext } from "react-router-dom";
import type { ActionCard, Project } from "@/api/endpoints";
import { useInsights } from "@/api/queries";
import { ACTION_TYPE_HELP, ACTION_TYPE_LABEL, ActionCardView } from "@/components/ActionCardView";
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
    const impactMax = useMemo(
        () => actions.reduce((max, a) => Math.max(max, a.impact_score), 0),
        [actions],
    );
    const shown = actions.filter(
        (a) =>
            (!platform || a.engine === platform) &&
            (!subtopic || a.subtopic === subtopic) &&
            (!status || a.status === status) &&
            (!outcome || a.outcome === outcome),
    );
    const openByType = new Map<string, ActionCard[]>();
    for (const a of shown.filter((x) => x.status === "open")) {
        openByType.set(a.type, [...(openByType.get(a.type) ?? []), a]);
    }
    const done = shown.filter((a) => a.status === "done");
    const filtered = !!(platform || subtopic || status || outcome);
    const clear = () => {
        setPlatform(null);
        setSubtopic(null);
        setStatus(null);
        setOutcome(null);
    };

    if (isLoading) return <Skeleton active paragraph={{ rows: 8 }} />;
    if (error) return <Alert type="error" showIcon message={error.message} />;

    const group = (key: string, title: string, help: string | undefined, list: ActionCard[]) => ({
        key,
        label: (
            <div>
                <div className="pe-actions-group-title">{title}</div>
                {help && <div className="pe-actions-group-help">{help}</div>}
            </div>
        ),
        children: (
            <div className="pe-actions-list">
                <AnimatePresence initial={false}>
                    {list.map((a) => (
                        <ActionCardView
                            key={a.id}
                            action={a}
                            projectId={project.id}
                            impactMax={impactMax}
                            defaultOpen={location.hash === `#${a.id}`}
                        />
                    ))}
                </AnimatePresence>
            </div>
        ),
    });

    return (
        <div style={{ display: "grid", gap: 24 }}>
            <div className="pe-actions-toolbar">
                <div className="pe-actions-summary">
                    <span>
                        <b className="pe-num">
                            {actions.filter((a) => a.status === "open").length}
                        </b>
                        open
                    </span>
                    <span>
                        <b className="pe-num">
                            {actions.filter((a) => a.status === "done").length}
                        </b>
                        done
                    </span>
                    {filtered && (
                        <span>
                            showing {shown.length} of {actions.length}
                        </span>
                    )}
                    {insights?.basis.low_confidence && (
                        <Tooltip title="These cards are computed from fewer crawls than the consolidation window asks for. Treat them as early signals.">
                            <Tag color="warning" bordered={false}>
                                Low confidence basis
                            </Tag>
                        </Tooltip>
                    )}
                </div>
                <div
                    data-testid="actions-filters"
                    style={{ display: "flex", flexWrap: "wrap", gap: 8 }}
                >
                    <Select
                        allowClear
                        variant="filled"
                        placeholder="Platform"
                        aria-label="Platform"
                        style={{ width: 180 }}
                        value={platform}
                        onChange={(v) => setPlatform(v ?? null)}
                        options={project.engines.map((e) => ({
                            value: e,
                            label: ENGINE_LABEL[e] ?? e,
                        }))}
                    />
                    <Select
                        allowClear
                        variant="filled"
                        placeholder="Subtopic"
                        aria-label="Subtopic"
                        style={{ width: 200 }}
                        value={subtopic}
                        onChange={(v) => setSubtopic(v ?? null)}
                        options={subtopics.map((s) => ({ value: s, label: s.trim() }))}
                    />
                    <Select
                        allowClear
                        variant="filled"
                        placeholder="Status"
                        aria-label="Status"
                        style={{ width: 120 }}
                        value={status}
                        onChange={(v) => setStatus(v ?? null)}
                        options={[
                            { value: "open", label: "Open" },
                            { value: "done", label: "Done" },
                        ]}
                    />
                    <Select
                        allowClear
                        variant="filled"
                        placeholder="Outcome"
                        aria-label="Outcome"
                        style={{ width: 150 }}
                        value={outcome}
                        onChange={(v) => setOutcome(v ?? null)}
                        options={[
                            { value: "pending", label: "Pending" },
                            { value: "improved", label: "Improved" },
                            { value: "unchanged", label: "No change" },
                            { value: "regressed", label: "Regressed" },
                        ]}
                    />
                    {filtered && (
                        <Button type="link" onClick={clear}>
                            Clear
                        </Button>
                    )}
                </div>
            </div>

            {!shown.length && <Empty description="No action matches these filters." />}

            {[...openByType.entries()].map(([type, list]) => (
                <Collapse
                    key={type}
                    ghost
                    className="pe-actions-group"
                    defaultActiveKey={[type]}
                    items={[
                        group(
                            type,
                            `${ACTION_TYPE_LABEL[type] ?? type} (${list.length})`,
                            ACTION_TYPE_HELP[type],
                            list,
                        ),
                    ]}
                />
            ))}

            {(status === null || status === "done") && done.length > 0 && (
                <Collapse
                    ghost
                    className="pe-actions-group"
                    defaultActiveKey={status === "done" ? ["done"] : []}
                    items={[
                        group(
                            "done",
                            `Done (${done.length})`,
                            "Checked off. Each card is scored again after the next consolidation.",
                            done,
                        ),
                    ]}
                />
            )}
        </div>
    );
}
