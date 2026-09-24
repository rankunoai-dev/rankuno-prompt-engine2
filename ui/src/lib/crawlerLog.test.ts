import {
    JSON_LIMIT_BYTES,
    fitsAsJson,
    pagePath,
    prefilter,
    rawContentType,
    sortFunnel,
} from "./crawlerLog";

const TOKENS = ["OAI-SearchBot", "GPTBot", "PerplexityBot", "ClaudeBot"];

const LOG = [
    '1.2.3.4 - - [01/Sep/2026:10:00:00 +0000] "GET /a HTTP/1.1" 200 12 "-" "Mozilla/5.0 (compatible; GPTBot/1.2)"',
    '5.6.7.8 - - [01/Sep/2026:10:00:01 +0000] "GET /b HTTP/1.1" 200 12 "-" "Mozilla/5.0 (Windows NT 10.0) Chrome/120"',
    '9.9.9.9 - - [01/Sep/2026:10:00:02 +0000] "GET /c HTTP/1.1" 200 12 "-" "OAI-SearchBot/1.0"',
    "", // blank lines are not lines
    '2.2.2.2 - - [01/Sep/2026:10:00:03 +0000] "GET /d HTTP/1.1" 200 12 "-" "perplexitybot/1.0"',
].join("\n");

describe("access-log pre-filter", () => {
    it("keeps only the lines that name a crawler and counts what it saw", () => {
        const out = prefilter(LOG, TOKENS);
        expect(out.total).toBe(4); // the blank line is not counted
        expect(out.kept).toBe(3); // the human Chrome request is dropped
        expect(out.text).toContain("GPTBot");
        expect(out.text).toContain("OAI-SearchBot");
        expect(out.text).not.toContain("Chrome/120");
    });

    it("matches a token whatever its case", () => {
        // The last line says "perplexitybot"; the catalogue says "PerplexityBot".
        expect(prefilter(LOG, ["PerplexityBot"]).kept).toBe(1);
        expect(prefilter(LOG, ["PERPLEXITYBOT"]).kept).toBe(1);
    });

    it("keeps a Cloudflare NDJSON record by the agent inside it", () => {
        const ndjson = [
            '{"ClientRequestUserAgent":"Mozilla/5.0 (compatible; GPTBot/1.2)","EdgeResponseStatus":200}',
            '{"ClientRequestUserAgent":"Mozilla/5.0 Safari/605","EdgeResponseStatus":200}',
        ].join("\n");
        const out = prefilter(ndjson, TOKENS);
        expect(out.kept).toBe(1);
        expect(out.total).toBe(2);
    });

    it("returns empty text when nothing matches, so the caller can say so", () => {
        const out = prefilter('1.1.1.1 - - "GET / HTTP/1.1" 200 1 "-" "Chrome"', TOKENS);
        expect(out.kept).toBe(0);
        expect(out.text).toBe("");
    });
});

describe("upload routing", () => {
    it("posts JSON up to the limit and raw beyond it", () => {
        expect(fitsAsJson(1024)).toBe(true);
        expect(fitsAsJson(JSON_LIMIT_BYTES)).toBe(true);
        expect(fitsAsJson(JSON_LIMIT_BYTES + 1)).toBe(false);
    });

    it("sends a gzipped file under its own content type", () => {
        expect(rawContentType(new File([""], "access.log"))).toBe("text/plain");
        expect(rawContentType(new File([""], "access.log.gz"))).toBe("application/gzip");
        expect(rawContentType(new File([""], "ACCESS.LOG.GZ"))).toBe("application/gzip");
    });
});

describe("funnel presentation", () => {
    it("shows the project's own pages as paths and other hosts in full", () => {
        expect(pagePath("gep.com/software/x", ["gep.com"])).toBe("/software/x");
        expect(pagePath("www.gep.com/a", ["https://www.gep.com/"])).toBe("/a");
        expect(pagePath("g2.com/categories/x", ["gep.com"])).toBe("g2.com/categories/x");
        expect(pagePath("gep.com", ["gep.com"])).toBe("/");
    });

    it("puts pages nobody cited first, then the most fetched", () => {
        const rows = [
            { url_key: "a", match: "exact", ok_fetches: 50 },
            { url_key: "b", match: "none", ok_fetches: 10 },
            { url_key: "c", match: "none", ok_fetches: 40 },
            { url_key: "d", match: "near", ok_fetches: 90 },
        ];
        expect(sortFunnel(rows).map((r) => r.url_key)).toEqual(["c", "b", "d", "a"]);
    });

    it("does not mutate the caller's array", () => {
        const rows = [
            { url_key: "a", match: "exact", ok_fetches: 1 },
            { url_key: "b", match: "none", ok_fetches: 1 },
        ];
        sortFunnel(rows);
        expect(rows.map((r) => r.url_key)).toEqual(["a", "b"]);
    });
});
