/** Shared state for the Atlas views: index, filters, as-of run, engines in play. */
import { createContext, useContext } from "react";
import type { AtlasPrompt, Engine, Project } from "@/api/endpoints";
import type { AtlasFilters, AtlasIndex } from "@/lib/atlas";

export interface AtlasState {
    index: AtlasIndex;
    filters: AtlasFilters;
    setFilters: (f: AtlasFilters) => void;
    asOf: string | null;
    engines: Engine[];
    prompts: AtlasPrompt[];
    /** The project tracking this LOB, if any: unlocks insights and the drawer roles. */
    project: Project | null;
    openPrompt: (promptId: string, engine?: Engine) => void;
    goView: (view: string) => void;
}

export const AtlasCtx = createContext<AtlasState | null>(null);

export function useAtlasState(): AtlasState {
    const ctx = useContext(AtlasCtx);
    if (!ctx) throw new Error("useAtlasState outside AtlasPage");
    return ctx;
}
