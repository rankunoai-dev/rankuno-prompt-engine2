/** Latest consolidation, crawls since, history, and "Consolidate now". */
import { useState } from "react";
import { App, Button, Card, InputNumber, Select, Space, Typography } from "antd";
import type { Project } from "@/api/endpoints";
import { usePositions } from "@/api/queries";
import { useConsolidate } from "@/api/mutations";
import { fmtDateTime } from "@/app/format";

export function ConsolidationPanel({ project }: { project: Project }) {
    const { message } = App.useApp();
    const [historyId, setHistoryId] = useState<string | null>(null);
    const { data: view } = usePositions(project.id, historyId);
    const consolidate = useConsolidate(project.id);
    const [window, setWindow] = useState<number>(project.consolidation_runs);
    const c = view?.consolidation ?? null;
    const since = view?.runs_since_last ?? 0;
    const remaining = Math.max(0, project.consolidation_runs - since);

    return (
        <Card
            title="Consolidated positioning"
            extra={
                <Space>
                    {(view?.history.length ?? 0) > 0 && (
                        <Select
                            aria-label="Consolidation history"
                            size="small"
                            style={{ minWidth: 260 }}
                            value={c?.id}
                            onChange={(v) => setHistoryId(v)}
                            options={(view?.history ?? []).map((h) => ({
                                value: h.id,
                                label: `${fmtDateTime(h.consolidated_at)} · ${h.window_runs} crawls · ${h.trigger}`,
                            }))}
                        />
                    )}
                    <InputNumber
                        aria-label="Crawls to consolidate"
                        size="small"
                        min={1}
                        max={50}
                        value={window}
                        onChange={(v) => setWindow(v ?? project.consolidation_runs)}
                        style={{ width: 80 }}
                    />
                    <Button
                        size="small"
                        loading={consolidate.isPending}
                        onClick={async () => {
                            try {
                                const created = await consolidate.mutateAsync({
                                    window_runs: window,
                                    note: "",
                                });
                                setHistoryId(null);
                                message.success(
                                    `Consolidated ${created.positions_count} positions over ${created.window_runs} crawl(s).`,
                                );
                            } catch (err) {
                                message.error(
                                    err instanceof Error ? err.message : "Could not consolidate.",
                                );
                            }
                        }}
                    >
                        Consolidate now
                    </Button>
                </Space>
            }
        >
            {c ? (
                <Typography.Paragraph style={{ margin: 0 }}>
                    Latest consolidation <b>{fmtDateTime(c.consolidated_at)}</b> · window{" "}
                    {c.window_runs} crawl(s) · {c.run_ids.length} pipeline run(s) ·{" "}
                    {c.positions_count} positions · {c.trigger}
                    {c.first_run_at && c.last_run_at && (
                        <>
                            {" "}
                            · covers {fmtDateTime(c.first_run_at)} → {fmtDateTime(c.last_run_at)}
                        </>
                    )}
                </Typography.Paragraph>
            ) : (
                <Typography.Paragraph style={{ margin: 0 }}>
                    No consolidation yet: {since} of {project.consolidation_runs} crawls done.
                    {remaining
                        ? ` The next automatic one happens after ${remaining} more full crawl${remaining > 1 ? "s" : ""}.`
                        : " Ready: the next full crawl will consolidate automatically."}
                </Typography.Paragraph>
            )}
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {since} crawl(s) since the last consolidation; automatic after every{" "}
                {project.consolidation_runs}. Every crawl stays stored on its own date.
            </Typography.Text>
        </Card>
    );
}
