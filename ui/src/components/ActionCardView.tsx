/**
 * One action card: what to do, which tracked prompts it is for, the evidence
 * behind it, and the analyst's state (done, owner, note). The checkbox writes
 * through `PUT /api/projects/{id}/actions/{action_id}`.
 */
import { useMemo, useState, type CSSProperties } from "react";
import { App, Button, Checkbox, Input, Tag, Tooltip, Typography } from "antd";
import {
    DownOutlined,
    EditOutlined,
    MessageOutlined,
    RightOutlined,
    UserOutlined,
} from "@ant-design/icons";
import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import type { ActionCard } from "@/api/endpoints";
import { usePrompts, useUpdateAction } from "@/api/queries";
import { fmtDateTime, hostOf, pct } from "@/app/format";
import { ENGINE_COLOR, ENGINE_LABEL, ENGINE_SHORT } from "@/app/theme";
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

/** One line per action type: what the section is telling the analyst. */
export const ACTION_TYPE_HELP: Record<string, string> = {
    convert_mention:
        "The platform names the brand in its answers but links to someone else. Give it a page worth linking.",
    own_claim:
        "A competitor is the source the platform cites for this topic, and the client is not cited at all.",
    read_but_rejected:
        "The platform opened a client page while answering and chose not to cite it.",
    aio_gap: "The client ranks in Google organic results but is missing from the AI Overview.",
    earned_placement:
        "The platform leans on third-party listings and reviews. Get the brand onto those pages.",
    freshness: "The sources being cited are newer than the client pages.",
    defend: "The client holds this citation today. Keep the page fresh so it stays that way.",
    landing_page: "No client page answers these prompts yet.",
};

const OUTCOME: Record<string, { color: string; label: string }> = {
    pending: { color: "default", label: "Pending next consolidation" },
    improved: { color: "success", label: "Improved" },
    unchanged: { color: "warning", label: "No change" },
    regressed: { color: "error", label: "Regressed" },
};

/** Prompts listed on the card before the "show more" toggle. */
const MAX_PROMPTS = 3;

const ENGINE_KEYS = Object.keys(ENGINE_LABEL).sort((a, b) => b.length - a.length);

/** Server copy carries enum names (`CHATGPT_SEARCH`) and untrimmed subtopics. */
function humanize(text: string, subtopic: string): string {
    let out = subtopic ? text.split(subtopic).join(subtopic.trim()) : text;
    for (const key of ENGINE_KEYS) out = out.split(key).join(ENGINE_LABEL[key] ?? key);
    return out;
}

function impactLevel(score: number, max: number): "high" | "medium" | "low" {
    const ratio = max > 0 ? score / max : 0;
    if (ratio >= 0.66) return "high";
    return ratio >= 0.33 ? "medium" : "low";
}

const IMPACT_LABEL = { high: "High impact", medium: "Medium impact", low: "Low impact" } as const;

