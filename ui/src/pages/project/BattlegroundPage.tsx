/**
 * The engine matrix: prompts grouped by subtopic × platforms. Consolidated
 * (last N crawls) or point-in-time (a chosen crawl). A cell click deep-links
 * the Inspection Drawer through the query string.
 */
import { useCallback, useMemo, useRef, useState } from "react";
import { Alert, Card, Collapse, Segmented, Select, Space, Typography } from "antd";
import { useOutletContext, useSearchParams } from "react-router-dom";
import type { Engine, Project, PromptResult } from "@/api/endpoints";
import { useAtlas, useCrawls, usePositions, useResults } from "@/api/queries";
import { fmtDateTime } from "@/app/format";
import { ENGINE_SHORT } from "@/app/theme";
import { EngineDot } from "@/components/EngineTag";
import { buildMatrix, cellFromPosition, cellFromSnapshot, type CellView } from "@/lib/matrix";
import { useUiStore } from "@/store/ui";
import { CellBadge } from "./battleground/CellBadge";
import { InspectionDrawer } from "./battleground/InspectionDrawer";

export function BattlegroundPage() {
    const { project } = useOutletContext<{ project: Project }>();
    const [params, setParams] = useSearchParams();
    const mode = useUiStore((s) => s.resultsMode);
    const setMode = useUiStore((s) => s.setResultsMode);
    const { data: results, isLoading } = useResults(project.id);
    const { data: positions } = usePositions(project.id);
    const { data: crawls } = useCrawls(project.id);
    const { data: atlas } = useAtlas(project.client.lob);
    const [crawlId, setCrawlId] = useState<string | null>(null);
    const engines = project.engines;

    const positionByKey = useMemo(
        () => new Map((positions?.positions ?? []).map((p) => [`${p.prompt_id}|${p.engine}`, p])),
        [positions],
    );
    const chosenCrawl = useMemo(
        () => (crawlId ? crawls?.find((c) => c.id === crawlId) : undefined),
        [crawlId, crawls],
    );
    const snapshotAtCrawl = useMemo(() => {
        const m = new Map<
            string,
            (typeof atlas extends undefined
                ? never
                : NonNullable<typeof atlas>)["snapshots"][number]
        >();
        if (!chosenCrawl || !atlas) return m;
        const runs = new Set(chosenCrawl.run_ids);
        for (const s of atlas.snapshots)
            if (runs.has(s.run_id)) m.set(`${s.prompt_id}|${s.engine}`, s);
        return m;
    }, [chosenCrawl, atlas]);

    const cellFor = useCallback(
        (r: PromptResult, e: Engine): CellView => {
            const due = r.due_on.includes(e);
            if (mode === "consolidated" && positions?.consolidation) {
                return cellFromPosition(
                    positionByKey.get(`${r.prompt.prompt_id}|${e}`),
                    project,
                    due,
                );
            }
            if (mode === "point" && chosenCrawl) {
                return cellFromSnapshot(
                    snapshotAtCrawl.get(`${r.prompt.prompt_id}|${e}`),
                    project,
                    due,
                );
            }
            return cellFromSnapshot(r.snapshots[e], project, due);
        },
        [mode, positions, positionByKey, chosenCrawl, snapshotAtCrawl, project],
    );
    const groups = useMemo(
        () => buildMatrix(results, engines, cellFor),
        [results, engines, cellFor],
    );

    const promptParam = params.get("prompt");
    const engineParam = params.get("engine") as Engine | null;
    const runParam = params.get("crawl");
    const openResult = useMemo(
        () => results?.find((r) => r.prompt.prompt_id === promptParam) ?? null,
        [results, promptParam],
    );
    const openEngine =
        engineParam && engines.includes(engineParam)
            ? engineParam
            : openResult
              ? (engines[0] ?? null)
              : null;

    const openCell = (promptId: string, engine: Engine) => {
        const next = new URLSearchParams(params);
        next.set("prompt", promptId);
        next.set("engine", engine);
        next.delete("crawl");
        setParams(next);
    };
    const closeDrawer = () => {
        const next = new URLSearchParams(params);
        next.delete("prompt");
        next.delete("engine");
        next.delete("crawl");
        setParams(next);
    };
    const setRun = (runId: string | null) => {
        const next = new URLSearchParams(params);
        if (runId) next.set("crawl", runId);
        else next.delete("crawl");
        setParams(next);
    };

    // Roving tabindex: arrow keys move between cells; Enter opens.
    const cellRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
    const flat = useMemo(
        () =>
            groups.flatMap((g) =>
                g.rows.map((row) => ({
                    row,
                    keys: engines.map((e) => `${row.result.prompt.prompt_id}|${e}`),
                })),
            ),
        [groups, engines],
    );
    const [focused, setFocused] = useState<string | null>(null);
    const onKeyDown = (e: React.KeyboardEvent, rowIdx: number, colIdx: number) => {
        const moves: Record<string, [number, number]> = {
            ArrowRight: [0, 1],
            ArrowLeft: [0, -1],
            ArrowDown: [1, 0],
            ArrowUp: [-1, 0],
        };
        const mv = moves[e.key];
        if (!mv) return;
        e.preventDefault();
        const r = Math.max(0, Math.min(flat.length - 1, rowIdx + mv[0]));
        const c = Math.max(0, Math.min(engines.length - 1, colIdx + mv[1]));
        const key = flat[r]?.keys[c];
        if (key) {
            setFocused(key);
            cellRefs.current.get(key)?.focus();
        }
    };

    const basis =
        mode === "consolidated"
            ? positions?.consolidation
                ? `Consolidated over ${positions.consolidation.window_runs} crawls (${fmtDateTime(positions.consolidation.first_run_at)} → ${fmtDateTime(positions.consolidation.last_run_at)})`
                : "No consolidation yet: showing the latest crawl, low confidence"
            : chosenCrawl
              ? `Crawl of ${fmtDateTime(chosenCrawl.started_at)}`
              : "Latest crawl";

    let rowCounter = -1;
    return (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Card
                styles={{ body: { padding: "12px 16px" } }}
                title={
                    <Space wrap>
                        <Segmented
                            aria-label="View mode"
                            value={mode}
                            onChange={(v) => setMode(v as "consolidated" | "point")}
                            options={[
                                {
                                    value: "consolidated",
                                    label: `Consolidated (last ${project.consolidation_runs} crawls)`,
                                },
                                { value: "point", label: "Point-in-time" },
                            ]}
                        />
                        {mode === "point" && (
                            <Select
                                aria-label="Crawl date"
                                allowClear
                                placeholder="Latest crawl"
                                style={{ minWidth: 220 }}
                                value={crawlId ?? undefined}
                                onChange={(v) => setCrawlId(v ?? null)}
                                options={(crawls ?? []).map((c) => ({
                                    value: c.id,
                                    label: `${fmtDateTime(c.started_at)}${c.full ? "" : " (partial)"}`,
                                }))}
                            />
                        )}
                    </Space>
                }
                extra={<Typography.Text type="secondary">{basis}</Typography.Text>}
            >
                <Space size={[12, 4]} wrap style={{ fontSize: 12 }}>
                    <Legend kind="linked" text="Linked (with rank)" />
                    <Legend kind="mentioned" text="Mentioned only" />
                    <Legend kind="competitor" text="Competitor wins" />
                    <Legend kind="absent" text="Absent" />
                    <span>
                        <span
                            style={{
                                display: "inline-block",
                                width: 6,
                                height: 6,
                                borderRadius: "50%",
                                background: "var(--ant-color-warning)",
                                marginRight: 4,
                            }}
                        />
                        due for a sample
                    </span>
                </Space>
            </Card>

            {mode === "consolidated" && !positions?.consolidation && (
                <Alert
                    type="warning"
                    showIcon
                    message={`Low confidence: ${positions?.runs_since_last ?? 0} of ${project.consolidation_runs} crawls`}
                    description="Cells show the latest single crawl until the first consolidation."
                />
            )}

            {isLoading ? (
                <Card loading />
            ) : !groups.length ? (
                <Card>
                    <Typography.Text type="secondary">
                        No prompts yet. Add some on the Prompts tab.
                    </Typography.Text>
                </Card>
            ) : (
                <Collapse
                    defaultActiveKey={groups.map((g) => g.subtopic)}
                    items={groups.map((g) => ({
                        key: g.subtopic,
                        label: (
                            <Space>
                                <Typography.Text strong>{g.subtopic}</Typography.Text>
                                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                    {g.rows.length} prompts · linked on {g.linked} of {g.sampled}{" "}
                                    sampled cells
                                </Typography.Text>
                            </Space>
                        ),
                        children: (
                            <div style={{ overflowX: "auto" }}>
                                <table
                                    role="grid"
                                    aria-label={`${g.subtopic} matrix`}
                                    style={{
                                        width: "100%",
                                        borderCollapse: "separate",
                                        borderSpacing: "0 4px",
                                    }}
                                >
                                    <thead>
                                        <tr>
                                            <th
                                                style={{
                                                    textAlign: "left",
                                                    fontWeight: 500,
                                                    fontSize: 12,
                                                    color: "var(--ant-color-text-tertiary)",
                                                    padding: "0 8px",
                                                    minWidth: 280,
                                                }}
                                            >
                                                Prompt
                                            </th>
                                            {engines.map((e) => (
                                                <th
                                                    key={e}
                                                    style={{
                                                        textAlign: "left",
                                                        fontWeight: 500,
                                                        fontSize: 12,
                                                        color: "var(--ant-color-text-tertiary)",
                                                        padding: "0 6px",
                                                    }}
                                                >
                                                    <EngineDot engine={e} /> {ENGINE_SHORT[e] ?? e}
                                                </th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {g.rows.map((row) => {
                                            rowCounter += 1;
                                            const rowIdx = rowCounter;
                                            return (
                                                <tr key={row.result.prompt.id}>
                                                    <td
                                                        style={{
                                                            padding: "0 8px",
                                                            fontSize: 13,
                                                            maxWidth: 420,
                                                        }}
                                                    >
                                                        {row.result.prompt.important && (
                                                            <span className="pe-star-on">★ </span>
                                                        )}
                                                        {row.result.prompt.prompt_text}
                                                        {!row.result.prompt.enabled && (
                                                            <Typography.Text type="secondary">
                                                                {" "}
                                                                · paused
                                                            </Typography.Text>
                                                        )}
                                                    </td>
                                                    {engines.map((e, colIdx) => {
                                                        const key = `${row.result.prompt.prompt_id}|${e}`;
                                                        const cell = row.cells[e]!;
                                                        return (
                                                            <td
                                                                key={e}
                                                                style={{ padding: "0 6px" }}
                                                                onKeyDown={(ev) =>
                                                                    onKeyDown(ev, rowIdx, colIdx)
                                                                }
                                                            >
                                                                <CellBadge
                                                                    cell={cell}
                                                                    label={`${row.result.prompt.prompt_text} on ${ENGINE_SHORT[e] ?? e}: ${cell.kind}${cell.rank ? ` rank ${cell.rank}` : ""}`}
                                                                    tabIndex={
                                                                        focused
                                                                            ? focused === key
                                                                                ? 0
                                                                                : -1
                                                                            : rowIdx === 0 &&
                                                                                colIdx === 0
                                                                              ? 0
                                                                              : -1
                                                                    }
                                                                    focusRef={(el) => {
                                                                        if (el)
                                                                            cellRefs.current.set(
                                                                                key,
                                                                                el,
                                                                            );
                                                                        else
                                                                            cellRefs.current.delete(
                                                                                key,
                                                                            );
                                                                    }}
                                                                    onOpen={() =>
                                                                        openCell(
                                                                            row.result.prompt
                                                                                .prompt_id,
                                                                            e,
                                                                        )
                                                                    }
                                                                />
                                                            </td>
                                                        );
                                                    })}
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        ),
                    }))}
                />
            )}

            <InspectionDrawer
                project={project}
                result={openResult}
                engine={openEngine}
                position={
                    openResult && openEngine
                        ? positionByKey.get(`${openResult.prompt.prompt_id}|${openEngine}`)
                        : undefined
                }
                runId={runParam}
                onRunChange={setRun}
                onClose={closeDrawer}
            />
        </Space>
    );
}

function Legend({ kind, text }: { kind: CellView["kind"]; text: string }) {
    const bg: Record<string, string> = {
        linked: "var(--ant-color-success)",
        mentioned: "var(--ant-color-warning)",
        competitor: "var(--ant-color-error)",
        absent: "var(--ant-color-text-quaternary)",
    };
    return (
        <span>
            <span
                style={{
                    display: "inline-block",
                    width: 10,
                    height: 10,
                    borderRadius: 2,
                    background: bg[kind],
                    marginRight: 5,
                    verticalAlign: "middle",
                }}
            />
            {text}
        </span>
    );
}
