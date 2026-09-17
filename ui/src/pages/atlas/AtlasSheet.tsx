import { useMemo, useState } from "react";
import { Select, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { AtlasPrompt, AtlasSnapshot, Engine } from "@/api/endpoints";
import { num, pct } from "@/app/format";
import { ENGINE_COLOR, ENGINE_SHORT } from "@/app/theme";
import { useAtlasState } from "./AtlasContext";

const titleCase = (v: string | null | undefined) =>
    v
        ? v
              .toLowerCase()
              .replace(/_/g, " ")
              .replace(/(^|\s)\S/g, (c) => c.toUpperCase())
        : "—";

export function AtlasSheet() {
    const { index, prompts, engines, asOf, openPrompt } = useAtlasState();
    const [groupBy, setGroupBy] = useState<
        "none" | "decision_stage" | "subtopic" | "prompt_type" | "search_intent"
    >("none");
    const rows = useMemo(() => {
        const sorted = [...prompts].sort((a, b) =>
            groupBy === "none"
                ? b.search_volume - a.search_volume
                : String(a[groupBy]).localeCompare(String(b[groupBy])) ||
                  b.search_volume - a.search_volume,
        );
        return sorted;
    }, [prompts, groupBy]);

    const columns: ColumnsType<AtlasPrompt> = [
        {
            title: "Prompt",
            key: "prompt",
            width: 380,
            render: (_, p) => (
                <div>
                    <a
                        onClick={() => openPrompt(p.prompt_id)}
                        style={{ color: "inherit", fontWeight: 500 }}
                    >
                        {p.prompt_text}
                    </a>
                    <div className="pe-muted" style={{ fontSize: 12 }}>
                        {p.subtopic} · {p.core_keyword} ·{" "}
                        <span className="pe-mono">{p.prompt_id.slice(0, 8)}</span>
                    </div>
                </div>
            ),
        },
        {
            title: "Type",
            key: "type",
            width: 110,
            render: (_, p) => (
                <Tag color={p.prompt_type === "BRANDED" ? "processing" : "default"}>
                    {p.prompt_type === "BRANDED" ? "Branded" : "Non-branded"}
                </Tag>
            ),
        },
        {
            title: "Stage · intent",
            key: "stage",
            width: 170,
            render: (_, p) => (
                <span className="pe-muted" style={{ fontSize: 12 }}>
                    {titleCase(p.decision_stage)} · {titleCase(p.search_intent)}
                </span>
            ),
        },
        {
            title: "Volume",
            key: "volume",
            width: 90,
            align: "right",
            sorter: (a, b) => a.search_volume - b.search_volume,
            render: (_, p) => <span className="pe-num">{num(p.search_volume)}</span>,
        },
        ...engines.map((e): ColumnsType<AtlasPrompt>[number] => ({
            title: ENGINE_SHORT[e] ?? e,
            key: e,
            width: 150,
            sorter: (a, b) =>
                (index.latest(a.prompt_id, e, asOf)?.client_citation_rate ?? -1) -
                (index.latest(b.prompt_id, e, asOf)?.client_citation_rate ?? -1),
            render: (_, p) => (
                <EngineCell
                    sn={index.latest(p.prompt_id, e, asOf)}
                    engine={e}
                    onClick={() => openPrompt(p.prompt_id, e)}
                />
            ),
        })),
        {
            title: "Landing page",
            key: "url",
            width: 220,
            render: (_, p) =>
                p.mapped_url ? (
                    <a
                        href={p.mapped_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="pe-mono pe-ellipsis"
                        style={{ display: "block", maxWidth: 200 }}
                        title={p.mapped_url}
                    >
                        {p.mapped_url.replace(/^https?:\/\/(www\.)?/, "")}
                    </a>
                ) : (
                    <Tag color="warning">△ Gap: {p.subtopic} page</Tag>
                ),
        },
        {
            title: "Verdict",
            key: "verdict",
            width: 100,
            render: (_, p) =>
                p.verdict ? (
                    <Tooltip title={p.verdict_reason ?? ""}>
                        <Tag color={p.verdict === "KEEP" ? "success" : "error"}>
                            {titleCase(p.verdict)}
                        </Tag>
                    </Tooltip>
                ) : (
                    <span className="pe-muted">—</span>
                ),
        },
    ];

    return (
        <Space direction="vertical" size={10} style={{ width: "100%" }}>
            <Space>
                <Typography.Text type="secondary">Group by</Typography.Text>
                <Select
                    aria-label="Group by"
                    size="small"
                    value={groupBy}
                    onChange={(v) => setGroupBy(v)}
                    style={{ width: 160 }}
                    options={[
                        { value: "none", label: "Nothing" },
                        { value: "decision_stage", label: "Decision stage" },
                        { value: "subtopic", label: "Subtopic" },
                        { value: "prompt_type", label: "Prompt type" },
                        { value: "search_intent", label: "Search intent" },
                    ]}
                />
                <Typography.Text type="secondary">{rows.length} prompts</Typography.Text>
            </Space>
            <Table<AtlasPrompt>
                size="small"
                rowKey="prompt_id"
                dataSource={rows}
                columns={columns}
                scroll={{ x: 1300 }}
                pagination={{
                    pageSize: 25,
                    showSizeChanger: true,
                    pageSizeOptions: [10, 25, 50, 100],
                }}
                virtual={rows.length > 200}
                rowClassName={(p, i) =>
                    groupBy !== "none" && i > 0 && rows[i - 1]?.[groupBy] !== p[groupBy]
                        ? "pe-group-start"
                        : ""
                }
            />
        </Space>
    );
}

export function EngineCell({
    sn,
    engine,
    onClick,
}: {
    sn: AtlasSnapshot | null;
    engine: Engine;
    onClick: () => void;
}) {
    if (!sn)
        return (
            <span className="pe-muted" style={{ fontSize: 12 }}>
                n/a
            </span>
        );
    const color = ENGINE_COLOR[engine] ?? "#888";
    return (
        <button
            type="button"
            onClick={onClick}
            className="pe-focus-ring"
            style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 7,
                background: "none",
                border: 0,
                padding: 0,
                cursor: "pointer",
                font: "inherit",
                color: "inherit",
            }}
            title={`${ENGINE_SHORT[engine]}: cited ${pct(sn.client_citation_rate)} · mentioned ${pct(sn.mention_rate)} · ${sn.client_cited_samples}/${sn.samples - sn.failed_samples} samples`}
        >
            <span
                aria-hidden
                style={{
                    width: 20,
                    height: 20,
                    borderRadius: "50%",
                    background: `conic-gradient(${color} ${Math.round(sn.client_citation_rate * 100)}%, var(--ant-color-fill-secondary) 0)`,
                    display: "grid",
                    placeItems: "center",
                }}
            >
                <span
                    style={{
                        width: 12,
                        height: 12,
                        borderRadius: "50%",
                        background: "var(--ant-color-bg-container)",
                    }}
                />
            </span>
            <span className="pe-num" style={{ fontSize: 12, lineHeight: 1.2, textAlign: "left" }}>
                <div>{pct(sn.client_citation_rate)} cited</div>
                <div className="pe-muted">
                    {pct(sn.mention_rate)} mentioned
                    {sn.client_best_rank ? ` · #${sn.client_best_rank}` : ""}
                </div>
            </span>
        </button>
    );
}
