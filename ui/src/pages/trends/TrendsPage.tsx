/**
 * Trends: one project over time. Per run (every pipeline run on its own
 * date) or consolidated (every stored position set); all platforms as
 * separate lines or a single one; citation rate and mention rate always in
 * separate charts, optionally narrowed to one prompt.
 */
import { useMemo, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { Alert, Card, Col, Empty, Row, Segmented, Select, Skeleton, Space, Typography } from "antd";
import { useSearchParams } from "react-router-dom";
import { endpoints, type Engine, type PositionsView } from "@/api/endpoints";
import { qk, useAtlas, usePositions, useProjects, usePrompts } from "@/api/queries";
import { ENGINE_LABEL } from "@/app/theme";
import { TrendLine } from "@/components/charts/Charts";
import { EngineDot } from "@/components/EngineTag";
import { AtlasIndex } from "@/lib/atlas";
import { consolidatedSeries, perRunSeries, type TrendMetric, type TrendSeries } from "@/lib/trends";
import { useUiStore } from "@/store/ui";

type Basis = "run" | "consolidated";
type MetricChoice = TrendMetric | "both";

export function TrendsPage() {
    const [params, setParams] = useSearchParams();
    const { data: projects, isLoading: projectsLoading } = useProjects();
    const lastProjectId = useUiStore((s) => s.lastProjectId);
    const projectId = params.get("project") ?? lastProjectId ?? projects?.[0]?.id ?? null;
    const project = projects?.find((p) => p.id === projectId) ?? null;
    const [basis, setBasis] = useState<Basis>("run");
    const [platform, setPlatform] = useState<Engine | "all">("all");
    const [metric, setMetric] = useState<MetricChoice>("both");
    const [promptId, setPromptId] = useState<string | "all">("all");

    const { data: prompts } = usePrompts(project?.id);
    const {
        data: atlas,
        isLoading: atlasLoading,
        error: atlasError,
    } = useAtlas(project?.client.lob ?? null);
    const { data: positions } = usePositions(project?.id);
    const history = positions?.history ?? [];
    const consolidationQueries = useQueries({
        queries: history.map((c) => ({
            queryKey: qk.positions(project?.id ?? "", c.id),
            queryFn: ({ signal }: { signal: AbortSignal }) =>
                endpoints.positions(project!.id, c.id, { signal }),
            enabled: !!project && basis === "consolidated",
        })),
    });

    const engines: Engine[] = useMemo(
        () => (platform === "all" ? (project?.engines ?? []) : [platform]),
        [platform, project],
    );
    const promptIds = useMemo<Set<string> | null>(() => {
        if (promptId !== "all") return new Set([promptId]);
        const tracked = new Set((prompts ?? []).map((p) => p.prompt_id));
        return tracked.size ? tracked : null;
    }, [prompts, promptId]);

    const series = useMemo<TrendSeries | null>(() => {
        if (!project) return null;
        if (basis === "run") {
            if (!atlas) return null;
            return perRunSeries(new AtlasIndex(atlas), promptIds, engines);
        }
        const views = consolidationQueries
            .map((q) => q.data)
            .filter((v): v is PositionsView => !!v);
        return consolidatedSeries(views, promptIds, engines);
    }, [project, basis, atlas, promptIds, engines, consolidationQueries]);

    const consolidatedLoading =
        basis === "consolidated" && consolidationQueries.some((q) => q.isLoading);
    const loading = projectsLoading || (basis === "run" && atlasLoading) || consolidatedLoading;
    const points = Object.keys(series?.basis ?? {}).length;

    return (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center" }}>
                <Typography.Title level={3} style={{ margin: 0 }}>
                    Trends
                </Typography.Title>
                <Select
                    aria-label="Project"
                    style={{ minWidth: 260 }}
                    value={project?.id}
                    placeholder="Choose a project"
                    onChange={(v) => {
                        const n = new URLSearchParams(params);
                        n.set("project", v);
                        setParams(n);
                        setPromptId("all");
                    }}
                    options={(projects ?? []).map((p) => ({
                        value: p.id,
                        label: `${p.name} · ${p.client.brand_name}`,
                    }))}
                />
                <Segmented<Basis>
                    aria-label="Basis"
                    value={basis}
                    onChange={setBasis}
                    options={[
                        { value: "run", label: "Per run" },
                        {
                            value: "consolidated",
                            label: `Consolidated (every ${project?.consolidation_runs ?? 3} crawls)`,
                        },
                    ]}
                />
                <Select<Engine | "all">
                    aria-label="Platform"
                    style={{ minWidth: 200 }}
                    value={platform}
                    onChange={setPlatform}
                    options={[
                        { value: "all", label: "All platforms (one line each)" },
                        ...(project?.engines ?? []).map((e) => ({
                            value: e,
                            label: (
                                <span>
                                    <EngineDot engine={e} /> {ENGINE_LABEL[e] ?? e}
                                </span>
                            ),
                        })),
                    ]}
                />
                <Select<MetricChoice>
                    aria-label="Metric"
                    style={{ width: 200 }}
                    value={metric}
                    onChange={setMetric}
                    options={[
                        { value: "both", label: "Citations and mentions" },
                        { value: "citation", label: "Citations only" },
                        { value: "mention", label: "Mentions only" },
                    ]}
                />
                <Select<string>
                    aria-label="Prompt"
                    showSearch
                    optionFilterProp="label"
                    style={{ minWidth: 280, maxWidth: 420 }}
                    value={promptId}
                    onChange={setPromptId}
                    options={[
                        { value: "all", label: `All tracked prompts (${prompts?.length ?? 0})` },
                        ...(prompts ?? []).map((p) => ({
                            value: p.prompt_id,
                            label: p.prompt_text,
                        })),
                    ]}
                />
            </div>

            {!project && !projectsLoading && <Empty description="Create a project first." />}
            {atlasError && basis === "run" && (
                <Alert type="error" showIcon message={atlasError.message} />
            )}
            {loading && <Skeleton active paragraph={{ rows: 6 }} />}

            {project && !loading && series && (
                <>
                    <Typography.Text type="secondary">
                        {basis === "run"
                            ? `${points} pipeline run${points === 1 ? "" : "s"}, each on its own date. Every point is the mean over the prompts in scope; hover a point or open the table for the value.`
                            : points
                              ? `${points} consolidation${points === 1 ? "" : "s"}; each point is the mean position over its window of crawls.`
                              : `No consolidation yet: ${positions?.runs_since_last ?? 0} of ${project.consolidation_runs} crawls done. Consolidate from the Runs tab or wait for the window to complete.`}
                    </Typography.Text>
                    {points > 0 && (
                        <Row gutter={[16, 16]}>
                            {(metric === "both" || metric === "citation") && (
                                <Col xs={24} xl={metric === "both" ? 12 : 24}>
                                    <Card
                                        size="small"
                                        title="Citation rate"
                                        extra={
                                            <Typography.Text type="secondary">
                                                client linked in the answer
                                            </Typography.Text>
                                        }
                                    >
                                        <TrendLine
                                            points={series.citation}
                                            label={`Citation rate per ${basis === "run" ? "run" : "consolidation"}`}
                                        />
                                    </Card>
                                </Col>
                            )}
                            {(metric === "both" || metric === "mention") && (
                                <Col xs={24} xl={metric === "both" ? 12 : 24}>
                                    <Card
                                        size="small"
                                        title="Mention rate"
                                        extra={
                                            <Typography.Text type="secondary">
                                                brand named in the text
                                            </Typography.Text>
                                        }
                                    >
                                        <TrendLine
                                            points={series.mention}
                                            label={`Mention rate per ${basis === "run" ? "run" : "consolidation"}`}
                                        />
                                    </Card>
                                </Col>
                            )}
                            <Col span={24}>
                                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                    Basis per point:{" "}
                                    {Object.entries(series.basis)
                                        .map(
                                            ([label, n]) =>
                                                `${label}: ${n} ${basis === "run" ? "checks" : "samples"}`,
                                        )
                                        .join(" · ")}
                                </Typography.Text>
                            </Col>
                        </Row>
                    )}
                    {basis === "run" && points === 0 && (
                        <Empty description="No run recorded for these prompts yet." />
                    )}
                </>
            )}
        </Space>
    );
}
