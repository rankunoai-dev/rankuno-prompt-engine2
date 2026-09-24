/** Client-ready PDFs and the alert destination (ADR 0024). */
import { useState } from "react";
import {
    Alert,
    App,
    Button,
    Card,
    Checkbox,
    Descriptions,
    Empty,
    Form,
    Input,
    Modal,
    Popconfirm,
    Select,
    Skeleton,
    Space,
    Switch,
    Table,
    Tag,
    Tooltip,
    Typography,
} from "antd";
import { DownloadOutlined, FilePdfOutlined } from "@ant-design/icons";
import { useOutletContext } from "react-router-dom";
import type { AlertRecord, AlertRule, Project, ReportRecord, ReportRequest } from "@/api/endpoints";
import { endpoints } from "@/api/endpoints";
import {
    useAlertHistory,
    useAlerts,
    useCreateReport,
    useDeleteReport,
    useReports,
    useSaveAlerts,
} from "@/api/queries";
import { fmtDateTime } from "@/app/format";

const STATE_COLOR: Record<string, string> = {
    queued: "default",
    running: "processing",
    done: "success",
    failed: "error",
};

/** Every rule, with the sentence that says what it costs the reader. */
const RULES: { value: AlertRule; label: string; help: string }[] = [
    {
        value: "citation_drop",
        label: "Citation rate really fell",
        help: "Only when the two windows' 95% intervals do not overlap, so ordinary sampling noise stays quiet.",
    },
    {
        value: "lost_prompt",
        label: "A starred prompt stopped being cited",
        help: "Uses the prompts you marked important, so this stays low volume.",
    },
    {
        value: "competitor_surge",
        label: "A competitor appeared on several prompts",
        help: "Fires when one domain crosses the share threshold on two or more prompts in a window.",
    },
    {
        value: "negative_claim",
        label: "An engine said something negative, with a source",
        help: "Needs sentiment scoring to be on for the project.",
    },
    {
        value: "engine_silent",
        label: "A platform stopped answering",
        help: "Operational: catches a blocked key or billing before a client notices the gap.",
    },
    {
        value: "spend",
        label: "The daily budget is nearly gone",
        help: "Operational. Not something to send to a client.",
    },
    {
        value: "crawl_failed",
        label: "A crawl failed",
        help: "Operational.",
    },
];

const SUPPRESSED: Record<string, string> = {
    alerts_off: "Alerts are switched off",
    rule_off: "That rule is not enabled",
    cooldown: "Same alert already sent recently",
    daily_limit: "Daily ceiling reached",
    no_destination: "Nowhere to send it",
    sending_disabled: "Sending is disabled (ceiling is zero)",
};

function bytes(size: number | null): string {
    if (!size) return "—";
    return size > 1_000_000
        ? `${(size / 1_000_000).toFixed(1)} MB`
        : `${Math.round(size / 1000)} kB`;
}

