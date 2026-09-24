/**
 * Which AI crawlers fetched which pages, and whether the engines then cited them.
 *
 * The tab answers one question the rest of the product cannot: when a page is
 * not cited, is that because no crawler ever fetched it, because the fetch was
 * blocked, or because the engine read it and chose something else. Prompt scope
 * does not apply here — a fetch belongs to a page, not to a prompt.
 */
import { useMemo, useRef, useState } from "react";
import {
    Alert,
    Card,
    Empty,
    Popconfirm,
    Popover,
    Segmented,
    Select,
    Skeleton,
    Space,
    Switch,
    Table,
    Tag,
    Tooltip,
    Typography,
} from "antd";
import { Link, useOutletContext } from "react-router-dom";
import type {
    BotSummary,
    CrawlerImportRecord,
    CrawlerLogView,
    FetchedNotCited,
    FunnelPage,
    PageFetches,
    Project,
} from "@/api/endpoints";
import { useCrawlerLogs, useDeleteCrawlerImport } from "@/api/queries";
import { ENGINE_LABEL } from "@/app/theme";
import { fmtDate, fmtDateTime, num, relative } from "@/app/format";
import { MATCH_TAG, PURPOSE, pagePath, sortFunnel } from "@/lib/crawlerLog";
import { CrawlerImportCard } from "./crawlers/CrawlerImportCard";

const WINDOWS = [7, 30, 90, 400];
const VERIFIED_WARN = 0.9;

function PurposeTag({ purpose }: { purpose: string }) {
    const spec = PURPOSE[purpose] ?? { label: purpose, color: "default" };
    return (
        <Tag color={spec.color} bordered={false}>
            {spec.label}
        </Tag>
    );
}

function Coverage({ view }: { view: CrawlerLogView }) {
    const vendors = Object.entries(view.ranges?.vendors ?? {});
    return (
        <Card size="small">
            <Space direction="vertical" size={4}>
                <Typography.Text>
                    Logs cover <b className="pe-num">{view.covered_days}</b> of the last {view.days}{" "}
                    days
                    {view.since && view.until
                        ? ` · ${fmtDate(view.since)} – ${fmtDate(view.until)}`
                        : ""}
                </Typography.Text>
                {vendors.length > 0 && (
                    <Tooltip
                        title={`Address lists fetched ${view.ranges ? fmtDateTime(view.ranges.fetched_at) : "—"}`}
                    >
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            Crawler address lists:{" "}
                            {vendors
                                .map(([vendor, date]) => `${vendor} ${fmtDate(String(date))}`)
                                .join(", ")}
                        </Typography.Text>
                    </Tooltip>
                )}
            </Space>
        </Card>
    );
}

