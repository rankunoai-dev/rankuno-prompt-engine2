import { Card, Col, Row, Space, Statistic, Tooltip, Typography } from "antd";
import { pct, money, num } from "@/app/format";
import { ENGINE_LABEL, ENGINE_SHORT } from "@/app/theme";
import { DomainBars, TrendLine } from "@/components/charts/Charts";
import { EngineDot } from "@/components/EngineTag";
import {
    contentGaps,
    engineScoreboard,
    shareOfVoice,
    stageHeatmap,
    mean,
    STAGES,
} from "@/lib/atlas";
import { useAtlasState } from "./AtlasContext";
import { fmtDate } from "@/app/format";

const titleCase = (v: string) =>
    v
        .toLowerCase()
        .replace(/_/g, " ")
        .replace(/(^|\s)\S/g, (c) => c.toUpperCase());

export function AtlasOverview() {
    const { index, prompts, engines, asOf, setFilters, filters, goView, openPrompt } =
        useAtlasState();
    const board = engineScoreboard(index, prompts, engines, asOf);
    const citedAny = prompts.filter((p) =>
        engines.some((e) => index.latest(p.prompt_id, e, asOf)?.client_cited),
    );
    const rates = prompts.flatMap((p) =>
        engines
            .map((e) => index.latest(p.prompt_id, e, asOf))
            .filter(Boolean)
            .map((s) => s!.client_citation_rate),
    );
    const mentions = prompts.flatMap((p) =>
        engines
            .map((e) => index.latest(p.prompt_id, e, asOf))
            .filter(Boolean)
            .map((s) => s!.mention_rate),
    );
    const triggers = prompts.flatMap((p) =>
        engines
            .map((e) => index.latest(p.prompt_id, e, asOf))
            .filter(Boolean)
            .map((s) => s!.web_trigger_rate),
    );
    const gaps = contentGaps(index, prompts, engines, asOf);
    const asRun = asOf ? index.runById.get(asOf) : index.runs[index.runs.length - 1];
    const ri = index.runs.findIndex((r) => r.run_id === asRun?.run_id);
    const prevRun = ri > 0 ? index.runs[ri - 1] : null;
    const prevCited = prevRun
        ? prompts.filter((p) =>
              engines.some((e) => index.latest(p.prompt_id, e, prevRun.run_id)?.client_cited),
          ).length
        : null;
    const spend = index.runs
        .filter((r) => !asRun || r.started_at <= asRun.started_at)
        .reduce((a, r) => a + (r.estimated_cost_usd || 0), 0);
    const trendPoints = engines.flatMap((e) =>
        board
            .find((b) => b.engine === e)!
            .trend.map((v, i) => ({
                run: index.runs[i]!.run_id,
                date: fmtDate(index.runs[i]!.started_at),
                engine: e,
                value: v,
            })),
    );
    const heat = stageHeatmap(index, prompts, engines, asOf);
    const sov = shareOfVoice(index, prompts, engines, asOf);
    const topDomains = sov.domains.filter((d) => d.count > 0 || d.role === "client").slice(0, 12);
    const delta = prevCited === null ? null : citedAny.length - prevCited;

    return (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Row gutter={[12, 12]}>
                <Col xs={24} md={8}>
                    <Card size="small" style={{ height: "100%" }}>
                        <Statistic
                            title="Prompts cited on at least one engine"
                            value={citedAny.length}
                            suffix={`/ ${prompts.length}`}
                        />
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {delta === null
                                ? "no earlier run to compare"
                                : `${delta > 0 ? "▲" : delta < 0 ? "▼" : "•"} ${Math.abs(delta)} vs run of ${fmtDate(prevRun!.started_at)}`}
                        </Typography.Text>
                    </Card>
                </Col>
                <Col xs={24} md={16}>
                    <Row gutter={[12, 12]}>
                        {[
                            [
                                "Mean citation rate",
                                pct(mean(rates)),
                                "across prompt × engine samples",
                            ],
                            ["Mean mention rate", pct(mean(mentions)), "brand named in the text"],
                            [
                                "Web-search trigger",
                                pct(mean(triggers)),
                                "engine reached the live web",
                            ],
                            [
                                "Content gaps",
                                String(gaps.reduce((a, g) => a + g.prompts.length, 0)),
                                "prompts with no landing page",
                            ],
                            [
                                "Spend to date",
                                money(spend),
                                `${index.runs.length} run(s) in this dataset`,
                            ],
                            [
                                "Branded / non-branded",
                                `${prompts.filter((p) => p.prompt_type === "BRANDED").length} / ${prompts.filter((p) => p.prompt_type !== "BRANDED").length}`,
                                "prompts in view",
                            ],
                        ].map(([t, v, s]) => (
                            <Col xs={12} lg={8} key={t}>
                                <Card size="small" style={{ height: "100%" }}>
                                    <Tooltip title={s}>
                                        <Statistic
                                            title={t}
                                            value={v}
                                            valueStyle={{ fontSize: 22 }}
                                        />
                                    </Tooltip>
                                </Card>
                            </Col>
                        ))}
                    </Row>
                </Col>
            </Row>

            <Row gutter={[12, 12]}>
                {board.map((b) => (
                    <Col xs={24} sm={12} xl={6} key={b.engine}>
                        <Card
                            size="small"
                            hoverable
                            onClick={() => goView(`engines:${b.engine}`)}
                            style={{ borderTop: `3px solid var(--pe-engine, #888)` }}
                            styles={{ body: { padding: 12 } }}
                        >
                            <Space size={6}>
                                <EngineDot engine={b.engine} />
                                <Typography.Text strong>
                                    {ENGINE_LABEL[b.engine] ?? b.engine}
                                </Typography.Text>
                            </Space>
                            <div
                                style={{
                                    display: "grid",
                                    gridTemplateColumns: "1fr 1fr",
                                    gap: "4px 12px",
                                    marginTop: 8,
                                    fontSize: 12,
                                }}
                                className="pe-num"
                            >
                                <span>
                                    Cited{" "}
                                    <b>
                                        {b.cited}/{b.audited}
                                    </b>
                                </span>
                                <span>
                                    Mean rate <b>{pct(b.meanCited)}</b>
                                </span>
                                <span>
                                    Mentioned <b>{pct(b.meanMentioned)}</b>
                                </span>
                                <span>
                                    Median rank{" "}
                                    <b>{b.medianBestRank ? `#${b.medianBestRank}` : "—"}</b>
                                </span>
                                <span>
                                    Web trigger <b>{pct(b.webTrigger)}</b>
                                </span>
                                <span>
                                    Failed <b>{b.failedSamples}</b>
                                </span>
                            </div>
                            <Sparkline values={b.trend} engine={b.engine} />
                        </Card>
                    </Col>
                ))}
            </Row>

            <Row gutter={[12, 12]}>
                <Col xs={24} xl={14}>
                    <Card
                        size="small"
                        title="Citation rate by run"
                        extra={
                            <Typography.Text type="secondary">
                                mean over prompts in view
                            </Typography.Text>
                        }
                    >
                        {index.runs.length > 1 ? (
                            <TrendLine points={trendPoints} label="Citation rate by run" />
                        ) : (
                            <Typography.Text type="secondary">
                                One run only; the trend appears from the second run.
                            </Typography.Text>
                        )}
                    </Card>
                </Col>
                <Col xs={24} xl={10}>
                    <Card
                        size="small"
                        title="Citation rate by decision stage"
                        extra={
                            <Typography.Text type="secondary">
                                click a cell to filter
                            </Typography.Text>
                        }
                    >
                        <div
                            style={{
                                display: "grid",
                                gridTemplateColumns: `110px repeat(${engines.length}, 1fr)`,
                                gap: 3,
                                fontSize: 12,
                            }}
                            role="table"
                            aria-label="Stage heatmap"
                        >
                            <div />
                            {engines.map((e) => (
                                <div
                                    key={e}
                                    style={{
                                        textAlign: "center",
                                        color: "var(--ant-color-text-tertiary)",
                                    }}
                                >
                                    {ENGINE_SHORT[e] ?? e}
                                </div>
                            ))}
                            {STAGES.map((st) => (
                                <FragmentRow
                                    key={st}
                                    label={titleCase(st)}
                                    cells={engines.map((e) =>
                                        heat.find((h) => h.row === st && h.engine === e)!,
                                    )}
                                    onClick={(e) => {
                                        setFilters({ ...filters, stage: st, engines: [e] });
                                        goView("sheet");
                                    }}
                                />
                            ))}
                        </div>
                    </Card>
                </Col>
            </Row>

            <Card
                size="small"
                title="Who gets cited instead"
                extra={
                    <Typography.Text type="secondary">
                        share of answers citing each domain · click a domain
                    </Typography.Text>
                }
            >
                <DomainBars
                    label="Domains cited"
                    points={topDomains.map((d) => ({
                        label: d.domain,
                        value: d.share,
                        role: d.role,
                    }))}
                    onClick={(dom) => {
                        setFilters({ ...filters, domain: dom });
                        goView("voice");
                    }}
                />
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {num(sov.total)} answers in view.{" "}
                    {gaps.length ? `${gaps.length} subtopics need a page.` : ""}{" "}
                    {citedAny[0] && (
                        <a onClick={() => openPrompt(citedAny[0]!.prompt_id)}>
                            Open the first cited prompt
                        </a>
                    )}
                </Typography.Text>
            </Card>
        </Space>
    );
}

