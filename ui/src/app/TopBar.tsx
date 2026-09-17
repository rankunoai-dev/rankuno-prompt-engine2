import { Badge, Button, Segmented, Select, Space, Tooltip } from "antd";
import { BulbOutlined, DesktopOutlined, MoonOutlined, SearchOutlined } from "@ant-design/icons";
import { useMatch, useNavigate } from "react-router-dom";
import { useActiveJobs, useProjects } from "@/api/queries";
import { useUiStore, type ThemeMode } from "@/store/ui";

export function TopBar() {
    const navigate = useNavigate();
    const match = useMatch("/projects/:id/:tab");
    const { data: projects } = useProjects();
    const { data: active } = useActiveJobs();
    const theme = useUiStore((s) => s.theme);
    const setTheme = useUiStore((s) => s.setTheme);
    const setPaletteOpen = useUiStore((s) => s.setPaletteOpen);
    const running = active?.length ?? 0;
    const currentId = match?.params.id;
    const tab = match?.params.tab ?? "overview";

    return (
        <div style={{ display: "flex", alignItems: "center", gap: 12, width: "100%" }}>
            <Select
                aria-label="Switch project"
                placeholder="Switch project"
                value={
                    currentId && projects?.some((p) => p.id === currentId) ? currentId : undefined
                }
                onChange={(id) => navigate(`/projects/${id}/${tab}`)}
                style={{ minWidth: 240 }}
                options={(projects ?? []).map((p) => ({
                    value: p.id,
                    label: `${p.name} · ${p.client.brand_name}`,
                }))}
                showSearch
                optionFilterProp="label"
                size="middle"
            />
            <Badge
                count={running ? `${running} run${running > 1 ? "s" : ""} in progress` : 0}
                color="var(--ant-color-primary)"
                style={{ fontWeight: 500 }}
                aria-live="polite"
            />
            <div style={{ flex: 1 }} />
            <Button
                icon={<SearchOutlined />}
                onClick={() => setPaletteOpen(true)}
                aria-label="Open command palette"
            >
                <span>Jump to…</span> <span className="pe-kbd">Ctrl K</span>
            </Button>
            <Tooltip title="Theme">
                <Segmented<ThemeMode>
                    aria-label="Theme"
                    value={theme}
                    onChange={(v) => setTheme(v)}
                    options={[
                        { value: "system", icon: <DesktopOutlined aria-label="System theme" /> },
                        { value: "light", icon: <BulbOutlined aria-label="Light theme" /> },
                        { value: "dark", icon: <MoonOutlined aria-label="Dark theme" /> },
                    ]}
                />
            </Tooltip>
            <Space size={0} />
        </div>
    );
}
