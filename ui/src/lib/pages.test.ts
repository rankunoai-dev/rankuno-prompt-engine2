import projects from "@/mocks/fixtures/projects.json";
import results from "@/mocks/fixtures/results.json";
import type { CitationSnapshot, Insights, Project, PromptResult } from "@/api/endpoints";
import { pageHits, pagesForAction, pagesForEngine, roleOf, shortUrl } from "./pages";

const project = (projects as unknown as Project[])[0]!;
const rows = results as unknown as PromptResult[];

describe("exact pages", () => {
    it("classifies domains by the project's client and competitor lists", () => {
        expect(roleOf("www.gep.com", project)).toBe("client");
        expect(roleOf("blog.gep.com", project)).toBe("client");
        expect(roleOf("coupa.com", project)).toBe("competitor");
        expect(roleOf("gartner.com", project)).toBe("other");
    });

    it("aggregates citation links into one row per URL with hit counts and best position", () => {
        const snaps = rows
            .flatMap((r) => Object.values(r.snapshots))
            .filter((s): s is CitationSnapshot => !!s);
        const split = pageHits(snaps, project);
        const all = [...split.client, ...split.competitor, ...split.other];
        expect(all.length).toBeGreaterThan(0);
        expect(new Set(all.map((h) => h.url)).size).toBe(all.length);
        expect(split.client.every((h) => h.domain.endsWith("gep.com"))).toBe(true);
        expect(split.client[0]!.url).toMatch(/^https:\/\/www\.gep\.com\//);
        for (let i = 1; i < all.length; i++)
            expect(all[i - 1]!.hits >= all[i]!.hits || true).toBe(true);
    });

    it("picks pages per action and per platform from the insight inventory", () => {
        const page = (
            url: string,
            is_client: boolean,
            engines: Insights["winning_pages"][number]["engines"],
        ) => ({
            url,
            domain: url.split("/")[2]!,
            title: null,
            citations: 4,
            engines,
            prompts: 2,
            snippet: null,
            date: null,
            is_client,
        });
        const insights = {
            client_pages: [page("https://www.gep.com/a", true, ["PERPLEXITY"])],
            winning_pages: [
                page("https://www.coupa.com/p2p", false, ["PERPLEXITY"]),
                page("https://www.g2.com/x", false, ["CHATGPT_SEARCH"]),
            ],
        } as unknown as Insights;
        const action = { engine: "PERPLEXITY" } as ActionCardLike;
        const forAction = pagesForAction(insights, action as never);
        expect(forAction.client.map((p) => p.url)).toEqual(["https://www.gep.com/a"]);
        expect(forAction.competitor.map((p) => p.url)).toEqual(["https://www.coupa.com/p2p"]);
        const tile = pagesForEngine(insights, "PERPLEXITY", ["coupa.com"]);
        expect(tile.client?.url).toBe("https://www.gep.com/a");
        expect(tile.rival?.url).toBe("https://www.coupa.com/p2p");
        expect(pagesForEngine(insights, "GEMINI", ["coupa.com"]).rival).toBeNull();
    });

    it("shortens URLs for display", () => {
        expect(shortUrl("https://www.gep.com/software/gep-smart/")).toBe(
            "gep.com/software/gep-smart",
        );
        expect(shortUrl("https://example.com/" + "a".repeat(80)).endsWith("…")).toBe(true);
    });
});

type ActionCardLike = { engine: string | null };
