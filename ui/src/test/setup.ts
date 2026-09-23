import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";
import { cleanup, configure } from "@testing-library/react";
import { server } from "@/mocks/server";
import { resetProjectAuth } from "@/lib/projectAuth";
import { resetMockState } from "@/mocks/handlers";

// AntD needs matchMedia and ResizeObserver; jsdom has neither.
if (!window.matchMedia) {
    window.matchMedia = (query: string) =>
        ({
            matches: query.includes("prefers-reduced-motion"),
            media: query,
            onchange: null,
            addListener: () => undefined,
            removeListener: () => undefined,
            addEventListener: () => undefined,
            removeEventListener: () => undefined,
            dispatchEvent: () => false,
        }) as MediaQueryList;
}
if (!window.ResizeObserver) {
    window.ResizeObserver = class {
        observe() {}
        unobserve() {}
        disconnect() {}
    } as unknown as typeof ResizeObserver;
}
// jsdom throws "not implemented" (with a stack trace) for the pseudo-element
// form; AntD calls it constantly, which made every commit take ~800 ms.
const nativeGetComputedStyle = window.getComputedStyle.bind(window);
window.getComputedStyle = ((el: Element) =>
    nativeGetComputedStyle(el)) as typeof window.getComputedStyle;
if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => undefined;
}

// Synchronous AntD renders of a full table exceed the 1 s default, and the
// Actions and Projects pages render every card at once under parallel workers.
configure({ asyncUtilTimeout: 20000 });

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
    cleanup();
    server.resetHandlers();
    resetMockState();
    window.localStorage.clear();
    window.sessionStorage.clear();
    resetProjectAuth();
});
afterAll(() => server.close());
