import { Card, Col, Row, Space, Statistic, Table, Tag, Typography } from "antd";
import { num, pct } from "@/app/format";
import { ENGINE_SHORT } from "@/app/theme";
import { EngineDot } from "@/components/EngineTag";
import { contentGaps, regDomain, shareOfVoice } from "@/lib/atlas";
import { useAtlasState } from "./AtlasContext";

const ROLE_LABEL = { client: "Client", comp: "Competitor", other: "Other" } as const;
const ROLE_COLOR = { client: "success", comp: "error", other: "default" } as const;

export function AtlasVoice() {
    const { index, prompts, engines, asOf, filters, setFilters, openPrompt } = useAtlasState();
    const sov = shareOfVoice(index, prompts, engines, asOf);
    const focus = filters.domain ? regDomain(filters.domain) : null;
    const rows = sov.domains.slice(0, 40);
    const hits = focus
        ? prompts
              .flatMap((p) =>
                  engines.flatMap((e) => {
                      const s = index.latest(p.prompt_id, e, asOf);
                      const pos = s ? s.cited_domains.map(regDomain).indexOf(focus) : -1;
                      return s && pos >= 0 ? [{ p, e, s, pos: pos + 1 }] : [];
                  }),
              )
              .sort((a, b) => a.pos - b.pos)
        : [];
    return (
        <Space direction="vertical" size={14} style={{ width: "100%" }}>
            <Typography.Text type="secondary">
                For each domain, the share of prompts in view whose latest answer on that engine
                cites it. Click a domain to see where it wins.
            </Typography.Text>
            <Table
                size="small"
                rowKey="domain"
                dataSource={rows}
                pagination={false}
                onRow={(r) => ({
                    onClick: () =>
                        setFilters({ ...filters, domain: r.domain === focus ? "" : r.domain }),
                    style: {
                        cursor: "pointer",
                        background: r.domain === focus ? "var(--ant-color-primary-bg)" : undefined,
                    },
                })}
                columns={[
                    {
                        title: "Domain",
                        render: (_, r) => <span className="pe-mono">{r.domain}</span>,
                    },
                    {
                        title: "Role",
                        render: (_, r) => (
                            <Tag color={ROLE_COLOR[r.role]}>{ROLE_LABEL[r.role]}</Tag>
                        ),
                    },
                    ...engines.map((e) => ({
                        title: (
                            <span>
                                <EngineDot engine={e} /> {ENGINE_SHORT[e]}
                            </span>
                        ),
                        align: "right" as const,
                        render: (_: unknown, r: (typeof rows)[number]) => {
                            const n = sov.perEngineTotal[e] ?? 0;
                            return (
                                <span className="pe-num">
                                    {n ? pct((r.perEngine[e] ?? 0) / n) : "·"}
                                </span>
                            );
                        },
                    })),
                    {
                        title: "Overall",
                        align: "right",
                        render: (_, r) => <span className="pe-num">{pct(r.share)}</span>,
                    },
                ]}
            />
            {sov.domains.length > rows.length && (
                <Typography.Text type="secondary">
                    {sov.domains.length - rows.length} more domains were cited less often and are
                    not listed.
                </Typography.Text>
            )}
            {focus && (
                <Card size="small" title={`Prompts where ${focus} is cited`}>
                    {hits.length ? (
                        <Table
                            size="small"
                            rowKey={(r) => `${r.p.prompt_id}|${r.e}`}
                            dataSource={hits}
                            pagination={{ pageSize: 20, hideOnSinglePage: true }}
                            onRow={(r) => ({
                                onClick: () => openPrompt(r.p.prompt_id, r.e),
                                style: { cursor: "pointer" },
                            })}
                            columns={[
                                { title: "Prompt", render: (_, r) => r.p.prompt_text },
                                {
                                    title: "Engine",
                                    render: (_, r) => (
                                        <span>
                                            <EngineDot engine={r.e} /> {ENGINE_SHORT[r.e]}
                                        </span>
                                    ),
                                },
                                {
                                    title: `${focus} position`,
                                    align: "right",
                                    render: (_, r) => `#${r.pos}`,
                                },
                                {
                                    title: "Client best rank",
                                    align: "right",
                                    render: (_, r) =>
                                        r.s.client_best_rank ? `#${r.s.client_best_rank}` : "—",
                                },
                                {
                                    title: "Client rate",
                                    align: "right",
                                    render: (_, r) => pct(r.s.client_citation_rate),
                                },
                            ]}
                        />
                    ) : (
                        <Typography.Text type="secondary">
                            Not cited in any answer for the current filters.
                        </Typography.Text>
                    )}
                </Card>
            )}
        </Space>
    );
}

