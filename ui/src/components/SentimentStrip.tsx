/**
 * "How engines describe you": one tile per platform with the negative share and
 * its 95% band, the attributes engines attach to the brand, and the worst
 * sentence with its source (ADR 0021). Averages on listicle answers are mostly
 * neutral, so the tile leads with the outliers and the attributes, not a score.
 */
import { Alert, Card, Col, Row, Space, Tag, Tooltip, Typography } from "antd";
import type { Insights, SentimentProfile } from "@/api/endpoints";
import { hostOf, pct, pctRange } from "@/app/format";
import { ENGINE_LABEL } from "@/app/theme";
import { EngineDot } from "./EngineTag";

export const POLARITY_COLOR: Record<string, string> = {
    positive: "success",
    neutral: "default",
    negative: "error",
    not_about_brand: "default",
};

export function SentimentStrip({ insights }: { insights: Insights }) {
    const coverage = insights.sentiment_coverage;
    if (!coverage.configured) {
        return (
            <Alert
                type="info"
                showIcon
                data-testid="sentiment-not-configured"
                message="Sentiment is not scored yet"
                description="Set ANTHROPIC_API_KEY on the server and run a crawl: every brand mention is then scored for polarity and attributes."
            />
        );
    }
    const client = insights.sentiment.filter((p) => p.entity === "client");
    return (
        <section aria-label="How engines describe you" data-testid="sentiment-strip">
            <Space
                style={{ width: "100%", justifyContent: "space-between", marginBottom: 8 }}
                align="baseline"
            >
                <Typography.Text strong>How engines describe you</Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {coverage.judged} sentences scored
                    {coverage.unscored ? ` · ${coverage.unscored} unscored` : ""}
                    {coverage.model ? ` · by ${coverage.model}` : ""}
                    {coverage.rubric_version ? ` · rubric ${coverage.rubric_version}` : ""}
                </Typography.Text>
            </Space>
            <Row gutter={[12, 12]}>
                {client.map((p) => (
                    <Col key={p.engine} xs={24} sm={12} xl={6}>
                        <SentimentTile p={p} />
                    </Col>
                ))}
            </Row>
        </section>
    );
}

function SentimentTile({ p }: { p: SentimentProfile }) {
    const scored = p.positive + p.neutral + p.negative;
    const worst = p.worst[0];
    return (
        <Card size="small" style={{ height: "100%" }} data-testid={`sentiment-${p.engine}`}>
            <Space direction="vertical" size={6} style={{ width: "100%" }}>
                <Space size={6}>
                    <EngineDot engine={p.engine} />
                    <Typography.Text strong>{ENGINE_LABEL[p.engine] ?? p.engine}</Typography.Text>
                </Space>
                {scored === 0 ? (
                    <Typography.Text type="secondary">
                        Not scored{p.unscored ? ` · ${p.unscored} unscored` : ""}
                    </Typography.Text>
                ) : (
                    <>
                        <Tooltip
                            title={`${p.negative} negative, ${p.neutral} neutral, ${p.positive} positive of ${scored} scored sentences${p.not_about_brand ? `; ${p.not_about_brand} not about the brand` : ""}`}
                        >
                            <span className="pe-num">
                                <Typography.Text
                                    strong
                                    style={{ fontSize: 18 }}
                                    type={p.negative > 0 ? "danger" : undefined}
                                >
                                    {pct(p.negative_share)}
                                </Typography.Text>{" "}
                                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                    negative
                                </Typography.Text>
                                {p.negative_share_low != null && (
                                    <Typography.Text
                                        type="secondary"
                                        style={{ fontSize: 11, display: "block", lineHeight: 1.2 }}
                                    >
                                        likely{" "}
                                        {pctRange(p.negative_share_low, p.negative_share_high)}
                                    </Typography.Text>
                                )}
                            </span>
                        </Tooltip>
                        {p.attributes.length > 0 && (
                            <div>
                                {p.attributes.slice(0, 3).map((a) => (
                                    <Tooltip key={a.attribute} title={`“${a.example}”`}>
                                        <Tag bordered={false} style={{ marginBottom: 4 }}>
                                            {a.attribute} ×{a.count}
                                        </Tag>
                                    </Tooltip>
                                ))}
                            </div>
                        )}
                        {worst && (
                            <blockquote
                                className="pe-quote"
                                style={{ margin: 0, fontSize: 12 }}
                                data-testid="sentiment-worst"
                            >
                                “{worst.text}”
                                {worst.url && (
                                    <div className="pe-muted">
                                        via{" "}
                                        <a
                                            href={worst.url}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                        >
                                            {hostOf(worst.url)}
                                        </a>
                                    </div>
                                )}
                            </blockquote>
                        )}
                    </>
                )}
            </Space>
        </Card>
    );
}
