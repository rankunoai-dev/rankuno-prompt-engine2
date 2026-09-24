import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/render";
import { App } from "@/App";
import { PROJECT_ID, PROJECT_2_ID, mockState } from "@/mocks/handlers";

/** A log whose lines name crawlers, so the pre-filter keeps them. */
const LOG_LINES = [
    '1.2.3.4 - - [01/Sep/2026:10:00:00 +0000] "GET /a HTTP/1.1" 200 12 "-" "GPTBot/1.2"',
    '1.2.3.5 - - [01/Sep/2026:10:00:01 +0000] "GET /b HTTP/1.1" 200 12 "-" "OAI-SearchBot/1.0"',
    '1.2.3.6 - - [01/Sep/2026:10:00:02 +0000] "GET /c HTTP/1.1" 200 12 "-" "Chrome/120"',
].join("\n");

function logFile(body: string, name = "access.log"): File {
    return new File([body], name, { type: "text/plain" });
}

/** The dragger's input only exists once the card has mounted. */
async function uploadLog(file: File) {
    await screen.findByText("Import an access log");
    const input = await waitFor(() => {
        const found = document.querySelector('input[type="file"]');
        if (!found) throw new Error("no file input yet");
        return found as HTMLInputElement;
    });
    fireEvent.change(input, { target: { files: [file] } });
}

describe("crawler logs tab", () => {
    it("shows coverage, crawlers, the funnel and the imports from a stored view", async () => {
        renderApp(<App />, { route: `/projects/${PROJECT_ID}/crawlers` });

        expect(await screen.findByText(/Logs cover/)).toBeInTheDocument();
        // 28 of 30: two days in the fixture are covered by no log at all.
        expect(screen.getByText("28")).toBeInTheDocument();
        expect(screen.getByText("Which crawlers came")).toBeInTheDocument();
        expect(screen.getByText("Fetch → consulted → cited")).toBeInTheDocument();
        expect(screen.getByText("Imports")).toBeInTheDocument();
        // A vendor with no published address list reads as unverifiable, not zero.
        expect(screen.getAllByText("unverifiable").length).toBeGreaterThan(0);
    });

    it("draws days no log covers as gaps rather than as zero", async () => {
        renderApp(<App />, { route: `/projects/${PROJECT_ID}/crawlers` });
        const strip = await screen.findByTestId("daily-strip");
        const covered = strip.querySelectorAll('[data-covered="yes"]');
        const gaps = strip.querySelectorAll('[data-covered="no"]');
        expect(covered.length).toBe(28);
        expect(gaps.length).toBe(2);
        expect(gaps[0]).toHaveAttribute("aria-label", expect.stringContaining("no log covers"));
    });

    it("hides assets by default and puts never-cited pages first", async () => {
        renderApp(<App />, { route: `/projects/${PROJECT_ID}/crawlers` });
        await screen.findByText("Fetch → consulted → cited");
        const rows = document.querySelectorAll(".ant-table-tbody tr.ant-table-row");
        expect(rows.length).toBeGreaterThan(0);
        expect(screen.queryByText("asset")).not.toBeInTheDocument();
        expect(screen.getByLabelText("Hide assets")).toBeChecked();
    });

    it("imports a small log as JSON and shows the stored sentence verbatim", async () => {
        renderApp(<App />, { route: `/projects/${PROJECT_ID}/crawlers` });
        const before = mockState.crawlerLogs[PROJECT_ID]?.imports.length ?? 0;

        await uploadLog(logFile(LOG_LINES));

        const panel = await screen.findByTestId("import-result", undefined, { timeout: 15000 });
        // Two of the three lines name a crawler; the Chrome line is dropped here.
        expect(screen.getByText(/Kept 2 of 3 lines that name a known crawler/)).toBeInTheDocument();
        expect(within(panel).getByText(/crawler fetches from/)).toBeInTheDocument();
        expect(within(panel).getByText("nginx / Apache")).toBeInTheDocument();
        expect(within(panel).getByText("verified on remote address")).toBeInTheDocument();
        expect(
            within(panel).getByText(
                "Stored as per-day counts per crawler and page. No address and no log line is kept.",
            ),
        ).toBeInTheDocument();
        await waitFor(() =>
            expect(mockState.crawlerLogs[PROJECT_ID]?.imports.length).toBe(before + 1),
        );
    });

    it("never renders a line of the uploaded file", async () => {
        renderApp(<App />, { route: `/projects/${PROJECT_ID}/crawlers` });
        await uploadLog(logFile(LOG_LINES));
        await screen.findByTestId("import-result", undefined, { timeout: 15000 });
        // The addresses in the file must not reach the screen.
        expect(document.body.textContent).not.toContain("1.2.3.4");
        expect(document.body.textContent).not.toContain("GET /a HTTP/1.1");
    });

    it("says so when the file names no crawler at all", async () => {
        renderApp(<App />, { route: `/projects/${PROJECT_ID}/crawlers` });
        await uploadLog(logFile('8.8.8.8 - - "GET / HTTP/1.1" 200 1 "-" "Chrome/120"'));
        expect(
            await screen.findByText(/No line in that file names a crawler this tracker knows/),
        ).toBeInTheDocument();
    });

    it("offers the import card alone when nothing has been uploaded", async () => {
        renderApp(<App />, { route: `/projects/${PROJECT_2_ID}/crawlers` });
        expect(await screen.findByText("Import an access log")).toBeInTheDocument();
        expect(
            await screen.findByText(/Upload an access log to see which AI crawlers/),
        ).toBeInTheDocument();
        expect(screen.getByText(/No address is stored/)).toBeInTheDocument();
        expect(screen.queryByText("Which crawlers came")).not.toBeInTheDocument();
    });

    it("deletes an import after confirming", async () => {
        const user = userEvent.setup();
        renderApp(<App />, { route: `/projects/${PROJECT_ID}/crawlers` });
        await screen.findByText("Imports");
        const before = mockState.crawlerLogs[PROJECT_ID]?.imports.length ?? 0;

        await user.click(screen.getAllByText("Delete")[0]!);
        await user.click(await screen.findByRole("button", { name: "Delete" }));

        await waitFor(() =>
            expect(mockState.crawlerLogs[PROJECT_ID]?.imports.length).toBe(before - 1),
        );
    });
});
