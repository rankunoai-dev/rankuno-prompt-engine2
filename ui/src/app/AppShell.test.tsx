import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/render";
import { App } from "@/App";
import { mockState } from "@/mocks/handlers";
import type { RunJob } from "@/api/endpoints";

describe("app shell", () => {
    it("redirects / to /projects and lists rail entries", async () => {
        renderApp(<App />, { route: "/" });
        expect(await screen.findByRole("link", { name: /projects/i })).toBeInTheDocument();
        expect(screen.getByRole("link", { name: /atlas/i })).toBeInTheDocument();
        expect(screen.getByRole("link", { name: /costs/i })).toBeInTheDocument();
    });

    it("shows the active-jobs badge from /api/jobs?active=true", async () => {
        const job: RunJob = {
            id: "job00000001",
            project_id: "e42161487b61",
            project_name: "Test run GEP",
            request: { force: false, prompt_ids: [], engines: null },
            state: "running",
            position: 0,
            created_at: new Date().toISOString(),
            started_at: new Date().toISOString(),
            finished_at: null,
            progress: {
                phase: "audit",
                message: "",
                checks_done: 1,
                checks_total: 4,
                batches_done: 0,
                batches_total: 1,
                engine_calls: 2,
                percent: 25,
                updated_at: new Date().toISOString(),
            },
            outcome: null,
            error: null,
        };
        mockState.jobs.push(job);
        renderApp(<App />, { route: "/projects" });
        expect(await screen.findByText("1 run in progress")).toBeInTheDocument();
    });

    it("opens the command palette with Ctrl+K and jumps to a project", async () => {
        const user = userEvent.setup();
        renderApp(<App />, { route: "/atlas" });
        await screen.findByRole("link", { name: /projects/i });
        await user.keyboard("{Control>}k{/Control}");
        const box = await screen.findByRole("textbox", { name: "Search" });
        await user.type(box, "GEP procurement");
        const option = await screen.findByRole("option", { name: /GEP procurement \(demo\)/ });
        await user.click(option);
        await waitFor(
            () =>
                expect(
                    screen.getByRole("heading", { level: 3, name: "GEP procurement (demo)" }),
                ).toBeInTheDocument(),
            // The first project route loads its lazy chunk plus three queries; on a
            // throttled laptop that exceeded 8 s (cycles 0016 and 0018).
            { timeout: 30000 },
        );
    });

    it("switches theme mode through the top bar", async () => {
        const user = userEvent.setup();
        renderApp(<App />, { route: "/projects" });
        await screen.findByRole("link", { name: /projects/i });
        await user.click(screen.getByLabelText("Dark theme"));
        await waitFor(() =>
            expect(document.documentElement.getAttribute("data-theme")).toBe("dark"),
        );
    });
});
