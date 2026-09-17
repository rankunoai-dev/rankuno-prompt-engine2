import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "framer-motion";
import { ThemeProvider } from "./app/ThemeProvider";
import { App } from "./App";
import "./app/global.css";

export const queryClient = new QueryClient({
    defaultOptions: {
        queries: { retry: 1, staleTime: 10_000, refetchOnWindowFocus: false },
    },
});

async function boot(): Promise<void> {
    const mock = import.meta.env.VITE_MOCK as string | undefined;
    if (mock) {
        const { startMockWorker } = await import("./mocks/browser");
        await startMockWorker(mock);
    }
    ReactDOM.createRoot(document.getElementById("root")!).render(
        <React.StrictMode>
            <QueryClientProvider client={queryClient}>
                <BrowserRouter>
                    <MotionConfig reducedMotion="user">
                        <ThemeProvider>
                            <App />
                        </ThemeProvider>
                    </MotionConfig>
                </BrowserRouter>
            </QueryClientProvider>
        </React.StrictMode>,
    );
}

void boot();
