import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/render";
import { App } from "@/App";
import { PROJECT_ID, mockState } from "@/mocks/handlers";

const FIRST = "What are the main capabilities of enterprise spend analysis tools?";

async function openPrompts() {
    const utils = renderApp(<App />, { route: `/projects/${PROJECT_ID}/prompts` });
    await screen.findByText(FIRST);
    return utils;
}
const rowOf = (text: string) => screen.getByText(text).closest("tr") as HTMLElement;

describe("prompts page", () => {
    it("lists prompts with verdict, volume and due state", async () => {
        await openPrompts();
        expect(screen.getByText(/Tracked prompts \(15\)/)).toBeInTheDocument();
        expect(screen.getAllByText("Linked").length).toBeGreaterThan(0);
        expect(screen.getAllByText("Absent").length).toBeGreaterThan(0);
    });

    it("stars a prompt optimistically and persists it", async () => {
        const user = userEvent.setup();
        await openPrompts();
        const row = rowOf(FIRST);
        await user.click(within(row).getByLabelText("Star"));
        expect(await within(row).findByLabelText("Unstar")).toBeInTheDocument();
        await waitFor(() =>
            expect(
                mockState.prompts[PROJECT_ID]?.find((p) => p.prompt_text === FIRST)?.important,
            ).toBe(true),
        );
    });

    it("imports pasted prompts and shows them", async () => {
        const user = userEvent.setup();
        await openPrompts();
        fireEvent.change(screen.getByLabelText("Prompts to import"), {
            target: {
                value: "Which S2P suite fits a mid-market manufacturer? | s2p suite | Selection\nno",
            },
        });
        await user.click(screen.getByText(/Import 1 prompt/));
        expect(
            await screen.findByText("Which S2P suite fits a mid-market manufacturer?"),
        ).toBeInTheDocument();
        expect(screen.getByText(/Tracked prompts \(16\)/)).toBeInTheDocument();
    });

    it("filters to starred prompts only", async () => {
        const user = userEvent.setup();
        const target = mockState.prompts[PROJECT_ID]?.[1];
        target!.important = true;
        await openPrompts();
        await user.click(screen.getByText("Starred"));
        await waitFor(() => expect(screen.queryByText(FIRST)).not.toBeInTheDocument());
        expect(screen.getByText(target!.prompt_text)).toBeInTheDocument();
    });

    it("deletes a prompt after confirmation", async () => {
        const user = userEvent.setup();
        await openPrompts();
        await user.click(within(rowOf(FIRST)).getByLabelText(`Delete ${FIRST}`));
        const pop = (await screen.findByText("Remove this prompt from the project?")).closest(
            ".ant-popconfirm",
        ) as HTMLElement;
        await user.click(within(pop).getByText("Delete"));
        await waitFor(() => expect(screen.queryByText(FIRST)).not.toBeInTheDocument());
        expect(mockState.prompts[PROJECT_ID]?.some((p) => p.prompt_text === FIRST)).toBe(false);
    });
});
