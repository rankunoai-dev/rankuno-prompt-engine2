/**
 * Cross-project explorer over the per-run snapshot dataset. Parity with the
 * hand-written `docs/prompt-atlas.html`: LOB picker, engine chips, filters,
 * "as of" run, six views, and the same Inspection Drawer as the Battleground.
 */
import { useCallback, useMemo, useState } from "react";
import { Alert, Button, Card, Input, Select, Skeleton, Space, Tabs, Typography } from "antd";
import { useSearchParams } from "react-router-dom";
import type { AtlasPrompt, Engine, Project, PromptResult } from "@/api/endpoints";
import { useAtlas, useProjects, usePositions } from "@/api/queries";
import { fmtDate } from "@/app/format";
import { EngineDot } from "@/components/EngineTag";
import {
    ATLAS_ENGINES,
    AtlasIndex,
    EMPTY_ATLAS_FILTERS,
    INTENTS,
    STAGES,
    filterAtlasPrompts,
    type AtlasFilters,
} from "@/lib/atlas";
import { useLenis } from "@/lib/useLenis";
import { InspectionDrawer } from "@/pages/project/battleground/InspectionDrawer";
import { AtlasCtx } from "./AtlasContext";
import { AtlasOverview } from "./AtlasOverview";
import { AtlasSheet } from "./AtlasSheet";
import { AtlasEngines } from "./AtlasEngines";
import { AtlasGaps, AtlasVoice } from "./AtlasVoiceGaps";
import { AtlasInsights } from "./AtlasInsights";

const titleCase = (v: string) =>
    v
        .toLowerCase()
        .replace(/_/g, " ")
        .replace(/(^|\s)\S/g, (c) => c.toUpperCase());

