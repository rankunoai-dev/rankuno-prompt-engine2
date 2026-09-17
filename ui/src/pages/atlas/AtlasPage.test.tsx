import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/render";
import { App } from "@/App";

describe("atlas", () => {
    it("loads the live dataset and shows the overview tiles", async () => {
        renderApp(<App />, { route: "/atlas" });
        expect(await screen.findByText("Prompts cited on at least one engine")).toBeInTheDocument();
        expect(screen.getByText("Citation rate by run")).toBeInTheDocument();
        expect(screen.getByText("Who gets cited instead")).toBeInTheDocument();
        expect(screen.getByRole("table", { name: "Stage heatmap" })).toBeInTheDocument();
    });

    it("switches to the master sheet and opens the drawer from a row", async () => {
        const user = userEvent.setup();
        renderApp(<App />, { route: "/atlas" });
        await screen.findByText("Prompts cited on at least one engine");
        await user.click(screen.getByText("Master prompt sheet"));
        const rows = await screen.findAllByText(/cited$/);
        expect(rows.length).toBeGreaterThan(0);
        const firstLink = document.querySelector(".ant-table-tbody a") as HTMLElement;
        await user.click(firstLink);
        const drawer = (await screen.findByText("Crawls, each on its own date")).closest(
            ".ant-drawer-content",
        ) as HTMLElement;
        expect(within(drawer).getByText("Citation links")).toBeInTheDocument();
    });

    it("share of voice lists the client domain first and filters by domain", async () => {
        const user = userEvent.setup();
        renderApp(<App />, { route: "/atlas" });
        await screen.findByText("Prompts cited on at least one engine");
        await user.click(screen.getByText("Share of voice"));
        const table = await screen.findByRole("table");
        const firstRow = within(table).getAllByRole("row")[1]!;
        expect(within(firstRow).getByText("gep.com")).toBeInTheDocument();
        expect(within(firstRow).getByText("Client")).toBeInTheDocument();
    });
});
