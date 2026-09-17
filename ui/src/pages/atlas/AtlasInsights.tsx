/** Claim ledger and query fan-out map, from the project's insights. */
import { Alert, Card, Col, Row, Skeleton, Space, Table, Tag, Typography } from "antd";
import { useInsights } from "@/api/queries";
import { pct } from "@/app/format";
import { ENGINE_SHORT } from "@/app/theme";
import { EngineDot } from "@/components/EngineTag";
import { useAtlasState } from "./AtlasContext";

export function AtlasInsights() {
    const { project, openPrompt } = useAtlasState();
    const { data, isLoading, error } = useInsights(project?.id);
    if (!project)
        return (
            <Alert
                type="info"
                showIcon
                message="No project tracks this line of business"
                description="Claims and fan-out come from a project's insights. Create a project for this LOB to see them."
            />
        );
    if (isLoading) return <Skeleton active paragraph={{ rows: 6 }} />;
    if (error) return <Alert type="error" showIcon message={error.message} />;
    if (!data) return null;
    return (
        <Space direction="vertical" size={14} style={{ width: "100%" }}>
            <Row gutter={[12, 12]}>
                <Col xs={24} xl={12}>
                    <Card
                        size="small"
                        title={`Query fan-out (${data.fanout.length})`}
                        extra={
                            <Typography.Text type="secondary">
                                the engines' own sub-queries
                            </Typography.Text>
                        }
                    >
                        {data.fanout.length ? (
                            <Table
                                size="small"
                                rowKey="query"
                                dataSource={data.fanout}
                                pagination={{ pageSize: 15, hideOnSinglePage: true }}
                                columns={[
                                    { title: "Query", dataIndex: "query" },
                                    {
                                        title: "Engines",
                                        render: (_, r) =>
                                            r.engines.map((e) => <EngineDot key={e} engine={e} />),
                                    },
                                    { title: "Prompts", dataIndex: "prompts", align: "right" },
                                    { title: "Subtopic", dataIndex: "subtopic" },
                                    {
                                        title: "Client covered",
                                        render: (_, r) => (
                                            <Tag color={r.client_covered ? "success" : "warning"}>
                                                {r.client_covered ? "yes" : "no"}
                                            </Tag>
                                        ),
                                    },
                                ]}
                            />
                        ) : (
                            <Typography.Text type="secondary">
                                No fan-out stored yet: samples captured before cycle 0011 carry no
                                queries.
                            </Typography.Text>
                        )}
                    </Card>
                </Col>
                <Col xs={24} xl={12}>
                    <Card
                        size="small"
                        title="Engine trust profile"
                        extra={
                            <Typography.Text type="secondary">
                                what kind of source each engine cites
                            </Typography.Text>
                        }
                    >
                        <Table
                            size="small"
                            rowKey={(r) => `${r.engine}|${r.domain_class}`}
                            dataSource={data.trust_profile}
                            pagination={false}
                            columns={[
                                {
                                    title: "Engine",
                                    render: (_, r) => (
                                        <span>
                                            <EngineDot engine={r.engine} /> {ENGINE_SHORT[r.engine]}
                                        </span>
                                    ),
                                },
                                { title: "Source class", dataIndex: "domain_class" },
                                { title: "Share", align: "right", render: (_, r) => pct(r.share) },
                                { title: "Citations", dataIndex: "citations", align: "right" },
                            ]}
                        />
                    </Card>
                </Col>
            </Row>
            <Card
                size="small"
                title={`Claim ledger (${data.claims.length})`}
                extra={
                    <Typography.Text type="secondary">
                        which sentence the engines attribute to whom
                    </Typography.Text>
                }
            >
                <Table
                    size="small"
                    rowKey={(r, i) => `${r.prompt_id}|${r.url}|${i}`}
                    dataSource={data.claims}
                    pagination={{ pageSize: 20, hideOnSinglePage: true }}
                    onRow={(r) => ({
                        onClick: () => openPrompt(r.prompt_id, r.engine),
                        style: { cursor: "pointer" },
                    })}
                    columns={[
                        { title: "Claim", dataIndex: "sentence", width: "45%" },
                        {
                            title: "Attributed to",
                            render: (_, r) => (
                                <Tag
                                    color={
                                        r.is_client
                                            ? "success"
                                            : r.is_competitor
                                              ? "error"
                                              : "default"
                                    }
                                >
                                    {r.domain}
                                </Tag>
                            ),
                        },
                        {
                            title: "Engine",
                            render: (_, r) => (
                                <span>
                                    <EngineDot engine={r.engine} /> {ENGINE_SHORT[r.engine]}
                                </span>
                            ),
                        },
                        {
                            title: "URL",
                            render: (_, r) => (
                                <a
                                    href={r.url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="pe-mono pe-ellipsis"
                                    style={{ display: "block", maxWidth: 260 }}
                                >
                                    {r.url.replace(/^https?:\/\/(www\.)?/, "")}
                                </a>
                            ),
                        },
                    ]}
                />
            </Card>
            {data.winning_pages.length > 0 && (
                <Card size="small" title="Pages that win the citations">
                    <Table
                        size="small"
                        rowKey="url"
                        dataSource={data.winning_pages}
                        pagination={{ pageSize: 10, hideOnSinglePage: true }}
                        columns={[
                            {
                                title: "Page",
                                render: (_, r) => (
                                    <a href={r.url} target="_blank" rel="noopener noreferrer">
                                        {r.title || r.url}
                                    </a>
                                ),
                            },
                            { title: "Domain", dataIndex: "domain" },
                            { title: "Citations", dataIndex: "citations", align: "right" },
                            { title: "Prompts", dataIndex: "prompts", align: "right" },
                            {
                                title: "Engines",
                                render: (_, r) =>
                                    r.engines.map((e) => <EngineDot key={e} engine={e} />),
                            },
                        ]}
                    />
                </Card>
            )}
        </Space>
    );
}
