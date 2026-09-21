/**
 * "Yours vs theirs": the exact client page that earned a citation and the
 * exact competitor page that took it. Used wherever an insight is stated.
 */
import { Tag, Tooltip, Typography } from "antd";
import type { PageInventory } from "@/api/endpoints";
import type { PageHit } from "@/lib/pages";
import { shortUrl } from "@/lib/pages";
import { EngineDot } from "./EngineTag";

export interface PageLine {
    url: string;
    title?: string | null;
    citations: number;
    bestPosition?: number | null;
    engines?: string[];
}

export const fromInventory = (p: PageInventory): PageLine => ({
    url: p.url,
    title: p.title,
    citations: p.citations,
    engines: p.engines,
});
export const fromHit = (h: PageHit): PageLine => ({
    url: h.url,
    title: h.title,
    citations: h.hits,
    bestPosition: h.bestPosition,
    engines: h.engines,
});

interface Props {
    client: PageLine[];
    competitor: PageLine[];
    /** Wording for the count: "citations" for inventories, "crawls" for windows. */
    unit?: string;
    /** Inventory counts span every platform, so a per-platform tile hides them. */
    showCount?: boolean;
    compact?: boolean;
    emptyClient?: string;
    emptyCompetitor?: string;
}

export function ExactPages({
    client,
    competitor,
    unit = "citations",
    showCount = true,
    compact = false,
    emptyClient = "No client page cited",
    emptyCompetitor = "No competitor page cited",
}: Props) {
    return (
        <div
            data-testid="exact-pages"
            style={{
                display: "grid",
                gridTemplateColumns: compact ? "1fr" : "repeat(auto-fit, minmax(260px, 1fr))",
                gap: compact ? 4 : 10,
                fontSize: 12,
            }}
        >
            <PageList
                label="Your page"
                tone="success"
                lines={client}
                unit={unit}
                empty={emptyClient}
                compact={compact}
                showCount={showCount}
            />
            <PageList
                label="Their page"
                tone="error"
                lines={competitor}
                unit={unit}
                empty={emptyCompetitor}
                compact={compact}
                showCount={showCount}
            />
        </div>
    );
}

function PageList({
    label,
    tone,
    lines,
    unit,
    empty,
    compact = false,
    showCount = true,
}: {
    label: string;
    tone: "success" | "error";
    lines: PageLine[];
    unit: string;
    empty: string;
    compact?: boolean;
    showCount?: boolean;
}) {
    return (
        <div>
            <Tag color={tone} style={{ marginBottom: 4 }}>
                {label}
            </Tag>
            {lines.length ? (
                <ul style={{ margin: 0, paddingLeft: 0, listStyle: "none" }}>
                    {lines.map((l) => (
                        <li
                            key={l.url}
                            style={{
                                marginBottom: 3,
                                display: "flex",
                                flexWrap: "wrap",
                                gap: compact ? 2 : 6,
                                alignItems: "baseline",
                            }}
                        >
                            <Tooltip title={l.title ? `${l.title} — ${l.url}` : l.url}>
                                <a
                                    href={l.url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="pe-mono pe-ellipsis"
                                    style={{
                                        maxWidth: compact ? "100%" : 360,
                                        display: "inline-block",
                                        verticalAlign: "bottom",
                                    }}
                                >
                                    {shortUrl(l.url, compact ? 34 : 60)}
                                </a>
                            </Tooltip>
                            {showCount && (
                                <Typography.Text
                                    type="secondary"
                                    className="pe-num"
                                    style={{
                                        whiteSpace: "nowrap",
                                        flexBasis: compact ? "100%" : undefined,
                                    }}
                                >
                                    {l.bestPosition ? `#${l.bestPosition} · ` : ""}
                                    {l.citations} {unit}
                                </Typography.Text>
                            )}
                            {l.engines?.map((e) => (
                                <EngineDot key={e} engine={e} size={6} />
                            ))}
                        </li>
                    ))}
                </ul>
            ) : (
                <Typography.Text type="secondary">{empty}</Typography.Text>
            )}
        </div>
    );
}
