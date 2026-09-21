/**
 * The usage ledger: what every vendor call cost, what it produced, and the
 * `COST_*` values the observed spend suggests.
 */
import { useMemo, useState } from "react";
import {
    Alert,
    App,
    Button,
    Card,
    Col,
    Row,
    Select,
    Skeleton,
    Space,
    Statistic,
    Switch,
    Table,
    Tag,
    Tooltip,
    Typography,
} from "antd";
import { CopyOutlined } from "@ant-design/icons";
import type { CostRecommendation, RunCost, VendorCost } from "@/api/endpoints";
import { useCosts, useProjects } from "@/api/queries";
import { money, num } from "@/app/format";

const money4 = (x: number | null | undefined) => money(x, 4);

export function CostsPage() {
    const { message } = App.useApp();
    const { data: projects } = useProjects();
    const [projectId, setProjectId] = useState<string | undefined>(undefined);
    const [days, setDays] = useState<number | undefined>(undefined);
    // Real spend by default: seeded demonstration rows are tagged `demo`.
    const [hideDemo, setHideDemo] = useState(true);
    const {
        data: report,
        isLoading,
        error,
    } = useCosts({
        project_id: projectId,
        days,
        exclude_source: hideDemo ? "demo" : undefined,
    });

    const envBlock = useMemo(
        () =>
            (report?.recommendations ?? [])
                .map((c) => `${c.setting.toUpperCase()}=${c.suggested}`)
                .join("\n"),
        [report],
    );

    return (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Space wrap align="center">
                <Typography.Title level={3} style={{ margin: 0 }}>
                    Costs
                </Typography.Title>
                <Select
                    aria-label="Scope"
                    style={{ minWidth: 260 }}
                    value={projectId ?? "all"}
                    onChange={(v) => setProjectId(v === "all" ? undefined : v)}
                    options={[
                        { value: "all", label: "Everything (all projects, CLI, live checks)" },
                        ...(projects ?? []).map((p) => ({
                            value: p.id,
                            label: `${p.name} · ${p.client.brand_name}`,
                        })),
                    ]}
                />
                <Select
                    aria-label="Period"
                    style={{ width: 150 }}
                    value={days ?? 0}
                    onChange={(v) => setDays(v === 0 ? undefined : v)}
                    options={[
                        { value: 0, label: "All time" },
                        { value: 1, label: "Last 24 h" },
                        { value: 7, label: "Last 7 days" },
                        { value: 30, label: "Last 30 days" },
                    ]}
                />
                <Switch
                    id="hide-demo"
                    aria-label="Hide demo data"
                    checked={hideDemo}
                    onChange={setHideDemo}
                />
                <label htmlFor="hide-demo">Hide demo data</label>
                {!hideDemo && (
                    <Tag color="warning">includes seeded demonstration rows (source "demo")</Tag>
                )}
            </Space>

            {isLoading && <Skeleton active paragraph={{ rows: 6 }} />}
            {error && <Alert type="error" showIcon message={error.message} />}
            {report && !report.calls && (
                <Alert
                    type="info"
                    showIcon
                    message="No vendor calls recorded for this scope yet."
                    description="Calls are recorded from the next run onwards."
                />
            )}
            {report && report.calls > 0 && (
                <>
                    <Row gutter={[12, 12]}>
                        <Col xs={12} md={6}>
                            <Card size="small">
                                <Statistic title="Vendor calls" value={report.calls} />
                            </Card>
                        </Col>
                        <Col xs={12} md={6}>
                            <Card size="small">
                                <Statistic
                                    title="Estimated (ledger reservation)"
                                    value={money4(report.total_estimated_usd)}
                                />
                            </Card>
                        </Col>
                        <Col xs={12} md={6}>
                            <Card size="small">
                                <Tooltip title="Vendor-reported where available, else modelled from list prices, else the estimate">
                                    <Statistic
                                        title="Actual"
                                        value={money4(report.total_actual_usd)}
                                    />
                                </Tooltip>
                            </Card>
                        </Col>
                        <Col xs={12} md={6}>
                            <Card size="small">
                                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                    By source
                                </Typography.Text>
                                <div>
                                    {Object.entries(report.by_source).map(([k, v]) => (
                                        <div
                                            key={k}
                                            style={{
                                                display: "flex",
                                                justifyContent: "space-between",
                                                fontSize: 13,
                                            }}
                                        >
                                            <span>{k}</span>
                                            <b className="pe-num">{v}</b>
                                        </div>
                                    ))}
                                </div>
                            </Card>
                        </Col>
                    </Row>

                    <Card title="Per vendor" size="small">
                        <Table<VendorCost>
                            size="small"
                            rowKey="vendor"
                            pagination={false}
                            dataSource={report.vendors}
                            scroll={{ x: 1100 }}
                            columns={[
                                { title: "Vendor", dataIndex: "vendor" },
                                { title: "Calls", dataIndex: "calls", align: "right" },
                                { title: "OK", dataIndex: "ok", align: "right" },
                                {
                                    title: "Errors",
                                    align: "right",
                                    render: (_, v) =>
                                        v.errors + v.refused ? (
                                            <Typography.Text type="danger">
                                                {v.errors + v.refused}
                                            </Typography.Text>
                                        ) : (
                                            0
                                        ),
                                },
                                {
                                    title: "Tokens in / out",
                                    align: "right",
                                    render: (_, v) =>
                                        `${num(v.input_tokens)} / ${num(v.output_tokens)}`,
                                },
                                { title: "Searches", dataIndex: "search_calls", align: "right" },
                                { title: "Units", dataIndex: "units", align: "right" },
                                {
                                    title: "Estimated",
                                    align: "right",
                                    render: (_, v) => money4(v.estimated_usd),
                                },
                                {
                                    title: "Actual",
                                    align: "right",
                                    render: (_, v) => (
                                        <Tooltip
                                            title={`${v.calls_with_vendor_cost} vendor-reported · ${v.calls_with_modelled_cost} modelled`}
                                        >
                                            {money4(v.actual_usd)}
                                        </Tooltip>
                                    ),
                                },
                                {
                                    title: "Mean per OK call",
                                    align: "right",
                                    render: (_, v) => money(v.mean_actual_per_ok_call, 5),
                                },
                                {
                                    title: "p50 / p95 latency",
                                    align: "right",
                                    render: (_, v) =>
                                        `${Math.round(v.latency_p50_ms)} / ${Math.round(v.latency_p95_ms)} ms`,
                                },
                            ]}
                        />
                    </Card>

                    {report.recommendations.length > 0 && (
                        <Card
                            title="Proposed cost parameters (observed mean × 1.15)"
                            size="small"
                            extra={
                                <Button
                                    size="small"
                                    icon={<CopyOutlined />}
                                    onClick={async () => {
                                        try {
                                            await navigator.clipboard.writeText(envBlock);
                                            message.success("Copied the .env block.");
                                        } catch {
                                            message.error(
                                                "Clipboard unavailable; select the block and copy it.",
                                            );
                                        }
                                    }}
                                >
                                    Copy .env block
                                </Button>
                            }
                        >
                            <Table<CostRecommendation>
                                size="small"
                                rowKey="setting"
                                pagination={false}
                                dataSource={report.recommendations}
                                columns={[
                                    {
                                        title: "Setting",
                                        render: (_, c) => (
                                            <span className="pe-mono">
                                                {c.setting.toUpperCase()}
                                            </span>
                                        ),
                                    },
                                    { title: "Vendor", dataIndex: "vendor" },
                                    { title: "Unit", dataIndex: "unit" },
                                    { title: "Current", dataIndex: "current", align: "right" },
                                    {
                                        title: "Observed mean",
                                        dataIndex: "observed_mean",
                                        align: "right",
                                    },
                                    {
                                        title: "Suggested",
                                        align: "right",
                                        render: (_, c) => <b>{c.suggested}</b>,
                                    },
                                    {
                                        title: "Based on",
                                        render: (_, c) => `${c.basis_calls} ok calls · ${c.basis}`,
                                    },
                                ]}
                            />
                            <pre
                                aria-label="env block"
                                style={{
                                    marginTop: 12,
                                    padding: 12,
                                    background: "var(--ant-color-fill-tertiary)",
                                    borderRadius: 8,
                                    fontSize: 12,
                                }}
                            >
                                {envBlock}
                            </pre>
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                Copy the suggested values into .env so the ledger reservation
                                matches what the vendors actually charge.
                            </Typography.Text>
                        </Card>
                    )}

                    {report.runs.length > 0 && (
                        <Card title={`Per run (${report.runs.length})`} size="small">
                            <Table<RunCost>
                                size="small"
                                rowKey="run_id"
                                pagination={{ pageSize: 10, hideOnSinglePage: true }}
                                dataSource={report.runs}
                                columns={[
                                    {
                                        title: "Run",
                                        render: (_, r) => (
                                            <span className="pe-mono">{r.run_id}</span>
                                        ),
                                    },
                                    { title: "Calls", dataIndex: "calls", align: "right" },
                                    {
                                        title: "Estimated",
                                        align: "right",
                                        render: (_, r) => money4(r.estimated_usd),
                                    },
                                    {
                                        title: "Actual",
                                        align: "right",
                                        render: (_, r) => money4(r.actual_usd),
                                    },
                                    {
                                        title: "By vendor",
                                        render: (_, r) =>
                                            Object.entries(r.by_vendor)
                                                .map(([k, v]) => `${k} ${money4(v)}`)
                                                .join(" · "),
                                    },
                                ]}
                            />
                        </Card>
                    )}

                    {report.notes.map((n) => (
                        <Typography.Text
                            key={n}
                            type="secondary"
                            style={{ fontSize: 12, display: "block" }}
                        >
                            {n}
                        </Typography.Text>
                    ))}
                </>
            )}
        </Space>
    );
}
