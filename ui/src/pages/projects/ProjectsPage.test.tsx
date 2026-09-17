import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/render";
import { App } from "@/App";
import { http, HttpResponse } from "msw";
import { mockState } from "@/mocks/handlers";
import { server } from "@/mocks/server";

/**
 * Role-with-name queries walk the whole AntD DOM and block the event loop for
 * seconds in jsdom, so these tests find cards by text and scope role queries
 * to the card or drawer.
 */
const card = (name: string) => screen.getByText(name).closest(".ant-card") as HTMLElement;
const findCard = async (name: string) =>
    (await screen.findByText(name)).closest(".ant-card") as HTMLElement;

async function openProjects() {
    const utils = renderApp(<App />, { route: "/projects" });
    await findCard("Test run");
    return utils;
}

/** One change event per field: typing key by key re-renders the 20-field form each time. */
function fill(container: HTMLElement, label: string, value: string) {
    fireEvent.change(within(container).getByLabelText(label), { target: { value } });
}

async function fillRequired(
    user: ReturnType<typeof userEvent.setup>,
    drawer: HTMLElement,
    name: string,
) {
    fill(drawer, "Name", name);
    fill(drawer, "Brand name", "Acme");
    fill(drawer, "Line of business", "Widgets");
    await user.type(within(drawer).getByLabelText("Client domains"), "acme.com{enter}");
    await user.type(within(drawer).getByLabelText("Seed keywords"), "widgets{enter}");
}

describe("projects page", () => {
    it("lists every project with brand, LOB, platforms and consolidation progress", async () => {
        await openProjects();
        const c = card("Test run");
        expect(
            within(c).getByText(/GEP · supply chain and procurement software/),
        ).toBeInTheDocument();
        expect(within(c).getAllByText(/ChatGPT|Perplexity|Gemini|Google AIO/).length).toBe(4);
        expect(await within(c).findByText("1 of 3 crawls")).toBeInTheDocument();
        expect(card("GEP procurement (demo)")).toBeInTheDocument();
    });

    it("filters by the search box", async () => {
        await openProjects();
        fireEvent.change(screen.getByLabelText("Search projects"), { target: { value: "demo" } });
        await waitFor(() => expect(screen.queryByText("Test run")).not.toBeInTheDocument());
        expect(card("GEP procurement (demo)")).toBeInTheDocument();
    });

    it("maps a 422 from the API onto the form field", async () => {
        server.use(
            http.post("/api/projects", () =>
                HttpResponse.json(
                    {
                        detail: [
                            {
                                loc: ["body", "client", "domains"],
                                msg: "Value error, acme.com is not a registrable domain",
                                type: "value_error",
                            },
                        ],
                    },
                    { status: 422 },
                ),
            ),
        );
        const user = userEvent.setup();
        await openProjects();
        await user.click(screen.getByText("New project"));
        const drawer = (await screen.findByText("Create project")).closest(
            ".ant-drawer-content",
        ) as HTMLElement;
        await fillRequired(user, drawer, "Acme");
        await user.click(within(drawer).getByText("Create project"));
        const error = await within(drawer).findByText(/is not a registrable domain/);
        // The message sits inside the "Client domains" form item, not in a toast only.
        expect(error.closest(".ant-form-item")).toHaveTextContent("Client domains");
    });

    it("creates a project and shows its card", async () => {
        const user = userEvent.setup();
        await openProjects();
        await user.click(screen.getByText("New project"));
        const drawer = (await screen.findByText("Create project")).closest(
            ".ant-drawer-content",
        ) as HTMLElement;
        await fillRequired(user, drawer, "Acme widgets");
        await user.click(within(drawer).getByText("Create project"));
        expect(await findCard("Acme widgets")).toBeInTheDocument();
        expect(mockState.projects.some((p) => p.name === "Acme widgets")).toBe(true);
    });

    it("deletes a project after confirmation and says history is kept", async () => {
        const user = userEvent.setup();
        await openProjects();
        const c = card("GEP procurement (demo)");
        await user.click(within(c).getByLabelText("Actions for GEP procurement (demo)"));
        await user.click(await screen.findByText("Delete"));
        const dialog = (await screen.findByText(/history is kept/i)).closest(
            ".ant-modal-content",
        ) as HTMLElement;
        await user.click(within(dialog).getByText("Delete", { selector: "span" }));
        await waitFor(() =>
            expect(screen.queryByText("GEP procurement (demo)")).not.toBeInTheDocument(),
        );
    });
});