function fmtNumber(key: string, value: number): string {
    if (/rate|share/.test(key) && value <= 1) return pct(value);
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

function plural(n: number, one: string, many: string): string | null {
    return n > 0 ? `${n} ${n === 1 ? one : many}` : null;
}

interface Props {
    action: ActionCard;
    projectId: string;
    defaultOpen?: boolean;
    /** Highest impact score in the list, so the pill reads relative to its peers. */
    impactMax?: number;
}

export function ActionCardView({ action, projectId, defaultOpen = false, impactMax }: Props) {
    const { message } = App.useApp();
    const update = useUpdateAction(projectId);
    const [owner, setOwner] = useState(action.owner ?? "");
    const [note, setNote] = useState(action.note ?? "");
    const [evidenceOpen, setEvidenceOpen] = useState(defaultOpen);
    const [editing, setEditing] = useState(false);
    const [allPrompts, setAllPrompts] = useState(false);
    const done = action.status === "done";
    const ev = action.evidence;
    // One cached query shared by every card; ids fall back to a short hash until it lands.
    const prompts = usePrompts(projectId);
    const promptText = useMemo(
        () => new Map((prompts.data ?? []).map((p) => [p.prompt_id, p.prompt_text])),
        [prompts.data],
    );

    const title = humanize(action.title, action.subtopic);
    const level = impactLevel(action.impact_score, impactMax ?? Math.max(action.impact_score, 1));
    const shownPrompts = allPrompts ? action.prompt_ids : action.prompt_ids.slice(0, MAX_PROMPTS);
    const hiddenPrompts = action.prompt_ids.length - MAX_PROMPTS;
    const evidenceSummary = [
        plural(ev.quotes.length, "quote", "quotes"),
        plural(ev.domains.length, "domain", "domains"),
        plural(ev.urls.length, "page", "pages"),
        plural(ev.queries.length, "search", "searches"),
    ]
        .filter(Boolean)
        .join(" · ");
    const dirty = owner !== (action.owner ?? "") || note !== (action.note ?? "");

    const save = async (body: { status?: string; owner?: string | null; note?: string | null }) => {
        try {
            await update.mutateAsync({ actionId: action.id, body });
        } catch (err) {
            message.error(err instanceof Error ? err.message : "Could not update the action.");
        }
    };

    const accent = done
        ? "var(--ant-color-success)"
        : ((action.engine && ENGINE_COLOR[action.engine]) ?? "var(--ant-color-primary)");

    return (
        <motion.article
            layout="position"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.98 }}
            transition={{ duration: 0.18 }}
            id={action.id}
            className={`pe-action${done ? " pe-action-done" : ""}`}
            style={{ "--pe-accent": accent } as CSSProperties}
        >
            <header className="pe-action-head">
                <Checkbox
                    aria-label={`Mark done: ${title}`}
                    checked={done}
                    onChange={(e) => save({ status: e.target.checked ? "done" : "open" })}
                />
                <div className="pe-action-headline">
                    <Typography.Text strong delete={done} className="pe-action-title">
                        {title}
                    </Typography.Text>
                    <div className="pe-action-meta">
                        {action.engine && <EngineTag engine={action.engine} short />}
                        <span>{action.subtopic.trim()}</span>
                        {done && (
                            <Tag
                                color={OUTCOME[action.outcome]?.color}
                                style={{ marginInlineEnd: 0 }}
                            >
                                {OUTCOME[action.outcome]?.label ?? action.outcome}
                            </Tag>
                        )}
                    </div>
                </div>
                <Tooltip
                    title={`Impact score ${action.impact_score.toFixed(2)}: cluster searches per month × rate gap × platform weight`}
                >
                    <span className={`pe-impact pe-impact-${level}`}>{IMPACT_LABEL[level]}</span>
                </Tooltip>
            </header>

            <p className="pe-action-body">{humanize(action.prescription, action.subtopic)}</p>

            {action.prompt_ids.length > 0 && (
                <div className="pe-action-prompts" data-testid="action-prompts">
                    <div className="pe-eyebrow">
                        {action.prompt_ids.length === 1
                            ? "Tracked prompt this is for"
                            : `Tracked prompts this is for · ${action.prompt_ids.length}`}
                    </div>
                    <ul>
                        {shownPrompts.map((pid) => (
                            <li key={pid}>
                                <MessageOutlined aria-hidden className="pe-muted" />
                                <Tooltip title="Open this prompt in the Battleground">
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
                    {hiddenPrompts > 0 && (
                        <Button
                            type="link"
                            size="small"
                            style={{ paddingInline: 0 }}
                            onClick={() => setAllPrompts((v) => !v)}
                        >
                            {allPrompts ? "Show fewer" : `Show ${hiddenPrompts} more`}
                        </Button>
                    )}
                </div>
            )}

            <footer className="pe-action-foot">
                <Button
                    type="text"
                    size="small"
                    aria-expanded={evidenceOpen}
                    icon={evidenceOpen ? <DownOutlined /> : <RightOutlined />}
                    onClick={() => setEvidenceOpen((v) => !v)}
                >
                    Evidence
                    {evidenceSummary && <span className="pe-muted"> · {evidenceSummary}</span>}
                </Button>
                <Button
                    type="text"
                    size="small"
                    aria-expanded={editing}
                    icon={action.owner ? <UserOutlined /> : <EditOutlined />}
                    onClick={() => setEditing((v) => !v)}
                >
                    {action.owner || action.note ? (
                        <span className="pe-ellipsis pe-action-assignee">
                            {action.owner ?? "Unassigned"}
                            {action.note ? ` · ${action.note}` : ""}
                        </span>
                    ) : (
                        "Assign owner or add a note"
                    )}
                </Button>
            </footer>

            {editing && (
                <div className="pe-action-edit">
                    <Input
                        aria-label="Owner"
                        placeholder="Owner"
                        prefix={<UserOutlined className="pe-muted" />}
                        value={owner}
                        onChange={(e) => setOwner(e.target.value)}
                        onBlur={() =>
                            owner !== (action.owner ?? "") && save({ owner: owner.trim() || null })
                        }
                        style={{ width: 200 }}
                    />
                    <Input
                        aria-label="Note"
                        placeholder="Note for the team"
                        value={note}
                        onChange={(e) => setNote(e.target.value)}
                        onBlur={() =>
                            note !== (action.note ?? "") && save({ note: note.trim() || null })
                        }
                        style={{ flex: 1, minWidth: 220 }}
                    />
                    {dirty && (
                        <Button
                            type="primary"
                            onClick={() =>
                                save({ owner: owner.trim() || null, note: note.trim() || null })
                            }
                        >
                            Save
                        </Button>
                    )}
                </div>
            )}

            {evidenceOpen && (
                <div className="pe-action-evidence">
                    {Object.keys(ev.numbers).length > 0 && (
                        <div className="pe-evidence-stats">
                            {Object.entries(ev.numbers).map(([k, v]) => (
                                <div key={k} className="pe-evidence-stat">
                                    <span className="pe-eyebrow">{k.replace(/_/g, " ")}</span>
                                    <span className="pe-num">{fmtNumber(k, v)}</span>
                                </div>
                            ))}
                        </div>
                    )}
                    {ev.quotes.length > 0 && (
                        <section>
                            <div className="pe-eyebrow">What the platform said</div>
                            {ev.quotes.map((q, i) => (
                                <blockquote key={i} className="pe-quote">
                                    “{q.text}”
                                    <div className="pe-muted">
                                        {ENGINE_SHORT[q.engine] ?? q.engine}
                                        {q.captured_at ? ` · ${fmtDateTime(q.captured_at)}` : ""}
                                        {q.entity ? ` · ${q.entity}` : ""}
                                    </div>
                                </blockquote>
                            ))}
                        </section>
                    )}
                    {ev.domains.length > 0 && (
                        <section>
                            <div className="pe-eyebrow">Who gets cited · share of answers</div>
                            <div className="pe-chip-row">
                                {ev.domains.map((d) => (
                                    <Tag key={d.domain} bordered={false}>
                                        {d.domain} <b className="pe-num">{pct(d.share)}</b>
                                    </Tag>
                                ))}
                            </div>
                        </section>
                    )}
                    {ev.urls.length > 0 && (
                        <section>
                            <div className="pe-eyebrow">Pages that won the citation</div>
                            <ul className="pe-url-list">
                                {ev.urls.map((u) => (
                                    <li key={u}>
                                        <a href={u} target="_blank" rel="noopener noreferrer">
                                            <b>{hostOf(u)}</b>
                                            {u.replace(/^https?:\/\/[^/]+/, "")}
                                        </a>
                                    </li>
                                ))}
                            </ul>
                        </section>
                    )}
                    {ev.queries.length > 0 && (
                        <section>
                            <div className="pe-eyebrow">What the platform searched for</div>
                            <div className="pe-chip-row">
                                {ev.queries.map((q) => (
                                    <Tag key={q} bordered={false}>
                                        {q}
                                    </Tag>
                                ))}
                            </div>
                        </section>
                    )}
                </div>
            )}
        </motion.article>
    );
}