function ByBot({ rows }: { rows: BotSummary[] }) {
    return (
        <Card title="Which crawlers came" size="small">
            <Table<BotSummary>
                dataSource={rows}
                rowKey="bot"
                size="small"
                pagination={false}
                columns={[
                    {
                        title: "Crawler",
                        dataIndex: "bot",
                        render: (bot: string, row) => (
                            <Space direction="vertical" size={0}>
                                <Space size={6}>
                                    <Typography.Text strong>{bot}</Typography.Text>
                                    {bot.startsWith("Googlebot") && (
                                        <Tooltip title="Shown for reference; Googlebot never produces an action card.">
                                            <Tag bordered={false}>reference</Tag>
                                        </Tooltip>
                                    )}
                                </Space>
                                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                    {row.vendor}
                                </Typography.Text>
                            </Space>
                        ),
                    },
                    {
                        title: "Purpose",
                        dataIndex: "purpose",
                        width: 130,
                        render: (purpose: string) => <PurposeTag purpose={purpose} />,
                    },
                    {
                        title: "Platform",
                        dataIndex: "engine",
                        width: 150,
                        render: (engine: string | null) =>
                            engine ? (
                                <Tag bordered={false}>{ENGINE_LABEL[engine] ?? engine}</Tag>
                            ) : (
                                <Typography.Text type="secondary">—</Typography.Text>
                            ),
                    },
                    {
                        title: "Fetches",
                        dataIndex: "hits",
                        width: 100,
                        align: "right",
                        render: (hits: number) => num(hits),
                    },
                    {
                        title: "Verified",
                        dataIndex: "verified_hits",
                        width: 150,
                        render: (verified: number | null, row) => {
                            if (verified === null)
                                return (
                                    <Tooltip title="This vendor publishes no address list, so a fetch cannot be verified.">
                                        <Typography.Text type="secondary">
                                            unverifiable
                                        </Typography.Text>
                                    </Tooltip>
                                );
                            const share = row.hits ? verified / row.hits : 1;
                            const body = `${num(verified)} / ${num(row.hits)}`;
                            return share < VERIFIED_WARN ? (
                                <Tooltip
                                    title={`${num(row.hits - verified)} fetches came from outside the vendor's published addresses`}
                                >
                                    <Typography.Text type="warning">{body}</Typography.Text>
                                </Tooltip>
                            ) : (
                                body
                            );
                        },
                    },
                    {
                        title: "Blocked",
                        dataIndex: "blocked",
                        width: 90,
                        align: "right",
                        render: (blocked: number) =>
                            blocked > 0 ? (
                                <Typography.Text type="danger">{num(blocked)}</Typography.Text>
                            ) : (
                                "—"
                            ),
                    },
                    { title: "Pages", dataIndex: "pages", width: 80, align: "right" },
                    {
                        title: "Last seen",
                        dataIndex: "last_seen",
                        width: 130,
                        render: (iso: string | null) => (iso ? relative(iso) : "—"),
                    },
                ]}
            />
        </Card>
    );
}

/** Daily volume with uncovered days drawn as gaps, never as zero. */
function DailyStrip({ days }: { days: CrawlerLogView["daily"] }) {
    const peak = Math.max(1, ...days.map((d) => d.hits));
    return (
        <Card title="Fetches per day" size="small">
            <div className="pe-crawler-strip" data-testid="daily-strip">
                {days.map((day) => {
                    const height = day.covered ? Math.max(3, (day.hits / peak) * 100) : 100;
                    const title = day.covered
                        ? `${fmtDate(day.day)}: ${num(day.hits)} fetch(es)`
                        : `${fmtDate(day.day)}: no log covers this day`;
                    return (
                        <Tooltip key={day.day} title={title}>
                            <div
                                data-covered={day.covered ? "yes" : "no"}
                                aria-label={title}
                                className={day.covered ? "pe-crawler-bar" : "pe-crawler-gap"}
                                style={{ height: `${height}%` }}
                            />
                        </Tooltip>
                    );
                })}
            </div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Hatched columns are days no uploaded log covers — not days without crawlers.
            </Typography.Text>
        </Card>
    );
}

function FetchPopover({ fetches }: { fetches: PageFetches[] }) {
    return (
        <Table<PageFetches>
            dataSource={fetches}
            rowKey="bot"
            size="small"
            pagination={false}
            columns={[
                { title: "Crawler", dataIndex: "bot" },
                {
                    title: "Purpose",
                    dataIndex: "purpose",
                    render: (p: string) => <PurposeTag purpose={p} />,
                },
                { title: "Fetches", dataIndex: "hits", align: "right" },
                { title: "OK", dataIndex: "ok", align: "right" },
                { title: "Blocked", dataIndex: "blocked", align: "right" },
                { title: "Redirected", dataIndex: "redirected", align: "right" },
                {
                    title: "Verified",
                    dataIndex: "verified",
                    align: "right",
                    render: (v: number | null) => (v === null ? "—" : num(v)),
                },
                {
                    title: "Last seen",
                    dataIndex: "last_seen",
                    render: (iso: string | null) => (iso ? relative(iso) : "—"),
                },
            ]}
        />
    );
}

