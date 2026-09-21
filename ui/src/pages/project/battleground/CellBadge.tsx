/** One matrix cell: a badge, a due dot, and a hover with the basis. */
import { Tooltip } from "antd";
import type { CellView } from "@/lib/matrix";
import { pct } from "@/app/format";
import { shortUrl } from "@/lib/pages";

const STYLE: Record<CellView["kind"], { bg: string; fg: string; label: (c: CellView) => string }> =
    {
        linked: {
            bg: "var(--ant-color-success-bg)",
            fg: "var(--ant-color-success-text)",
            label: (c) => `Linked${c.rank ? ` #${c.rank}` : ""}`,
        },
        mentioned: {
            bg: "var(--ant-color-warning-bg)",
            fg: "var(--ant-color-warning-text)",
            label: () => "Mentioned only",
        },
        competitor: {
            bg: "var(--ant-color-error-bg)",
            fg: "var(--ant-color-error-text)",
            label: (c) => `${c.competitor} wins`,
        },
        absent: {
            bg: "var(--ant-color-fill-secondary)",
            fg: "var(--ant-color-text-secondary)",
            label: () => "Absent",
        },
        unsampled: {
            bg: "transparent",
            fg: "var(--ant-color-text-quaternary)",
            label: () => "—",
        },
    };

interface Props {
    cell: CellView;
    onOpen: () => void;
    tabIndex: number;
    label: string;
    focusRef?: (el: HTMLButtonElement | null) => void;
}

export function CellBadge({ cell, onOpen, tabIndex, label, focusRef }: Props) {
    const s = STYLE[cell.kind];
    const basis =
        cell.kind === "unsampled"
            ? "Not sampled yet"
            : `${cell.samples} samples over ${cell.crawls} crawl${cell.crawls === 1 ? "" : "s"} · cited ${pct(cell.citedRate)} · mentioned ${pct(cell.mentionRate)}`;
    return (
        <Tooltip
            title={
                <div style={{ maxWidth: 320 }}>
                    <div>{basis}</div>
                    {cell.snippet && (
                        <div style={{ marginTop: 6, fontStyle: "italic" }}>“{cell.snippet}”</div>
                    )}
                    {cell.topDomains.length > 0 && (
                        <div style={{ marginTop: 6 }}>Cites: {cell.topDomains.join(", ")}</div>
                    )}
                    {cell.clientUrl && (
                        <div style={{ marginTop: 6 }}>
                            Your page: {shortUrl(cell.clientUrl, 46)}
                        </div>
                    )}
                    {cell.competitorUrl && (
                        <div style={{ marginTop: 2 }}>
                            Their page: {shortUrl(cell.competitorUrl, 46)}
                        </div>
                    )}
                    {cell.due && <div style={{ marginTop: 6 }}>Due for a new sample</div>}
                </div>
            }
        >
            <button
                ref={focusRef}
                type="button"
                className="pe-focus-ring"
                aria-label={label}
                data-kind={cell.kind}
                tabIndex={tabIndex}
                onClick={onOpen}
                style={{
                    position: "relative",
                    width: "100%",
                    minWidth: 120,
                    border: 0,
                    borderRadius: 6,
                    padding: "5px 8px",
                    background: s.bg,
                    color: s.fg,
                    font: "inherit",
                    fontSize: 12,
                    fontWeight: 500,
                    cursor: "pointer",
                    textAlign: "left",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                }}
            >
                {s.label(cell)}
                {cell.due && (
                    <span
                        aria-hidden
                        style={{
                            position: "absolute",
                            top: 4,
                            right: 4,
                            width: 6,
                            height: 6,
                            borderRadius: "50%",
                            background: "var(--ant-color-warning)",
                        }}
                    />
                )}
            </button>
        </Tooltip>
    );
}
