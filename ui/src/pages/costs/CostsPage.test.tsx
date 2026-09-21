import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/render";
import { App } from "@/App";
import { server } from "@/mocks/server";
import costs from "@/mocks/fixtures/costs.json";

describe("costs page", () => {
    it("shows totals, the vendor table and the proposed .env block", async () => {
        renderApp(<App />, { route: "/costs" });
        expect(await screen.findByText("Vendor calls")).toBeInTheDocument();
        const firstVendor = (costs as { vendors: { vendor: string }[] }).vendors[0]!.vendor;
        expect(screen.getAllByText(firstVendor).length).toBeGreaterThan(0);
        const rec = (
            costs as { recommendations: { setting: string; suggested: number | string }[] }
        ).recommendations[0];
        if (rec) {
            const block = screen.getByLabelText("env block");
            expect(block).toHaveTextContent(`${rec.setting.toUpperCase()}=${rec.suggested}`);
        }
    });

    it("hides demo rows by default and asks the server for them only when toggled", async () => {
        const seen: string[] = [];
        server.events.on("request:start", ({ request }) => {
            if (request.url.includes("/api/costs")) seen.push(new URL(request.url).search);
        });
        const user = userEvent.setup();
        renderApp(<App />, { route: "/costs" });
        await screen.findByText("Vendor calls");
        expect(seen[0]).toContain("exclude_source=demo");
        await user.click(screen.getByLabelText("Hide demo data"));
        await waitFor(() => expect(seen.some((s) => !s.includes("exclude_source"))).toBe(true));
        expect(await screen.findByText(/includes seeded demonstration rows/)).toBeInTheDocument();
    });
});
