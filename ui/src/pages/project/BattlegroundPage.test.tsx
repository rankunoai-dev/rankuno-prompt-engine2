import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/render";
import { App } from "@/App";
import { PROJECT_ID } from "@/mocks/handlers";

const LINKED = "What are leading approaches to digital supplier risk management?";

async function openMatrix(route = `/projects/${PROJECT_ID}/battleground`) {
    const utils = renderApp(<App />, { route });
    await screen.findByText(LINKED);
    return utils;
}

describe("battleground", () => {
    it("renders one badge per prompt × platform with the low-confidence banner", async () => {
        await openMatrix();
        expect(screen.getByText(/Low confidence: 1 of 3 crawls/)).toBeInTheDocument();
        const row = screen.getByText(LINKED).closest("tr") as HTMLElement;
        const badges = within(row).getAllByRole("button");
        expect(badges.length).toBe(4);
        expect(badges[0]).toHaveAttribute("data-kind", "linked");
        expect(badges[0]).toHaveTextContent("Linked #3");
    });

    it("opens the inspection drawer from a cell and deep-links it", async () => {
        const user = userEvent.setup();
        await openMatrix();
        const row = screen.getByText(LINKED).closest("tr") as HTMLElement;
        await user.click(within(row).getAllByRole("button")[0]!);
        const drawer = (await screen.findByText("Consolidated position")).closest(
            ".ant-drawer-content",
        ) as HTMLElement;
        expect(within(drawer).getByText("Citation links")).toBeInTheDocument();
        expect(
            await within(drawer).findByText(/AI-Powered Supplier Risk Management/),
        ).toBeInTheDocument();
        expect(within(drawer).getByText("gep.com #3")).toBeInTheDocument();
        expect(within(drawer).getByText(/No consolidated position yet/)).toBeInTheDocument();
    });

    it("opens the drawer directly from the query string", async () => {
        await openMatrix(
            `/projects/${PROJECT_ID}/battleground?prompt=e0055540e4b059b8&engine=CHATGPT_SEARCH`,
        );
        const drawer = (await screen.findByText("Consolidated position")).closest(
            ".ant-drawer-content",
        ) as HTMLElement;
        expect(
            within(drawer).getByText(
                "What are the main capabilities of enterprise spend analysis tools?",
            ),
        ).toBeInTheDocument();
        expect(within(drawer).getByText("ChatGPT Search")).toBeInTheDocument();
    });

    it("moves focus between cells with the arrow keys", async () => {
        const user = userEvent.setup();
        await openMatrix();
        const first = screen.getAllByRole("button", { name: /on Google AIO/ })[0]!;
        first.focus();
        await user.keyboard("{ArrowRight}");
        await waitFor(() =>
            expect(document.activeElement?.getAttribute("aria-label")).toMatch(/on ChatGPT/),
        );
    });
});
