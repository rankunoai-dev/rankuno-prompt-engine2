/**
 * Layer 1: where do we stand, what changed, what do we do now. Nothing else.
 */
import {
    Alert,
    Button,
    Card,
    Col,
    Empty,
    Row,
    Skeleton,
    Space,
    Tag,
    Tooltip,
    Typography,
} from "antd";
import { ArrowDownOutlined, ArrowUpOutlined, RightOutlined } from "@ant-design/icons";
import { AnimatePresence } from "framer-motion";
import { Link, useOutletContext } from "react-router-dom";
import type { EngineHealth, InsightChange, Insights, Project } from "@/api/endpoints";
import { useInsights, usePositions } from "@/api/queries";
import { pct } from "@/app/format";
import { ENGINE_LABEL } from "@/app/theme";
import { ActionCardView } from "@/components/ActionCardView";
import { EngineDot } from "@/components/EngineTag";
import { VerdictTag } from "@/components/VerdictTag";
import { ExactPages, fromInventory } from "@/components/ExactPages";
import { pagesForEngine } from "@/lib/pages";
import { useLenis } from "@/lib/useLenis";

const CHANGE_LABEL: Record<string, string> = {
    flip_up: "Now cited",
    flip_down: "No longer cited",
    rank_up: "Rank up",
    rank_down: "Rank down",
    new_competitor: "New competitor",
    lost_platform: "Lost platform",
};

export function OverviewPage() {
    const { project } = useOutletContext<{ project: Project }>();
    const { data: insights, isLoading, error } = useInsights(project.id);
    const { data: positions } = usePositions(project.id);
    useLenis();

    if (isLoading) return <Skeleton active paragraph={{ rows: 8 }} />;
    if (error) return <Alert type="error" showIcon message={error.message} />;
    if (!insights) return null;

    const open = insights.actions.filter((a) => a.status === "open");
    const top = open.slice(0, 3);
    const basis = insights.basis;

    return (
        <Space direction="vertical" size={18} style={{ width: "100%" }}>
            {basis.low_confidence && (
                <Alert
                    type="warning"
                    showIcon
                    message={`Low confidence: ${basis.crawls} of ${project.consolidation_runs} crawls`}
                    description={
                        basis.computed_from === "consolidation"
                            ? `Computed from consolidation ${basis.consolidation_id?.slice(0, 8)} over ${basis.samples} samples.`
                            : basis.computed_from === "latest_crawl"
                              ? `Computed from the latest single crawl (${basis.samples} samples). Verdicts firm up after ${project.consolidation_runs} full crawls.`
                              : "No crawl yet. Run the project from the Runs tab."
                    }
                />
            )}

            <section aria-label="Health">
                <Row gutter={[12, 12]}>
                    {insights.health.map((h) => (
                        <Col key={h.engine} xs={24} sm={12} xl={6}>
                            <HealthTile
                                h={h}
                                insights={insights}
                                competitors={project.client.competitor_domains}
                            />
                        </Col>
                    ))}
                    {!insights.health.length && (
                        <Col span={24}>
                            <Empty description="No platform data yet" />
                        </Col>
                    )}
                </Row>
            </section>

            <Row gutter={[16, 16]}>
                <Col xs={24} lg={10}>
                    <Card title="What changed" size="small">
                        {insights.changes.length ? (
                            <Space direction="vertical" size={8} style={{ width: "100%" }}>
                                {insights.changes.slice(0, 5).map((c, i) => (
                                    <ChangeLine key={i} c={c} projectId={project.id} />
                                ))}
                            </Space>
                        ) : (
                            <Typography.Text type="secondary">
                                {positions?.history.length
                                    ? "Nothing moved between the last two consolidations."
                                    : `No previous consolidation to compare: ${positions?.runs_since_last ?? 0} of ${project.consolidation_runs} crawls done.`}
                            </Typography.Text>
                        )}
                    </Card>
                </Col>
                <Col xs={24} lg={14}>
                    <Card
                        title={`Top actions${open.length ? ` (${Math.min(3, open.length)} of ${open.length})` : ""}`}
                        size="small"
                        extra={
                            open.length > 3 && (
                                <Link to={`/projects/${project.id}/actions`}>
                                    <Button size="small" type="link">
                                        Show all actions ({open.length}) <RightOutlined />
                                    </Button>
                                </Link>
                            )
                        }
                    >
                        {top.length ? (
                            <Space direction="vertical" size={16} style={{ width: "100%" }}>
                                <AnimatePresence initial={false}>
                                    {top.map((a) => (
                                        <ActionCardView
                                            key={a.id}
                                            action={a}
                                            projectId={project.id}
                                            impactMax={Math.max(...open.map((x) => x.impact_score))}
                                        />
                                    ))}
                                </AnimatePresence>
                            </Space>
                        ) : (
                            <Typography.Text type="secondary">
                                No open action. Cards appear once a crawl finds a mention without a
                                link, a competitor page winning a cluster, a read-but-rejected
                                client page, or an AI Overview gap.
                            </Typography.Text>
                        )}
                    </Card>
                </Col>
            </Row>
        </Space>
    );
}

