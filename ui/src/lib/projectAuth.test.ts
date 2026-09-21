import {
    clearToken,
    encodeBasic,
    needsProjectAuth,
    projectIdFromPath,
    registerUnlockHandler,
    requestUnlock,
    setToken,
    tokenFor,
} from "./projectAuth";

describe("project credentials on the client", () => {
    it("encodes owner and password as UTF-8 Basic", () => {
        expect(encodeBasic("gaurav", "open sesame")).toBe(`Basic ${btoa("gaurav:open sesame")}`);
        const token = encodeBasic("zoë", "pässwörd");
        const bytes = Uint8Array.from(atob(token.slice(6)), (c) => c.charCodeAt(0));
        expect(new TextDecoder().decode(bytes)).toBe("zoë:pässwörd");
    });

    it("keeps one credential per project for the tab and forgets it on lock", () => {
        expect(tokenFor("p1")).toBeNull();
        setToken("p1", "gaurav", "open sesame");
        setToken("p2", "priya", "another one");
        expect(tokenFor("p1")).toBe(encodeBasic("gaurav", "open sesame"));
        expect(JSON.parse(sessionStorage.getItem("pe.projectAuth.v1")!)).toHaveProperty("p2");
        expect(localStorage.length).toBe(0); // never on disk-backed storage
        clearToken("p1");
        expect(tokenFor("p1")).toBeNull();
        expect(tokenFor("p2")).not.toBeNull();
    });

    it("attaches the credential to writes and the access probe only", () => {
        expect(projectIdFromPath("/api/projects/abc123/prompts/x")).toBe("abc123");
        expect(projectIdFromPath("/api/projects")).toBeNull();
        expect(projectIdFromPath("/api/costs")).toBeNull();
        expect(needsProjectAuth("PUT", "/api/projects/abc123")).toBe(true);
        expect(needsProjectAuth("POST", "/api/projects/abc123/run")).toBe(true);
        expect(needsProjectAuth("GET", "/api/projects/abc123/access")).toBe(true);
        expect(needsProjectAuth("GET", "/api/projects/abc123/insights")).toBe(false);
        expect(needsProjectAuth("POST", "/api/projects")).toBe(false);
    });

    it("resolves false at once with no dialog mounted, and shares one dialog per project", async () => {
        const ask = { projectId: "p1", owner: "gaurav", reason: "locked" as const };
        await expect(requestUnlock(ask)).resolves.toBe(false);

        let calls = 0;
        let settle: (ok: boolean) => void = () => undefined;
        const off = registerUnlockHandler(() => {
            calls += 1;
            return new Promise<boolean>((resolve) => (settle = resolve));
        });
        const first = requestUnlock(ask);
        const second = requestUnlock(ask);
        settle(true);
        await expect(Promise.all([first, second])).resolves.toEqual([true, true]);
        expect(calls).toBe(1);
        off();
        await expect(requestUnlock(ask)).resolves.toBe(false);
    });
});
