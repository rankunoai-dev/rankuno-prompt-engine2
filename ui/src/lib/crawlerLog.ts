/**
 * Preparing an access log in the browser before it is uploaded.
 *
 * A production access log is hundreds of megabytes and almost none of it is a
 * crawler. The server keys on the same user-agent tokens the bot catalogue
 * publishes, so filtering on those tokens here loses nothing and usually turns
 * a 300 MB file into a few hundred kilobytes — small enough to post as JSON.
 * Anything still large after filtering is streamed to the server untouched.
 *
 * Nothing in this module renders a line of the file. A log line contains an IP
 * address, and the one place an address could reach the screen is a "preview"
 * of what was filtered, so there is none.
 */

/** Everything over this, after filtering, is uploaded as a raw file instead. */
export const JSON_LIMIT_BYTES = 2_000_000;

export interface Filtered {
    /** The lines that name a known crawler, joined with newlines. */
    text: string;
    /** How many lines were kept. */
    kept: number;
    /** How many lines the file held. */
    total: number;
    /** Byte length of `text` as it will be sent. */
    bytes: number;
}

/**
 * Keep the lines that name a crawler token, case-insensitively.
 *
 * Works for both shapes the server accepts: a combined-format line carries the
 * agent in its last quoted field, and a Cloudflare NDJSON record carries it in
 * `ClientRequestUserAgent`. Both are plain substring matches, so one pass does
 * the job without parsing either format.
 */
export function prefilter(text: string, tokens: string[]): Filtered {
    const needles = tokens.map((token) => token.toLowerCase()).filter(Boolean);
    const lines = text.split(/\r?\n/);
    let total = 0;
    const kept: string[] = [];
    for (const line of lines) {
        if (!line.trim()) continue;
        total += 1;
        const lowered = line.toLowerCase();
        if (needles.some((needle) => lowered.includes(needle))) kept.push(line);
    }
    const joined = kept.length ? kept.join("\n") + "\n" : "";
    return {
        text: joined,
        kept: kept.length,
        total,
        bytes: new Blob([joined]).size,
    };
}

/** True when the filtered text is small enough to post as JSON. */
export function fitsAsJson(bytes: number): boolean {
    return bytes <= JSON_LIMIT_BYTES;
}

/** The content type a raw upload should carry, from the file's own name. */
export function rawContentType(file: File): string {
    return file.name.toLowerCase().endsWith(".gz") ? "application/gzip" : "text/plain";
}

/**
 * `Blob.text()` where it exists, `FileReader` where it does not.
 *
 * Safari only grew `Blob.text()` in 14, and jsdom still lacks it, so the one
 * path that reads a user's file keeps the older reader as a fallback rather
 * than failing with "file.text is not a function".
 */
function readAsText(file: File): Promise<string> {
    if (typeof file.text === "function") return file.text();
    return new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result ?? ""));
        reader.onerror = () => reject(reader.error ?? new Error("Could not read that file."));
        reader.readAsText(file);
    });
}

/**
 * Read a log file as text, transparently decompressing `.gz`.
 *
 * `DecompressionStream` is in every browser this app supports; where it is not
 * (jsdom, older Safari) a gzipped file simply cannot be pre-filtered, and the
 * caller falls back to the raw upload path, which is what the server wants for
 * a big file anyway.
 */
export async function readLogText(file: File): Promise<string | null> {
    if (!file.name.toLowerCase().endsWith(".gz")) return readAsText(file);
    const Decompressor = (globalThis as { DecompressionStream?: typeof DecompressionStream })
        .DecompressionStream;
    if (!Decompressor) return null;
    try {
        const stream = file.stream().pipeThrough(new Decompressor("gzip"));
        return await new Response(stream).text();
    } catch {
        return null;
    }
}

/** How a crawler's purpose is labelled and coloured wherever it appears. */
export const PURPOSE: Record<string, { label: string; color: string }> = {
    training: { label: "Training", color: "default" },
    index: { label: "Search index", color: "blue" },
    live_fetch: { label: "Live fetch", color: "green" },
};

/** How a log format is named for a reader. */
export const FORMAT_LABEL: Record<string, string> = {
    combined: "nginx / Apache",
    cloudflare: "Cloudflare",
};

/** What the import verified the crawler against. */
export const BASIS_LABEL: Record<string, string> = {
    remote_addr: "verified on remote address",
    xff: "verified on X-Forwarded-For",
    cloudflare: "verified by Cloudflare",
    none: "not verifiable",
};

/** The funnel's verdict on whether the engines cited a fetched page. */
export const MATCH_TAG: Record<string, { label: string; color: string } | null> = {
    exact: { label: "Cited", color: "green" },
    near: { label: "Cited as a variant", color: "gold" },
    none: null,
};

/** `gep.com/software/x` shown as `/software/x` when it is the project's own domain. */
export function pagePath(urlKey: string, domains: string[]): string {
    const host = urlKey.split("/", 1)[0] ?? "";
    const owned = domains.some((d) => {
        const bare = d
            .replace(/^https?:\/\//, "")
            .replace(/^www\./, "")
            .split("/")[0];
        return bare === host || host.endsWith(`.${bare}`);
    });
    return owned ? urlKey.slice(host.length) || "/" : urlKey;
}

/**
 * Funnel rows in the order an analyst should read them: pages nobody cited
 * first, then by how often a crawler actually succeeded in fetching them.
 */
export function sortFunnel<T extends { match: string; ok_fetches: number }>(rows: T[]): T[] {
    return [...rows].sort((a, b) => {
        const uncited = Number(b.match === "none") - Number(a.match === "none");
        return uncited !== 0 ? uncited : b.ok_fetches - a.ok_fetches;
    });
}
