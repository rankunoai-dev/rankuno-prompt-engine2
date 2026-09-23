/** Formatting helpers shared by every page. */

export const pct = (x: number | null | undefined, digits = 0): string =>
    x === null || x === undefined || Number.isNaN(x) ? "—" : `${(x * 100).toFixed(digits)}%`;

/** A 95% band as "30–88%"; em dash when either bound is missing. */
export const pctRange = (
    low: number | null | undefined,
    high: number | null | undefined,
): string =>
    low === null || low === undefined || high === null || high === undefined
        ? "—"
        : `${Math.round(low * 100)}–${Math.round(high * 100)}%`;

export const money = (x: number | null | undefined, digits = 2): string =>
    x === null || x === undefined ? "—" : `$${Number(x).toFixed(digits)}`;

export const num = (x: number | null | undefined): string =>
    x === null || x === undefined ? "—" : x.toLocaleString("en-US");

export function fmtDate(iso: string | null | undefined): string {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function fmtDateTime(iso: string | null | undefined): string {
    if (!iso) return "—";
    return new Date(iso).toLocaleString(undefined, {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
    });
}

export function fmtDuration(ms: number): string {
    const s = Math.max(0, Math.round(ms / 1000));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function relative(iso: string | null | undefined): string {
    if (!iso) return "—";
    const diff = Date.now() - new Date(iso).getTime();
    const abs = Math.abs(diff);
    const unit =
        abs < 60_000
            ? ["second", 1000]
            : abs < 3_600_000
              ? ["minute", 60_000]
              : abs < 86_400_000
                ? ["hour", 3_600_000]
                : ["day", 86_400_000];
    const n = Math.round(abs / (unit[1] as number));
    const word = `${n} ${unit[0]}${n === 1 ? "" : "s"}`;
    return diff >= 0 ? `${word} ago` : `in ${word}`;
}

/** Elapsed-time intervals used by the scheduler: daily, weekly, monthly, 4d, 10h, 6w. */
export function intervalToMs(interval: string): number | null {
    const v = interval.trim().toLowerCase();
    const named: Record<string, number> = {
        daily: 86_400_000,
        weekly: 7 * 86_400_000,
        monthly: 30 * 86_400_000,
    };
    if (named[v]) return named[v] ?? null;
    const m = v.match(/^(\d+)([hdw])$/);
    if (!m) return null;
    const n = Number(m[1]);
    return n * ({ h: 3_600_000, d: 86_400_000, w: 7 * 86_400_000 }[m[2] as "h" | "d" | "w"] ?? 0);
}

export const isPresetInterval = (value: string, presets: { value: string }[]): boolean =>
    presets.some((p) => p.value === value);

export const splitList = (s: string): string[] =>
    s
        .split(/[\n,]/)
        .map((x) => x.trim())
        .filter(Boolean);

export const hostOf = (url: string): string => {
    try {
        return new URL(url).hostname.replace(/^www\./, "");
    } catch {
        return url;
    }
};
