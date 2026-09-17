/**
 * Browser worker used only when `VITE_MOCK=1`: lets the app run with no
 * control plane. `VITE_MOCK=planned` mocks only the insight routes in front
 * of a real server that predates cycle 0011.
 */
import { setupWorker } from "msw/browser";
import { handlers, plannedHandlers } from "./handlers";

export function startMockWorker(mode: string): Promise<unknown> {
    const worker = setupWorker(...(mode === "planned" ? plannedHandlers : handlers));
    return worker.start({ onUnhandledRequest: "bypass", quiet: true });
}
