/**
 * Upload a web-server access log.
 *
 * Two paths, chosen by size after the browser drops every line that does not
 * name a known crawler: small logs post as JSON, big ones stream to the server
 * as the original file with a progress bar. The pre-filter is what makes a
 * 300 MB production log usable from a browser at all, and it keys on exactly
 * the tokens the server keys on, so nothing measurable is lost.
 *
 * No line of the file is ever rendered: a log line carries an IP address.
 */
import { useRef, useState } from "react";
import {
    Alert,
    App,
    Card,
    Descriptions,
    Input,
    Progress,
    Space,
    Tag,
    Typography,
    Upload,
} from "antd";
import { InboxOutlined } from "@ant-design/icons";
import type { CrawlerImportResult } from "@/api/endpoints";
import { endpoints } from "@/api/endpoints";
import { ApiError } from "@/api/client";
import { uploadFile } from "@/api/upload";
import { useCrawlerBots, useCrawlerRefresh } from "@/api/queries";
import { num } from "@/app/format";
import {
    BASIS_LABEL,
    FORMAT_LABEL,
    fitsAsJson,
    prefilter,
    rawContentType,
    readLogText,
} from "@/lib/crawlerLog";

/** Counters worth showing; zeros are hidden except for verified fetches. */
const COUNTERS: { key: keyof CrawlerImportResult; label: string; always?: boolean }[] = [
    { key: "lines", label: "Lines read" },
    { key: "parsed", label: "Parsed" },
    { key: "unparsed", label: "Unparsed" },
    { key: "duplicate_lines", label: "Duplicate lines" },
    { key: "verified_hits", label: "Verified fetches", always: true },
    { key: "stealth_hits", label: "Unnamed crawler hits" },
    { key: "hosts_skipped", label: "Other hosts skipped" },
    { key: "no_host", label: "Lines with no host" },
    { key: "methods_skipped", label: "Non-GET skipped" },
    { key: "sensitive_dropped", label: "Sensitive paths dropped" },
];

function ResultPanel({
    result,
    onOpenImports,
}: {
    result: CrawlerImportResult;
    onOpenImports: () => void;
}) {
    const span =
        result.span_from && result.span_to
            ? `${new Date(result.span_from).toLocaleDateString(undefined, { day: "2-digit", month: "short" })} – ${new Date(result.span_to).toLocaleDateString(undefined, { day: "2-digit", month: "short" })}`
            : "an unknown period";
    return (
        <Space direction="vertical" size={10} style={{ width: "100%" }} data-testid="import-result">
            <Space wrap align="center">
                <Typography.Text strong>
                    {num(result.hits)} crawler fetches from {num(result.matched)} matched lines
                    across {span}
                </Typography.Text>
                <Tag bordered={false}>{FORMAT_LABEL[result.format] ?? result.format}</Tag>
                <Tag
                    bordered={false}
                    color={result.verification_basis === "none" ? "default" : "blue"}
                >
                    {BASIS_LABEL[result.verification_basis] ?? result.verification_basis}
                </Tag>
                {result.sampled && <Tag color="warning">sampled</Tag>}
            </Space>

            <Descriptions size="small" column={{ xs: 2, sm: 3, lg: 5 }} colon={false}>
                {COUNTERS.filter((c) => c.always || Number(result[c.key] ?? 0) > 0).map((c) => (
                    <Descriptions.Item key={String(c.key)} label={c.label}>
                        {num(Number(result[c.key] ?? 0))}
                    </Descriptions.Item>
                ))}
            </Descriptions>

            {result.no_host > 0 && (
                <Alert
                    type="info"
                    showIcon
                    message="This log carries no host field; requests were attributed to the project's domains."
                />
            )}
            {result.keys_truncated && (
                <Alert
                    type="warning"
                    showIcon
                    message="More than 200,000 distinct pages; the smallest were dropped."
                />
            )}
            {result.sampled && (
                <Alert
                    type="warning"
                    showIcon
                    message="Cloudflare sampled this log; counts are scaled by the sample interval."
                />
            )}
            {result.overlaps.length > 0 && (
                <Alert
                    type="info"
                    showIcon
                    message={
                        <span>
                            Shares days with {result.overlaps.length} earlier import(s); per day the
                            import with the most lines is used.{" "}
                            <Typography.Link onClick={onOpenImports}>See imports</Typography.Link>
                        </span>
                    }
                />
            )}
            {/* The data-processing answer, in the API's own words. Never paraphrased. */}
            <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
                {result.stored}
            </Typography.Paragraph>
        </Space>
    );
}

