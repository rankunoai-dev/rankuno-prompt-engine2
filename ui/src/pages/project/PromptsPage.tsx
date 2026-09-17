/**
 * Master prompt table: configuration (star, on/off, overrides), the latest
 * verdict per prompt, and the research facts (volume, intent, stage) joined
 * from the atlas dataset. Everything is client-side; the API returns the
 * full list.
 */
import { useMemo, useState } from "react";
import {
    App,
    Button,
    Card,
    Input,
    Modal,
    Popconfirm,
    Select,
    Space,
    Switch,
    Table,
    Tag,
    Tooltip,
    Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { StarFilled, StarOutlined } from "@ant-design/icons";
import { Link, useOutletContext } from "react-router-dom";
import type { Engine, Project, TrackedPromptUpdate } from "@/api/endpoints";
import { useAtlas, usePrompts, useResults } from "@/api/queries";
import { useDeletePrompt, useRunProject, useUpdatePrompt } from "@/api/mutations";
import { fmtDateTime, num, pct, relative } from "@/app/format";
import { askNotifyPermission } from "@/app/notify";
import { EngineCheckboxes } from "@/components/EngineCheckboxes";
import { EngineDot } from "@/components/EngineTag";
import { IntervalPicker } from "@/components/IntervalPicker";
import {
    EMPTY_FILTERS,
    buildPromptRows,
    filterPromptRows,
    type PromptFilters,
    type PromptRowView,
    type PromptVerdict,
} from "@/lib/promptView";
import { useUiStore } from "@/store/ui";
import { ImportCard } from "./prompts/ImportCard";
import { OverridesEditor } from "./prompts/OverridesEditor";

const VERDICT: Record<PromptVerdict, { label: string; color: string }> = {
    linked: { label: "Linked", color: "success" },
    mentioned: { label: "Mentioned only", color: "warning" },
    absent: { label: "Absent", color: "default" },
    unsampled: { label: "Not sampled", color: "default" },
};
/** Stable empty selection: a fresh array per read would loop the store hook. */
const NO_SELECTION: string[] = [];
const VERDICT_ORDER: Record<PromptVerdict, number> = {
    linked: 0,
    mentioned: 1,
    absent: 2,
    unsampled: 3,
};
const titleCase = (v: string | null) =>
    v
        ? v
              .toLowerCase()
              .replace(/_/g, " ")
              .replace(/(^|\s)\S/g, (c) => c.toUpperCase())
        : "—";

export function PromptsPage() {
    const { project } = useOutletContext<{ project: Project }>();
    const { message } = App.useApp();
    const { data: prompts, isLoading } = usePrompts(project.id);
    const { data: results } = useResults(project.id);
    const { data: atlas } = useAtlas(project.client.lob);
    const update = useUpdatePrompt(project.id);
    const remove = useDeletePrompt(project.id);
    const run = useRunProject(project.id);
    const pageSize = useUiStore((s) => s.pageSize);
    const setPageSize = useUiStore((s) => s.setPageSize);
    const selected = useUiStore((s) => s.selection[project.id] ?? NO_SELECTION);
    const setSelection = useUiStore((s) => s.setSelection);
    const [filters, setFilters] = useState<PromptFilters>(EMPTY_FILTERS);
    const [editing, setEditing] = useState<{
        id: string;
        text: string;
        keyword: string;
        subtopic: string;
    } | null>(null);
    const [bulk, setBulk] = useState<"interval" | "platforms" | null>(null);
    const [bulkInterval, setBulkInterval] = useState<string | null>(null);
    const [bulkEngines, setBulkEngines] = useState<Engine[] | null>(null);

    const rows = useMemo(() => buildPromptRows(prompts, results, atlas), [prompts, results, atlas]);
    const shown = useMemo(() => filterPromptRows(rows, filters), [rows, filters]);
    const intents = useMemo(
        () => [...new Set(rows.map((r) => r.intent).filter(Boolean))] as string[],
        [rows],
    );
    const stages = useMemo(
        () => [...new Set(rows.map((r) => r.stage).filter(Boolean))] as string[],
        [rows],
    );

    const patch = async (tid: string, body: TrackedPromptUpdate, ok = "Prompt saved.") => {
        try {
            await update.mutateAsync({ tid, body });
            if (ok) message.success(ok);
        } catch (err) {
            message.error(err instanceof Error ? err.message : "Could not save the prompt.");
        }
    };
    const bulkPatch = async (body: TrackedPromptUpdate, ok: string) => {
        try {
            await Promise.all(selected.map((tid) => update.mutateAsync({ tid, body })));
            message.success(ok);
        } catch (err) {
            message.error(err instanceof Error ? err.message : "Could not update the prompts.");
        }
    };

    const columns: ColumnsType<PromptRowView> = [
        {
            title: "",
            key: "star",
            width: 40,
            render: (_, r) => (
                <Button
                    type="text"
                    size="small"
                    aria-label={r.prompt.important ? "Unstar" : "Star"}
                    aria-pressed={r.prompt.important}
                    icon={
                        r.prompt.important ? (
                            <StarFilled className="pe-star-on" />
                        ) : (
                            <StarOutlined />
                        )
                    }
                    onClick={() => patch(r.prompt.id, { important: !r.prompt.important }, "")}
                />
            ),
        },
        {
            title: "On",
            key: "enabled",
            width: 60,
            render: (_, r) => (
                <Switch
                    size="small"
                    aria-label={`Enabled: ${r.prompt.prompt_text}`}
                    checked={r.prompt.enabled}
                    onChange={(v) => patch(r.prompt.id, { enabled: v }, "")}
                />
            ),
        },
        {
            title: "Prompt",
            key: "prompt",
            width: 360,
            sorter: (a, b) => (a.prompt.subtopic ?? "").localeCompare(b.prompt.subtopic ?? ""),
            render: (_, r) =>
                editing?.id === r.prompt.id ? (
                    <Space direction="vertical" size={4} style={{ width: "100%" }}>
                        <Input.TextArea
                            aria-label="Edit prompt text"
                            autoSize
                            value={editing.text}
                            onChange={(e) => setEditing({ ...editing, text: e.target.value })}
                        />
                        <Space.Compact block>
                            <Input
                                aria-label="Edit keyword"
                                placeholder="keyword"
                                value={editing.keyword}
                                onChange={(e) =>
                                    setEditing({ ...editing, keyword: e.target.value })
                                }
                            />
                            <Input
                                aria-label="Edit subtopic"
                                placeholder="subtopic"
                                value={editing.subtopic}
                                onChange={(e) =>
                                    setEditing({ ...editing, subtopic: e.target.value })
                                }
                            />
                        </Space.Compact>
                        <Space>
                            <Button
                                size="small"
                                type="primary"
                                onClick={async () => {
                                    await patch(r.prompt.id, {
                                        prompt_text: editing.text,
                                        keyword: editing.keyword.trim() || null,
                                        subtopic: editing.subtopic.trim() || null,
                                    });
                                    setEditing(null);
                                }}
                            >
                                Save
                            </Button>
                            <Button size="small" onClick={() => setEditing(null)}>
                                Cancel
                            </Button>
                        </Space>
                    </Space>
                ) : (
                    <div>
                        <Link
                            to={`/projects/${project.id}/battleground?prompt=${r.prompt.prompt_id}`}
                            style={{ color: "inherit", fontWeight: 500 }}
                        >
                            {r.prompt.prompt_text}
                        </Link>
                        <div
                            className="pe-muted"
                            style={{ fontSize: 12, display: "flex", gap: 8, flexWrap: "wrap" }}
                        >
                            {r.prompt.subtopic && <span>{r.prompt.subtopic}</span>}
                            {r.prompt.keyword && <span>· {r.prompt.keyword}</span>}
                            <Button
                                type="link"
                                size="small"
                                style={{ padding: 0, height: "auto", fontSize: 12 }}
                                onClick={() =>
                                    setEditing({
                                        id: r.prompt.id,
                                        text: r.prompt.prompt_text,
                                        keyword: r.prompt.keyword ?? "",
                                        subtopic: r.prompt.subtopic ?? "",
                                    })
                                }
                            >
                                Edit
                            </Button>
                        </div>
                    </div>
                ),
        },
        {
            title: "Volume",
            key: "volume",
            align: "right",
            width: 90,
            sorter: (a, b) => (a.searchVolume ?? -1) - (b.searchVolume ?? -1),
            render: (_, r) => (
                <Tooltip
                    title={
                        r.atlas
                            ? "Semrush monthly searches from the research pass"
                            : "Not in the research dataset (custom prompt)"
                    }
                >
                    <span className="pe-num">{num(r.searchVolume)}</span>
                </Tooltip>
            ),
        },
        {
            title: "Intent · stage",
            key: "intent",
            width: 170,
            render: (_, r) => (
                <span className="pe-muted" style={{ fontSize: 12 }}>
                    {titleCase(r.intent)} · {titleCase(r.stage)}
                </span>
            ),
        },
        {
            title: "Verdict",
            key: "verdict",
            width: 170,
            sorter: (a, b) => VERDICT_ORDER[a.verdict] - VERDICT_ORDER[b.verdict],
            render: (_, r) => (
                <Tooltip
                    title={
                        r.sampledOn.length
                            ? `Sampled on ${r.sampledOn.length} platform(s); linked on ${r.linkedOn.length}; mention rate ${pct(r.mentionRate)}`
                            : "No sample yet"
                    }
                >
                    <Tag color={VERDICT[r.verdict].color} style={{ marginInlineEnd: 4 }}>
                        {VERDICT[r.verdict].label}
                    </Tag>
                    {r.linkedOn.map((e) => (
                        <EngineDot key={e} engine={e} size={7} />
                    ))}
                </Tooltip>
            ),
        },
        {
            title: "Cited %",
            key: "cited",
            align: "right",
            width: 90,
            sorter: (a, b) => (a.citedRate ?? -1) - (b.citedRate ?? -1),
            render: (_, r) => <span className="pe-num">{pct(r.citedRate)}</span>,
        },
        {
            title: "Interval · platforms · samples",
            key: "overrides",
            width: 300,
            render: (_, r) => (
                <OverridesEditor
                    project={project}
                    prompt={r.prompt}
                    onSave={(body) => patch(r.prompt.id, body, "Overrides saved.")}
                />
            ),
        },
        {
            title: "Due",
            key: "due",
            width: 90,
            render: (_, r) =>
                r.dueOn.length ? (
                    <Tooltip title={`Due on ${r.dueOn.join(", ")}`}>
                        <Tag color="warning">{r.dueOn.length} due</Tag>
                    </Tooltip>
                ) : (
                    <span className="pe-muted">—</span>
                ),
        },
        {
            title: "Updated",
            key: "updated",
            width: 120,
            sorter: (a, b) => a.prompt.updated_at.localeCompare(b.prompt.updated_at),
            render: (_, r) => (
                <Tooltip title={fmtDateTime(r.prompt.updated_at)}>
                    <span className="pe-muted" style={{ fontSize: 12 }}>
                        {relative(r.prompt.updated_at)}
                    </span>
                </Tooltip>
            ),
        },
        {
            title: "",
            key: "actions",
            width: 150,
            render: (_, r) => (
                <Space size={4}>
                    <Popconfirm
                        title="Remove this prompt from the project?"
                        description="Its history stays in the Atlas."
                        okText="Delete"
                        okButtonProps={{ danger: true }}
                        onConfirm={async () => {
                            try {
                                await remove.mutateAsync(r.prompt.id);
                                message.success("Prompt removed.");
                            } catch (err) {
                                message.error(
                                    err instanceof Error
                                        ? err.message
                                        : "Could not remove the prompt.",
                                );
                            }
                        }}
                    >
                        <Button size="small" danger aria-label={`Delete ${r.prompt.prompt_text}`}>
                            Delete
                        </Button>
                    </Popconfirm>
                </Space>
            ),
        },
    ];

    const sel = (key: keyof PromptFilters) => (v: unknown) =>
        setFilters((f) => ({ ...f, [key]: (v ?? null) as never }));

    return (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <ImportCard projectId={project.id} />
            <Card
                title={`Tracked prompts (${rows.length})`}
                extra={
                    <Space wrap>
                        <Input.Search
                            allowClear
                            aria-label="Search prompts"
                            placeholder="Search"
                            value={filters.q}
                            onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value }))}
                            style={{ width: 200 }}
                        />
                        <Select
                            allowClear
                            placeholder="Intent"
                            aria-label="Intent"
                            style={{ width: 140 }}
                            value={filters.intent}
                            onChange={sel("intent")}
                            options={intents.map((v) => ({ value: v, label: titleCase(v) }))}
                        />
                        <Select
                            allowClear
                            placeholder="Stage"
                            aria-label="Decision stage"
                            style={{ width: 150 }}
                            value={filters.stage}
                            onChange={sel("stage")}
                            options={stages.map((v) => ({ value: v, label: titleCase(v) }))}
                        />
                        <Select
                            allowClear
                            placeholder="Platform"
                            aria-label="Platform"
                            style={{ width: 160 }}
                            value={filters.platform}
                            onChange={sel("platform")}
                            options={project.engines.map((e) => ({ value: e, label: e }))}
                        />
                        <Select
                            allowClear
                            placeholder="Verdict"
                            aria-label="Verdict"
                            style={{ width: 150 }}
                            value={filters.verdict}
                            onChange={sel("verdict")}
                            options={(Object.keys(VERDICT) as PromptVerdict[]).map((v) => ({
                                value: v,
                                label: VERDICT[v].label,
                            }))}
                        />
                        <Select
                            allowClear
                            placeholder="Due"
                            aria-label="Due"
                            style={{ width: 120 }}
                            value={filters.due}
                            onChange={sel("due")}
                            options={[
                                { value: "due", label: "Due now" },
                                { value: "not_due", label: "Not due" },
                            ]}
                        />
                        <Select
                            allowClear
                            placeholder="Enabled"
                            aria-label="Enabled"
                            style={{ width: 120 }}
                            value={filters.enabled}
                            onChange={sel("enabled")}
                            options={[
                                { value: "on", label: "Enabled" },
                                { value: "off", label: "Disabled" },
                            ]}
                        />
                        <Button
                            type={filters.starred ? "primary" : "default"}
                            icon={<StarFilled />}
                            aria-pressed={filters.starred}
                            onClick={() => setFilters((f) => ({ ...f, starred: !f.starred }))}
                        >
                            Starred
                        </Button>
                    </Space>
                }
            >
                {selected.length > 0 && (
                    <Space wrap style={{ marginBottom: 12 }} data-testid="bulk-bar">
                        <Typography.Text strong>{selected.length} selected</Typography.Text>
                        <Popconfirm
                            title={`Run ${selected.length} prompt(s) now on their platforms?`}
                            description="Every engine call is charged to the ledger."
                            okText="Run selected"
                            onConfirm={async () => {
                                askNotifyPermission();
                                try {
                                    await run.mutateAsync({
                                        force: true,
                                        prompt_ids: selected,
                                        engines: null,
                                    });
                                    message.success("Run queued. Follow it on the Runs tab.");
                                } catch (err) {
                                    message.error(
                                        err instanceof Error
                                            ? err.message
                                            : "Could not start the run.",
                                    );
                                }
                            }}
                        >
                            <Button size="small">Run selected</Button>
                        </Popconfirm>
                        <Button
                            size="small"
                            onClick={() => bulkPatch({ important: true }, "Starred.")}
                        >
                            Star
                        </Button>
                        <Button
                            size="small"
                            onClick={() => bulkPatch({ important: false }, "Unstarred.")}
                        >
                            Unstar
                        </Button>
                        <Button size="small" onClick={() => setBulk("interval")}>
                            Set interval
                        </Button>
                        <Button size="small" onClick={() => setBulk("platforms")}>
                            Set platforms
                        </Button>
                        <Popconfirm
                            title={`Delete ${selected.length} prompt(s)?`}
                            description="Their history stays in the Atlas."
                            okText="Delete"
                            okButtonProps={{ danger: true }}
                            onConfirm={async () => {
                                try {
                                    await Promise.all(
                                        selected.map((tid) => remove.mutateAsync(tid)),
                                    );
                                    setSelection(project.id, []);
                                    message.success("Prompts removed.");
                                } catch (err) {
                                    message.error(
                                        err instanceof Error
                                            ? err.message
                                            : "Could not remove the prompts.",
                                    );
                                }
                            }}
                        >
                            <Button size="small" danger>
                                Delete
                            </Button>
                        </Popconfirm>
                        <Button
                            size="small"
                            type="link"
                            onClick={() => setSelection(project.id, [])}
                        >
                            Clear
                        </Button>
                    </Space>
                )}
                <Table<PromptRowView>
                    size="small"
                    rowKey={(r) => r.prompt.id}
                    loading={isLoading}
                    dataSource={shown}
                    columns={columns}
                    scroll={{ x: 1700 }}
                    virtual={shown.length > 200}
                    rowSelection={{
                        selectedRowKeys: selected,
                        onChange: (keys) => setSelection(project.id, keys.map(String)),
                    }}
                    pagination={{
                        pageSize,
                        pageSizeOptions: [10, 25, 50, 100],
                        showSizeChanger: true,
                        onChange: (_, size) => setPageSize(size),
                        showTotal: (total) => `${total} prompt(s)`,
                    }}
                    locale={{
                        emptyText: prompts?.length
                            ? "No prompt matches these filters."
                            : "No prompts yet. Import a file or add one above.",
                    }}
                />
            </Card>

            <Modal
                open={bulk === "interval"}
                title={`Set the interval for ${selected.length} prompt(s)`}
                onCancel={() => setBulk(null)}
                onOk={async () => {
                    await bulkPatch({ interval: bulkInterval }, "Interval set.");
                    setBulk(null);
                }}
            >
                <IntervalPicker allowInherit value={bulkInterval} onChange={setBulkInterval} />
            </Modal>
            <Modal
                open={bulk === "platforms"}
                title={`Set the platforms for ${selected.length} prompt(s)`}
                onCancel={() => setBulk(null)}
                onOk={async () => {
                    await bulkPatch(
                        bulkEngines === null ? { engines: null } : { engines: bulkEngines },
                        "Platforms set.",
                    );
                    setBulk(null);
                }}
            >
                <EngineCheckboxes
                    allowInherit
                    inherited={project.engines}
                    value={bulkEngines}
                    onChange={setBulkEngines}
                />
            </Modal>
        </Space>
    );
}
