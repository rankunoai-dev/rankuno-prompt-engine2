import type { Engine, Project } from "@/api/endpoints";
import projects from "@/mocks/fixtures/projects.json";
import { schema, toBody, toForm } from "./ProjectForm";

const ENGINES: Engine[] = ["GOOGLE_AI_OVERVIEW", "CHATGPT_SEARCH", "PERPLEXITY", "GEMINI"];

describe("project form body", () => {
    it("keeps branding and the sampling policy when an existing project is edited", () => {
        const stored = {
            ...(projects as unknown as Project[])[0]!,
            sampling_policy: "save",
            brand: {
                client_name: "GEP",
                agency_name: "RankUno",
                primary_colour: "#123456",
                logo_id: "abc123",
                footer_note: "Confidential",
                show_spend: true,
            },
        } as Project;
        // The analyst renames the project; the form has no branding or policy inputs.
        const values = schema.parse({ ...toForm(stored, ENGINES), name: "Renamed" });
        const body = toBody(values, stored);
        expect(body.name).toBe("Renamed");
        expect(body.sampling_policy).toBe("save");
        expect(body.brand).toEqual(stored.brand);
    });

    it("uses the defaults for a new project", () => {
        const values = schema.parse({
            ...toForm(null, ENGINES),
            name: "New",
            brand_name: "Acme",
            lob: "Widgets",
            domains: ["acme.com"],
            seed_keywords: ["widgets"],
        });
        const body = toBody(values);
        expect(body.sampling_policy).toBe("fixed");
        expect(body.brand?.primary_colour).toBe("#1f3a5f");
    });
});
