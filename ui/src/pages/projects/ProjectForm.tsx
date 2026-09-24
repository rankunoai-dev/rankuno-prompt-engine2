/**
 * Create/edit Drawer. Every field the API accepts is here; validation for
 * shape happens client-side with Zod, and server 422s are mapped onto the
 * same fields verbatim.
 */
import { useEffect, useMemo } from "react";
import {
    Alert,
    App,
    Button,
    Checkbox,
    Divider,
    Drawer,
    Form,
    Input,
    InputNumber,
    Select,
    Space,
    Typography,
} from "antd";
import { z } from "zod";
import { ApiError } from "@/api/client";
import type { Engine, Project, ProjectCreate } from "@/api/endpoints";
import { useOptions } from "@/api/queries";
import { useCreateProject, useUpdateProject } from "@/api/mutations";
import { intervalToMs } from "@/app/format";
import { OWNER_RULES, PASSWORD_RULES } from "@/app/ProjectUnlock";
import { EngineCheckboxes } from "@/components/EngineCheckboxes";
import { IntervalPicker, INTERVAL_PATTERN } from "@/components/IntervalPicker";

const ENGINES: Engine[] = ["GOOGLE_AI_OVERVIEW", "CHATGPT_SEARCH", "PERPLEXITY", "GEMINI"];

const schema = z.object({
    name: z.string().trim().min(1, "Give the project a name").max(80),
    enabled: z.boolean(),
    brand_name: z.string().trim().min(1, "Brand name is required"),
    lob: z.string().trim().min(1, "Line of business is required"),
    aliases: z.array(z.string()).default([]),
    domains: z.array(z.string()).min(1, "At least one client domain"),
    competitor_domains: z.array(z.string()).default([]),
    competitor_names: z.array(z.string()).default([]),
    seed_keywords: z.array(z.string()).min(1, "At least one seed keyword"),
    subtopics: z.array(z.string()).default([]),
    landing_pages: z.string().default(""),
    engines: z.array(z.string()).min(1, "Track at least one platform"),
    engine_models: z.record(z.string(), z.string().nullable().optional()).default({}),
    interval: z
        .string()
        .trim()
        .regex(INTERVAL_PATTERN, "Use a preset or a value like 4d, 10h or 6w"),
    samples_per_engine: z.number().int().min(1).max(10).nullable(),
    max_engine_calls: z.number().int().min(1).nullable(),
    reuse_within_hours: z.number().int().min(0).nullable(),
    track_keyword_rank: z.boolean(),
    resolve_redirects: z.boolean(),
    generate_prompts: z.boolean(),
    consolidation_runs: z.number().int().min(1).max(50),
    locale_country: z
        .string()
        .trim()
        .length(2, "Two-letter country code, e.g. US")
        .or(z.literal("")),
    locale_language: z.string().trim().min(2).max(5).or(z.literal("")),
    locale_city: z.string().trim().max(80).default(""),
    locale_region: z.string().trim().max(80).default(""),
    locale_serp_location: z.string().trim().max(160).default(""),
    brand_client_name: z.string().trim().max(120).default(""),
    brand_agency_name: z.string().trim().max(120).default(""),
    brand_colour: z
        .string()
        .trim()
        .regex(/^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/, "A hex colour like #1f3a5f")
        .default("#1f3a5f"),
    brand_footer: z.string().trim().max(200).default(""),
    brand_show_spend: z.boolean().default(false),
    brand_logo_id: z.string().trim().max(32).default(""),
    notes: z.string().max(2000).default(""),
    sentiment: z.boolean(),
});

/** Owner credential (ADR 0019): asked once, at creation; validated by the form rules. */
interface CredentialFields {
    protect: boolean;
    cred_owner: string;
    cred_password: string;
    cred_confirm: string;
}

type FormValues = z.input<typeof schema> & CredentialFields;

