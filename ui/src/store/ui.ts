/**
 * UI-only state (Zustand). Server state lives in TanStack Query; anything
 * addressable by URL (project, tab, drawer) lives in the router. What is left
 * is the viewer's own preferences and transient selection.
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";

export type ThemeMode = "system" | "light" | "dark";
export type ResultsMode = "consolidated" | "point";

interface UiState {
    theme: ThemeMode;
    setTheme: (mode: ThemeMode) => void;
    railCollapsed: boolean;
    toggleRail: () => void;
    pageSize: number;
    setPageSize: (n: number) => void;
    resultsMode: ResultsMode;
    setResultsMode: (m: ResultsMode) => void;
    paletteOpen: boolean;
    setPaletteOpen: (open: boolean) => void;
    /** Prompt ids selected in the prompts table, per project. */
    selection: Record<string, string[]>;
    setSelection: (projectId: string, ids: string[]) => void;
    /** Job ids whose progress card the analyst hid. */
    hiddenJobs: string[];
    hideJob: (jobId: string) => void;
    lastProjectId: string | null;
    setLastProjectId: (id: string | null) => void;
    /** Job the analyst chose to watch, per project. */
    watchedJobs: Record<string, string>;
    setWatchedJob: (projectId: string, jobId: string) => void;
}

/** The slice `partialize` writes to localStorage; `migrate` receives and returns it. */
type Persisted = Pick<
    UiState,
    "theme" | "railCollapsed" | "pageSize" | "resultsMode" | "lastProjectId"
>;

export const useUiStore = create<UiState>()(
    persist(
        (set) => ({
            theme: "dark",
            setTheme: (theme) => set({ theme }),
            railCollapsed: false,
            toggleRail: () => set((s) => ({ railCollapsed: !s.railCollapsed })),
            pageSize: 25,
            setPageSize: (pageSize) => set({ pageSize }),
            resultsMode: "consolidated",
            setResultsMode: (resultsMode) => set({ resultsMode }),
            paletteOpen: false,
            setPaletteOpen: (paletteOpen) => set({ paletteOpen }),
            selection: {},
            setSelection: (projectId, ids) =>
                set((s) => ({ selection: { ...s.selection, [projectId]: ids } })),
            hiddenJobs: [],
            hideJob: (jobId) => set((s) => ({ hiddenJobs: [...s.hiddenJobs, jobId].slice(-50) })),
            lastProjectId: null,
            setLastProjectId: (lastProjectId) => set({ lastProjectId }),
            watchedJobs: {},
            setWatchedJob: (projectId, jobId) =>
                set((s) => ({ watchedJobs: { ...s.watchedJobs, [projectId]: jobId } })),
        }),
        {
            name: "prompt-engine-ui",
            // v1: dark became the default. A stored "system" under v0 was never a
            // choice (it was the old default), so it follows; explicit light/dark stay.
            version: 1,
            migrate: (persisted, version) => {
                const state = persisted as Persisted;
                if (version < 1 && state.theme === "system") return { ...state, theme: "dark" };
                return state;
            },
            partialize: (s) => ({
                theme: s.theme,
                railCollapsed: s.railCollapsed,
                pageSize: s.pageSize,
                resultsMode: s.resultsMode,
                lastProjectId: s.lastProjectId,
            }),
        },
    ),
);
