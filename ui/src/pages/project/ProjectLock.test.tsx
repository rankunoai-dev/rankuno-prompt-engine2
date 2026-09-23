import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/render";
import { App } from "@/App";
import { PROJECT_ID, mockState, protectMockProject } from "@/mocks/handlers";
import { tokenFor } from "@/lib/projectAuth";

const dialogOf = (el: HTMLElement) => el.closest(".ant-modal-content") as HTMLElement;

describe("owner credential (ADR 0019)", () => {
    it("reads freely, asks on the first write, rejects a wrong password, then saves", async () => {
        protectMockProject(PROJECT_ID, "gaurav", "open sesame");
        const user = userEvent.setup();
        renderApp(<App />, { route: `/projects/${PROJECT_ID}/actions` });

        // Reading needs nothing: the badge says who holds the key and the cards load.
        const badge = await screen.findByTestId("project-lock");
        expect(within(badge).getByText(/Read-only · owner gaurav/)).toBeInTheDocument();
        // Action groups start closed — the headers are the summary — so open the
        // first one to reach a card. This test is about the credential, not the layout.
        await user.click((await screen.findAllByRole("button", { name: /\(\d+\)/ }))[0]!);
        const boxes = await screen.findAllByLabelText(/Mark done:/);

        // The first write is refused by the API and opens the unlock dialog instead of failing.
        await user.click(boxes[0]!);
        const dialog = dialogOf(await screen.findByText("Unlock this project to make changes"));
        expect(mockState.actions[PROJECT_ID]).toBeUndefined();
        expect(within(dialog).getByLabelText("Owner name")).toHaveValue("gaurav");

        const password = within(dialog).getByLabelText("Password");
        fireEvent.change(password, { target: { value: "a wrong guess" } });
        await user.click(within(dialog).getByText("Unlock", { selector: "span" }));
        expect(await within(dialog).findByText("Wrong owner name or password.")).toBeVisible();
        expect(tokenFor(PROJECT_ID)).toBeNull();

        // The right one is stored for the tab and the original write is retried.
        fireEvent.change(password, { target: { value: "open sesame" } });
        await user.click(within(dialog).getByText("Unlock", { selector: "span" }));
        await waitFor(() => expect(screen.getByText(/Done \(1\)/)).toBeInTheDocument());
        expect(Object.values(mockState.actions[PROJECT_ID] ?? {})[0]?.status).toBe("done");
        expect(within(screen.getByTestId("project-lock")).getByText(/Unlocked/)).toBeVisible();

        // Locking again forgets the credential.
        await user.click(within(screen.getByTestId("project-lock")).getByText("Lock"));
        expect(tokenFor(PROJECT_ID)).toBeNull();
        expect(within(screen.getByTestId("project-lock")).getByText(/Read-only/)).toBeVisible();
    }, 120_000);

    it("lets anyone claim an open project from its header", async () => {
        const user = userEvent.setup();
        renderApp(<App />, { route: `/projects/${PROJECT_ID}/actions` });
        const badge = await screen.findByTestId("project-lock");
        expect(within(badge).getByText("Open to everyone")).toBeInTheDocument();

        await user.click(within(badge).getByText("Protect"));
        const dialog = dialogOf(await screen.findByText("Protect this project"));
        fireEvent.change(within(dialog).getByLabelText("Owner name"), {
            target: { value: "priya" },
        });
        fireEvent.change(within(dialog).getByLabelText("New password"), {
            target: { value: "a long passphrase" },
        });
        fireEvent.change(within(dialog).getByLabelText("Repeat the password"), {
            target: { value: "a long passphrase" },
        });
        await user.click(within(dialog).getByText("Protect project", { selector: "span" }));

        await waitFor(() =>
            expect(within(screen.getByTestId("project-lock")).getByText(/Unlocked/)).toBeVisible(),
        );
        expect(mockState.projects.find((p) => p.id === PROJECT_ID)).toMatchObject({
            protected: true,
            owner: "priya",
        });
        expect(tokenFor(PROJECT_ID)).not.toBeNull();
    }, 120_000);
});
