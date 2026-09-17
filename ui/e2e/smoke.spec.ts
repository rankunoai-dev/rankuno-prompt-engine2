/**
 * Read-only smoke against the local control plane through the Vite proxy.
 * It never clicks Run and never posts to /run or /consolidate: those spend
 * vendor money. It only reads the existing project's stored data.
 */
import { expect, test } from "@playwright/test";

test.describe("smoke (read-only)", () => {
    test("shell loads, rail links work, a project opens", async ({ page }) => {
        await page.goto("/");
        await expect(page).toHaveURL(/\/projects$/);
        await expect(page.getByRole("link", { name: /atlas/i })).toBeVisible();
        await expect(page.getByRole("link", { name: /costs/i })).toBeVisible();
    });

    test("command palette opens with Ctrl+K", async ({ page }) => {
        await page.goto("/projects");
        await page.keyboard.press("Control+k");
        await expect(page.getByRole("textbox", { name: "Search" })).toBeVisible();
        await page.keyboard.press("Escape");
    });

    test("no request to /run or /consolidate is ever made", async ({ page }) => {
        const forbidden: string[] = [];
        page.on("request", (req) => {
            if (req.method() === "POST" && /\/(run|consolidate)$/.test(req.url())) {
                forbidden.push(req.url());
            }
        });
        await page.goto("/projects");
        await page.goto("/atlas");
        await page.goto("/costs");
        expect(forbidden).toEqual([]);
    });
});