export function AtlasPage() {
    const [params, setParams] = useSearchParams();
    const { data: projects } = useProjects();
    const lob = params.get("lob");
    const { data, isLoading, error } = useAtlas(lob);
    const [filters, setFilters] = useState<AtlasFilters>(EMPTY_ATLAS_FILTERS);
    const [asOfState, setAsOf] = useState<string | null>(null);
    const [view, setView] = useState("overview");
    const [engineTab, setEngineTab] = useState<Engine | undefined>(undefined);
    useLenis(view === "overview");

    const index = useMemo(() => (data ? new AtlasIndex(data) : null), [data]);
    const lobs = useMemo(() => {
        const s = new Set<string>();
        (projects ?? []).forEach((p) => s.add(p.client.lob));
        if (data?.meta.lob) s.add(data.meta.lob);
        (data?.prompts ?? []).forEach((p) => s.add(p.lob));
        return [...s].sort();
    }, [projects, data]);
    const project: Project | null = useMemo(
        () => (projects ?? []).find((p) => p.client.lob === (lob ?? data?.meta.lob)) ?? null,
        [projects, lob, data],
    );
    const { data: positions } = usePositions(project?.id);
    const asOf =
        asOfState ?? (index?.runs.length ? index.runs[index.runs.length - 1]!.run_id : null);
    const engines = filters.engines;
    const prompts = useMemo(
        () => (index ? filterAtlasPrompts(index, filters, asOf) : []),
        [index, filters, asOf],
    );

    const openPrompt = useCallback(
        (promptId: string, engine?: Engine) => {
            const next = new URLSearchParams(params);
            next.set("prompt", promptId);
            next.set("engine", engine ?? engines[0] ?? "CHATGPT_SEARCH");
            setParams(next);
        },
        [params, setParams, engines],
    );
    const goView = (v: string) => {
        if (v.startsWith("engines:")) {
            setEngineTab(v.slice(8) as Engine);
            setView("engines");
        } else setView(v);
    };

    const openPromptId = params.get("prompt");
    const openEngine = params.get("engine") as Engine | null;
    const drawerResult = useMemo<PromptResult | null>(() => {
        if (!index || !openPromptId) return null;
        const p = index.promptById.get(openPromptId);
        if (!p) return null;
        return syntheticResult(p, index, asOf);
    }, [index, openPromptId, asOf]);
    const drawerProject: Project = project ?? syntheticProject(data);

    if (isLoading) return <Skeleton active paragraph={{ rows: 8 }} />;
    if (error)
        return (
            <Alert
                type="error"
                showIcon
                message={error.message}
                description="The atlas dataset is built live from the tracker database; run a project first."
            />
        );
    if (!index || !data) return null;

    const dirty =
        filters.engines.length < 4 ||
        filters.promptType ||
        filters.stage ||
        filters.intent ||
        filters.cited ||
        filters.verdict ||
        filters.domain ||
        filters.q;

    return (
        <AtlasCtx.Provider
            value={{
                index,
                filters,
                setFilters,
                asOf,
                engines,
                prompts,
                project,
                openPrompt,
                goView,
            }}
        >
            <Space direction="vertical" size={14} style={{ width: "100%" }}>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center" }}>
                    <Typography.Title level={3} style={{ margin: 0 }}>
                        Atlas
                    </Typography.Title>
                    <Select
                        aria-label="Line of business"
                        style={{ minWidth: 280 }}
                        value={lob ?? data.meta.lob ?? undefined}
                        placeholder="All lines of business"
                        onChange={(v) => {
                            const n = new URLSearchParams(params);
                            if (v) n.set("lob", v);
                            else n.delete("lob");
                            n.delete("prompt");
                            n.delete("engine");
                            setParams(n);
                            setAsOf(null);
                        }}
                        allowClear
                        options={lobs.map((l) => ({ value: l, label: l }))}
                    />
                    <Typography.Text type="secondary">
                        {data.meta.brand_name} · {data.meta.domains.join(", ")} ·{" "}
                        {index.prompts.length} prompts · {data.snapshots.length} snapshots ·{" "}
                        {index.runs.length} runs
                    </Typography.Text>
                    <div style={{ flex: 1 }} />
                    <Select
                        aria-label="As of run"
                        style={{ minWidth: 240 }}
                        value={asOf ?? undefined}
                        onChange={(v) => setAsOf(v)}
                        options={[...index.runs].reverse().map((r, i) => ({
                            value: r.run_id,
                            label: `${fmtDate(r.started_at)} ${i === 0 ? "(latest)" : ""} · ${r.run_id.slice(0, 8)}${positions?.history.some((c) => c.run_ids.includes(r.run_id)) ? " · consolidated" : ""}`,
                        }))}
                    />
                </div>

                <Card size="small" styles={{ body: { padding: "10px 14px" } }}>
                    <Space wrap size={[8, 8]}>
                        {ATLAS_ENGINES.map((e) => {
                            const on = filters.engines.includes(e);
                            return (
                                <Button
                                    key={e}
                                    size="small"
                                    type={on ? "default" : "text"}
                                    aria-pressed={on}
                                    onClick={() => {
                                        const next = on
                                            ? filters.engines.filter((x) => x !== e)
                                            : [...filters.engines, e];
                                        if (next.length)
                                            setFilters({
                                                ...filters,
                                                engines: ATLAS_ENGINES.filter((x) =>
                                                    next.includes(x),
                                                ),
                                            });
                                    }}
                                >
                                    <EngineDot engine={e} /> {e.replace(/_/g, " ").toLowerCase()}
                                </Button>
                            );
                        })}
                        <Select
                            allowClear
                            size="small"
                            placeholder="Prompt type"
                            aria-label="Prompt type"
                            style={{ width: 140 }}
                            value={filters.promptType}
                            onChange={(v) => setFilters({ ...filters, promptType: v ?? null })}
                            options={[
                                { value: "BRANDED", label: "Branded" },
                                { value: "NON_BRANDED", label: "Non-branded" },
                            ]}
                        />
                        <Select
                            allowClear
                            size="small"
                            placeholder="Stage"
                            aria-label="Decision stage"
                            style={{ width: 150 }}
                            value={filters.stage}
                            onChange={(v) => setFilters({ ...filters, stage: v ?? null })}
                            options={STAGES.map((s) => ({ value: s, label: titleCase(s) }))}
                        />
                        <Select
                            allowClear
                            size="small"
                            placeholder="Intent"
                            aria-label="Search intent"
                            style={{ width: 140 }}
                            value={filters.intent}
                            onChange={(v) => setFilters({ ...filters, intent: v ?? null })}
                            options={INTENTS.map((s) => ({ value: s, label: titleCase(s) }))}
                        />
                        <Select
                            allowClear
                            size="small"
                            placeholder="Citation status"
                            aria-label="Citation status"
                            style={{ width: 150 }}
                            value={filters.cited}
                            onChange={(v) => setFilters({ ...filters, cited: v ?? null })}
                            options={[
                                { value: "yes", label: "Client cited" },
                                { value: "no", label: "Not cited" },
                                { value: "gap", label: "Content gap" },
                            ]}
                        />
                        <Select
                            allowClear
                            size="small"
                            placeholder="Verdict"
                            aria-label="Verdict"
                            style={{ width: 110 }}
                            value={filters.verdict}
                            onChange={(v) => setFilters({ ...filters, verdict: v ?? null })}
                            options={[
                                { value: "KEEP", label: "Keep" },
                                { value: "DROP", label: "Drop" },
                            ]}
                        />
                        <Input
                            size="small"
                            allowClear
                            placeholder="Domain"
                            aria-label="Domain"
                            style={{ width: 140 }}
                            value={filters.domain}
                            onChange={(e) => setFilters({ ...filters, domain: e.target.value })}
                        />
                        <Input.Search
                            size="small"
                            allowClear
                            placeholder="Search prompts"
                            aria-label="Search prompts"
                            style={{ width: 200 }}
                            value={filters.q}
                            onChange={(e) => setFilters({ ...filters, q: e.target.value })}
                        />
                        {dirty && (
                            <Button
                                size="small"
                                type="link"
                                onClick={() => setFilters(EMPTY_ATLAS_FILTERS)}
                            >
                                Reset filters
                            </Button>
                        )}
                        <Typography.Text type="secondary">
                            {prompts.length} of {index.prompts.length} prompts
                        </Typography.Text>
                    </Space>
                </Card>

                <Tabs
                    activeKey={view}
                    onChange={setView}
                    items={[
                        { key: "overview", label: "Overview", children: <AtlasOverview /> },
                        { key: "sheet", label: "Master prompt sheet", children: <AtlasSheet /> },
                        {
                            key: "engines",
                            label: "Engines",
                            children: <AtlasEngines key={engineTab ?? "x"} initial={engineTab} />,
                        },
                        { key: "voice", label: "Share of voice", children: <AtlasVoice /> },
                        { key: "gaps", label: "Content gaps", children: <AtlasGaps /> },
                        { key: "insights", label: "Claims & fan-out", children: <AtlasInsights /> },
                    ]}
                />

                <InspectionDrawer
                    project={drawerProject}
                    result={drawerResult}
                    engine={
                        drawerResult
                            ? openEngine && ATLAS_ENGINES.includes(openEngine)
                                ? openEngine
                                : (engines[0] ?? null)
                            : null
                    }
                    position={undefined}
                    runId={params.get("crawl")}
                    onRunChange={(rid) => {
                        const n = new URLSearchParams(params);
                        if (rid) n.set("crawl", rid);
                        else n.delete("crawl");
                        setParams(n);
                    }}
                    onClose={() => {
                        const n = new URLSearchParams(params);
                        n.delete("prompt");
                        n.delete("engine");
                        n.delete("crawl");
                        setParams(n);
                    }}
                />
            </Space>
        </AtlasCtx.Provider>
    );
}

