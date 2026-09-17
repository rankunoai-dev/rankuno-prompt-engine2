/**
 * Ctrl/⌘ K: jump to a project, a page of the current project, a prompt, or an
 * open action. Items are plain buttons so arrow keys and Enter work.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Input, Modal, Typography } from "antd";
import { useMatch, useNavigate } from "react-router-dom";
import { useInsights, useProjects, usePrompts } from "@/api/queries";
import { useUiStore } from "@/store/ui";

interface Item {
    kind: "Project" | "Page" | "Prompt" | "Action";
    label: string;
    hint?: string;
    go: () => void;
}

const PAGES: [string, string][] = [
    ["overview", "Overview"],
    ["battleground", "Battleground"],
    ["prompts", "Prompts"],
    ["runs", "Runs & spend"],
    ["actions", "Actions"],
];

export function CommandPalette() {
    const open = useUiStore((s) => s.paletteOpen);
    const setOpen = useUiStore((s) => s.setPaletteOpen);
    const navigate = useNavigate();
    const match = useMatch("/projects/:id/*");
    const projectId = match?.params.id;
    const { data: projects } = useProjects();
    const { data: prompts } = usePrompts(open ? projectId : undefined);
    const { data: insights } = useInsights(open ? projectId : undefined);
    const [q, setQ] = useState("");
    const [sel, setSel] = useState(0);
    const listRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
                e.preventDefault();
                setOpen(!open);
            }
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [open, setOpen]);

    useEffect(() => {
        if (open) {
            setQ("");
            setSel(0);
        }
    }, [open]);

    const items = useMemo<Item[]>(() => {
        const needle = q.trim().toLowerCase();
        const out: Item[] = [];
        const go = (to: string) => () => {
            setOpen(false);
            navigate(to);
        };
        for (const p of projects ?? []) {
            const label = `${p.name} · ${p.client.brand_name}`;
            if (!needle || label.toLowerCase().includes(needle)) {
                out.push({
                    kind: "Project",
                    label,
                    hint: p.client.lob,
                    go: go(`/projects/${p.id}/overview`),
                });
            }
        }
        const pages: [string, string][] = projectId
            ? [...PAGES, ["/atlas", "Atlas"], ["/costs", "Costs"]]
            : [
                  ["/projects", "Projects"],
                  ["/atlas", "Atlas"],
                  ["/costs", "Costs"],
              ];
        for (const [key, label] of pages) {
            if (!needle || label.toLowerCase().includes(needle)) {
                out.push({
                    kind: "Page",
                    label,
                    go: go(key.startsWith("/") ? key : `/projects/${projectId}/${key}`),
                });
            }
        }
        if (needle && projectId) {
            for (const pr of prompts ?? []) {
                if (pr.prompt_text.toLowerCase().includes(needle)) {
                    out.push({
                        kind: "Prompt",
                        label: pr.prompt_text,
                        hint: pr.subtopic ?? pr.keyword ?? undefined,
                        go: go(`/projects/${projectId}/battleground?prompt=${pr.prompt_id}`),
                    });
                }
            }
            for (const a of insights?.actions ?? []) {
                if (a.status === "open" && a.title.toLowerCase().includes(needle)) {
                    out.push({
                        kind: "Action",
                        label: a.title,
                        hint: a.subtopic,
                        go: go(`/projects/${projectId}/actions#${a.id}`),
                    });
                }
            }
        }
        return out.slice(0, 14);
    }, [q, projects, prompts, insights, projectId, navigate, setOpen]);

    useEffect(() => setSel(0), [items.length]);

    const onKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === "ArrowDown") {
            e.preventDefault();
            setSel((s) => Math.min(items.length - 1, s + 1));
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setSel((s) => Math.max(0, s - 1));
        } else if (e.key === "Enter") {
            items[sel]?.go();
        }
    };

    return (
        <Modal
            open={open}
            onCancel={() => setOpen(false)}
            footer={null}
            closable={false}
            title={null}
            width={620}
            style={{ top: 80 }}
            destroyOnClose
            aria-label="Command palette"
        >
            <Input
                autoFocus
                size="large"
                placeholder="Jump to a project, page, prompt or action…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={onKeyDown}
                aria-label="Search"
                variant="borderless"
            />
            <div
                ref={listRef}
                role="listbox"
                aria-label="Results"
                style={{ maxHeight: 360, overflow: "auto" }}
            >
                {items.map((it, i) => (
                    <button
                        key={`${it.kind}:${it.label}`}
                        type="button"
                        role="option"
                        aria-selected={i === sel}
                        onMouseEnter={() => setSel(i)}
                        onClick={it.go}
                        style={{
                            display: "flex",
                            gap: 12,
                            alignItems: "center",
                            width: "100%",
                            textAlign: "left",
                            padding: "8px 10px",
                            border: 0,
                            borderRadius: 8,
                            cursor: "pointer",
                            background: i === sel ? "var(--ant-color-primary-bg)" : "transparent",
                            color: "inherit",
                            font: "inherit",
                        }}
                    >
                        <Typography.Text
                            type="secondary"
                            style={{ minWidth: 58, fontSize: 11, textTransform: "uppercase" }}
                        >
                            {it.kind}
                        </Typography.Text>
                        <span className="pe-ellipsis" style={{ flex: 1 }}>
                            {it.label}
                        </span>
                        {it.hint && (
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                {it.hint}
                            </Typography.Text>
                        )}
                    </button>
                ))}
                {!items.length && (
                    <Typography.Text type="secondary" style={{ display: "block", padding: 12 }}>
                        Nothing matches.
                    </Typography.Text>
                )}
            </div>
        </Modal>
    );
}