function FragmentRow({
    label,
    cells,
    onClick,
}: {
    label: string;
    cells: { engine: string; value: number | null; prompts: number }[];
    onClick: (engine: never) => void;
}) {
    return (
        <>
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    color: "var(--ant-color-text-secondary)",
                }}
            >
                {label}
            </div>
            {cells.map((c) => (
                <button
                    key={c.engine}
                    type="button"
                    className="pe-focus-ring"
                    onClick={() => onClick(c.engine as never)}
                    title={`${label} · ${ENGINE_SHORT[c.engine] ?? c.engine}: ${pct(c.value)} over ${c.prompts} prompts`}
                    style={{
                        height: 36,
                        border: 0,
                        borderRadius: 5,
                        cursor: "pointer",
                        font: "inherit",
                        fontVariantNumeric: "tabular-nums",
                        background:
                            c.value === null
                                ? "var(--ant-color-fill-tertiary)"
                                : `rgba(31, 94, 255, ${0.12 + c.value * 0.75})`,
                        color: c.value !== null && c.value > 0.5 ? "#fff" : "var(--ant-color-text)",
                    }}
                >
                    {c.value === null ? "·" : pct(c.value)}
                </button>
            ))}
        </>
    );
}

export function Sparkline({ values, engine }: { values: (number | null)[]; engine: string }) {
    const pts = values.map((v, i) => ({ x: i, y: v ?? 0, has: v !== null }));
    const w = 200;
    const h = 36;
    const n = Math.max(1, pts.length - 1);
    const path = pts
        .map((p, i) => `${i === 0 ? "M" : "L"}${(p.x / n) * w},${h - 3 - p.y * (h - 6)}`)
        .join(" ");
    const last = pts[pts.length - 1];
    const color =
        {
            GOOGLE_AI_OVERVIEW: "#2a78d6",
            CHATGPT_SEARCH: "#eb6834",
            PERPLEXITY: "#1baf7a",
            GEMINI: "#7c6ce0",
        }[engine] ?? "#888";
    return (
        <svg
            viewBox={`0 0 ${w} ${h}`}
            style={{ width: "100%", height: 36, marginTop: 8 }}
            aria-hidden
        >
            {pts.length > 1 && (
                <path d={path} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />
            )}
            {last && (
                <circle cx={(last.x / n) * w} cy={h - 3 - last.y * (h - 6)} r={3.5} fill={color} />
            )}
        </svg>
    );
}
