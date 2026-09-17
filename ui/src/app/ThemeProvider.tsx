/**
 * Resolves the analyst's theme choice (system by default) into an AntD
 * ConfigProvider and stamps `data-theme` on the root so plain CSS follows.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { useUiStore } from "@/store/ui";
import { buildTheme } from "./theme";

function useMedia(query: string): boolean {
    const [matches, setMatches] = useState(
        () => typeof window !== "undefined" && !!window.matchMedia?.(query).matches,
    );
    useEffect(() => {
        if (!window.matchMedia) return;
        const mq = window.matchMedia(query);
        const onChange = (e: MediaQueryListEvent) => setMatches(e.matches);
        mq.addEventListener?.("change", onChange);
        return () => mq.removeEventListener?.("change", onChange);
    }, [query]);
    return matches;
}

export function useReducedMotion(): boolean {
    return useMedia("(prefers-reduced-motion: reduce)");
}

function useSystemDark(): boolean {
    const [dark, setDark] = useState(() =>
        typeof window !== "undefined" && window.matchMedia
            ? window.matchMedia("(prefers-color-scheme: dark)").matches
            : false,
    );
    useEffect(() => {
        if (!window.matchMedia) return;
        const mq = window.matchMedia("(prefers-color-scheme: dark)");
        const onChange = (e: MediaQueryListEvent) => setDark(e.matches);
        mq.addEventListener?.("change", onChange);
        return () => mq.removeEventListener?.("change", onChange);
    }, []);
    return dark;
}

export function useIsDark(): boolean {
    const mode = useUiStore((s) => s.theme);
    const systemDark = useSystemDark();
    return mode === "dark" || (mode === "system" && systemDark);
}

export function ThemeProvider({ children }: { children: ReactNode }) {
    const dark = useIsDark();
    const mode = useUiStore((s) => s.theme);
    useEffect(() => {
        const root = document.documentElement;
        if (mode === "system") root.removeAttribute("data-theme");
        else root.setAttribute("data-theme", mode);
        root.style.colorScheme = dark ? "dark" : "light";
        document.body.style.background = dark ? "#0f1218" : "#f6f7f9";
    }, [dark, mode]);
    const reduced = useReducedMotion();
    const theme = useMemo(() => buildTheme(dark, reduced), [dark, reduced]);
    return (
        <ConfigProvider theme={theme} virtual={import.meta.env.MODE !== "test"}>
            <AntdApp message={{ maxCount: 3 }} notification={{ placement: "bottomRight" }}>
                {children}
            </AntdApp>
        </ConfigProvider>
    );
}
