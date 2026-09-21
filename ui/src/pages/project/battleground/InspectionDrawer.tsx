/**
 * Layer 3: everything behind one prompt × platform cell. Consolidated numbers
 * when a position set exists, every crawl on its own date, the answer with
 * brand sentences highlighted and citations annotated, links, consulted
 * URLs, the engine's queries and the organic ranks.
 */
import { useMemo } from "react";
import {
    Alert,
    Descriptions,
    Divider,
    Drawer,
    Empty,
    Select,
    Skeleton,
    Space,
    Tag,
    Typography,
} from "antd";
import type {
    AtlasSnapshot,
    ConsolidatedPosition,
    Engine,
    Project,
    PromptResult,
} from "@/api/endpoints";
import { useAtlas, useCrawls, useSamples } from "@/api/queries";
import { ENGINE_LABEL } from "@/app/theme";
import { fmtDateTime, hostOf, pct } from "@/app/format";
import { EngineTag } from "@/components/EngineTag";
import { highlightMentions } from "@/lib/matrix";
import { ExactPages, fromHit } from "@/components/ExactPages";
import { pageHits } from "@/lib/pages";

interface Props {
    project: Project;
    result: PromptResult | null;
    engine: Engine | null;
    position: ConsolidatedPosition | undefined;
    runId: string | null;
    onRunChange: (runId: string | null) => void;
    onClose: () => void;
}

const normalise = (d: string) => d.toLowerCase().replace(/^www\./, "");

function domainRole(domain: string, project: Project): "client" | "competitor" | "other" {
    const n = normalise(domain);
    if (project.client.domains.map(normalise).some((d) => n === d || n.endsWith(`.${d}`)))
        return "client";
    if (
        project.client.competitor_domains.map(normalise).some((d) => n === d || n.endsWith(`.${d}`))
    ) {
        return "competitor";
    }
    return "other";
}

const ROLE_COLOR = { client: "success", competitor: "error", other: "default" } as const;

