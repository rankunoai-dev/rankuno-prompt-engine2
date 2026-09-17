/** Renders a tree with fresh providers and a memory router for tests. */
import type { ReactElement, ReactNode } from "react";
import { render, type RenderOptions } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { MotionConfig } from "framer-motion";
import { ThemeProvider } from "@/app/ThemeProvider";

export function makeQueryClient(): QueryClient {
    return new QueryClient({
        defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
    });
}

interface Options extends Omit<RenderOptions, "wrapper"> {
    route?: string;
    /** Route pattern to mount `ui` at, e.g. `/projects/:id/prompts`. Defaults to `*`. */
    path?: string;
    client?: QueryClient;
}

export function renderApp(ui: ReactElement, opts: Options = {}) {
    const client = opts.client ?? makeQueryClient();
    const route = opts.route ?? "/";
    const path = opts.path ?? "*";
    function Wrapper({ children }: { children: ReactNode }) {
        return (
            <QueryClientProvider client={client}>
                <MemoryRouter initialEntries={[route]}>
                    <MotionConfig reducedMotion="always">
                        <ThemeProvider>
                            <Routes>
                                <Route path={path} element={children} />
                            </Routes>
                        </ThemeProvider>
                    </MotionConfig>
                </MemoryRouter>
            </QueryClientProvider>
        );
    }
    return { client, ...render(ui, { wrapper: Wrapper, ...opts }) };
}
