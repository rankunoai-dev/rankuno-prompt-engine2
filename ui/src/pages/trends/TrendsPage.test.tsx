import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/render";
import { App } from "@/App";
import { endpoints } from "@/api/endpoints";
import { PROJECT_ID } from "@/mocks/handlers";

describe("trends page", () => {
    it("charts citation and mention rates per run for the project's platforms", async () => {
        renderApp(<App />, { route: `/trends?project=${PROJECT_ID}` });
        expect(await screen.findByText("Citation rate")).toBeInTheDocument();
        expect(screen.getByText("Mention rate")).toBeInTheDocument();
        expect(screen.getByText(/pipeline runs?, each on its own date/)).toBeInTheDocument();
        const tables = screen.getAllByRole("table");
        expect(tables.length).toBe(2);
        expect(within(tables[0]!).getByText("Google AIO")).toBeInTheDocument();
        expect(within(tables[0]!).getByText("Gemini")).toBeInTheDocument();
    });

    it("narrows to one platform and to mentions only", async () => {
        const user = userEvent.setup();
        renderApp(<App />, { route: `/trends?project=${PROJECT_ID}` });
        await screen.findByText("Citation rate");
        await user.click(screen.getByRole("combobox", { name: "Platform" }));
        await user.click(
            await screen.findByText("Perplexity", {
                selector: ".ant-select-item-option-content span",
            }),
        );
        await user.click(screen.getByRole("combobox", { name: "Metric" }));
        await user.click(
            await screen.findByText("Mentions only", {
                selector: ".ant-select-item-option-content",
            }),
        );
        await waitFor(() => expect(screen.queryByText("Citation rate")).not.toBeInTheDocument());
        const table = screen.getByRole("table");
        expect(within(table).getByText("Perplexity")).toBeInTheDocument();
        expect(within(table).queryByText("Gemini")).not.toBeInTheDocument();
    });

    it("shows the consolidated series once a consolidation exists", async () => {
        const user = userEvent.setup();
        const { client } = renderApp(<App />, { route: `/trends?project=${PROJECT_ID}` });
        await screen.findByText("Citation rate");
        await user.click(screen.getByText(/^Consolidated/));
        expect(
            await screen.findByText(/No consolidation yet: 1 of 3 crawls done/),
        ).toBeInTheDocument();
        await endpoints.consolidate(PROJECT_ID, { window_runs: 1, note: "" });
        await client.invalidateQueries({ queryKey: ["projects", PROJECT_ID, "positions"] });
        await user.click(screen.getByText("Per run"));
        await user.click(screen.getByText(/^Consolidated/));
        expect(await screen.findByText(/1 consolidation;/)).toBeInTheDocument();
        expect(screen.getAllByRole("table").length).toBe(2);
    });
});
