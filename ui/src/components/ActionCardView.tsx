/**
 * One action card: title, prescription, impact, the evidence behind it, an
 * owner, a note and the done checkbox. The checkbox writes through
 * `PUT /api/projects/{id}/actions/{action_id}`.
 */
import { useMemo, useState } from "react";
import {
    App,
    Button,
    Card,
    Checkbox,
    Collapse,
    Input,
    Space,
    Tag,
    Tooltip,
    Typography,
} from "antd";
import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import type { ActionCard } from "@/api/endpoints";
import { usePrompts, useUpdateAction } from "@/api/queries";
import { fmtDateTime, hostOf, pct } from "@/app/format";
import { ENGINE_SHORT } from "@/app/theme";
import { EngineTag } from "./EngineTag";

export const ACTION_TYPE_LABEL: Record<string, string> = {
    convert_mention: "Convert mention to citation",
    own_claim: "Own the claim a competitor owns",
    read_but_rejected: "Read but rejected",
    aio_gap: "AI Overview gap",
    earned_placement: "Earned placement",
    freshness: "Freshness",
    defend: "Defend",
    landing_page: "Landing page missing",
};

const OUTCOME: Record<string, { color: string; label: string }> = {
    pending: { color: "default", label: "Pending next consolidation" },
    improved: { color: "success", label: "Improved" },
    unchanged: { color: "warning", label: "No change" },
    regressed: { color: "error", label: "Regressed" },
};

/** Prompts listed on the card before "+N more". */
const MAX_PROMPTS = 6;

interface Props {
    action: ActionCard;
    projectId: string;
    defaultOpen?: boolean;
}

