/**
 * The only charts the brief allows, on the Atlas page only: trend line,
 * domain bars, rank histogram (the stage heatmap is an HTML grid). Each has a
 * table twin so no value is reachable only by hover, and `@ant-design/plots`
 * is loaded lazily to keep it out of the main bundle. In tests the table twin
 * renders alone: jsdom has no canvas.
 */
import { Suspense, lazy, useState, type ReactNode } from "react";
import { Button, Skeleton, Table } from "antd";
import { ENGINE_COLOR, ENGINE_SHORT } from "@/app/theme";
import { useIsDark } from "@/app/ThemeProvider";
import { pct } from "@/app/format";

const IS_TEST = import.meta.env.MODE === "test";
const Line = lazy(() => import("@ant-design/plots").then((m) => ({ default: m.Line })));
const Bar = lazy(() => import("@ant-design/plots").then((m) => ({ default: m.Bar })));
const Column = lazy(() => import("@ant-design/plots").then((m) => ({ default: m.Column })));

/** G2 ships its own light/dark palettes; follow the app theme so labels stay legible. */
function usePlotTheme(): "classic" | "classicDark" {
    return useIsDark() ? "classicDark" : "classic";
}

function Frame({ chart, table, label }: { chart: ReactNode; table: ReactNode; label: string }) {
    const [showTable, setShowTable] = useState(IS_TEST);
    return (
        <div>
            {showTable ? (
                table
            ) : (
                <Suspense fallback={<Skeleton active paragraph={{ rows: 4 }} />}>
                    <div role="img" aria-label={label}>
                        {chart}
                    </div>
                </Suspense>
            )}
            {!IS_TEST && (
                <Button
                    size="small"
                    type="link"
                    style={{ paddingInline: 0 }}
                    onClick={() => setShowTable((v) => !v)}
                >
                    {showTable ? "Chart view" : "Table view"}
                </Button>
            )}
        </div>
    );
}

export interface TrendPoint {
    run: string;
    date: string;
    engine: string;
    value: number | null;
}

export function TrendLine({ points, label }: { points: TrendPoint[]; label: string }) {
    const plotTheme = usePlotTheme();
    const engines = [...new Set(points.map((p) => p.engine))];
    const runs = [...new Set(points.map((p) => p.date))];
    const data = points
        .filter((p) => p.value !== null)
        .map((p) => ({
            ...p,
            engine: ENGINE_SHORT[p.engine] ?? p.engine,
            value: Math.round(p.value! * 1000) / 10,
        }));
    const colorMap = Object.fromEntries(
        engines.map((e) => [ENGINE_SHORT[e] ?? e, ENGINE_COLOR[e] ?? "#888"]),
    );
    return (
        <Frame
            label={label}
            chart={
                <Line
                    theme={plotTheme}
                    data={data}
                    xField="date"
                    yField="value"
                    colorField="engine"
                    height={240}
                    scale={{
                        color: { domain: Object.keys(colorMap), range: Object.values(colorMap) },
                        y: { domainMin: 0, domainMax: 100 },
                    }}
                    axis={{ y: { labelFormatter: (v: number) => `${v}%` } }}
                    style={{ lineWidth: 2 }}
                    point={{ sizeField: 3 }}
                    tooltip={{ items: [{ channel: "y", valueFormatter: (v: number) => `${v}%` }] }}
                    legend={{ color: { position: "top" } }}
                />
            }
            table={
                <Table
                    size="small"
                    pagination={false}
                    rowKey="date"
                    dataSource={runs.map((d) => ({
                        date: d,
                        ...Object.fromEntries(
                            engines.map((e) => [
                                e,
                                points.find((p) => p.date === d && p.engine === e)?.value ?? null,
                            ]),
                        ),
                    }))}
                    columns={[
                        { title: "Run", dataIndex: "date" },
                        ...engines.map((e) => ({
                            title: ENGINE_SHORT[e] ?? e,
                            dataIndex: e,
                            align: "right" as const,
                            render: (v: number | null) => pct(v),
                        })),
                    ]}
                />
            }
        />
    );
}

export interface BarPoint {
    label: string;
    value: number;
    role?: "client" | "comp" | "other";
}

export function DomainBars({
    points,
    label,
    onClick,
}: {
    points: BarPoint[];
    label: string;
    onClick?: (label: string) => void;
}) {
    const plotTheme = usePlotTheme();
    const data = points.map((p) => ({ ...p, pct: Math.round(p.value * 1000) / 10 }));
    return (
        <Frame
            label={label}
            chart={
                <Bar
                    theme={plotTheme}
                    data={data}
                    xField="label"
                    yField="pct"
                    colorField="role"
                    height={Math.max(160, 26 * data.length + 40)}
                    scale={{
                        color: {
                            domain: ["client", "comp", "other"],
                            range: ["#1f5eff", "#5b6474", "#b8bfca"],
                        },
                    }}
                    axis={{ y: { labelFormatter: (v: number) => `${v}%` } }}
                    legend={false}
                    style={{ maxWidth: 22, radiusTopLeft: 4, radiusTopRight: 4 }}
                    tooltip={{ items: [{ channel: "y", valueFormatter: (v: number) => `${v}%` }] }}
                    onReady={(plot: {
                        chart: {
                            on: (
                                ev: string,
                                cb: (e: { data?: { data?: { label?: string } } }) => void,
                            ) => void;
                        };
                    }) => {
                        if (onClick)
                            plot.chart.on(
                                "interval:click",
                                (e) => e.data?.data?.label && onClick(e.data.data.label),
                            );
                    }}
                />
            }
            table={
                <Table
                    size="small"
                    pagination={false}
                    rowKey="label"
                    dataSource={data}
                    onRow={(r) => ({
                        onClick: () => onClick?.(r.label),
                        style: { cursor: onClick ? "pointer" : undefined },
                    })}
                    columns={[
                        {
                            title: "Domain",
                            dataIndex: "label",
                            render: (v: string, r) => (
                                <span className="pe-mono">
                                    {v}
                                    {r.role === "client"
                                        ? " (client)"
                                        : r.role === "comp"
                                          ? " (competitor)"
                                          : ""}
                                </span>
                            ),
                        },
                        {
                            title: "Share",
                            dataIndex: "value",
                            align: "right",
                            render: (v: number) => pct(v),
                        },
                    ]}
                />
            }
        />
    );
}

export function RateHistogram({
    bins,
    engine,
    label,
}: {
    bins: number[];
    engine: string;
    label: string;
}) {
    const plotTheme = usePlotTheme();
    const bands = ["0–20%", "20–40%", "40–60%", "60–80%", "80–100%"];
    const data = bins.map((n, i) => ({ band: bands[i]!, prompts: n }));
    return (
        <Frame
            label={label}
            chart={
                <Column
                    theme={plotTheme}
                    data={data}
                    xField="band"
                    yField="prompts"
                    height={200}
                    style={{
                        fill: ENGINE_COLOR[engine] ?? "#888",
                        maxWidth: 24,
                        radiusTopLeft: 4,
                        radiusTopRight: 4,
                    }}
                    axis={{ x: { title: "citation rate" }, y: { title: "prompts" } }}
                />
            }
            table={
                <Table
                    size="small"
                    pagination={false}
                    rowKey="band"
                    dataSource={data}
                    columns={[
                        { title: "Citation rate", dataIndex: "band" },
                        { title: "Prompts", dataIndex: "prompts", align: "right" },
                    ]}
                />
            }
        />
    );
}