export function AtlasGaps() {
    const { index, prompts, engines, asOf, openPrompt } = useAtlasState();
    const gaps = contentGaps(index, prompts, engines, asOf);
    const mapped = prompts.filter((p) => !p.content_gap);
    const byUrl = new Map<string, typeof mapped>();
    for (const p of mapped) byUrl.set(p.mapped_url!, [...(byUrl.get(p.mapped_url!) ?? []), p]);
    return (
        <Space direction="vertical" size={14} style={{ width: "100%" }}>
            <Row gutter={[12, 12]}>
                {[
                    ["Gap prompts", String(gaps.reduce((a, g) => a + g.prompts.length, 0))],
                    ["Pages to create", String(gaps.length)],
                    ["Demand behind gaps", num(gaps.reduce((a, g) => a + g.volume, 0))],
                    ["Gaps already cited", String(gaps.reduce((a, g) => a + g.alreadyCited, 0))],
                ].map(([t, v]) => (
                    <Col xs={12} md={6} key={t}>
                        <Card size="small">
                            <Statistic title={t} value={v} valueStyle={{ fontSize: 22 }} />
                        </Card>
                    </Col>
                ))}
            </Row>
            <Row gutter={[12, 12]}>
                {gaps.map((g) => (
                    <Col xs={24} lg={12} key={g.subtopic}>
                        <Card
                            size="small"
                            title={
                                <span>
                                    <Tag color="warning">△ Content gap</Tag> Need {g.subtopic} page
                                </span>
                            }
                            extra={
                                <code style={{ fontSize: 11 }}>
                                    [CONTENT GAP: Need {g.subtopic} Page]
                                </code>
                            }
                        >
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                {g.prompts.length} prompt(s) · {num(g.volume)} searches/mo ·{" "}
                                {g.alreadyCited} already cited without a page
                            </Typography.Text>
                            <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
                                {g.prompts.map((p) => (
                                    <li key={p.prompt_id}>
                                        <a onClick={() => openPrompt(p.prompt_id)}>
                                            {p.prompt_text}
                                        </a>{" "}
                                        <Tag>{p.decision_stage.toLowerCase()}</Tag>
                                    </li>
                                ))}
                            </ul>
                            <Typography.Paragraph
                                style={{ marginTop: 10, marginBottom: 0, fontSize: 12 }}
                            >
                                <b>Page brief:</b> answer these prompts in the first 100 words, one
                                definition sentence, an FAQ block per prompt, and target the fan-out
                                queries the engines ran for this subtopic.
                            </Typography.Paragraph>
                        </Card>
                    </Col>
                ))}
                {!gaps.length && (
                    <Col span={24}>
                        <Typography.Text type="secondary">
                            No content gaps in the current selection.
                        </Typography.Text>
                    </Col>
                )}
            </Row>
            <Card size="small" title="Landing-page coverage">
                <Table
                    size="small"
                    rowKey={(r) => r[0]}
                    dataSource={[...byUrl.entries()].sort((a, b) => b[1].length - a[1].length)}
                    pagination={false}
                    columns={[
                        {
                            title: "Landing page",
                            render: (_, [url, ps]) => (
                                <div>
                                    <a
                                        href={url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="pe-mono"
                                    >
                                        {url.replace(/^https?:\/\/(www\.)?/, "")}
                                    </a>
                                    <div style={{ fontSize: 12 }}>
                                        {ps.map((p) => (
                                            <a
                                                key={p.prompt_id}
                                                onClick={() => openPrompt(p.prompt_id)}
                                                style={{ marginRight: 8 }}
                                            >
                                                {p.prompt_text.slice(0, 60)}
                                                {p.prompt_text.length > 60 ? "…" : ""}
                                            </a>
                                        ))}
                                    </div>
                                </div>
                            ),
                        },
                        { title: "Prompts", align: "right", render: (_, [, ps]) => ps.length },
                        ...engines.map((e) => ({
                            title: ENGINE_SHORT[e],
                            align: "right" as const,
                            render: (_: unknown, [, ps]: [string, typeof mapped]) =>
                                `${ps.filter((p) => index.latest(p.prompt_id, e, asOf)?.client_cited).length}/${ps.length}`,
                        })),
                    ]}
                />
            </Card>
        </Space>
    );
}