/** A PromptResult-shaped view over the atlas so the shared drawer can read it. */
function syntheticResult(p: AtlasPrompt, index: AtlasIndex, asOf: string | null): PromptResult {
    const now = p.created_at;
    const snapshots: PromptResult["snapshots"] = {};
    for (const e of ATLAS_ENGINES) {
        const s = index.latest(p.prompt_id, e, asOf);
        snapshots[e] = s
            ? {
                  ...s,
                  response_ids: [],
                  reused: false,
                  consulted_urls: [],
                  mention_detected: s.mention_rate > 0,
              }
            : null;
    }
    return {
        prompt: {
            id: p.prompt_id,
            project_id: "atlas",
            prompt_id: p.prompt_id,
            prompt_text: p.prompt_text,
            keyword: p.core_keyword,
            subtopic: p.subtopic,
            important: false,
            enabled: true,
            interval: null,
            engines: null,
            samples_per_engine: null,
            created_at: now,
            updated_at: p.last_seen,
        },
        effective_interval: "—",
        effective_engines: ATLAS_ENGINES,
        snapshots,
        organic_prompt: null,
        organic_keyword: null,
        due_on: [],
    };
}

function syntheticProject(data: ReturnType<typeof useAtlas>["data"]): Project {
    const meta = data?.meta;
    return {
        id: "atlas",
        name: meta?.lob ?? "Atlas",
        client: {
            brand_name: meta?.brand_name ?? "",
            lob: meta?.lob ?? "",
            aliases: meta?.aliases ?? [],
            domains: meta?.domains ?? [],
            competitor_domains: meta?.competitor_domains ?? [],
            competitor_names: [],
            seed_keywords: [],
            subtopics: [],
            landing_pages: [],
        },
        engines: ATLAS_ENGINES,
        engine_models: {},
        interval: "daily",
        enabled: true,
        samples_per_engine: null,
        generate_prompts: false,
        track_keyword_rank: true,
        resolve_redirects: false,
        max_engine_calls: null,
        reuse_within_hours: null,
        consolidation_runs: 3,
        notes: "",
        created_at: "",
        updated_at: "",
    };
}
