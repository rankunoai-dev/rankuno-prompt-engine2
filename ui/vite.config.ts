/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// `/api` and `/reports` are proxied to the control plane during development so
// the browser never needs CORS and never talks to a vendor directly.
const CONTROL_PLANE = process.env.VITE_CONTROL_PLANE ?? "http://127.0.0.1:8787";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": { target: CONTROL_PLANE, changeOrigin: true },
      "/reports": { target: CONTROL_PLANE, changeOrigin: true },
      "/openapi.json": { target: CONTROL_PLANE, changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1500 },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
    restoreMocks: true,
    testTimeout: 20_000,
    hookTimeout: 20_000,
  },
});
