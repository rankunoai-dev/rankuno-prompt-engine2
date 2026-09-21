/**
 * Collapsible left rail (Projects, Atlas, Costs), top bar, animated outlet.
 * The job announcer and the command palette live here so they exist on every
 * page.
 */
import { Layout } from "antd";
import { AnimatePresence, motion } from "framer-motion";
import { useLocation, useOutlet } from "react-router-dom";
import { useState } from "react";
import { Rail } from "./Rail";
import { TopBar } from "./TopBar";
import { CommandPalette } from "./CommandPalette";
import { JobAnnouncer } from "./JobAnnouncer";
import { ProjectUnlockDialog } from "./ProjectUnlock";
import { useUiStore } from "@/store/ui";

const { Sider, Header, Content } = Layout;

/**
 * Keeps the outlet element it was created with. Inside an AnimatePresence the
 * exiting page container would otherwise re-render the *new* route while it
 * animates out, mounting every page twice per navigation.
 */
function FrozenOutlet() {
    const outlet = useOutlet();
    const [frozen] = useState(outlet);
    return frozen;
}

export function AppShell() {
    const location = useLocation();
    const collapsed = useUiStore((s) => s.railCollapsed);
    const toggleRail = useUiStore((s) => s.toggleRail);
    // Animate on page change, not on every query-string change (drawer open/close).
    const routeKey = location.pathname;
    return (
        <Layout style={{ minHeight: "100%" }}>
            <Sider
                collapsible
                collapsed={collapsed}
                onCollapse={toggleRail}
                width={220}
                collapsedWidth={64}
                breakpoint="lg"
                style={{ borderRight: "1px solid var(--ant-color-border-secondary)" }}
            >
                <Rail collapsed={collapsed} />
            </Sider>
            <Layout>
                <Header
                    style={{
                        position: "sticky",
                        top: 0,
                        zIndex: 10,
                        borderBottom: "1px solid var(--ant-color-border-secondary)",
                        display: "flex",
                        alignItems: "center",
                    }}
                >
                    <TopBar />
                </Header>
                <Content style={{ padding: "20px 24px 48px", minWidth: 0 }}>
                    <AnimatePresence mode="wait" initial={false}>
                        <motion.div
                            key={routeKey}
                            initial={{ opacity: 0, y: 6 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -4 }}
                            transition={{ duration: 0.18, ease: "easeOut" }}
                        >
                            <FrozenOutlet />
                        </motion.div>
                    </AnimatePresence>
                </Content>
            </Layout>
            <CommandPalette />
            <JobAnnouncer />
            <ProjectUnlockDialog />
        </Layout>
    );
}