function ExportDialog({
    project,
    open,
    onClose,
}: {
    project: Project;
    open: boolean;
    onClose: () => void;
}) {
    const { message } = App.useApp();
    const create = useCreateReport(project.id);
    const [form] = Form.useForm<ReportRequest & { email_text: string }>();

    const submit = async () => {
        const values = await form.validateFields();
        const recipients = (values.email_text ?? "")
            .split(/[\s,;]+/)
            .map((s) => s.trim())
            .filter(Boolean);
        try {
            await create.mutateAsync({
                title: values.title || null,
                narrative: values.narrative ?? true,
                include_actions: values.include_actions ?? true,
                include_sentiment: values.include_sentiment ?? true,
                include_pages: values.include_pages ?? true,
                email_to: recipients,
            });
            message.success(
                recipients.length
                    ? `Report queued; it will be emailed to ${recipients.length} recipient(s).`
                    : "Report queued.",
            );
            onClose();
        } catch (error) {
            message.error(error instanceof Error ? error.message : "Could not queue the report.");
        }
    };

    return (
        <Modal
            open={open}
            onCancel={onClose}
            onOk={submit}
            okText="Generate"
            confirmLoading={create.isPending}
            title="Export an executive report"
            destroyOnHidden
        >
            <Form
                form={form}
                layout="vertical"
                initialValues={{
                    narrative: true,
                    include_actions: true,
                    include_sentiment: true,
                    include_pages: true,
                    email_text: "",
                }}
            >
                <Form.Item
                    name="title"
                    label="Title"
                    extra="Printed on the cover. Defaults to the current month."
                >
                    <Input placeholder="AI visibility report — September 2026" maxLength={120} />
                </Form.Item>
                <Form.Item name="narrative" valuePropName="checked" style={{ marginBottom: 4 }}>
                    <Checkbox>
                        Write the executive summary with AI
                        <Typography.Text
                            type="secondary"
                            style={{ display: "block", fontSize: 12 }}
                        >
                            Every figure it writes is checked against the measured numbers; anything
                            that does not match is replaced with the standard wording.
                        </Typography.Text>
                    </Checkbox>
                </Form.Item>
                <Form.Item
                    name="include_actions"
                    valuePropName="checked"
                    style={{ marginBottom: 0 }}
                >
                    <Checkbox>Include recommended actions</Checkbox>
                </Form.Item>
                <Form.Item
                    name="include_sentiment"
                    valuePropName="checked"
                    style={{ marginBottom: 0 }}
                >
                    <Checkbox>Include how the engines describe the brand</Checkbox>
                </Form.Item>
                <Form.Item name="include_pages" valuePropName="checked">
                    <Checkbox>Include the cited pages and their URLs</Checkbox>
                </Form.Item>
                <Form.Item
                    name="email_text"
                    label="Email it to (optional)"
                    extra="Comma or newline separated. Needs SMTP settings on the server."
                >
                    <Input.TextArea rows={2} placeholder="priya@client.com, sam@client.com" />
                </Form.Item>
            </Form>
        </Modal>
    );
}

