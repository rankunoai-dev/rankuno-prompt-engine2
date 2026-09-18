import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/render";
import { App } from "@/App";
import { PROJECT_ID, mockState } from "@/mocks/handlers";

describe("overview and actions", () => {
    it("shows the confidence banner, one health tile per platform and the top actions", async () => {
        renderApp(<App />, { route: `/projects/${PROJECT_ID}/overview` });
        expect(await screen.findByText(/Low confidence: 1 of 3 crawls/)).toBeInTheDocument();
        for (const e of ["GOOGLE_AI_OVERVIEW", "CHATGPT_SEARCH", "PERPLEXITY", "GEMINI"]) {
            expect(screen.getByTestId(`health-${e}`)).toBeInTheDocument();
        }
        expect(screen.getByText(/Top actions/)).toBeInTheDocument();
        expect(screen.getAllByLabelText(/Mark done:/).length).toBeGreaterThan(0);
        expect(screen.getAllByLabelText(/Mark done:/).length).toBeLessThanOrEqual(3);
        expect(screen.getByText(/No previous consolidation to compare/)).toBeInTheDocument();
    });

    it("checks an action off and it moves to the Done group on the actions page", async () => {
        const user = userEvent.setup();
        renderApp(<App />, { route: `/projects/${PROJECT_ID}/actions` });
        const boxes = await screen.findAllByLabelText(/Mark done:/);
        const first = boxes[0]!;
        const title = first.getAttribute("aria-label")!.replace("Mark done: ", "");
        await user.click(first);
        await waitFor(() => expect(screen.getByText(/Done \(1\)/)).toBeInTheDocument());
        const actionId = Object.keys(mockState.actions[PROJECT_ID] ?? {})[0];
        expect(actionId).toBeTruthy();
        expect(mockState.actions[PROJECT_ID]![actionId!]!.status).toBe("done");
        await user.click(screen.getByText(/Done \(1\)/));
        const done = screen.getByText(/Done \(1\)/).closest(".ant-collapse-item") as HTMLElement;
        expect(within(done).getByText(title)).toBeInTheDocument();
        expect(within(done).getByText("Pending next consolidation")).toBeInTheDocument();
    });

    it("filters actions by platform", async () => {
        const user = userEvent.setup();
        renderApp(<App />, { route: `/projects/${PROJECT_ID}/actions` });
        const total = (await screen.findAllByLabelText(/Mark done:/)).length;
        await user.click(screen.getByRole("combobox", { name: "Platform" }));
        await user.click(
            await screen.findByText("Gemini", { selector: ".ant-select-item-option-content" }),
        );
        await waitFor(() => {
            const shown = screen.queryAllByLabelText(/Mark done:/).length;
            expect(shown).toBeLessThan(total);
        });
    });

    it("names the tracked prompts each action is for, in words rather than ids", async () => {
        renderApp(<App />, { route: `/projects/${PROJECT_ID}/actions` });
        const blocks = await screen.findAllByTestId("action-prompts");
        const block = blocks[0]!;
        expect(within(block).getByText(/Tracked prompts? this is for/)).toBeInTheDocument();
        await waitFor(() => {
            const text = within(block).getAllByRole("link")[0]!.textContent ?? "";
            expect(text).toMatch(/\s/); // a sentence, not an 8-character hash
        });
        expect(within(block).getAllByRole("link")[0]).toHaveAttribute(
            "href",
            expect.stringContaining("/battleground?prompt="),
        );
    });
});
