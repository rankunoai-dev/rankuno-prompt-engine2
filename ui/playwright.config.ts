import { defineConfig } from "@playwright/test";

// Read-only smoke against the local control plane. It never posts a run.
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 0,
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173", headless: true },
  webServer: {
    command: "npm run dev -- --port 5173",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: true,
    timeout: 60_000,
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
