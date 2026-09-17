import { screen } from "@testing-library/react";
import { renderApp } from "@/test/render";
import { App } from "@/App";
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
});