export function CrawlerImportCard({
    projectId,
    onOpenImports,
}: {
    projectId: string;
    onOpenImports: () => void;
}) {
    const { message } = App.useApp();
    const { data: bots } = useCrawlerBots();
    const refresh = useCrawlerRefresh(projectId);
    const [note, setNote] = useState("");
    const [busy, setBusy] = useState(false);
    const [progress, setProgress] = useState<number | null>(null);
    const [summary, setSummary] = useState<string | null>(null);
    const [result, setResult] = useState<CrawlerImportResult | null>(null);
    const [error, setError] = useState<{ detail: string; importId?: string } | null>(null);
    const lastFile = useRef<File | null>(null);

    const tokens = (bots ?? []).map((b) => b.token);

    const send = async (file: File) => {
        lastFile.current = file;
        setBusy(true);
        setError(null);
        setResult(null);
        setProgress(null);
        try {
            const text = await readLogText(file);
            let outcome: CrawlerImportResult;
            if (text !== null && tokens.length) {
                const filtered = prefilter(text, tokens);
                setSummary(
                    `Kept ${num(filtered.kept)} of ${num(filtered.total)} lines that name a known crawler`,
                );
                if (!filtered.kept) {
                    setError({
                        detail: "No line in that file names a crawler this tracker knows.",
                    });
                    return;
                }
                if (fitsAsJson(filtered.bytes)) {
                    outcome = await endpoints.importCrawlerLog(projectId, filtered.text, note);
                } else {
                    setProgress(0);
                    outcome = await uploadFile<CrawlerImportResult>(
                        projectId,
                        `/api/projects/${projectId}/crawler-logs/import`,
                        new Blob([filtered.text], { type: "text/plain" }),
                        { contentType: "text/plain", onProgress: setProgress },
                    );
                }
            } else {
                // A compressed file this browser cannot expand: the server can.
                setSummary(null);
                setProgress(0);
                outcome = await uploadFile<CrawlerImportResult>(
                    projectId,
                    `/api/projects/${projectId}/crawler-logs/import`,
                    file,
                    { contentType: rawContentType(file), onProgress: setProgress },
                );
            }
            setResult(outcome);
            await refresh();
            message.success("Log imported.");
        } catch (err) {
            if (err instanceof ApiError) {
                const detail = typeof err.detail === "string" ? err.detail : err.message;
                const importId =
                    err.status === 409 && err.detail && typeof err.detail === "object"
                        ? String((err.detail as { import_id?: string }).import_id ?? "")
                        : undefined;
                setError({
                    detail:
                        err.status === 409
                            ? "That exact file has already been imported."
                            : err.status === 413
                              ? `${detail} Upload the original file instead of pasting it.`
                              : detail,
                    importId: importId || undefined,
                });
            } else {
                setError({ detail: err instanceof Error ? err.message : "The import failed." });
            }
        } finally {
            setBusy(false);
            setProgress(null);
        }
    };

    return (
        <Card title="Import an access log">
            <Space direction="vertical" size={12} style={{ width: "100%" }}>
                <Upload.Dragger
                    accept=".log,.txt,.gz,.json,.ndjson,text/plain,application/gzip"
                    showUploadList={false}
                    disabled={busy}
                    beforeUpload={(file) => {
                        void send(file as unknown as File);
                        return false;
                    }}
                >
                    <p className="ant-upload-drag-icon">
                        <InboxOutlined />
                    </p>
                    <p className="ant-upload-text">Drop an access log here, or click to choose</p>
                    <p className="ant-upload-hint">
                        nginx or Apache combined format, or Cloudflare Logpush NDJSON. Gzip is fine.
                        The file is filtered in your browser first; only lines naming a crawler are
                        sent, and no address is stored.
                    </p>
                </Upload.Dragger>

                <Input
                    aria-label="Note"
                    maxLength={500}
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="e.g. nginx access.log, 1–30 Sept"
                />

                {summary && <Typography.Text type="secondary">{summary}</Typography.Text>}
                {progress !== null && (
                    <Progress percent={Math.round(progress * 100)} size="small" status="active" />
                )}
                {busy && progress === null && (
                    <Typography.Text type="secondary">Reading…</Typography.Text>
                )}

                {error && (
                    <Alert
                        type="error"
                        showIcon
                        message={error.detail}
                        description={
                            error.importId ? (
                                <Typography.Link onClick={onOpenImports}>
                                    See the earlier import {error.importId}
                                </Typography.Link>
                            ) : undefined
                        }
                    />
                )}
                {result && <ResultPanel result={result} onOpenImports={onOpenImports} />}
                {!result && !busy && lastFile.current === null && (
                    <Typography.Text type="secondary">
                        Nothing is uploaded until you choose a file.
                    </Typography.Text>
                )}
            </Space>
        </Card>
    );
}
