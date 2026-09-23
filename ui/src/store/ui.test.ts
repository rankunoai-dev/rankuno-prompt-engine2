import { useUiStore } from "@/store/ui";

describe("ui store theme default", () => {
    it("defaults to dark", () => {
        expect(useUiStore.getState().theme).toBe("dark");
    });

    it("migrates the old 'system' default to dark but keeps an explicit light", () => {
        const migrate = useUiStore.persist.getOptions().migrate!;
        expect(migrate({ theme: "system" }, 0)).toMatchObject({ theme: "dark" });
        expect(migrate({ theme: "light" }, 0)).toMatchObject({ theme: "light" });
        expect(migrate({ theme: "system" }, 1)).toMatchObject({ theme: "system" });
    });
});
