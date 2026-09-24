import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/render";
import { App } from "@/App";
import { PROJECT_ID, mockState } from "@/mocks/handlers";

const HOOK = "https://hooks.slack.com/services/T04AB/B05CD/xyz123secret";

describe("reports and alerts", () => {
    it("exports a report and lists it with its provenance and a download link", async () => {
        const user = userEvent.setup();
        renderApp(<App />, { route: `/projects/${PROJECT_ID}/reports` });

        expect(await screen.findByText("Executive reports")).toBeInTheDocument();
        // The list loads after the card; wait for it rather than racing the query.
        expect(await screen.findByText(/No reports yet/)).toBeInTheDocument();

        await user.click(screen.getByRole("button", { name: /Export a report/ }));
        const dialog = await screen.findByRole("dialog");
        fireEvent.change(within(dialog).getByLabelText("Title"), {
            target: { value: "September board pack" },
        });
        await user.click(within(dialog).getByRole("button", { name: "Generate" }));

        const row = await screen.findByText("September board pack");
        expect(row).toBeInTheDocument();
        await waitFor(() => expect(screen.getByText("done")).toBeInTheDocument());
        // The reader can always tell who wrote the words and get the file.
        expect(screen.getByText("model")).toBeInTheDocument();
        const download = screen.getByRole("link", { name: /PDF/ });
        expect(download).toHaveAttribute(
            "href",
            expect.stringContaining(`/api/projects/${PROJECT_ID}/reports/`),
        );
        expect(mockState.reports[PROJECT_ID]).toHaveLength(1);
    });

    it("turning off the AI summary is recorded on the row", async () => {
        const user = userEvent.setup();
        renderApp(<App />, { route: `/projects/${PROJECT_ID}/reports` });
        await user.click(await screen.findByRole("button", { name: /Export a report/ }));
        const dialog = await screen.findByRole("dialog");
        await user.click(
            within(dialog).getByRole("checkbox", { name: /Write the executive summary/ }),
        );
        await user.click(within(dialog).getByRole("button", { name: "Generate" }));

        await waitFor(() => expect(screen.getByText("template")).toBeInTheDocument());
        expect(mockState.reports[PROJECT_ID]?.[0]?.request.narrative).toBe(false);
    });

    it("saves a Slack destination and never shows the webhook again", async () => {
        const user = userEvent.setup();
        renderApp(<App />, { route: `/projects/${PROJECT_ID}/reports` });

        const input = await screen.findByLabelText("Slack webhook");
        fireEvent.change(input, { target: { value: HOOK } });
        await user.click(screen.getByRole("button", { name: "Save" }));

        await waitFor(() => expect(screen.getByText("Configured")).toBeInTheDocument());
        expect(screen.queryByText(/xyz123secret/)).not.toBeInTheDocument();
        expect(screen.getByText(/hooks\.slack\.com\/services\//)).toBeInTheDocument();
        expect(mockState.alerts[PROJECT_ID]?.slack_configured).toBe(true);
    });

    it("refuses a destination that is not a Slack webhook", async () => {
        const user = userEvent.setup();
        renderApp(<App />, { route: `/projects/${PROJECT_ID}/reports` });
        const input = await screen.findByLabelText("Slack webhook");
        fireEvent.change(input, { target: { value: "https://evil.example.com/hook" } });
        await user.click(screen.getByRole("button", { name: "Save" }));

        expect(
            await screen.findByText(/must be an https:\/\/hooks\.slack\.com/),
        ).toBeInTheDocument();
        expect(mockState.alerts[PROJECT_ID]?.slack_configured).toBeFalsy();
    });

    it("only the citation-drop rule is on by default and a rule can be added", async () => {
        const user = userEvent.setup();
        renderApp(<App />, { route: `/projects/${PROJECT_ID}/reports` });

        const drop = await screen.findByRole("checkbox", { name: /Citation rate really fell/ });
        expect(drop).toBeChecked();
        const negative = screen.getByRole("checkbox", {
            name: /An engine said something negative/,
        });
        expect(negative).not.toBeChecked();

        await user.click(negative);
        await waitFor(() =>
            expect(mockState.alerts[PROJECT_ID]?.rules).toContain("negative_claim"),
        );
    });

    it("alerts cannot be switched on with nowhere to send them", async () => {
        const user = userEvent.setup();
        renderApp(<App />, { route: `/projects/${PROJECT_ID}/reports` });
        await user.click(await screen.findByLabelText("Alerts enabled"));
        expect(await screen.findByText(/before enabling alerts/)).toBeInTheDocument();
        expect(mockState.alerts[PROJECT_ID]?.enabled).toBe(false);
    });
});