function AlertsCard({ project }: { project: Project }) {
    const { message } = App.useApp();
    const { data, isLoading } = useAlerts(project.id);
    const save = useSaveAlerts(project.id);
    const [webhook, setWebhook] = useState("");

    if (isLoading || !data) return <Skeleton active paragraph={{ rows: 4 }} />;

    const update = async (body: Parameters<typeof save.mutateAsync>[0], note: string) => {
        try {
            await save.mutateAsync(body);
            message.success(note);
            setWebhook("");
        } catch (error) {
            message.error(error instanceof Error ? error.message : "Could not save.");
        }
    };

    return (
        <Card
            title="Alerts"
            extra={
                <Switch
                    checked={data.enabled}
                    loading={save.isPending}
                    onChange={(enabled) =>
                        update({ enabled }, enabled ? "Alerts are on." : "Alerts are off.")
                    }
                    checkedChildren="On"
                    unCheckedChildren="Off"
                    aria-label="Alerts enabled"
                />
            }
        >
            <Space direction="vertical" size={16} style={{ width: "100%" }}>
                <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
                    A crawl that closes a window compares it with the previous one and sends what
                    passes the rules below. The same alert stays quiet for three days after it goes
                    out, and there is a hard ceiling per project per day.
                </Typography.Paragraph>

                <div>
                    <Typography.Text strong>Slack</Typography.Text>
                    <div style={{ marginTop: 6 }}>
                        {data.slack_configured ? (
                            <Space>
                                <Tag color="green">Configured</Tag>
                                <Typography.Text type="secondary">
                                    {data.slack_hint}
                                </Typography.Text>
                                <Button
                                    size="small"
                                    danger
                                    onClick={() =>
                                        update({ slack_webhook: "" }, "Slack destination removed.")
                                    }
                                >
                                    Remove
                                </Button>
                            </Space>
                        ) : (
                            <Space.Compact style={{ width: "100%" }}>
                                <Input
                                    value={webhook}
                                    onChange={(e) => setWebhook(e.target.value)}
                                    placeholder="https://hooks.slack.com/services/…"
                                    aria-label="Slack webhook"
                                />
                                <Button
                                    type="primary"
                                    disabled={!webhook.trim()}
                                    onClick={() =>
                                        update({ slack_webhook: webhook.trim() }, "Slack saved.")
                                    }
                                >
                                    Save
                                </Button>
                            </Space.Compact>
                        )}
                    </div>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        An incoming webhook URL. It is stored like a credential and never shown
                        again.
                    </Typography.Text>
                </div>

                <div>
                    <Typography.Text strong>Email</Typography.Text>
                    <div style={{ marginTop: 6 }}>
                        <Select
                            mode="tags"
                            style={{ width: "100%" }}
                            placeholder="priya@client.com"
                            aria-label="Alert recipients"
                            value={data.email_hints}
                            open={false}
                            onChange={(values: string[]) =>
                                update(
                                    { email_to: values.filter((v) => !v.includes("***")) },
                                    "Recipients saved.",
                                )
                            }
                        />
                    </div>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        Addresses are masked once saved. Re-enter the full list to change it.
                    </Typography.Text>
                </div>

                <div>
                    <Typography.Text strong>What to send</Typography.Text>
                    <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
                        {RULES.map((rule) => (
                            <Tooltip key={rule.value} title={rule.help} placement="left">
                                <Checkbox
                                    checked={data.rules.includes(rule.value)}
                                    onChange={(e) => {
                                        const next = e.target.checked
                                            ? [...data.rules, rule.value]
                                            : data.rules.filter((r) => r !== rule.value);
                                        void update({ rules: next }, "Rules saved.");
                                    }}
                                >
                                    {rule.label}
                                </Checkbox>
                            </Tooltip>
                        ))}
                    </div>
                </div>
            </Space>
        </Card>
    );
}

function AlertHistory({ project }: { project: Project }) {
    const { data } = useAlertHistory(project.id);
    if (!data?.length) return null;
    return (
        <Card title="Alert history" size="small">
            <Table<AlertRecord>
                dataSource={data}
                rowKey="id"
                size="small"
                pagination={{ pageSize: 8, hideOnSinglePage: true }}
                columns={[
                    {
                        title: "When",
                        dataIndex: "fired_at",
                        width: 170,
                        render: (value: string) => fmtDateTime(value),
                    },
                    { title: "What", dataIndex: "title" },
                    {
                        title: "Sent",
                        dataIndex: "delivered",
                        width: 220,
                        render: (delivered: boolean, row) =>
                            delivered ? (
                                <Tag color="green">{row.channels.join(", ") || "sent"}</Tag>
                            ) : (
                                <Tooltip title={row.error ?? undefined}>
                                    <Tag>
                                        {SUPPRESSED[row.suppressed_reason ?? ""] ??
                                            row.suppressed_reason ??
                                            "not sent"}
                                    </Tag>
                                </Tooltip>
                            ),
                    },
                ]}
            />
        </Card>
    );
}