function Funnel({ view, project }: { view: CrawlerLogView; project: Project }) {
    const [bot, setBot] = useState<string | null>(null);
    const [purpose, setPurpose] = useState<string | null>(null);
    const [match, setMatch] = useState<string | null>(null);
    const [hideAssets, setHideAssets] = useState(true);

    const rows = useMemo(() => {
        const filtered = view.pages.filter((page) => {
            if (hideAssets && page.is_asset) return false;
            if (match && page.match !== match) return false;
            if (bot && !page.fetches.some((f) => f.bot === bot)) return false;
            if (purpose && !page.fetches.some((f) => f.purpose === purpose)) return false;
            return true;
        });
        return sortFunnel(filtered);
    }, [view.pages, bot, purpose, match, hideAssets]);

    return (
        <Card
            title="Fetch → consulted → cited"
            size="small"
            extra={
                <Space wrap size={8}>
                    <Select
                        allowClear
                        size="small"
                        placeholder="Crawler"
                        aria-label="Crawler"
                        style={{ width: 150 }}
                        value={bot}
                        onChange={(v) => setBot(v ?? null)}
                        options={view.by_bot.map((b) => ({ value: b.bot, label: b.bot }))}
                    />
                    <Select
                        allowClear
                        size="small"
                        placeholder="Purpose"
                        aria-label="Purpose"
                        style={{ width: 140 }}
                        value={purpose}
                        onChange={(v) => setPurpose(v ?? null)}
                        options={Object.entries(PURPOSE).map(([value, spec]) => ({
                            value,
                            label: spec.label,
                        }))}
                    />
                    <Select
                        allowClear
                        size="small"
                        placeholder="Cited?"
                        aria-label="Cited"
                        style={{ width: 150 }}
                        value={match}
                        onChange={(v) => setMatch(v ?? null)}
                        options={[
                            { value: "exact", label: "Cited" },
                            { value: "near", label: "Cited as a variant" },
                            { value: "none", label: "Never cited" },
                        ]}
                    />
                    <Space size={4}>
                        <Switch
                            size="small"
                            checked={hideAssets}
                            onChange={setHideAssets}
                            aria-label="Hide assets"
                        />
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            Hide assets
                        </Typography.Text>
                    </Space>
                </Space>
            }
        >
            <Table<FunnelPage>
                dataSource={rows}
                rowKey="url_key"
                size="small"
                pagination={{ pageSize: 20, hideOnSinglePage: true }}
                rowClassName={(row) => (row.is_asset ? "pe-row-muted" : "")}
                columns={[
                    {
                        title: "Page",
                        dataIndex: "url_key",
                        render: (key: string, row) => (
                            <Space direction="vertical" size={2}>
                                <Typography.Link
                                    href={`https://${key}`}
                                    target="_blank"
                                    rel="noreferrer noopener"
                                >
                                    {pagePath(key, project.client.domains)}
                                </Typography.Link>
                                <Space size={4} wrap>
                                    {row.is_asset && <Tag bordered={false}>asset</Tag>}
                                    {row.redirected_only && (
                                        <Tag color="gold" bordered={false}>
                                            redirects — check the target
                                        </Tag>
                                    )}
                                    {row.fetches.some((f) => f.blocked > 0) && (
                                        <Tag color="red" bordered={false}>
                                            blocked
                                        </Tag>
                                    )}
                                </Space>
                            </Space>
                        ),
                    },
                    {
                        title: "Fetches",
                        dataIndex: "total_fetches",
                        width: 110,
                        align: "right",
                        render: (total: number, row) => (
                            <Popover
                                content={<FetchPopover fetches={row.fetches} />}
                                title="Per crawler"
                                trigger="hover"
                            >
                                <Typography.Text underline>{num(total)}</Typography.Text>
                            </Popover>
                        ),
                    },
                    {
                        title: "Consulted",
                        dataIndex: "consulted",
                        width: 110,
                        align: "right",
                        render: (consulted: number | null) =>
                            consulted === null ? (
                                <Tooltip title="Only ChatGPT reports the pages it read">
                                    <Typography.Text type="secondary">n/a</Typography.Text>
                                </Tooltip>
                            ) : (
                                num(consulted)
                            ),
                    },
                    {
                        title: "Cited",
                        key: "cited",
                        width: 110,
                        align: "right",
                        render: (_: unknown, row) => {
                            const entries = Object.entries(row.cited ?? {});
                            const total = entries.reduce((sum, [, n]) => sum + Number(n), 0);
                            if (!total)
                                return <Typography.Text type="secondary">0</Typography.Text>;
                            return (
                                <Tooltip
                                    title={entries
                                        .map(([e, n]) => `${ENGINE_LABEL[e] ?? e}: ${n}`)
                                        .join(" · ")}
                                >
                                    <Typography.Text underline>{num(total)}</Typography.Text>
                                </Tooltip>
                            );
                        },
                    },
                    {
                        title: "Verdict",
                        dataIndex: "match",
                        width: 150,
                        render: (value: string, row) => {
                            const tag = MATCH_TAG[value];
                            if (!tag) return <Typography.Text type="secondary">—</Typography.Text>;
                            const chip = (
                                <Tag color={tag.color} bordered={false}>
                                    {tag.label}
                                </Tag>
                            );
                            return value === "near" && row.near_urls.length ? (
                                <Tooltip title={row.near_urls.join("\n")}>{chip}</Tooltip>
                            ) : (
                                chip
                            );
                        },
                    },
                    {
                        title: "Last fetch",
                        dataIndex: "last_fetch",
                        width: 120,
                        render: (iso: string | null) => (iso ? relative(iso) : "—"),
                    },
                    {
                        title: "Last cited",
                        dataIndex: "last_cited",
                        width: 120,
                        render: (iso: string | null) => (iso ? relative(iso) : "—"),
                    },
                ]}
            />
        </Card>
    );
}