export function ActionCardView({ action, projectId, defaultOpen = false }: Props) {
    const { message } = App.useApp();
    const update = useUpdateAction(projectId);
    const [owner, setOwner] = useState(action.owner ?? "");
    const [note, setNote] = useState(action.note ?? "");
    const done = action.status === "done";
    const ev = action.evidence;
    // One cached query shared by every card; ids fall back to a short hash until it lands.
    const prompts = usePrompts(projectId);
    const promptText = useMemo(
        () => new Map((prompts.data ?? []).map((p) => [p.prompt_id, p.prompt_text])),
        [prompts.data],
    );

    const save = async (body: { status?: string; owner?: string | null; note?: string | null }) => {
        try {
            await update.mutateAsync({ actionId: action.id, body });
        } catch (err) {
            message.error(err instanceof Error ? err.message : "Could not update the action.");
        }
    };

    return (
        <motion.div
            layout
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.98 }}
            transition={{ duration: 0.18 }}
        >
            <Card
                id={action.id}
                size="small"
                style={{
                    opacity: done ? 0.75 : 1,
                    borderLeft: `3px solid ${done ? "var(--ant-color-success)" : "var(--ant-color-primary)"}`,
                }}
                title={
                    <Space align="start" style={{ width: "100%" }}>
                        <Checkbox
                            aria-label={`Mark done: ${action.title}`}
                            checked={done}
                            onChange={(e) => save({ status: e.target.checked ? "done" : "open" })}
                        />
                        <div style={{ whiteSpace: "normal" }}>
                            <Typography.Text strong delete={done}>
                                {action.title}
                            </Typography.Text>
                            <div style={{ marginTop: 4 }}>
                                <Space size={4} wrap>
                                    <Tag>{ACTION_TYPE_LABEL[action.type] ?? action.type}</Tag>
                                    {action.engine && <EngineTag engine={action.engine} short />}
                                    <Tag>{action.subtopic}</Tag>
                                    <Tooltip title="Expected impact: cluster searches per month × rate gap × platform weight">
                                        <Tag color="processing">
                                            impact {action.impact_score.toFixed(2)}
                                        </Tag>
                                    </Tooltip>
                                    {done && (
                                        <Tag color={OUTCOME[action.outcome]?.color}>
                                            {OUTCOME[action.outcome]?.label ?? action.outcome}
                                        </Tag>
                                    )}
                                </Space>
                            </div>
                        </div>
                    </Space>
                }
            >
                <Typography.Paragraph style={{ marginBottom: 8 }}>
                    {action.prescription}
                </Typography.Paragraph>
                {action.prompt_ids.length > 0 && (
                    <div style={{ marginBottom: 8 }} data-testid="action-prompts">
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {action.prompt_ids.length === 1
                                ? "Tracked prompt this is for:"
                                : `Tracked prompts this is for (${action.prompt_ids.length}):`}
                        </Typography.Text>
                        <ul style={{ margin: "4px 0 0", paddingLeft: 18, fontSize: 13 }}>
                            {action.prompt_ids.slice(0, MAX_PROMPTS).map((pid) => (
                                <li key={pid}>
                                    <Tooltip title={`Open in Battleground · ${pid.slice(0, 8)}`}>
                                        <Link
                                            to={`/projects/${projectId}/battleground?prompt=${pid}${action.engine ? `&engine=${action.engine}` : ""}`}
                                        >
                                            {promptText.get(pid) ?? (
                                                <span className="pe-mono">{pid.slice(0, 8)}</span>
                                            )}
                                        </Link>
                                    </Tooltip>
                                </li>
                            ))}
                        </ul>
                        {action.prompt_ids.length > MAX_PROMPTS && (
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                +{action.prompt_ids.length - MAX_PROMPTS} more
                            </Typography.Text>
                        )}
                    </div>
                )}
                <Collapse
                    ghost
                    size="small"
                    defaultActiveKey={defaultOpen ? ["evidence"] : []}
                    items={[
                        {
                            key: "evidence",
                            label: `Evidence · ${ev.quotes.length} quote(s), ${ev.domains.length} domain(s), ${ev.urls.length} URL(s), ${ev.queries.length} quer${ev.queries.length === 1 ? "y" : "ies"}`,
                            children: (
                                <Space direction="vertical" size={10} style={{ width: "100%" }}>
                                    {ev.quotes.map((q, i) => (
                                        <blockquote
                                            key={i}
                                            style={{
                                                margin: 0,
                                                paddingLeft: 10,
                                                borderLeft: "2px solid var(--ant-color-border)",
                                                fontSize: 13,
                                            }}
                                        >
                                            “{q.text}”
                                            <div className="pe-muted" style={{ fontSize: 12 }}>
                                                {ENGINE_SHORT[q.engine] ?? q.engine}
                                                {q.captured_at
                                                    ? ` · ${fmtDateTime(q.captured_at)}`
                                                    : ""}
                                                {q.entity ? ` · ${q.entity}` : ""}
                                            </div>
                                        </blockquote>
                                    ))}
                                    {ev.domains.length > 0 && (
                                        <div>
                                            {ev.domains.map((d) => (
                                                <Tag key={d.domain}>
                                                    {d.domain} {pct(d.share)}
                                                </Tag>
                                            ))}
                                        </div>
                                    )}
                                    {ev.urls.length > 0 && (
                                        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
                                            {ev.urls.map((u) => (
                                                <li key={u}>
                                                    <a
                                                        href={u}
                                                        target="_blank"
                                                        rel="noopener noreferrer"
                                                    >
                                                        {hostOf(u)}{" "}
                                                        {u.replace(/^https?:\/\/[^/]+/, "")}
                                                    </a>
                                                </li>
                                            ))}
                                        </ul>
                                    )}
                                    {ev.queries.length > 0 && (
                                        <div>
                                            <Typography.Text
                                                type="secondary"
                                                style={{ fontSize: 12 }}
                                            >
                                                The engine searched for:{" "}
                                            </Typography.Text>
                                            {ev.queries.map((q) => (
                                                <Tag key={q}>{q}</Tag>
                                            ))}
                                        </div>
                                    )}
                                    {Object.keys(ev.numbers).length > 0 && (
                                        <Typography.Text
                                            type="secondary"
                                            style={{ fontSize: 12 }}
                                            className="pe-num"
                                        >
                                            {Object.entries(ev.numbers)
                                                .map(
                                                    ([k, v]) =>
                                                        `${k.replace(/_/g, " ")} ${typeof v === "number" && v <= 1 && k.includes("rate") ? pct(v) : v}`,
                                                )
                                                .join(" · ")}
                                        </Typography.Text>
                                    )}
                                </Space>
                            ),
                        },
                    ]}
                />
                <Space wrap style={{ marginTop: 8 }}>
                    <Input
                        aria-label="Owner"
                        size="small"
                        placeholder="Owner"
                        value={owner}
                        onChange={(e) => setOwner(e.target.value)}
                        onBlur={() =>
                            owner !== (action.owner ?? "") && save({ owner: owner.trim() || null })
                        }
                        style={{ width: 160 }}
                    />
                    <Input
                        aria-label="Note"
                        size="small"
                        placeholder="Note"
                        value={note}
                        onChange={(e) => setNote(e.target.value)}
                        onBlur={() =>
                            note !== (action.note ?? "") && save({ note: note.trim() || null })
                        }
                        style={{ width: 280 }}
                    />
                    {(owner !== (action.owner ?? "") || note !== (action.note ?? "")) && (
                        <Button
                            size="small"
                            onClick={() =>
                                save({ owner: owner.trim() || null, note: note.trim() || null })
                            }
                        >
                            Save
                        </Button>
                    )}
                </Space>
            </Card>
        </motion.div>
    );
}