function toForm(p: Project | null, defaultEngines: Engine[]): FormValues {
    return {
        name: p?.name ?? "",
        enabled: p?.enabled ?? true,
        brand_name: p?.client.brand_name ?? "",
        lob: p?.client.lob ?? "",
        aliases: p?.client.aliases ?? [],
        domains: p?.client.domains ?? [],
        competitor_domains: p?.client.competitor_domains ?? [],
        competitor_names: p?.client.competitor_names ?? [],
        seed_keywords: p?.client.seed_keywords ?? [],
        subtopics: p?.client.subtopics ?? [],
        landing_pages: (p?.client.landing_pages ?? []).join("\n"),
        engines: p?.engines ?? defaultEngines,
        engine_models: (p?.engine_models as Record<string, string> | undefined) ?? {},
        interval: p?.interval ?? "daily",
        samples_per_engine: p?.samples_per_engine ?? null,
        max_engine_calls: p?.max_engine_calls ?? null,
        reuse_within_hours: p?.reuse_within_hours ?? null,
        track_keyword_rank: p?.track_keyword_rank ?? true,
        resolve_redirects: p?.resolve_redirects ?? false,
        generate_prompts: p?.generate_prompts ?? false,
        consolidation_runs: p?.consolidation_runs ?? 3,
        locale_country: p?.locale?.country ?? "",
        locale_language: p?.locale?.language ?? "",
        locale_city: p?.locale?.city ?? "",
        locale_region: p?.locale?.region ?? "",
        locale_serp_location: p?.locale?.serp_location ?? "",
        brand_client_name: p?.brand?.client_name ?? "",
        brand_agency_name: p?.brand?.agency_name ?? "",
        brand_colour: p?.brand?.primary_colour ?? "#1f3a5f",
        brand_footer: p?.brand?.footer_note ?? "",
        brand_show_spend: p?.brand?.show_spend ?? false,
        brand_logo_id: p?.brand?.logo_id ?? "",
        notes: p?.notes ?? "",
        sentiment: p?.sentiment ?? true,
        protect: true,
        cred_owner: "",
        cred_password: "",
        cred_confirm: "",
    };
}

function toBody(v: z.output<typeof schema>): ProjectCreate {
    return {
        name: v.name,
        enabled: v.enabled,
        client: {
            brand_name: v.brand_name,
            lob: v.lob,
            aliases: v.aliases,
            domains: v.domains,
            competitor_domains: v.competitor_domains,
            competitor_names: v.competitor_names,
            seed_keywords: v.seed_keywords,
            subtopics: v.subtopics,
            landing_pages: v.landing_pages
                .split(/\r?\n/)
                .map((s) => s.trim())
                .filter(Boolean),
        },
        engines: v.engines as Engine[],
        engine_models: Object.fromEntries(
            Object.entries(v.engine_models).filter(([, m]) => !!m),
        ) as ProjectCreate["engine_models"],
        interval: v.interval.toLowerCase(),
        samples_per_engine: v.samples_per_engine,
        max_engine_calls: v.max_engine_calls,
        reuse_within_hours: v.reuse_within_hours,
        track_keyword_rank: v.track_keyword_rank,
        resolve_redirects: v.resolve_redirects,
        generate_prompts: v.generate_prompts,
        consolidation_runs: v.consolidation_runs,
        locale: v.locale_country
            ? {
                  country: v.locale_country.toUpperCase(),
                  language: (v.locale_language || "en").toLowerCase(),
                  city: v.locale_city || null,
                  region: v.locale_region || null,
                  serp_location: v.locale_serp_location || null,
                  timezone: null,
              }
            : null,
        brand: {
            client_name: v.brand_client_name || null,
            agency_name: v.brand_agency_name || null,
            primary_colour: v.brand_colour || "#1f3a5f",
            logo_id: v.brand_logo_id || null,
            footer_note: v.brand_footer || null,
            show_spend: v.brand_show_spend,
        },
        sampling_policy: "fixed",
        notes: v.notes,
        sentiment: v.sentiment,
    };
}

/** Server field paths (`client.brand_name`) → form field names. */
const FIELD_MAP: Record<string, string> = {
    "client.brand_name": "brand_name",
    "client.lob": "lob",
    "client.aliases": "aliases",
    "client.domains": "domains",
    "client.competitor_domains": "competitor_domains",
    "client.competitor_names": "competitor_names",
    "client.seed_keywords": "seed_keywords",
    "client.subtopics": "subtopics",
    "client.landing_pages": "landing_pages",
    "credentials.owner": "cred_owner",
    "credentials.password": "cred_password",
};

