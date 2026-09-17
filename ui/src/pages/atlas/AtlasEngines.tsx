import { useState } from "react";
import { Card, Col, Row, Segmented, Space, Statistic, Table, Tag, Typography } from "antd";
import type { AtlasPrompt, Engine } from "@/api/endpoints";
import { pct } from "@/app/format";
import { ENGINE_LABEL, ENGINE_SHORT } from "@/app/theme";
import { DomainBars, RateHistogram } from "@/components/charts/Charts";
import { EngineDot } from "@/components/EngineTag";
import { engineScoreboard, rateBands, shareOfVoice, ATLAS_ENGINES } from "@/lib/atlas";
import { useAtlasState } from "./AtlasContext";
import { Sparkline } from "./AtlasOverview";

const CALLED: Record<string, string> = {
    GOOGLE_AI_OVERVIEW:
        "SerpApi google, then google_ai_overview with page_token · triggered when an AI Overview rendered",
    CHATGPT_SEARCH:
        "OpenAI Responses API with web_search forced · triggered when a web_search_call is present",
    PERPLEXITY:
        "Perplexity Agent API, perplexity/sonar, web_search forced · triggered when sources are returned",
    GEMINI: "Gemini generateContent with google_search grounding · triggered when webSearchQueries is non-empty",
};

export function AtlasEngines({ initial }: { initial?: Engine }) {
    const { index, prompts, asOf, openPrompt } = useAtlasState();
    const [engine, setEngine] = useState<Engine>(initial ?? "PERPLEXITY");
    const score = engineScoreboard(index, prompts, [engine], asOf)[0]!;
    const bins = rateBands(index, prompts, engine, asOf);
    const sov = shareOfVoice(index, prompts, [engine], asOf);
    const ranked = prompts
        .map((p) => ({ p, sn: index.latest(p.prompt_id, engine, asOf) }))
        .filter((x): x is { p: AtlasPrompt; sn: NonNullable<typeof x.sn> } => !!x.sn)
        .sort(
            (a, b) =>
                b.sn.client_citation_rate - a.sn.client_citation_rate ||
                (a.sn.client_best_rank ?? 99) - (b.sn.client_best_rank ?? 99),
        );

    return (
        <Space direction="vertical" size={14} style={{ width: "100%" }}>
            <Segmented
                aria-label="Engine"
                value={engine}
                onChange={(v) => setEngine(v as Engine)}
                options={ATLAS_ENGINES.map((e) => ({
                    value: e,
                    label: (
                        <span>
                            <EngineDot engine={e} /> {ENGINE_SHORT[e]}
                        </span>
                    ),
                }))}
            />
            <Typography.Text type="secondary">
                {ENGINE_LABEL[engine]}: {CALLED[engine]}
            </Typography.Text>
            <Row gutter={[12, 12]}>
                {[
                    ["Prompts cited", `${score.cited} / ${score.audited}`],
                    ["Mean citation rate", pct(score.meanCited)],
                    ["Mean mention rate", pct(score.meanMentioned)],
                    ["Median best rank", score.medianBestRank ? `#${score.medianBestRank}` : "—"],
                    ["Web-search trigger", pct(score.webTrigger)],
                ].map(([t, v]) => (
                    <Col xs={12} md={8} xl={4} key={t}>
                        <Card size="small">
                            <Statistic title={t} value={v} valueStyle={{ fontSize: 22 }} />
                        </Card>
                    </Col>
                ))}
                <Col xs={24} xl={4}>
                    <Card size="small">
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            Mean rate by run
                        </Typography.Text>
                        <Sparkline values={score.trend} engine={engine} />
                    </Card>
                </Col>
            </Row>
            <Row gutter={[12, 12]}>
                <Col xs={24} lg={10}>
                    <Card size="small" title="Distribution of citation rates">
                        <RateHistogram
                            bins={bins}
                            engine={engine}
                            label={`Citation rate distribution on ${ENGINE_SHORT[engine]}`}
                        />
                    </Card>
                </Col>
                <Col xs={24} lg={14}>
                    <Card size="small" title="Domains this engine cites">
                        <DomainBars
                            label={`Domains cited on ${ENGINE_SHORT[engine]}`}
                            points={sov.domains
                                .filter((d) => d.count > 0 || d.role === "client")
                                .slice(0, 10)
                                .map((d) => ({ label: d.domain, value: d.share, role: d.role }))}
                        />
                    </Card>
                </Col>
            </Row>
            <Card size="small" title="Prompts ranked by citation rate">
                <Table
                    size="small"
                    rowKey={(r) => r.p.prompt_id}
                    dataSource={ranked}
                    pagination={{ pageSize: 25, hideOnSinglePage: true }}
                    onRow={(r) => ({
                        onClick: () => openPrompt(r.p.prompt_id, engine),
                        style: { cursor: "pointer" },
                    })}
                    columns={[
                        {
                            title: "Prompt",
                            render: (_, r) => (
                                <div>
                                    <div style={{ fontWeight: 500 }}>{r.p.prompt_text}</div>
                                    <div className="pe-muted" style={{ fontSize: 12 }}>
                                        {r.p.prompt_type === "BRANDED" ? "Branded" : "Non-branded"}{" "}
                                        · {r.p.decision_stage.toLowerCase()}
                                    </div>
                                </div>
                            ),
                        },
                        {
                            title: "Cited",
                            align: "right",
                            render: (_, r) => (
                                <span className="pe-num">{pct(r.sn.client_citation_rate)}</span>
                            ),
                        },
                        {
                            title: "Mentioned",
                            align: "right",
                            render: (_, r) => (
                                <span className="pe-num">{pct(r.sn.mention_rate)}</span>
                            ),
                        },
                        {
                            title: "Best rank",
                            align: "right",
                            render: (_, r) =>
                                r.sn.client_best_rank ? `#${r.sn.client_best_rank}` : "—",
                        },
                        {
                            title: "History",
                            width: 160,
                            render: (_, r) => (
                                <Sparkline
                                    values={index
                                        .historyFor(r.p.prompt_id, engine, asOf)
                                        .map((s) => s.client_citation_rate)}
                                    engine={engine}
                                />
                            ),
                        },
                        {
                            title: "Competitors ahead",
                            render: (_, r) => {
                                const ahead = Object.entries(r.sn.competitor_citations)
                                    .filter(
                                        ([, rank]) =>
                                            r.sn.client_best_rank === null ||
                                            rank < r.sn.client_best_rank,
                                    )
                                    .sort((a, b) => a[1] - b[1]);
                                return ahead.length ? (
                                    ahead.slice(0, 3).map(([d, rank]) => (
                                        <Tag key={d}>
                                            {d} #{rank}
                                        </Tag>
                                    ))
                                ) : (
                                    <span className="pe-muted">none</span>
                                );
                            },
                        },
                    ]}
                />
            </Card>
        </Space>
    );
}