const NEVER_CITED_SHOWN = 6;

function NeverCited({ rows, project }: { rows: FetchedNotCited[]; project: Project }) {
    if (!rows.length) return null;
    // Twelve of these push the funnel below the fold; the rest are on Actions.
    const shown = rows.slice(0, NEVER_CITED_SHOWN);
    const hidden = rows.length - shown.length;
    return (
        <Card title="Fetched, never cited" size="small">
            <div style={{ display: "grid", gap: 12 }}>
                {shown.map((row) => (
                    <Card key={`${row.bot}:${row.url_key}`} size="small" type="inner">
                        <Space direction="vertical" size={4} style={{ width: "100%" }}>
                            <Typography.Text strong>
                                {row.bot} fetched {pagePath(row.url_key, project.client.domains)}{" "}
                                {num(row.fetches)}× in {row.days} days
                            </Typography.Text>
                            <Typography.Text type="secondary">
                                {row.engine
                                    ? (ENGINE_LABEL[row.engine] ?? row.engine)
                                    : "Its platform"}{" "}
                                never cited it
                                {row.consulted === null
                                    ? ""
                                    : row.consulted > 0
                                      ? ` · read in ${num(row.consulted)} answer(s)`
                                      : " · never read"}
                                {row.blocked > 0 ? ` · ${num(row.blocked)} blocked` : ""}
                            </Typography.Text>
                            {row.queries.length > 0 && (
                                <Space size={4} wrap>
                                    {row.queries.slice(0, 6).map((q) => (
                                        <Tag key={q} bordered={false}>
                                            {q}
                                        </Tag>
                                    ))}
                                </Space>
                            )}
                            <Link to={`/projects/${project.id}/actions?type=fetched_not_cited`}>
                                Open the action card
                            </Link>
                        </Space>
                    </Card>
                ))}
                {hidden > 0 && (
                    <Link to={`/projects/${project.id}/actions?type=fetched_not_cited`}>
                        {hidden} more page(s) fetched but never cited — see the Actions tab
                    </Link>
                )}
            </div>
        </Card>
    );
}