function HealthTile({
    h,
    insights,
    competitors,
}: {
    h: EngineHealth;
    insights: Insights | undefined;
    competitors: string[];
}) {
    const pages = pagesForEngine(insights, h.engine, competitors);
    const delta = h.delta_cited_rate;
    return (
        <Tooltip
            title={`${h.samples} samples over ${h.crawls} crawl${h.crawls === 1 ? "" : "s"} · ${h.prompts} prompts · volatility ${pct(h.volatility)}`}
        >
            <Card size="small" style={{ height: "100%" }} data-testid={`health-${h.engine}`}>
                <Space direction="vertical" size={6} style={{ width: "100%" }}>
                    <Space size={6}>
                        <EngineDot engine={h.engine} />
                        <Typography.Text strong>
                            {ENGINE_LABEL[h.engine] ?? h.engine}
                        </Typography.Text>
                    </Space>
                    <VerdictTag verdict={h.verdict} losingTo={h.losing_to} />
                    <Space size={16} wrap className="pe-num">
                        <Stat label="cited" value={pct(h.cited_rate)} />
                        <Stat label="mentioned" value={pct(h.mention_rate)} />
                        <Stat label="best rank" value={h.best_rank ? `#${h.best_rank}` : "—"} />
                    </Space>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {delta === null ? (
                            "no previous consolidation"
                        ) : delta > 0.005 ? (
                            <Tag color="success" icon={<ArrowUpOutlined />}>
                                {pct(delta)} vs previous
                            </Tag>
                        ) : delta < -0.005 ? (
                            <Tag color="error" icon={<ArrowDownOutlined />}>
                                {pct(delta)} vs previous
                            </Tag>
                        ) : (
                            "unchanged vs previous"
                        )}
                    </Typography.Text>
                    {(pages.client || pages.rival) && (
                        <ExactPages
                            compact
                            showCount={false}
                            unit="citations"
                            client={pages.client ? [fromInventory(pages.client)] : []}
                            competitor={pages.rival ? [fromInventory(pages.rival)] : []}
                            emptyClient="No client page cited here"
                            emptyCompetitor="No competitor page cited here"
                        />
                    )}
                </Space>
            </Card>
        </Tooltip>
    );
}

function Stat({ label, value }: { label: string; value: string }) {
    return (
        <span>
            <Typography.Text strong style={{ fontSize: 18 }}>
                {value}
            </Typography.Text>{" "}
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {label}
            </Typography.Text>
        </span>
    );
}

function ChangeLine({ c, projectId }: { c: InsightChange; projectId: string }) {
    const up = c.kind === "flip_up" || c.kind === "rank_up";
    const down = c.kind === "flip_down" || c.kind === "rank_down" || c.kind === "lost_platform";
    return (
        <Link
            to={`/projects/${projectId}/battleground?prompt=${c.prompt_id}&engine=${c.engine}`}
            style={{ color: "inherit" }}
        >
            <Space align="start">
                <Tag color={up ? "success" : down ? "error" : "warning"} style={{ marginTop: 2 }}>
                    {CHANGE_LABEL[c.kind] ?? c.kind}
                </Tag>
                <div>
                    <div style={{ fontSize: 13 }}>{c.prompt_text}</div>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {ENGINE_LABEL[c.engine] ?? c.engine} · {c.before} → {c.after}
                        {c.text && c.text !== c.prompt_text ? ` · ${c.text}` : ""}
                    </Typography.Text>
                </div>
            </Space>
        </Link>
    );
}
