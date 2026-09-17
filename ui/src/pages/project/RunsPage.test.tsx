import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/render";
import { App } from "@/App";
import { PROJECT_ID, mockState } from "@/mocks/handlers";

async function openRuns() {
    const utils = renderApp(<App />, { route: `/projects/${PROJECT_ID}/runs` });
    await screen.findByText("Crawl history");
    return utils;
}

describe("runs page", () => {
    it("shows crawl history, pipeline runs and the consolidation status", async () => {
        await openRuns();
        expect(await screen.findByText("2e87b4d46a5d4edd")).toBeInTheDocument();
        expect(screen.getByText("full")).toBeInTheDocument();
        expect(screen.getByText(/No consolidation yet: 1 of 3 crawls done/)).toBeInTheDocument();
    });

    it("starts a run, watches it to completion and lists it in the jobs table", async () => {
        const user = userEvent.setup();
        await openRuns();
        await user.click(screen.getByText("Run due now"));
        await user.click(await screen.findByText("Run due"));
        const card = await screen.findByTestId("job-progress");
        expect(within(card).getByRole("progressbar", { name: "Run progress" })).toBeInTheDocument();
        await waitFor(() => expect(within(card).getByText("Run finished")).toBeInTheDocument(), {
            timeout: 12_000,
        });
        expect(
            within(card).getByText(/Ran 1 batch\(es\), 4 prompt\(s\): success/),
        ).toBeInTheDocument();
        expect(mockState.jobs[0]?.state).toBe("finished");
        expect(await screen.findByText("finished")).toBeInTheDocument();
    });

    it("consolidates on demand with a custom window and updates the panel", async () => {
        const user = userEvent.setup();
        await openRuns();
        const spin = screen.getByLabelText("Crawls to consolidate");
        await user.clear(spin);
        await user.type(spin, "1");
        await user.click(screen.getByText("Consolidate now"));
        expect(await screen.findByText(/Latest consolidation/)).toBeInTheDocument();
        expect(screen.getByText(/window 1 crawl\(s\)/)).toBeInTheDocument();
        expect(mockState.consolidations[PROJECT_ID]?.length).toBe(1);
    });

    it("shows the API detail verbatim when consolidation is refused", async () => {
        const user = userEvent.setup();
        mockState.crawls[PROJECT_ID] = [];
        await openRuns();
        await user.click(screen.getByText("Consolidate now"));
        expect(
            await screen.findByText("No crawl of this project has been recorded yet."),
        ).toBeInTheDocument();
    });
});