export function InspectionDrawer({
    project,
    result,
    engine,
    position,
    runId,
    onRunChange,
    onClose,
}: Props) {
    const open = !!result && !!engine;
    const promptId = result?.prompt.prompt_id ?? "";
    const { data: atlas } = useAtlas(open ? project.client.lob : null);
    const { data: crawls } = useCrawls(open ? project.id : undefined);
    const history = useMemo<AtlasSnapshot[]>(
        () =>
            (atlas?.snapshots ?? [])
                .filter((s) => s.prompt_id === promptId && s.engine === engine)
                .sort((a, b) => b.captured_at.localeCompare(a.captured_at)),
        [atlas, promptId, engine],
    );
    const latest = result && engine ? result.snapshots[engine] : null;
    const effectiveRun = runId ?? history[0]?.run_id ?? null;
    const {
        data: samples,
        isLoading: samplesLoading,
        error: samplesError,
    } = useSamples(
        open ? project.id : undefined,
        open && engine ? { prompt_id: promptId, engine, run_id: effectiveRun } : null,
    );
    const crawlOfRun = (rid: string) => crawls?.find((c) => c.run_ids.includes(rid));
    const sample = samples?.[0] ?? null;
    const answerText =
        sample?.answer_text || sample?.answer_excerpt || latest?.answer_excerpt || "";
    const snippets = useMemo(
        () => [
            ...(sample?.mentions.map((m) => m.snippet) ?? []),
            ...(latest?.mention_snippets.map((m) => m.snippet) ?? []),
        ],
        [sample, latest],
    );
    const claimByStart = useMemo(() => {
        const m = new Map<string, string>();
        for (const c of sample?.citation_claims ?? []) m.set(c.sentence, hostOf(c.url));
        return m;
    }, [sample]);
    const links = sample?.citation_links.length
        ? sample.citation_links
        : (latest?.citation_links ?? []);
    const consulted = sample?.consulted_urls.length
        ? sample.consulted_urls
        : (latest?.consulted_urls ?? []);
    const queries = sample?.search_queries ?? [];
    // Pages across every stored crawl for this prompt on this platform.
    const pages = useMemo(() => pageHits(history, project), [history, project]);

    return (
        <Drawer
            open={open}
            onClose={onClose}
            width={720}
            destroyOnClose
            title={
                result && engine ? (
                    <Space direction="vertical" size={2}>
                        <Typography.Text strong style={{ whiteSpace: "normal" }}>
                            {result.prompt.prompt_text}
                        </Typography.Text>
                        <Space size={6}>
                            <EngineTag engine={engine} />
                            {result.prompt.subtopic && <Tag>{result.prompt.subtopic}</Tag>}
                            <Typography.Text type="secondary" className="pe-mono">
                                {result.prompt.prompt_id}
                            </Typography.Text>
                        </Space>
                    </Space>
                ) : null
            }
        >
            {result && engine && (
                <Space direction="vertical" size={18} style={{ width: "100%" }}>
                    <section aria-label="Consolidated numbers">
                        <Typography.Title level={5} style={{ marginTop: 0 }}>
                            Consolidated position
                        </Typography.Title>
                        {position ? (
                            <>
                                <Descriptions size="small" column={3} bordered>
                                    <Descriptions.Item label="Cited">
                                        {pct(position.citation_rate)} ({position.cited_samples}/
                                        {position.samples})
                                    </Descriptions.Item>
                                    <Descriptions.Item label="Mentioned">
                                        {pct(position.mention_rate)} ({position.mention_samples}/
                                        {position.samples})
                                    </Descriptions.Item>
                                    <Descriptions.Item label="Basis">
                                        {position.samples} samples · {position.runs} run(s)
                                    </Descriptions.Item>
                                    <Descriptions.Item label="Best rank">
                                        {position.best_rank ? `#${position.best_rank}` : "never"}
                                    </Descriptions.Item>
                                    <Descriptions.Item label="Mean rank">
                                        {position.mean_rank ?? "—"}
                                    </Descriptions.Item>
                                    <Descriptions.Item label="Window">
                                        {fmtDateTime(position.first_run_at)} →{" "}
                                        {fmtDateTime(position.last_run_at)}
                                    </Descriptions.Item>
                                </Descriptions>
                                <RankDistribution dist={position.rank_distribution} />
                                <div style={{ marginTop: 8 }}>
                                    {Object.entries(position.cited_domain_share)
                                        .sort((a, b) => b[1] - a[1])
                                        .slice(0, 8)
                                        .map(([d, share]) => (
                                            <Tag key={d} color={ROLE_COLOR[domainRole(d, project)]}>
                                                {d} {pct(share)}
                                            </Tag>
                                        ))}
                                </div>
                                {Object.keys(position.competitor_citations).length > 0 && (
                                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                        Competitors best:{" "}
                                        {Object.entries(position.competitor_citations)
                                            .map(([d, r]) => `${d} #${r}`)
                                            .join(", ")}
                                    </Typography.Text>
                                )}
                            </>
                        ) : (
                            <Alert
                                type="info"
                                showIcon
                                message="No consolidated position yet"
                                description="Numbers below come from single crawls and are low confidence until the consolidation window completes."
                            />
                        )}
                    </section>

                    <section aria-label="Exact pages">
                        <Typography.Title level={5}>Exact pages over every crawl</Typography.Title>
                        <ExactPages
                            unit="crawls"
                            client={pages.client.slice(0, 4).map(fromHit)}
                            competitor={pages.competitor.slice(0, 4).map(fromHit)}
                            emptyClient="No client page has been cited for this prompt here"
                            emptyCompetitor="No competitor page has been cited here"
                        />
                    </section>

                    <section aria-label="Point-in-time history">
                        <Space style={{ width: "100%", justifyContent: "space-between" }} wrap>
                            <Typography.Title level={5} style={{ margin: 0 }}>
                                Crawls, each on its own date
                            </Typography.Title>
                            <Select
                                aria-label="Crawl"
                                size="small"
                                style={{ minWidth: 260 }}
                                value={effectiveRun ?? undefined}
                                placeholder="Latest"
                                onChange={(v) => onRunChange(v)}
                                options={history.map((h) => ({
                                    value: h.run_id,
                                    label: `${fmtDateTime(h.captured_at)} · ${h.client_cited ? `linked #${h.client_best_rank ?? "?"}` : h.mention_rate > 0 ? "mentioned" : "absent"}`,
                                }))}
                            />
                        </Space>
                        {history.length ? (
                            <table
                                style={{
                                    width: "100%",
                                    fontSize: 12,
                                    marginTop: 8,
                                    borderCollapse: "collapse",
                                }}
                            >
                                <thead>
                                    <tr
                                        style={{
                                            color: "var(--ant-color-text-tertiary)",
                                            textAlign: "left",
                                        }}
                                    >
                                        <th style={{ padding: "4px 6px" }}>Date</th>
                                        <th style={{ padding: "4px 6px" }}>Crawl</th>
                                        <th style={{ padding: "4px 6px" }}>Cited</th>
                                        <th style={{ padding: "4px 6px" }}>Mentioned</th>
                                        <th style={{ padding: "4px 6px" }}>Rank</th>
                                        <th style={{ padding: "4px 6px" }}>Model</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {history.map((h) => (
                                        <tr
                                            key={h.run_id}
                                            className="pe-row-clickable"
                                            onClick={() => onRunChange(h.run_id)}
                                            style={{
                                                background:
                                                    h.run_id === effectiveRun
                                                        ? "var(--ant-color-primary-bg)"
                                                        : undefined,
                                                borderTop:
                                                    "1px solid var(--ant-color-border-secondary)",
                                            }}
                                        >
                                            <td style={{ padding: "4px 6px" }}>
                                                {fmtDateTime(h.captured_at)}
                                            </td>
                                            <td style={{ padding: "4px 6px" }} className="pe-mono">
                                                {crawlOfRun(h.run_id)?.id.slice(0, 8) ??
                                                    h.run_id.slice(0, 8)}
                                            </td>
                                            <td style={{ padding: "4px 6px" }}>
                                                {pct(h.client_citation_rate)} (
                                                {h.client_cited_samples}/{h.samples})
                                            </td>
                                            <td style={{ padding: "4px 6px" }}>
                                                {pct(h.mention_rate)}
                                            </td>
                                            <td style={{ padding: "4px 6px" }}>
                                                {h.client_best_rank
                                                    ? `#${h.client_best_rank}`
                                                    : "—"}
                                            </td>
                                            <td style={{ padding: "4px 6px" }} className="pe-mono">
                                                {h.model}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        ) : (
                            <Typography.Text type="secondary">
                                No stored crawl for this prompt on {ENGINE_LABEL[engine]}.
                            </Typography.Text>
                        )}
                    </section>

                    <section aria-label="Answer">
                        <Typography.Title level={5}>Answer text</Typography.Title>
                        {samplesLoading && <Skeleton active paragraph={{ rows: 4 }} />}
                        {samplesError && (
                            <Alert
                                type="warning"
                                showIcon
                                message={samplesError.message}
                                description="Showing the stored excerpt instead."
                            />
                        )}
                        {!samplesLoading && !answerText && (
                            <Empty description="No answer text stored" />
                        )}
                        {answerText && (
                            <Typography.Paragraph
                                style={{ whiteSpace: "pre-wrap", lineHeight: 1.6 }}
                            >
                                {highlightMentions(answerText, snippets).map((part, i) => {
                                    const claim = [...claimByStart.entries()].find(([sentence]) =>
                                        part.text.includes(sentence),
                                    );
                                    return (
                                        <span
                                            key={i}
                                            style={
                                                part.hit
                                                    ? {
                                                          background: "var(--ant-color-warning-bg)",
                                                          borderRadius: 3,
                                                          padding: "0 2px",
                                                      }
                                                    : undefined
                                            }
                                        >
                                            {part.text}
                                            {claim && (
                                                <Tag
                                                    style={{ marginInline: 4, fontSize: 10 }}
                                                    color={
                                                        ROLE_COLOR[domainRole(claim[1], project)]
                                                    }
                                                >
                                                    {claim[1]}
                                                </Tag>
                                            )}
                                        </span>
                                    );
                                })}
                            </Typography.Paragraph>
                        )}
                        {samples && samples.length > 1 && (
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                {samples.length} samples in this crawl; showing the first.
                            </Typography.Text>
                        )}
                    </section>

                    <section aria-label="Citations">
                        <Typography.Title level={5}>Citation links</Typography.Title>
                        {links.length ? (
                            <ol style={{ paddingLeft: 20, margin: 0 }}>
                                {links.map((c) => (
                                    <li key={c.url} style={{ marginBottom: 4 }}>
                                        <a href={c.url} target="_blank" rel="noopener noreferrer">
                                            {c.title || c.url}
                                        </a>{" "}
                                        <Tag
                                            color={ROLE_COLOR[domainRole(c.domain, project)]}
                                            style={{ marginInlineStart: 4 }}
                                        >
                                            {c.domain} #{c.position}
                                        </Tag>
                                    </li>
                                ))}
                            </ol>
                        ) : (
                            <Typography.Text type="secondary">No citation links.</Typography.Text>
                        )}
                        {consulted.length > 0 && (
                            <>
                                <Divider plain style={{ margin: "12px 0" }}>
                                    Consulted but not cited ({consulted.length})
                                </Divider>
                                <ul style={{ paddingLeft: 20, margin: 0, fontSize: 12 }}>
                                    {consulted.map((u) => (
                                        <li key={u}>
                                            <a href={u} target="_blank" rel="noopener noreferrer">
                                                {u}
                                            </a>
                                            {domainRole(hostOf(u), project) === "client" && (
                                                <Tag color="error" style={{ marginInlineStart: 6 }}>
                                                    read but rejected
                                                </Tag>
                                            )}
                                        </li>
                                    ))}
                                </ul>
                            </>
                        )}
                    </section>

                    <section aria-label="Engine queries and organic rank">
                        <Typography.Title level={5}>The engine searched for</Typography.Title>
                        {queries.length ? (
                            <Space wrap>
                                {queries.map((q) => (
                                    <Tag key={q}>{q}</Tag>
                                ))}
                            </Space>
                        ) : (
                            <Typography.Text type="secondary">
                                No fan-out queries stored for this sample (captured before cycle
                                0011).
                            </Typography.Text>
                        )}
                        <Descriptions size="small" column={2} style={{ marginTop: 12 }}>
                            <Descriptions.Item label="Organic rank for the prompt">
                                {result.organic_prompt?.client_position
                                    ? `#${result.organic_prompt.client_position}`
                                    : "not ranked"}
                            </Descriptions.Item>
                            <Descriptions.Item label="Organic rank for the keyword">
                                {result.organic_keyword?.client_position
                                    ? `#${result.organic_keyword.client_position}`
                                    : "not ranked"}
                            </Descriptions.Item>
                        </Descriptions>
                    </section>
                </Space>
            )}
        </Drawer>
    );
}

function RankDistribution({ dist }: { dist: Record<string, number> }) {
    const entries = Object.entries(dist).sort((a, b) => {
        if (a[0] === "not cited") return 1;
        if (b[0] === "not cited") return -1;
        return Number(a[0]) - Number(b[0]);
    });
    const total = entries.reduce((a, [, n]) => a + n, 0);
    if (!total) return null;
    return (
        <div style={{ marginTop: 10 }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Rank distribution across {total} samples
            </Typography.Text>
            <div
                style={{
                    display: "flex",
                    height: 12,
                    borderRadius: 999,
                    overflow: "hidden",
                    gap: 2,
                    marginTop: 4,
                }}
            >
                {entries.map(([k, n]) => (
                    <div
                        key={k}
                        title={`${k === "not cited" ? "not cited" : `#${k}`}: ${n}`}
                        style={{
                            width: `${(n / total) * 100}%`,
                            background:
                                k === "not cited"
                                    ? "var(--ant-color-fill-secondary)"
                                    : "var(--ant-color-success)",
                            opacity: k === "not cited" ? 1 : Math.max(0.35, 1 - Number(k) * 0.12),
                        }}
                    />
                ))}
            </div>
        </div>
    );
}