function Imports({
    rows,
    projectId,
    anchor,
}: {
    rows: CrawlerImportRecord[];
    projectId: string;
    anchor: (node: HTMLDivElement | null) => void;
}) {
    const remove = useDeleteCrawlerImport(projectId);
    return (
        <div ref={anchor}>
            <Card title="Imports" size="small">
                <Table<CrawlerImportRecord>
                    dataSource={rows}
                    rowKey="id"
                    size="small"
                    pagination={{ pageSize: 10, hideOnSinglePage: true }}
                    columns={[
                        {
                            title: "Imported",
                            dataIndex: "imported_at",
                            width: 170,
                            render: (iso: string) => fmtDateTime(iso),
                        },
                        { title: "Format", dataIndex: "format", width: 110 },
                        {
                            title: "Covers",
                            key: "span",
                            width: 190,
                            render: (_: unknown, row) =>
                                row.span_from && row.span_to
                                    ? `${fmtDate(row.span_from)} – ${fmtDate(row.span_to)}`
                                    : "—",
                        },
                        {
                            title: "Lines",
                            key: "lines",
                            width: 170,
                            render: (_: unknown, row) =>
                                `${num(row.lines)} / ${num(row.parsed)} / ${num(row.matched)}`,
                        },
                        { title: "Verified on", dataIndex: "verification_basis", width: 150 },
                        {
                            title: "Note",
                            dataIndex: "note",
                            render: (note: string, row) => (
                                <Space size={4} wrap>
                                    <span>{note || "—"}</span>
                                    {row.sampled && <Tag bordered={false}>sampled</Tag>}
                                    {row.overlaps.length > 0 && (
                                        <Tooltip title={row.overlaps.join(", ")}>
                                            <Tag bordered={false}>
                                                overlaps {row.overlaps.length}
                                            </Tag>
                                        </Tooltip>
                                    )}
                                    {row.purged_at && (
                                        <Typography.Text type="secondary">
                                            aggregates purged
                                        </Typography.Text>
                                    )}
                                </Space>
                            ),
                        },
                        {
                            title: "",
                            key: "actions",
                            width: 90,
                            render: (_: unknown, row) => (
                                <Popconfirm
                                    title="Delete this import?"
                                    description="Its aggregates go with it."
                                    okText="Delete"
                                    onConfirm={() => remove.mutateAsync(row.id)}
                                >
                                    <Typography.Link type="danger">Delete</Typography.Link>
                                </Popconfirm>
                            ),
                        },
                    ]}
                />
            </Card>
        </div>
    );
}

export function CrawlerLogsPage() {
    const { project } = useOutletContext<{ project: Project }>();
    const [days, setDays] = useState(30);
    const { data, isLoading, error } = useCrawlerLogs(project.id, days);
    const importsAnchor = useRef<HTMLDivElement | null>(null);
    const setImportsAnchor = (node: HTMLDivElement | null) => {
        importsAnchor.current = node;
    };
    const openImports = () =>
        importsAnchor.current?.scrollIntoView({ behavior: "smooth", block: "start" });

    if (error) return <Alert type="error" showIcon message={error.message} />;

    const stealth = Object.entries(data?.stealth ?? {});

    return (
        <div style={{ display: "grid", gap: 24 }}>
            <CrawlerImportCard projectId={project.id} onOpenImports={openImports} />

            {isLoading && <Skeleton active paragraph={{ rows: 6 }} />}

            {!isLoading && data && data.imports.length === 0 && (
                <Empty
                    description={
                        <Space direction="vertical" size={4}>
                            <span>
                                Upload an access log to see which AI crawlers fetched which pages,
                                whether the fetch succeeded, and whether the engines then cited the
                                page.
                            </span>
                            <Typography.Text type="secondary">
                                Only per-day counts per crawler and page are kept. No address is
                                stored and no log line leaves your browser unfiltered.
                            </Typography.Text>
                        </Space>
                    }
                />
            )}

            {!isLoading && data && data.imports.length > 0 && (
                <>
                    <Space wrap>
                        <Segmented
                            value={days}
                            onChange={(value) => setDays(Number(value))}
                            options={WINDOWS.map((d) => ({ label: `${d} days`, value: d }))}
                        />
                    </Space>
                    <Coverage view={data} />
                    {stealth.length > 0 && (
                        <Alert
                            type="info"
                            showIcon
                            message={stealth
                                .map(
                                    ([vendor, hits]) =>
                                        `${num(Number(hits))} requests came from ${vendor}'s published crawler addresses without naming a crawler.`,
                                )
                                .join(" ")}
                            description="A crawler that does not identify itself still resolves to its vendor's published addresses. Counted per vendor and per day, never per address."
                        />
                    )}
                    <ByBot rows={data.by_bot} />
                    <DailyStrip days={data.daily} />
                    <NeverCited rows={data.fetched_not_cited} project={project} />
                    <Funnel view={data} project={project} />
                    <Imports rows={data.imports} projectId={project.id} anchor={setImportsAnchor} />
                </>
            )}
        </div>
    );
}