function consolidationHint(interval: string, runs: number): string {
    const ms = intervalToMs(interval);
    if (!ms) return `Positions are consolidated after every ${runs} full crawls.`;
    const days = (ms * runs) / 86_400_000;
    const span =
        days >= 1 ? `${Math.round(days * 10) / 10} days` : `${Math.round(days * 24)} hours`;
    return `Run every ${interval} with ${runs} → positions consolidated every ${span} from ${runs} crawls.`;
}

interface Props {
    open: boolean;
    project: Project | null;
    onClose: () => void;
    onSaved?: (project: Project) => void;
}

export function ProjectForm({ open, project, onClose, onSaved }: Props) {
    const { message } = App.useApp();
    const { data: options } = useOptions();
    const [form] = Form.useForm<FormValues>();
    const create = useCreateProject();
    const update = useUpdateProject();
    const pending = create.isPending || update.isPending;
    const defaultEngines = useMemo(
        () => options?.engines.map((e) => e.value) ?? ENGINES,
        [options],
    );

    useEffect(() => {
        if (open) form.setFieldsValue(toForm(project, defaultEngines));
    }, [open, project, defaultEngines, form]);

    const interval = Form.useWatch("interval", form) ?? "daily";
    const runs = Form.useWatch("consolidation_runs", form) ?? 3;
    const engines = (Form.useWatch("engines", form) ?? []) as string[];
    const protect = Form.useWatch("protect", form) ?? true;

    const setFieldErrors = (entries: [string, string][]) =>
        form.setFields(
            entries.map(([name, msg]) => ({ name: name as keyof FormValues, errors: [msg] })),
        );

    const submit = async (raw: FormValues) => {
        const parsed = schema.safeParse(raw);
        if (!parsed.success) {
            setFieldErrors(parsed.error.issues.map((i) => [String(i.path[0]), i.message]));
            const first = parsed.error.issues[0];
            if (first) message.error(`${String(first.path[0])}: ${first.message}`);
            return;
        }
        const body = toBody(parsed.data);
        if (!project && raw.protect) {
            body.credentials = { owner: raw.cred_owner.trim(), password: raw.cred_password };
        }
        try {
            const saved = project
                ? await update.mutateAsync({ id: project.id, body })
                : await create.mutateAsync(body);
            message.success(project ? "Project saved." : "Project created.");
            onSaved?.(saved);
            onClose();
        } catch (err) {
            if (err instanceof ApiError && Object.keys(err.fieldErrors).length) {
                setFieldErrors(
                    Object.entries(err.fieldErrors).map(([path, msg]) => [
                        FIELD_MAP[path] ?? path,
                        msg,
                    ]),
                );
            }
            message.error(err instanceof Error ? err.message : "Could not save the project.");
        }
    };

    // Comma, newline or Enter commits a tag; the dropdown only ever echoes the
    // typed value, so it is hidden rather than disabled (Enter still commits).
    const tagSelect = (placeholder: string) => (
        <Select
            mode="tags"
            tokenSeparators={[",", "\n"]}
            placeholder={placeholder}
            dropdownStyle={{ display: "none" }}
            suffixIcon={null}
        />
    );

    return (
        <Drawer
            title={project ? `Edit ${project.name}` : "New project"}
            open={open}
            onClose={onClose}
            width={640}
            destroyOnClose
            extra={
                <Space>
                    <Button onClick={onClose}>Cancel</Button>
                    <Button type="primary" loading={pending} onClick={() => form.submit()}>
                        {project ? "Save" : "Create project"}
                    </Button>
                </Space>
            }
        >
            <Form<FormValues>
                form={form}
                layout="vertical"
                onFinish={submit}
                requiredMark="optional"
                initialValues={toForm(project, defaultEngines)}
            >
                <Form.Item name="name" label="Name" rules={[{ required: true }]}>
                    <Input maxLength={80} />
                </Form.Item>
                <Form.Item name="enabled" valuePropName="checked">
                    <Checkbox>Enabled (included in scheduled runs)</Checkbox>
                </Form.Item>
                <Form.Item name="sentiment" valuePropName="checked" style={{ marginTop: -12 }}>
                    <Checkbox>
                        Score brand mentions after each crawl (sentiment and attributes; needs the
                        server key)
                    </Checkbox>
                </Form.Item>

                {!project && (
                    <>
                        <Divider orientation="left" plain>
                            Who can change this project
                        </Divider>
                        <Form.Item
                            name="protect"
                            valuePropName="checked"
                            style={{ marginBottom: 8 }}
                        >
                            <Checkbox>Protect it with an owner credential (recommended)</Checkbox>
                        </Form.Item>
                        {protect ? (
                            <>
                                <Typography.Paragraph type="secondary">
                                    Everyone who can open this app will be able to read the project.
                                    Only someone with this credential can edit it, run it, or delete
                                    it. There is no password reset in the app, so store it somewhere
                                    safe.
                                </Typography.Paragraph>
                                <Form.Item
                                    name="cred_owner"
                                    label="Owner name"
                                    rules={OWNER_RULES}
                                    extra="Shown to readers beside the lock."
                                >
                                    <Input autoComplete="username" maxLength={64} />
                                </Form.Item>
                                <Space.Compact block>
                                    <Form.Item
                                        name="cred_password"
                                        label="Password"
                                        rules={PASSWORD_RULES}
                                        style={{ flex: 1 }}
                                    >
                                        <Input.Password autoComplete="new-password" />
                                    </Form.Item>
                                    <Form.Item
                                        name="cred_confirm"
                                        label="Repeat the password"
                                        dependencies={["cred_password"]}
                                        style={{ flex: 1 }}
                                        rules={[
                                            { required: true, message: "Repeat the password" },
                                            ({ getFieldValue }) => ({
                                                validator: (_, value) =>
                                                    !value ||
                                                    value === getFieldValue("cred_password")
                                                        ? Promise.resolve()
                                                        : Promise.reject(
                                                              new Error("The two passwords differ"),
                                                          ),
                                            }),
                                        ]}
                                    >
                                        <Input.Password autoComplete="new-password" />
                                    </Form.Item>
                                </Space.Compact>
                            </>
                        ) : (
                            <Alert
                                type="warning"
                                showIcon
                                style={{ marginBottom: 16 }}
                                message="Anyone who can open this app will be able to edit, run and delete this project. It can be protected later from the project header."
                            />
                        )}
                    </>
                )}

                <Divider orientation="left" plain>
                    Client
                </Divider>
                <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
                    Used only to read answers. Nothing here is sent to the engines.
                </Typography.Paragraph>
                <Space.Compact block>
                    <Form.Item
                        name="brand_name"
                        label="Brand name"
                        rules={[{ required: true }]}
                        style={{ flex: 1 }}
                    >
                        <Input />
                    </Form.Item>
                    <Form.Item
                        name="lob"
                        label="Line of business"
                        rules={[{ required: true }]}
                        style={{ flex: 1 }}
                    >
                        <Input />
                    </Form.Item>
                </Space.Compact>
                <Form.Item name="aliases" label="Aliases">
                    {tagSelect("GEP SMART, NEXXE…")}
                </Form.Item>
                <Form.Item name="domains" label="Client domains" rules={[{ required: true }]}>
                    {tagSelect("gep.com")}
                </Form.Item>
                <Form.Item name="competitor_domains" label="Competitor domains">
                    {tagSelect("coupa.com, sap.com")}
                </Form.Item>
                <Form.Item name="competitor_names" label="Competitor names (for mention detection)">
                    {tagSelect("Coupa, SAP Ariba")}
                </Form.Item>
                <Form.Item name="seed_keywords" label="Seed keywords" rules={[{ required: true }]}>
                    {tagSelect("procurement software")}
                </Form.Item>
                <Form.Item name="subtopics" label="Subtopics">
                    {tagSelect("optional")}
                </Form.Item>
                <Form.Item
                    name="landing_pages"
                    label="Landing pages (one URL per line, for mapping)"
                >
                    <Input.TextArea rows={3} placeholder="https://www.gep.com/software/…" />
                </Form.Item>

                <Divider orientation="left" plain>
                    Platforms
                </Divider>
                <Form.Item name="engines" rules={[{ required: true }]}>
                    <EngineCheckboxes />
                </Form.Item>
                <Space direction="vertical" style={{ width: "100%" }} size={0}>
                    {ENGINES.filter((e) => engines.includes(e)).map((e) => (
                        <Form.Item
                            key={e}
                            name={["engine_models", e]}
                            label={`${options?.engines.find((x) => x.value === e)?.label ?? e} model`}
                        >
                            <Select
                                allowClear
                                placeholder="Server default"
                                options={options?.models[e] ?? []}
                            />
                        </Form.Item>
                    ))}
                </Space>

                <Divider orientation="left" plain>
                    Schedule
                </Divider>
                <Form.Item
                    name="interval"
                    label="Default run interval"
                    extra="Elapsed time since the last sample of each prompt on each platform. Prompts can override it."
                >
                    <IntervalPicker />
                </Form.Item>
                <Form.Item
                    name="consolidation_runs"
                    label="Consolidation window (full crawls)"
                    extra={consolidationHint(interval, Number(runs) || 3)}
                >
                    <InputNumber min={1} max={50} style={{ width: 160 }} />
                </Form.Item>

                <Divider orientation="left" plain>
                    Market
                </Divider>
                <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
                    Answer engines localise. Leave the country blank to use the server default.
                    Google AI Overview, ChatGPT Search and Perplexity take this market; Gemini has
                    no location field in its API and follows its billing account.{" "}
                    {project ? "It is frozen once the project has crawled." : ""}
                </Typography.Paragraph>
                <Space wrap size={16} align="start">
                    <Form.Item
                        name="locale_country"
                        label="Country"
                        extra="ISO code, e.g. US, GB, IN"
                    >
                        <Input style={{ width: 110 }} maxLength={2} placeholder="US" />
                    </Form.Item>
                    <Form.Item name="locale_language" label="Language" extra="e.g. en, en-gb, hi">
                        <Input style={{ width: 120 }} maxLength={5} placeholder="en" />
                    </Form.Item>
                    <Form.Item name="locale_city" label="City" extra="ChatGPT and Perplexity">
                        <Input style={{ width: 180 }} placeholder="Mumbai" />
                    </Form.Item>
                    <Form.Item name="locale_region" label="Region or state">
                        <Input style={{ width: 180 }} placeholder="Maharashtra" />
                    </Form.Item>
                    <Form.Item
                        name="locale_serp_location"
                        label="Google location"
                        extra="Must match SerpApi's own list, e.g. Mumbai, Maharashtra, India"
                    >
                        <Input style={{ width: 280 }} placeholder="Mumbai, Maharashtra, India" />
                    </Form.Item>
                </Space>

                <Divider orientation="left" plain>
                    Engine controls
                </Divider>
                <Space wrap size={16} align="start">
                    <Form.Item
                        name="samples_per_engine"
                        label="Samples per platform"
                        extra="1–10, blank = setting"
                    >
                        <InputNumber min={1} max={10} style={{ width: 140 }} />
                    </Form.Item>
                    <Form.Item
                        name="max_engine_calls"
                        label="Max engine calls per run"
                        extra="blank = unlimited"
                    >
                        <InputNumber min={1} style={{ width: 160 }} />
                    </Form.Item>
                    <Form.Item
                        name="reuse_within_hours"
                        label="Reuse snapshots newer than"
                        extra="hours"
                    >
                        <InputNumber min={0} style={{ width: 140 }} />
                    </Form.Item>
                </Space>
                <Form.Item
                    name="track_keyword_rank"
                    valuePropName="checked"
                    style={{ marginBottom: 4 }}
                >
                    <Checkbox>Track Google organic rank for seed keywords</Checkbox>
                </Form.Item>
                <Form.Item
                    name="resolve_redirects"
                    valuePropName="checked"
                    style={{ marginBottom: 4 }}
                >
                    <Checkbox>Resolve Gemini redirect links</Checkbox>
                </Form.Item>
                <Form.Item name="generate_prompts" valuePropName="checked">
                    <Checkbox>
                        Also run the Semrush-generated 10 + 10 prompt set at the interval
                    </Checkbox>
                </Form.Item>
                <Form.Item name="notes" label="Notes">
                    <Input.TextArea rows={3} maxLength={2000} />
                </Form.Item>
                {(create.error || update.error) && !(create.error instanceof ApiError) && (
                    <Alert
                        type="error"
                        showIcon
                        message={String((create.error ?? update.error)?.message)}
                    />
                )}
            </Form>
        </Drawer>
    );
}