export function ReportsPage() {
    const { project } = useOutletContext<{ project: Project }>();
    const { message } = App.useApp();
    const { data: reports, isLoading, error } = useReports(project.id);
    const remove = useDeleteReport(project.id);
    const [open, setOpen] = useState(false);

    if (error) return <Alert type="error" showIcon message={error.message} />;

    const columns = [
        {
            title: "Report",
            dataIndex: "title",
            render: (title: string, row: ReportRecord) => (
                <Space direction="vertical" size={0}>
                    <Typography.Text strong>{title}</Typography.Text>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {row.window_label || fmtDateTime(row.created_at)}
                    </Typography.Text>
                </Space>
            ),
        },
        {
            title: "State",
            dataIndex: "state",
            width: 140,
            render: (state: string, row: ReportRecord) => (
                <Tooltip title={row.error ?? undefined}>
                    <Tag color={STATE_COLOR[state] ?? "default"}>{state}</Tag>
                </Tooltip>
            ),
        },
        {
            title: "Summary by",
            dataIndex: "narrative_source",
            width: 150,
            render: (source: string | null, row: ReportRecord) =>
                source ? (
                    <Tooltip
                        title={
                            source === "template"
                                ? "Generated from the measured figures, without a model."
                                : `${row.narrative_model ?? "model"} — every figure checked against the measured numbers.`
                        }
                    >
                        <Tag>{source}</Tag>
                    </Tooltip>
                ) : (
                    "—"
                ),
        },
        {
            title: "Pages",
            dataIndex: "pages",
            width: 90,
            render: (pages: number | null, row: ReportRecord) =>
                pages ? `${pages} · ${bytes(row.size_bytes)}` : "—",
        },
        {
            title: "Emailed",
            dataIndex: "emailed_to",
            width: 100,
            render: (count: number) => (count ? `${count} recipient(s)` : "—"),
        },
        {
            title: "",
            key: "actions",
            width: 170,
            render: (_: unknown, row: ReportRecord) => (
                <Space>
                    <Button
                        size="small"
                        icon={<DownloadOutlined />}
                        disabled={row.state !== "done" || !!row.purged_at}
                        href={endpoints.reportDownloadUrl(project.id, row.id)}
                        download
                    >
                        PDF
                    </Button>
                    <Popconfirm
                        title="Delete this report?"
                        okText="Delete"
                        onConfirm={async () => {
                            await remove.mutateAsync(row.id);
                            message.success("Report deleted.");
                        }}
                    >
                        <Button size="small" danger type="text">
                            Delete
                        </Button>
                    </Popconfirm>
                </Space>
            ),
        },
    ];

    return (
        <div style={{ display: "grid", gap: 24 }}>
            <Card
                title="Executive reports"
                extra={
                    <Button type="primary" icon={<FilePdfOutlined />} onClick={() => setOpen(true)}>
                        Export a report
                    </Button>
                }
            >
                <Descriptions
                    size="small"
                    column={{ xs: 1, sm: 2, lg: 4 }}
                    items={[
                        {
                            key: "client",
                            label: "Cover name",
                            children: project.brand.client_name ?? project.client.brand_name,
                        },
                        {
                            key: "agency",
                            label: "Prepared by",
                            children: project.brand.agency_name ?? "—",
                        },
                        {
                            key: "colour",
                            label: "Colour",
                            children: (
                                <Space size={6}>
                                    <span
                                        aria-hidden
                                        style={{
                                            display: "inline-block",
                                            width: 12,
                                            height: 12,
                                            borderRadius: 3,
                                            background: project.brand.primary_colour,
                                        }}
                                    />
                                    {project.brand.primary_colour}
                                </Space>
                            ),
                        },
                        {
                            key: "spend",
                            label: "Vendor cost",
                            children: project.brand.show_spend ? "Shown" : "Hidden",
                        },
                    ]}
                />
                <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
                    Branding lives on the project’s settings. A report covers the latest
                    consolidated window and states the market it was measured in.
                </Typography.Paragraph>
            </Card>

            {isLoading ? (
                <Skeleton active paragraph={{ rows: 4 }} />
            ) : reports?.length ? (
                <Table<ReportRecord>
                    dataSource={reports}
                    rowKey="id"
                    columns={columns}
                    pagination={{ pageSize: 10, hideOnSinglePage: true }}
                />
            ) : (
                <Empty description="No reports yet. Export one to send a client the month's numbers." />
            )}

            <AlertsCard project={project} />
            <AlertHistory project={project} />

            <ExportDialog project={project} open={open} onClose={() => setOpen(false)} />
        </div>
    );
}
